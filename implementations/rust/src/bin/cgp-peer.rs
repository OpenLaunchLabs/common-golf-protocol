//! Diagnostic interop peer. Direct addresses here are test/support inputs, not a product UI.
use anyhow::{bail, Context, Result};
use common_golf_protocol::{
    custom::{Contract, Format},
    identity::Identity,
    Draft, Peer, Role,
};
use serde_json::json;
use std::{
    collections::{HashMap, HashSet},
    io::Write,
    net::SocketAddr,
    path::Path,
    sync::{Arc, Mutex},
    time::Duration,
};

#[tokio::main]
async fn main() -> Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() % 2 != 0 {
        bail!("usage: cgp-peer [--draft 1|2|3 (default 3)] --role listen|connect --host HOST --port PORT --output FILE --identity DIR [--duration SECONDS] [--version VERSION]");
    }
    let args: HashMap<_, _> = args
        .chunks(2)
        .map(|p| (p[0].as_str(), p[1].as_str()))
        .collect();
    let draft = match args.get("--draft").copied() {
        Some("1") => Draft::One,
        Some("2") => Draft::Two,
        Some("3") | None => Draft::default(),
        _ => bail!("unsupported draft"),
    };
    let version = args
        .get("--version")
        .copied()
        .unwrap_or(draft.protocol_version())
        .to_owned();
    if !matches!(
        (draft, version.as_str()),
        (Draft::One, "0.1.0" | "0.1.1") | (Draft::Two, "0.2.0") | (Draft::Three, "0.3.0")
    ) {
        bail!("version is not part of selected draft");
    }
    let duration = Duration::from_secs_f64(args.get("--duration").copied().unwrap_or("3").parse()?);
    let role = args.get("--role").copied().unwrap_or("listen");
    let port: u16 = args.get("--port").copied().unwrap_or("0").parse()?;
    let host = args.get("--host").copied().unwrap_or("127.0.0.1");
    if role == "discover" {
        let devices = common_golf_protocol::discovery::browse(duration).await?;
        let mut output =
            std::fs::File::create(args.get("--output").copied().unwrap_or("cgp-output.jsonl"))?;
        for device in devices {
            if let Some(address) = device.addresses.first() {
                writeln!(
                    output,
                    "{}",
                    json!({"kind":"discovered","name":device.name,"port":address.port()})
                )?;
            }
        }
        return Ok(());
    }
    let identity = Identity::load_or_create(Path::new(
        args.get("--identity").copied().unwrap_or(".cgp-identity"),
    ))?;
    let fixtures = args
        .get("--fixtures")
        .map(std::fs::read_to_string)
        .transpose()?
        .map(|text| serde_json::from_str::<serde_json::Value>(&text))
        .transpose()?;
    let output = Arc::new(Mutex::new(std::fs::File::create(
        args.get("--output").copied().unwrap_or("cgp-output.jsonl"),
    )?));
    if role == "listen" {
        let host = if args.contains_key("--advertise") {
            "0.0.0.0"
        } else {
            host
        };
        let address: SocketAddr = format!("{host}:{port}").parse()?;
        let endpoint = noq::Endpoint::server(identity.server_config(&[draft])?, address)?;
        let _advertisement = args
            .get("--advertise")
            .map(|name| {
                common_golf_protocol::discovery::Advertisement::new(
                    name,
                    &format!("cgp-rust-{}.local.", std::process::id()),
                    endpoint.local_addr().unwrap().port(),
                    if draft == Draft::Three {
                        HashMap::from([
                            ("key".into(), identity.fingerprint()),
                            ("alpn".into(), "draft-3".into()),
                        ])
                    } else {
                        HashMap::new()
                    },
                )
            })
            .transpose()?;
        println!("KEY {}", identity.fingerprint());
        println!("READY {}", endpoint.local_addr()?.port());
        std::io::stdout().flush()?;
        let deadline = tokio::time::Instant::now() + duration;
        let mut sessions = tokio::task::JoinSet::new();
        loop {
            tokio::select! {
                _ = tokio::time::sleep_until(deadline) => break,
                incoming = endpoint.accept() => {
                    let Some(incoming) = incoming else { break; };
                    let output = output.clone();
                    let version = version.clone();
                    let fixtures = fixtures.clone();
                    sessions.spawn(async move {
                        match incoming.await {
                            Ok(connection) => exchange(connection, true, version, output, deadline, fixtures, None).await,
                            Err(error) => { eprintln!("handshake: {error}"); Ok(()) }
                        }
                    });
                }
                result = sessions.join_next(), if !sessions.is_empty() => {
                    if let Some(result) = result { result??; }
                }
            }
        }
        sessions.shutdown().await;
        endpoint.close(0_u32.into(), b"done");
        endpoint.wait_idle().await;
    } else if role == "connect" {
        let endpoint = noq::Endpoint::client("0.0.0.0:0".parse()?)?;
        endpoint.set_default_client_config(identity.client_config(&[draft])?);
        let connection = tokio::time::timeout(
            Duration::from_secs(5),
            endpoint.connect(format!("{host}:{port}").parse()?, "common-golf.local")?,
        )
        .await
        .context("handshake timeout")??;
        exchange(
            connection,
            false,
            version,
            output,
            tokio::time::Instant::now() + duration,
            fixtures,
            args.get("--expected-key").map(|s| s.to_string()),
        )
        .await?;
        endpoint.close(0_u32.into(), b"done");
        endpoint.wait_idle().await;
    } else {
        bail!("role must be listen or connect");
    }
    Ok(())
}
async fn exchange(
    connection: noq::Connection,
    device: bool,
    version: String,
    output: Arc<Mutex<std::fs::File>>,
    deadline: tokio::time::Instant,
    fixtures: Option<serde_json::Value>,
    expected_key: Option<String>,
) -> Result<()> {
    let role = if device {
        Role::DeviceController
    } else {
        Role::Simulator
    };
    let draft = connection
        .handshake_data()
        .and_then(|d| d.downcast::<noq::crypto::rustls::HandshakeData>().ok())
        .and_then(|d| d.protocol.as_deref().and_then(Draft::from_alpn))
        .context("missing ALPN")?;
    let mut info = json!({"type":"info","protocol_version":version,"name":"Rust reference","channels":if device {vec![0,1,3,4,5,8]} else {vec![0,6,7]}});
    let mut peer = if draft == Draft::Three {
        if device {
            info["contracts"] = json!([
            {"channel":1000,"contract":"io.common-golf.interop-json-v1"},
            {"channel":1001,"contract":"io.common-golf.interop-raw-v1"},
            {"channel":1002,"contract":"io.common-golf.interop-datagram-v1"},
            {"channel":1003,"contract":"io.common-golf.open-connect-v1"}]);
        }
        Peer::with_contracts(
            connection,
            role,
            info,
            vec![
                Contract::new("io.common-golf.interop-json-v1", Format::Json),
                Contract::new("io.common-golf.interop-raw-v1", Format::Raw),
                Contract::new("io.common-golf.interop-datagram-v1", Format::Datagram),
                Contract::open_connect(),
            ],
            expected_key.as_deref(),
        )
        .await?
    } else {
        Peer::new(connection, role, info, HashSet::from([1000])).await?
    };
    let mut pending_custom = Vec::new();
    if let Some(fixtures) = fixtures {
        let draft_key = match peer.draft {
            Draft::One => "1",
            Draft::Two => "2",
            Draft::Three => "3",
        };
        let records = fixtures["drafts"][draft_key][if device { "listen" } else { "connect" }]
            .as_array()
            .context("invalid message fixture corpus")?;
        for record in records {
            let channel = u32::try_from(record["channel"].as_u64().context("fixture channel")?)?;
            if channel < 1000 {
                peer.send(channel, &record["message"]).await?;
            } else {
                pending_custom.push((channel, record["message"].clone()));
            }
        }
    } else {
        if device {
            let mut shot = json!({"type":"shot","shot_number":1,"ball_speed_meters_per_second":60.0,"vertical_launch_angle_degrees":12.0,"horizontal_launch_angle_degrees":-1.0,"final":true});
            match peer.draft {
                Draft::One => shot["reduced_accuracy"] = json!(true),
                Draft::Two | Draft::Three => {
                    shot["estimated"] = json!(["ball_speed_meters_per_second"])
                }
            }
            if peer.draft == Draft::Three {
                shot.as_object_mut().unwrap().remove("shot_number");
                shot.as_object_mut().unwrap().remove("final");
                shot["shot_uuid"] = json!("017f22e2-79b0-7cc3-98c4-dc0c0c07398f");
            }
            peer.send(1, &shot).await?;
            let club = if peer.draft == Draft::Three {
                json!({"type":"club","shot_uuid":"017f22e2-79b0-7cc3-98c4-dc0c0c07398f","club_speed_meters_per_second":40.0,"estimated":["club_speed_meters_per_second"]})
            } else {
                json!({"type":"club","shot_number":1,"club_speed_meters_per_second":40.0,"estimated":["club_speed_meters_per_second"]})
            };
            peer.send(3, &club).await?;
            peer.send(
                4,
                &json!({"type":if peer.draft==Draft::Three {"device_status"}else{"status"},"ready":true,"battery_percent":90}),
            )
            .await?;
            peer.send(
                5,
                &json!({"type":"control","command":"left","event":"press"}),
            )
            .await?;
            peer.send(
                8,
                &if peer.draft==Draft::Three {json!({"type":"flight","shot_uuid":"017f22e2-79b0-7cc3-98c4-dc0c0c07398f","carry_distance_meters":210})}else{json!({"type":"flight","shot_number":1,"carry_distance_meters":210})},
            )
            .await?;
            pending_custom.push((1000,json!({"type":"private","name":"io.common-golf.interop-json-v1","message":"opaque JSON ☃"})));
            peer.send(if peer.draft==Draft::Three {1003}else{2}, &json!({"DeviceID":"Rust reference","Units":"Yards","ShotNumber":2,"APIversion":"1","BallData":{"Speed":130.0,"SpinAxis":0.0,"VLA":12.0,"HLA":-1.0},"ShotDataOptions":{"ContainsBallData":true,"ContainsClubData":false}})).await?;
        } else {
            peer.send(6, &json!({"type":if peer.draft==Draft::Three {"simulator_state"}else{"state"},"ready":true}))
                .await?;
            peer.send(7, &if peer.draft==Draft::Three {json!({"type":"result","shot_uuid":"017f22e2-79b0-7cc3-98c4-dc0c0c07398f","accepted":true})}else{json!({"type":"result","shot_number":1,"accepted":true})})
                .await?;
        }
    }
    {
        let mut file = output.lock().unwrap();
        writeln!(
            file,
            "{}",
            json!({"kind":"connected","fingerprint":peer.fingerprint,"alpn":String::from_utf8_lossy(peer.draft.alpn())})
        )?;
        file.flush()?;
    }
    for (channel, message) in pending_custom {
        peer.send(channel, &message).await?;
    }
    if peer.draft == Draft::Three {
        peer.wait_for_menu().await?;
        let mut raw = peer.open_raw(1001, false).await?;
        let output = raw.output.as_mut().unwrap();
        output.write_all(&[0, 255, 128, 0, 97, 98, 99]).await?;
        output.finish()?;
        peer.send_datagram(1002, &[0, 255, 128, 0, 97, 98, 99])?;
    }
    let mut sample_tick = tokio::time::interval(Duration::from_millis(100));
    let mut sample_count = 0;
    let mut count = 0;
    loop {
        tokio::select! {
            _ = tokio::time::sleep_until(deadline) => break,
            _ = sample_tick.tick(), if peer.draft==Draft::Three && sample_count<5 => {
                peer.send_datagram(1002,&[0,255,128,0,97,98,99])?;sample_count+=1;
            }
            Some(mut raw) = peer.custom_streams.recv(), if peer.draft==Draft::Three => {
                let payload=raw.input.as_mut().unwrap().read_to_end(65535).await?;
                let mut file=output.lock().unwrap();writeln!(file,"{}",json!({"kind":"raw","channel":raw.channel,"payload_hex":common_golf_protocol::identity::hex(&payload)}))?;file.flush()?;
            }
            Some(datagram) = peer.datagrams.recv(), if peer.draft==Draft::Three => {
                let mut file=output.lock().unwrap();writeln!(file,"{}",json!({"kind":"datagram","channel":datagram.channel,"sequence":datagram.sequence,"payload_hex":common_golf_protocol::identity::hex(&datagram.payload)}))?;file.flush()?;
            }
            Some(error) = peer.datagram_errors.recv(), if peer.draft==Draft::Three => {
                bail!("datagram {}/{}: {}", error.channel, error.sequence, error.error);
            }
            event = peer.incoming.recv() => {
                let Some(event) = event else { break; };
                {
                    let mut file = output.lock().unwrap();
                    writeln!(file, "{}", json!({"channel":event.channel,"message":event.message}))?;
                    file.flush()?;
                }
                count += 1;
                if let Some(reply) = event.reply { reply.send(&json!({"Code":200,"Message":"Shot received"})).await?; }
            }
        }
    }
    if count == 0 {
        bail!("peer provided no messages");
    }
    Ok(())
}
