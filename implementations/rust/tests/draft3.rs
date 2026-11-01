use common_golf_protocol::{
    custom::{Contract, Format},
    identity::{peer_fingerprint, Identity},
    Draft, Peer, Role,
};
use serde_json::json;
use std::time::Duration;

#[tokio::test]
async fn diagnostic_peer_without_draft_uses_draft_three() {
    use std::io::BufRead;
    struct RunningPeer(std::process::Child, std::path::PathBuf);
    impl Drop for RunningPeer {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
            let _ = std::fs::remove_dir_all(&self.1);
        }
    }
    let temporary = std::env::temp_dir().join(format!(
        "cgp-default-{}-{}",
        std::process::id(),
        common_golf_protocol::draft3::new_shot_uuid().unwrap()
    ));
    std::fs::create_dir(&temporary).unwrap();
    let child = std::process::Command::new(env!("CARGO_BIN_EXE_cgp-peer"))
        .args(["--role", "listen", "--duration", "5", "--identity"])
        .arg(temporary.join("identity"))
        .arg("--output")
        .arg(temporary.join("messages.jsonl"))
        .stdout(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    let mut running = RunningPeer(child, temporary);
    let output = running.0.stdout.take().unwrap();
    let mut key = String::new();
    let mut port = None;
    for line in std::io::BufReader::new(output).lines() {
        let line = line.unwrap();
        if let Some(value) = line.strip_prefix("KEY ") {
            key = value.into();
        }
        if let Some(value) = line.strip_prefix("READY ") {
            port = Some(value.parse::<u16>().unwrap());
            break;
        }
    }
    let identity = Identity::generate().unwrap();
    let endpoint = noq::Endpoint::client("127.0.0.1:0".parse().unwrap()).unwrap();
    endpoint.set_default_client_config(identity.client_config(&[Draft::default()]).unwrap());
    let connecting = endpoint
        .connect(
            format!("127.0.0.1:{}", port.unwrap()).parse().unwrap(),
            "unused.invalid",
        )
        .unwrap();
    let connection = tokio::time::timeout(Duration::from_secs(3), connecting)
        .await
        .unwrap()
        .unwrap();
    let mut peer = Peer::with_contracts(
        connection,
        Role::Simulator,
        json!({"type":"info","protocol_version":"0.3.0"}),
        Vec::new(),
        Some(&key),
    )
    .await
    .unwrap();
    assert_eq!(peer.draft, Draft::Three);
    assert_eq!(
        next(&mut peer, 0).await.message["protocol_version"],
        "0.3.0"
    );
    let shot = next(&mut peer, 1).await.message;
    assert!(common_golf_protocol::draft3::valid_shot_uuid(
        shot["shot_uuid"].as_str().unwrap()
    ));
    assert!(shot.get("shot_number").is_none());
}

async fn pair() -> (
    noq::Endpoint,
    noq::Endpoint,
    noq::Connection,
    noq::Connection,
    String,
) {
    pair_with_send_buffer(None).await
}
async fn pair_with_send_buffer(
    send_buffer: Option<usize>,
) -> (
    noq::Endpoint,
    noq::Endpoint,
    noq::Connection,
    noq::Connection,
    String,
) {
    let server_id = Identity::generate().unwrap();
    let client_id = Identity::generate().unwrap();
    let key = server_id.fingerprint();
    let mut config = server_id.server_config(&[Draft::Three]).unwrap();
    if let Some(size) = send_buffer {
        std::sync::Arc::get_mut(&mut config.transport)
            .unwrap()
            .datagram_send_buffer_size(size);
    }
    let server = noq::Endpoint::server(config, "127.0.0.1:0".parse().unwrap()).unwrap();
    let client = noq::Endpoint::client("127.0.0.1:0".parse().unwrap()).unwrap();
    client.set_default_client_config(client_id.client_config(&[Draft::Three]).unwrap());
    let outgoing = client
        .connect(server.local_addr().unwrap(), "unused.invalid")
        .unwrap();
    let (remote, local) = tokio::join!(
        async { server.accept().await.unwrap().await.unwrap() },
        outgoing
    );
    let local = local.unwrap();
    assert_eq!(
        peer_fingerprint(&remote).unwrap(),
        Some(client_id.fingerprint())
    );
    let identity = local
        .peer_identity()
        .unwrap()
        .downcast::<Vec<rustls::pki_types::CertificateDer<'static>>>()
        .unwrap();
    assert_eq!(identity[0].len(), 44);
    (server, client, remote, local, key)
}
fn contracts() -> Vec<Contract> {
    vec![
        Contract::new("com.test-json-v1", Format::Json),
        Contract::new("com.test-raw-v1", Format::Raw),
        Contract::new("com.test-dgram-v1", Format::Datagram),
        Contract::open_connect(),
    ]
}
fn info() -> serde_json::Value {
    json!({"type":"info","protocol_version":"0.3.0","contracts":[{"channel":1000,"contract":"com.test-json-v1"},{"channel":1001,"contract":"com.test-raw-v1"},{"channel":1002,"contract":"com.test-dgram-v1"},{"channel":1003,"contract":"io.common-golf.open-connect-v1"}]})
}
async fn next(peer: &mut Peer, channel: u32) -> common_golf_protocol::Event {
    loop {
        let e = tokio::time::timeout(Duration::from_secs(2), peer.incoming.recv())
            .await
            .unwrap()
            .unwrap();
        if e.channel == channel {
            return e;
        }
    }
}
#[tokio::test]
async fn raw_keys_menu_snapshots_custom_bytes_and_replies() {
    let (_s, _c, remote, local, key) = pair().await;
    let mut device = Peer::with_contracts(remote, Role::Device, info(), contracts(), None)
        .await
        .unwrap();
    let mut sim = Peer::with_contracts(
        local,
        Role::Simulator,
        json!({"type":"info","protocol_version":"0.3.0"}),
        contracts(),
        Some(&key),
    )
    .await
    .unwrap();
    sim.wait_for_menu().await.unwrap();
    assert_eq!(sim.menu().len(), 4);
    let id = common_golf_protocol::draft3::new_shot_uuid().unwrap();
    let shot = json!({"type":"shot","shot_uuid":id,"ball_speed_meters_per_second":2,"vertical_launch_angle_degrees":0,"horizontal_launch_angle_degrees":0});
    device.send(1, &shot).await.unwrap();
    assert_eq!(next(&mut sim, 1).await.message, shot);
    device.send(1000, &json!({"opaque":"json"})).await.unwrap();
    assert_eq!(next(&mut sim, 1000).await.message["opaque"], "json");
    device
        .send(1003, &json!({"ShotNumber":1,"shot_uuid":id}))
        .await
        .unwrap();
    let e = next(&mut sim, 1003).await;
    e.reply
        .unwrap()
        .send(&json!({"Code":200,"Message":"accepted"}))
        .await
        .unwrap();
    assert_eq!(next(&mut device, 1003).await.message["Code"], 200);
    let mut raw = device.open_raw(1001, false).await.unwrap();
    raw.output
        .as_mut()
        .unwrap()
        .write_all(&[0, 255, 128, 0])
        .await
        .unwrap();
    raw.output.as_mut().unwrap().finish().unwrap();
    let mut received = tokio::time::timeout(Duration::from_secs(2), sim.custom_streams.recv())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        received
            .input
            .as_mut()
            .unwrap()
            .read_to_end(100)
            .await
            .unwrap(),
        [0, 255, 128, 0]
    );
    device.send_datagram(1002, &[0, 255, 128, 0]).unwrap();
    let packet = tokio::time::timeout(Duration::from_secs(2), sim.datagrams.recv())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        (packet.channel, packet.sequence, packet.payload),
        (1002, 0, vec![0, 255, 128, 0])
    );
    assert!(device.send_datagram(1002, &vec![0; 65536]).is_err());
    assert!(device.send(2, &json!({})).await.is_err());
    assert!(device
        .send(1, &json!({"type":"shot","shot_uuid":"bad"}))
        .await
        .is_err());
    let mut changed = info();
    changed["contracts"] = json!([]);
    assert!(device.send(0, &changed).await.is_err());
}
#[tokio::test]
async fn server_stream_overtaking_info_waits_and_decline_prevents_reopen() {
    let (_s, _c, remote, local, key) = pair().await;
    let mut sim = Peer::with_contracts(
        local,
        Role::Simulator,
        json!({"type":"info","protocol_version":"0.3.0"}),
        contracts(),
        Some(&key),
    )
    .await
    .unwrap();
    let mut early = remote.open_uni().await.unwrap();
    early.write_all(&1000u32.to_be_bytes()).await.unwrap();
    early
        .write_all(&common_golf_protocol::framing::encode(&json!({"early":true})).unwrap())
        .await
        .unwrap();
    assert!(
        tokio::time::timeout(Duration::from_millis(50), early.stopped())
            .await
            .is_err()
    );
    let mut device = Peer::with_contracts(remote, Role::Device, info(), contracts(), None)
        .await
        .unwrap();
    assert_eq!(next(&mut sim, 1000).await.message["early"], true);
    // A duplicate live stream receives STOP_SENDING 1, which forbids reopening.
    device.send(1000, &json!({"duplicate":true})).await.unwrap();
    tokio::time::sleep(Duration::from_millis(50)).await;
    device.finish(1000).ok();
    assert!(device.send(1000, &json!({"retry":true})).await.is_err());
}
#[tokio::test]
async fn missing_or_wrong_pin_is_rejected_before_channels() {
    let (_s, _c, remote, local, _key) = pair().await;
    assert!(Peer::with_contracts(
        local,
        Role::Simulator,
        json!({"type":"info"}),
        contracts(),
        Some(&"0".repeat(64))
    )
    .await
    .is_err());
    assert!(remote.accept_uni().await.is_err());
    assert!(Identity::generate()
        .unwrap()
        .server_config(&[Draft::Three, Draft::Two])
        .is_err());
}

#[tokio::test]
async fn malformed_snapshot_id_is_stream_local_and_recoverable() {
    let (_s, _c, remote, local, key) = pair().await;
    let remote_wire = remote.clone();
    let local_wire = local.clone();
    let mut device = Peer::with_contracts(remote, Role::Device, info(), contracts(), None)
        .await
        .unwrap();
    let mut sim = Peer::with_contracts(
        local,
        Role::Simulator,
        json!({"type":"info","protocol_version":"0.3.0"}),
        contracts(),
        Some(&key),
    )
    .await
    .unwrap();
    let uuid = "017f22e2-79b0-7cc3-98c4-dc0c0c07398f";
    for (channel, name) in [(1u32, "shot"), (3, "club"), (7, "result"), (8, "flight")] {
        let wire = if channel != 7 {
            &remote_wire
        } else {
            &local_wire
        };
        let mut malformed = json!({"type":name,"shot_uuid":uuid.to_uppercase()});
        for attempt in 0..2 {
            let mut stream = wire.open_uni().await.unwrap();
            stream.write_all(&channel.to_be_bytes()).await.unwrap();
            stream
                .write_all(&common_golf_protocol::framing::encode(&malformed).unwrap())
                .await
                .unwrap();
            assert_eq!(
                tokio::time::timeout(Duration::from_secs(2), stream.stopped())
                    .await
                    .unwrap()
                    .unwrap(),
                Some(2u32.into()),
                "channel {channel}, malformed attempt {attempt}"
            );
            if attempt == 0 && channel != 3 {
                malformed.as_object_mut().unwrap().remove("shot_uuid");
            } else {
                malformed["shot_uuid"] = json!("017f22e2-79b0-4cc3-98c4-dc0c0c07398f");
            }
        }
        let valid = json!({"type":name,"shot_uuid":uuid});
        if channel != 7 {
            device.send(channel, &valid).await.unwrap();
            assert_eq!(next(&mut sim, channel).await.message, valid);
        } else {
            sim.send(channel, &valid).await.unwrap();
            assert_eq!(next(&mut device, channel).await.message, valid);
        }
    }
}

#[tokio::test]
async fn datagram_congestion_preserves_queued_samples_and_reports_dispatch_errors() {
    // Two packets fit in QUIC's queue. Enqueue the burst without yielding so
    // dispatch must wait for network progress instead of evicting older packets.
    let (_s, _c, remote, local, key) = pair_with_send_buffer(Some(64)).await;
    let offers = json!({"type":"info","protocol_version":"0.3.0","contracts":[
        {"channel":1002,"contract":"com.test-dgram-v1"},
        {"channel":1004,"contract":"com.test-dgram-v1"}
    ]});
    let mut device = Peer::with_contracts(remote, Role::Device, offers, contracts(), None)
        .await
        .unwrap();
    let mut sim = Peer::with_contracts(
        local,
        Role::Simulator,
        json!({"type":"info","protocol_version":"0.3.0"}),
        contracts(),
        Some(&key),
    )
    .await
    .unwrap();
    sim.wait_for_menu().await.unwrap();
    for channel in [1002, 1004] {
        for sample in 0u8..8 {
            device.send_datagram(channel, &[sample; 16]).unwrap();
        }
        assert!(device.send_datagram(channel, b"queue full").is_err());
    }
    let mut received = std::collections::BTreeSet::new();
    for _ in 0..16 {
        let packet = tokio::time::timeout(Duration::from_secs(3), sim.datagrams.recv())
            .await
            .unwrap()
            .unwrap();
        assert_eq!(packet.payload, vec![packet.sequence as u8; 16]);
        received.insert((packet.channel, packet.sequence));
    }
    assert_eq!(
        received.len(),
        16,
        "no samples evicted from the local QUIC queue"
    );
    // This fits the advertised path limit but not our deliberately tiny local
    // QUIC buffer, exercising a real asynchronous TooLarge dispatch rejection.
    assert!(device.max_datagram_payload().unwrap() > 80);
    device.send_datagram(1002, &[0; 80]).unwrap();
    let failed = tokio::time::timeout(Duration::from_secs(2), device.datagram_errors.recv())
        .await
        .unwrap()
        .unwrap();
    assert_eq!((failed.channel, failed.sequence), (1002, 8));
    assert!(matches!(failed.error, noq::SendDatagramError::TooLarge));
    device
        .send(4, &json!({"type":"device_status","ready":true}))
        .await
        .unwrap();
    assert_eq!(next(&mut sim, 4).await.message["ready"], true);
    device.send_datagram(1002, b"after-error").unwrap();
    let packet = tokio::time::timeout(Duration::from_secs(2), sim.datagrams.recv())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(packet.sequence, 9);
    assert_eq!(packet.payload, b"after-error");
}
