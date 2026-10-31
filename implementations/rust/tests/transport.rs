use common_golf_protocol::{identity::Identity, Draft, Peer, Role};
use serde_json::json;
use std::{collections::HashSet, time::Duration};

async fn connections(
    draft: Draft,
) -> (
    noq::Endpoint,
    noq::Endpoint,
    noq::Connection,
    noq::Connection,
) {
    let server_id = Identity::generate().unwrap();
    let client_id = Identity::generate().unwrap();
    let server = noq::Endpoint::server(
        server_id.server_config(&[draft]).unwrap(),
        "127.0.0.1:0".parse().unwrap(),
    )
    .unwrap();
    let client = noq::Endpoint::client("127.0.0.1:0".parse().unwrap()).unwrap();
    client.set_default_client_config(client_id.client_config(&[draft]).unwrap());
    let connect = client
        .connect(server.local_addr().unwrap(), "ignored.local")
        .unwrap();
    let (remote, local) = tokio::join!(
        async { server.accept().await.unwrap().await.unwrap() },
        connect
    );
    let local = local.unwrap();
    assert_eq!(
        common_golf_protocol::identity::peer_fingerprint(&local).unwrap(),
        Some(server_id.fingerprint())
    );
    assert_eq!(
        common_golf_protocol::identity::peer_fingerprint(&remote).unwrap(),
        Some(client_id.fingerprint())
    );
    (server, client, remote, local)
}
async fn event(peer: &mut Peer, channel: u32) -> serde_json::Value {
    loop {
        let event = tokio::time::timeout(Duration::from_secs(2), peer.incoming.recv())
            .await
            .unwrap()
            .unwrap();
        if event.channel == channel {
            return event.message;
        }
    }
}
#[tokio::test]
async fn malformed_and_unowned_streams_do_not_close_connection() {
    let (_server, _client, device, sim) = connections(Draft::Two).await;
    let mut receiver = Peer::new(
        sim,
        Role::Simulator,
        json!({"type":"info","protocol_version":"0.2.0"}),
        HashSet::new(),
    )
    .await
    .unwrap();
    let mut wrong = device.open_uni().await.unwrap();
    wrong.write_all(&6_u32.to_be_bytes()).await.unwrap();
    assert_eq!(wrong.stopped().await.unwrap(), Some(1_u32.into()));
    let mut malformed = device.open_uni().await.unwrap();
    malformed
        .write_all(&[0, 0, 0, 1, 0, 1, 0, 0])
        .await
        .unwrap();
    assert_eq!(malformed.stopped().await.unwrap(), Some(2_u32.into()));
    assert!(device.close_reason().is_none());
    let mut good = device.open_uni().await.unwrap();
    good.write_all(&1_u32.to_be_bytes()).await.unwrap();
    let shot = json!({"type":"shot","shot_number":1,"ball_speed_meters_per_second":50,"vertical_launch_angle_degrees":12,"horizontal_launch_angle_degrees":0});
    good.write_all(&common_golf_protocol::framing::encode(&shot).unwrap())
        .await
        .unwrap();
    assert_eq!(event(&mut receiver, 1).await, shot);
    let mut duplicate = device.open_uni().await.unwrap();
    duplicate.write_all(&1_u32.to_be_bytes()).await.unwrap();
    assert_eq!(duplicate.stopped().await.unwrap(), Some(1_u32.into()));
    good.finish().unwrap();
    good.stopped().await.unwrap();
    let mut reopened = device.open_uni().await.unwrap();
    reopened.write_all(&1_u32.to_be_bytes()).await.unwrap();
    let mut next = shot.clone();
    next["shot_number"] = 2.into();
    reopened
        .write_all(&common_golf_protocol::framing::encode(&next).unwrap())
        .await
        .unwrap();
    assert_eq!(event(&mut receiver, 1).await, next);
}
#[tokio::test]
async fn mismatched_alpn_fails_handshake() {
    let identity = Identity::generate().unwrap();
    let server = noq::Endpoint::server(
        identity.server_config(&[Draft::One]).unwrap(),
        "127.0.0.1:0".parse().unwrap(),
    )
    .unwrap();
    let client = noq::Endpoint::client("127.0.0.1:0".parse().unwrap()).unwrap();
    client.set_default_client_config(identity.client_config(&[Draft::Two]).unwrap());
    let connect = client
        .connect(server.local_addr().unwrap(), "common-golf.local")
        .unwrap();
    let (server_result, client_result) =
        tokio::join!(async { server.accept().await.unwrap().await }, connect);
    assert!(server_result.is_err());
    assert!(client_result.is_err());
}
#[test]
fn patch_replaces_nested_objects_and_clears_nulls() {
    let mut snapshot = json!({"player":{"name":"One","dexterity":"right"},"ready":true})
        .as_object()
        .unwrap()
        .clone();
    let patch = json!({"player":{"name":"Two"},"ready":null});
    common_golf_protocol::apply_patch(&mut snapshot, patch.as_object().unwrap());
    assert_eq!(snapshot["player"], json!({"name":"Two"}));
    assert!(!snapshot.contains_key("ready"));
}
#[test]
fn identity_is_persistent_and_private() {
    let directory = std::env::temp_dir().join(format!("cgp-rust-id-{}", std::process::id()));
    let first = Identity::load_or_create(&directory).unwrap();
    let second = Identity::load_or_create(&directory).unwrap();
    assert_eq!(first.fingerprint(), second.fingerprint());
    assert_eq!(first.fingerprint().len(), 64);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            std::fs::metadata(directory.join("identity.pem"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
    }
    std::fs::remove_dir_all(directory).unwrap();
}

#[tokio::test]
async fn discovery_advertises_bound_port() {
    use common_golf_protocol::discovery::{browse, Advertisement};
    let name = format!("CGP Rust test {}", std::process::id());
    let advertisement = Advertisement::new(
        &name,
        &format!("cgp-test-{}.local.", std::process::id()),
        54321,
        Default::default(),
    )
    .unwrap();
    let devices = browse(Duration::from_secs(5)).await.unwrap();
    let found = devices
        .iter()
        .find(|d| d.name.starts_with(&name))
        .expect("mDNS multicast must be enabled for discovery test");
    assert!(found.addresses.iter().any(|a| a.port() == 54321));
    drop(advertisement);
}

#[tokio::test]
async fn listener_serves_two_connections_and_maximum_frames() {
    let identity = Identity::generate().unwrap();
    let server = noq::Endpoint::server(
        identity.server_config(&[Draft::One, Draft::Two]).unwrap(),
        "127.0.0.1:0".parse().unwrap(),
    )
    .unwrap();
    let client = noq::Endpoint::client("127.0.0.1:0".parse().unwrap()).unwrap();
    client.set_default_client_config(identity.client_config(&[Draft::Two]).unwrap());
    let first = client
        .connect(server.local_addr().unwrap(), "local")
        .unwrap();
    let (first_remote, first_local) = tokio::join!(
        async { server.accept().await.unwrap().await.unwrap() },
        first
    );
    let second = client
        .connect(server.local_addr().unwrap(), "local")
        .unwrap();
    let (second_remote, second_local) = tokio::join!(
        async { server.accept().await.unwrap().await.unwrap() },
        second
    );
    let mut peers = Vec::new();
    for connection in [first_local.unwrap(), second_local.unwrap()] {
        peers.push(
            Peer::new(
                connection,
                Role::Simulator,
                json!({"type":"info","protocol_version":"0.2.0"}),
                HashSet::new(),
            )
            .await
            .unwrap(),
        );
    }
    let mut value = json!({"type":"status","message":""});
    let overhead = serde_json::to_vec(&value).unwrap().len();
    value["message"] = "x".repeat(65535 - overhead).into();
    let bytes = common_golf_protocol::framing::encode(&value).unwrap();
    assert_eq!(bytes.len(), 65539);
    for remote in [&first_remote, &second_remote] {
        let mut malformed = remote.open_uni().await.unwrap();
        malformed
            .write_all(&[0, 0, 0, 1, 0, 0, 0, 2, b'{'])
            .await
            .unwrap();
        malformed.finish().unwrap();
        let mut stream = remote.open_uni().await.unwrap();
        stream.write_all(&4_u32.to_be_bytes()).await.unwrap();
        stream.write_all(&bytes).await.unwrap();
        stream.finish().unwrap();
    }
    for peer in &mut peers {
        assert_eq!(event(peer, 4).await, value);
        assert!(peer.connection.close_reason().is_none());
    }
}

#[tokio::test]
async fn runtime_delivers_json_without_schema_conformance() {
    let (_server, _client, device, sim) = connections(Draft::Two).await;
    let mut receiver = Peer::new(sim, Role::Simulator, json!({}), HashSet::new())
        .await
        .unwrap();
    let mut sender = Peer::new(device, Role::Device, json!({}), HashSet::new())
        .await
        .unwrap();
    for message in [
        json!({}),
        json!({"type":"shot","ball_speed_meters_per_second":-1}),
        json!({"type":"shot","estimated":["missing"],"future":{"x":true}}),
    ] {
        sender.send(1, &message).await.unwrap();
        assert_eq!(event(&mut receiver, 1).await, message);
    }
}

#[tokio::test]
async fn supported_drafts_exchange_private_json_and_open_connect_replies() {
    for draft in [Draft::One, Draft::Two] {
        let (_server, _client, device, sim) = connections(draft).await;
        let mut sender = Peer::new(
            device,
            Role::Device,
            json!({"type":"info"}),
            HashSet::from([1000]),
        )
        .await
        .unwrap();
        let mut receiver = Peer::new(
            sim,
            Role::Simulator,
            json!({"type":"info"}),
            HashSet::from([1000]),
        )
        .await
        .unwrap();
        sender
            .send(1000, &json!({"type":"private","name":"test","value":"☃"}))
            .await
            .unwrap();
        assert_eq!(event(&mut receiver, 1000).await["value"], "☃");
        assert!(sender.send(999, &json!({})).await.is_err());
        assert!(sender.send(1001, &json!({})).await.is_err());
        let request = json!({"DeviceID":"test","Units":"Yards","ShotNumber":1,"APIversion":"1"});
        sender.send(2, &request).await.unwrap();
        let reply = loop {
            let event = tokio::time::timeout(Duration::from_secs(2), receiver.incoming.recv())
                .await
                .unwrap()
                .unwrap();
            if event.channel == 2 {
                assert_eq!(event.message, request);
                break event.reply.unwrap();
            }
        };
        let response = json!({"Code":200,"Message":"Shot received"});
        reply.send(&response).await.unwrap();
        assert_eq!(event(&mut sender, 2).await, response);
        sender.send(1, &json!({"type":"shot","estimated":["ball_speed_meters_per_second"],"reduced_accuracy":true,"ball_speed_meters_per_second":50})).await.unwrap();
        let shot = event(&mut receiver, 1).await;
        assert_eq!(shot.get("estimated").is_some(), draft == Draft::Two);
        assert_eq!(shot.get("reduced_accuracy").is_some(), draft == Draft::One);
    }
}
