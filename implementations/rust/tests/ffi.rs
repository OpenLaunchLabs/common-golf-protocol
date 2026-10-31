//! Run with CGP_C_EXAMPLE pointing to the compiled C consumer.
use common_golf_protocol::{identity::Identity, Draft, Peer, Role};
use std::{collections::HashMap, time::Duration};

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires compiled C consumer in CGP_C_EXAMPLE"]
async fn native_c_consumer_exchanges_quic_and_rejects_wrong_pin() {
    let executable = std::env::var("CGP_C_EXAMPLE").expect("set CGP_C_EXAMPLE");
    let identity = Identity::generate().unwrap();
    let endpoint = noq::Endpoint::server(
        identity.server_config(&[Draft::Two]).unwrap(),
        "127.0.0.1:0".parse().unwrap(),
    )
    .unwrap();
    let device = common_golf_protocol::discovery::Discovered {
        name: "test".into(),
        addresses: vec![endpoint.local_addr().unwrap()],
        properties: HashMap::new(),
    };
    let descriptor = serde_json::to_string(&device).unwrap();
    let server = tokio::spawn(async move {
        // First attempt authenticates the wrong pin and must close without info.
        let connection = endpoint.accept().await.unwrap().await.unwrap();
        assert!(connection.accept_uni().await.is_err());
        let connection = endpoint.accept().await.unwrap().await.unwrap();
        for channel in [2u32, 9] {
            let (mut send, _recv) = connection.open_bi().await.unwrap();
            send.write_all(&channel.to_be_bytes()).await.unwrap();
            assert_eq!(send.stopped().await.unwrap().unwrap(), 1u32.into());
        }
        let mut peer = Peer::new(
            connection,
            Role::Device,
            serde_json::json!({"type":"info", "name":"test"}),
            Default::default(),
        )
        .await
        .unwrap();
        loop {
            let event = peer.incoming.recv().await.unwrap();
            if event.channel == 6 {
                assert_eq!(event.message["type"], "state");
                peer.send(4, &serde_json::json!({"type":"status"}))
                    .await
                    .unwrap();
                tokio::time::sleep(Duration::from_millis(500)).await;
                break;
            }
        }
    });
    let pin = identity.fingerprint();
    let client = tokio::task::spawn_blocking(move || {
        let wrong = std::process::Command::new(&executable)
            .args([&descriptor, &"0".repeat(64)])
            .status()
            .unwrap();
        assert!(!wrong.success());
        let good = std::process::Command::new(&executable)
            .args([&descriptor, &pin])
            .status()
            .unwrap();
        assert!(good.success());
    });
    tokio::time::timeout(Duration::from_secs(30), async {
        client.await.unwrap();
        server.await.unwrap();
    })
    .await
    .unwrap();
}
