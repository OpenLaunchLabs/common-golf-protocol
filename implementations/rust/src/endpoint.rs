//! Discovery-first endpoint setup. Retain the endpoint while using its peers.
use crate::{
    discovery::{Advertisement, Discovered},
    identity::Identity,
    Draft,
};
use anyhow::{bail, Result};
use std::{collections::HashMap, time::Duration};

pub struct Listener {
    pub endpoint: noq::Endpoint,
    _advertisement: Advertisement,
}
impl Listener {
    /// Bind an ephemeral UDP port and advertise the actual bound port on all interfaces.
    pub fn bind(
        identity: &Identity,
        drafts: &[Draft],
        name: &str,
        hostname: &str,
        properties: HashMap<String, String>,
    ) -> Result<Self> {
        let endpoint =
            noq::Endpoint::server(identity.server_config(drafts)?, "0.0.0.0:0".parse()?)?;
        let advertisement =
            Advertisement::new(name, hostname, endpoint.local_addr()?.port(), properties)?;
        Ok(Self {
            endpoint,
            _advertisement: advertisement,
        })
    }
}
pub struct Connected {
    pub endpoint: noq::Endpoint,
    pub connection: noq::Connection,
}
/// Connect to a DNS-SD result. No user-facing address or port is necessary.
pub async fn connect(
    identity: &Identity,
    drafts: &[Draft],
    device: &Discovered,
) -> Result<Connected> {
    for address in &device.addresses {
        let bind = if address.is_ipv6() {
            "[::]:0"
        } else {
            "0.0.0.0:0"
        };
        let endpoint = noq::Endpoint::client(bind.parse()?)?;
        endpoint.set_default_client_config(identity.client_config(drafts)?);
        if let Ok(connecting) = endpoint.connect(*address, "common-golf.local") {
            if let Ok(Ok(connection)) =
                tokio::time::timeout(Duration::from_secs(5), connecting).await
            {
                return Ok(Connected {
                    endpoint,
                    connection,
                });
            }
        }
    }
    bail!("no discovered address completed a Common Golf handshake")
}
