//! Discovery-first draft 3 launch monitor and simulator using the Rust library.
use anyhow::{bail, Context, Result};
use common_golf_protocol::{endpoint, identity::Identity, Draft, Peer, Role};
use serde_json::json;
use std::{collections::HashMap, path::Path, time::Duration};

#[tokio::main]
async fn main() -> Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() % 2 != 0 {
        bail!("usage: cgp-demo --role listen|connect [--name NAME] [--identity DIR] [--duration SECONDS]");
    }
    let args: HashMap<_, _> = args
        .chunks(2)
        .map(|p| (p[0].as_str(), p[1].as_str()))
        .collect();
    let role = args.get("--role").copied().unwrap_or("listen");
    let name = args.get("--name").copied().unwrap_or("Rust launch monitor");
    let default_identity = format!(".cgp-identities/rust-{role}");
    let identity = Identity::load_or_create(Path::new(
        args.get("--identity").copied().unwrap_or(&default_identity),
    ))?;
    let duration =
        Duration::from_secs_f64(args.get("--duration").copied().unwrap_or("60").parse()?);
    println!("fingerprint: {}", identity.fingerprint());
    let deadline = tokio::time::Instant::now() + duration;
    match role {
        "listen" => {
            let server = endpoint::Server::bind(
                &identity,
                &[Draft::Three],
                name,
                &format!("cgp-rust-demo-{}.local.", std::process::id()),
                HashMap::from([("role".into(), "launch_monitor".into())]),
            )?;
            println!("advertising {name}");
            let mut sessions = tokio::task::JoinSet::new();
            loop {
                tokio::select! {
                    _ = tokio::time::sleep_until(deadline) => break,
                    incoming = server.endpoint.accept() => {
                        let Some(incoming) = incoming else { break; };
                        let name = name.to_owned();
                        sessions.spawn(async move { exchange(incoming.await?, Role::Device, &name, deadline, None).await });
                    }
                    done = sessions.join_next(), if !sessions.is_empty() => {
                        if let Some(done) = done {
                            if let Err(error) = done? { eprintln!("connection ended: {error}"); }
                        }
                    }
                }
            }
            sessions.shutdown().await;
            server.endpoint.close(0u32.into(), b"demo complete");
            server.endpoint.wait_idle().await;
        }
        "connect" => {
            let devices = common_golf_protocol::discovery::browse(Duration::from_secs(2)).await?;
            let fullname = format!("{name}.{}", common_golf_protocol::discovery::SERVICE);
            let device = devices
                .iter()
                .find(|d| d.name == name || d.name == fullname)
                .context("named launch monitor was not discovered")?;
            // The demo server binds IPv4; try those discovered addresses first.
            let mut device = device.clone();
            device.addresses.sort_by_key(|address| address.is_ipv6());
            let connected = endpoint::connect(&identity, &[Draft::Three], &device).await?;
            let deadline = tokio::time::Instant::now() + duration;
            exchange(
                connected.connection.clone(),
                Role::Simulator,
                "Rust simulator",
                deadline,
                device.properties.get("key").map(String::as_str),
            )
            .await?;
            connected.endpoint.close(0u32.into(), b"demo complete");
            connected.endpoint.wait_idle().await;
        }
        _ => bail!("role must be listen or connect"),
    }
    Ok(())
}
async fn exchange(
    connection: noq::Connection,
    role: Role,
    name: &str,
    deadline: tokio::time::Instant,
    expected_key: Option<&str>,
) -> Result<()> {
    let device = role == Role::Device;
    let mut peer = Peer::with_contracts(connection, role,
        json!({"type":"info","protocol_version":"0.3.0","name":name,"channels":if device {vec![0,1,4]} else {vec![0,6,7]}}), Vec::new(), expected_key).await?;
    println!("connected: {:?}", peer.fingerprint);
    if device {
        peer.send(4, &json!({"type":"device_status","ready":true}))
            .await?;
    } else {
        peer.send(6, &json!({"type":"simulator_state","ready":true}))
            .await?;
    }
    let mut shots = tokio::time::interval(Duration::from_secs(2));
    loop {
        tokio::select! {
            _ = tokio::time::sleep_until(deadline) => break,
            _ = peer.connection.closed() => break,
            _ = shots.tick(), if device => {
                let shot_uuid=common_golf_protocol::draft3::new_shot_uuid()?;
                peer.send(1, &json!({"type":"shot","shot_uuid":shot_uuid,"ball_speed_meters_per_second":60,"vertical_launch_angle_degrees":12,"horizontal_launch_angle_degrees":0})).await?;
            }
            event = peer.incoming.recv() => {
                let Some(event) = event else { break; };
                println!("channel {}: {}", event.channel, event.message);
                if !device && event.channel == 1 {
                    peer.send(7, &json!({"type":"result","shot_uuid":event.message["shot_uuid"],"accepted":true})).await?;
                }
            }
        }
    }
    Ok(())
}
