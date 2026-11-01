//! DNS-SD registration and browsing. Hold the registration for the server's lifetime.
use anyhow::Result;
use mdns_sd::{ServiceDaemon, ServiceEvent, ServiceInfo};
use std::{collections::HashMap, net::SocketAddr, time::Duration};
pub const SERVICE: &str = "_common-golf._udp.local.";

pub struct Advertisement {
    daemon: ServiceDaemon,
    fullname: String,
}
impl Advertisement {
    pub fn new(
        name: &str,
        hostname: &str,
        port: u16,
        properties: HashMap<String, String>,
    ) -> Result<Self> {
        let daemon = ServiceDaemon::new()?;
        daemon.enable_interface(mdns_sd::IfKind::LoopbackV4)?;
        daemon.set_multicast_loop_v4(true)?;
        let info =
            ServiceInfo::new(SERVICE, name, hostname, "", port, properties)?.enable_addr_auto();
        let fullname = info.get_fullname().to_owned();
        daemon.register(info)?;
        Ok(Self { daemon, fullname })
    }
}
impl Drop for Advertisement {
    fn drop(&mut self) {
        let _ = self.daemon.unregister(&self.fullname);
        let _ = self.daemon.shutdown();
    }
}
#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct Discovered {
    pub name: String,
    pub addresses: Vec<SocketAddr>,
    pub properties: HashMap<String, String>,
}
/// Collect resolved servers for a bounded interval. Applications can browse repeatedly.
pub async fn browse(duration: Duration) -> Result<Vec<Discovered>> {
    let daemon = ServiceDaemon::new()?;
    daemon.enable_interface(mdns_sd::IfKind::LoopbackV4)?;
    daemon.set_multicast_loop_v4(true)?;
    let events = daemon.browse(SERVICE)?;
    let mut found = HashMap::new();
    let deadline = tokio::time::Instant::now() + duration;
    loop {
        match tokio::time::timeout_at(deadline, events.recv_async()).await {
            Ok(Ok(ServiceEvent::ServiceResolved(info))) => {
                found.insert(
                    info.get_fullname().to_owned(),
                    Discovered {
                        name: info.get_fullname().to_owned(),
                        addresses: info
                            .get_addresses()
                            .iter()
                            .map(|ip| SocketAddr::new(*ip, info.get_port()))
                            .collect(),
                        properties: info
                            .get_properties()
                            .iter()
                            .map(|p| (p.key().to_owned(), p.val_str().to_owned()))
                            .collect(),
                    },
                );
            }
            Ok(Ok(_)) => (),
            _ => break,
        }
    }
    daemon.stop_browse(SERVICE)?;
    daemon.shutdown()?;
    Ok(found.into_values().collect())
}
