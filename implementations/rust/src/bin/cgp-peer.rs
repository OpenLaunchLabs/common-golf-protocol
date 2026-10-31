//! Diagnostic interop peer. Direct addresses here are test/support inputs, not a product UI.
use anyhow::{bail, Context, Result};
use common_golf_protocol::{identity::Identity, Draft, Peer, Role};
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
        bail!("usage: cgp-peer --draft 1|2 --role listen|connect --host HOST --port PORT --output FILE --identity DIR [--duration SECONDS] [--version VERSION]");
    }
    let args: HashMap<_, _> = args
        .chunks(2)
        .map(|p| (p[0].as_str(), p[1].as_str()))
        .collect();
    let draft = match args.get("--draft").copied().unwrap_or("2") {
        "1" => Draft::One,
        "2" => Draft::Two,
        _ => bail!("unsupported draft"),
    };
    let version = args
        .get("--version")
        .copied()
        .unwrap_or(draft.protocol_version())
        .to_owned();
    if !matches!(
        (draft, version.as_str()),
        (Draft::One, "0.1.0" | "0.1.1") | (Draft::Two, "0.2.0")
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
                    HashMap::new(),
                )
            })
            .transpose()?;
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
                            Ok(connection) => exchange(connection, true, version, output, deadline, fixtures).await,
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
) -> Result<()> {
    let role = if device {
        Role::DeviceController
    } else {
        Role::Simulator
    };
    let private = HashSet::from([1000]);
    let mut peer = Peer::new(connection, role, json!({"type":"info","protocol_version":version,"name":"Rust reference","channels":if device {vec![0,1,2,3,4,5,8]} else {vec![0,6,7]}}), private).await?;
    let mut pending_custom = Vec::new();
    if let Some(fixtures) = fixtures {
        let draft_key = match peer.draft {
            Draft::One => "1",
            Draft::Two => "2",
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
                Draft::Two => shot["estimated"] = json!(["ball_speed_meters_per_second"]),
            }
            peer.send(1, &shot).await?;
            peer.send(3, &json!({"type":"club","shot_number":1,"club_speed_meters_per_second":40.0,"estimated":["club_speed_meters_per_second"]})).await?;
            peer.send(
                4,
                &json!({"type":"status","ready":true,"battery_percent":90}),
            )
            .await?;
            peer.send(
                5,
                &json!({"type":"control","command":"left","event":"press"}),
            )
            .await?;
            peer.send(
                8,
                &json!({"type":"flight","shot_number":1,"carry_distance_meters":210}),
            )
            .await?;
            pending_custom.push((1000,json!({"type":"private","name":"io.common-golf.interop-json-v1","message":"opaque JSON ☃"})));
            peer.send(2, &json!({"DeviceID":"Rust reference","Units":"Yards","ShotNumber":2,"APIversion":"1","BallData":{"Speed":130.0,"SpinAxis":0.0,"VLA":12.0,"HLA":-1.0},"ShotDataOptions":{"ContainsBallData":true,"ContainsClubData":false}})).await?;
        } else {
            peer.send(6, &json!({"type":"state","ready":true,"mode":"range"}))
                .await?;
            peer.send(7, &json!({"type":"result","shot_number":1,"accepted":true}))
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
    let mut count = 0;
    loop {
        tokio::select! {
            _ = tokio::time::sleep_until(deadline) => break,
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
