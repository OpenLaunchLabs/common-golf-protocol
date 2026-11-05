//! Draft 3 immutable server menus and locally registered contract definitions.
use crate::Role;
use anyhow::{bail, ensure, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::{BTreeMap, HashMap, HashSet, VecDeque},
    sync::Mutex,
};
use tokio::sync::watch;
pub const MAX_RAW_STREAMS: usize = 16;
pub const OPEN_CONNECT: &str = "io.common-golf.open-connect-v1";
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Format {
    Json,
    Raw,
    Datagram,
    RawDatagram,
}
/// Application roles permitted to initiate streams, independent of the QUIC role.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StreamInitiator {
    Either,
    Device,
    Simulator,
}
#[derive(Clone, Debug)]
pub struct Contract {
    pub identifier: String,
    pub format: Format,
    pub initiator: StreamInitiator,
    pub bidirectional: bool,
}
impl Contract {
    pub fn new(identifier: &str, format: Format) -> Self {
        Self {
            identifier: identifier.into(),
            format,
            initiator: StreamInitiator::Either,
            bidirectional: false,
        }
    }
    pub fn open_connect() -> Self {
        Self {
            identifier: OPEN_CONNECT.into(),
            format: Format::Json,
            initiator: StreamInitiator::Device,
            bidirectional: true,
        }
    }
    pub fn opens(&self, role: Role) -> bool {
        self.initiator == StreamInitiator::Either
            || match self.initiator {
                StreamInitiator::Device => matches!(role, Role::Device | Role::DeviceController),
                StreamInitiator::Simulator => role == Role::Simulator,
                StreamInitiator::Either => true,
            }
    }
    pub fn reads(&self, role: Role) -> bool {
        self.initiator == StreamInitiator::Either
            || match self.initiator {
                StreamInitiator::Device => role == Role::Simulator,
                StreamInitiator::Simulator => matches!(role, Role::Device | Role::DeviceController),
                StreamInitiator::Either => true,
            }
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Offer {
    pub channel: u32,
    pub contract: String,
}
pub fn valid_identifier(value: &str) -> bool {
    (1..=128).contains(&value.len())
        && value
            .bytes()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || b"._-".contains(&c))
}
pub fn menu(info: &Value) -> Result<BTreeMap<u32, String>> {
    ensure!(
        info.get("type").and_then(Value::as_str) == Some("info"),
        "channel 0 requires info"
    );
    if let Some(reads) = info.get("reads") {
        let reads = reads
            .as_array()
            .ok_or_else(|| anyhow::anyhow!("reads must be an array"))?;
        ensure!(
            reads
                .iter()
                .all(|c| c.as_u64().is_some_and(|c| c <= 8 && c != 2)),
            "invalid reads"
        );
    }
    if let Some(reads) = info.get("reads_contracts") {
        let reads = reads
            .as_array()
            .ok_or_else(|| anyhow::anyhow!("reads_contracts must be an array"))?;
        ensure!(
            reads
                .iter()
                .all(|s| s.as_str().is_some_and(valid_identifier)),
            "invalid reads_contracts"
        );
    }
    let Some(offers) = info.get("contracts") else {
        return Ok(BTreeMap::new());
    };
    let offers = offers
        .as_array()
        .ok_or_else(|| anyhow::anyhow!("contracts must be an array"))?;
    ensure!(offers.len() <= 16, "at most 16 menu entries");
    let mut result = BTreeMap::new();
    for offer in offers {
        let channel = offer
            .get("channel")
            .and_then(Value::as_u64)
            .filter(|c| (1000..=u32::MAX as u64).contains(c))
            .ok_or_else(|| {
                anyhow::anyhow!("menu channel must be a uint32 decimal integer >=1000")
            })? as u32;
        let id = offer
            .get("contract")
            .and_then(Value::as_str)
            .filter(|s| valid_identifier(s))
            .ok_or_else(|| anyhow::anyhow!("invalid contract identifier"))?;
        ensure!(
            result.insert(channel, id.into()).is_none(),
            "duplicate channel"
        );
    }
    Ok(result)
}
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum MenuStatus {
    Pending,
    Ready,
    Invalid,
}
pub(crate) struct State {
    is_server: bool,
    contracts: HashMap<String, Contract>,
    mapping: Mutex<Option<BTreeMap<u32, String>>>,
    status: watch::Sender<MenuStatus>,
    pub declined: Mutex<HashSet<u32>>,
}
impl State {
    pub fn new(is_server: bool, contracts: Vec<Contract>, info: &Value) -> Result<Self> {
        let mut registry = HashMap::new();
        for contract in contracts {
            ensure!(
                valid_identifier(&contract.identifier),
                "invalid registered contract"
            );
            if contract.identifier == OPEN_CONNECT {
                ensure!(
                    contract.format == Format::Json
                        && contract.initiator == StreamInitiator::Device
                        && contract.bidirectional,
                    "invalid Open Connect contract definition"
                );
            }
            ensure!(
                registry
                    .insert(contract.identifier.clone(), contract)
                    .is_none(),
                "duplicate contract definition"
            );
        }
        let mapping = if is_server {
            Some(menu(info)?)
        } else {
            menu(info)?;
            None
        };
        if let Some(mapping) = &mapping {
            ensure!(
                mapping.values().all(|id| registry.contains_key(id)),
                "server offered unregistered contract"
            );
        }
        Ok(Self {
            is_server,
            contracts: registry,
            mapping: Mutex::new(mapping),
            status: watch::channel(if is_server {
                MenuStatus::Ready
            } else {
                MenuStatus::Pending
            })
            .0,
            declined: Mutex::new(HashSet::new()),
        })
    }
    pub fn receive_info(&self, info: &Value) -> Result<()> {
        let offered = match menu(info) {
            Ok(m) => m,
            Err(e) => {
                if !self.is_server && self.status.borrow().eq(&MenuStatus::Pending) {
                    self.status.send_replace(MenuStatus::Invalid);
                }
                return Err(e);
            }
        };
        if self.is_server {
            return Ok(());
        }
        let mut mapping = self.mapping.lock().unwrap();
        if *self.status.borrow() == MenuStatus::Invalid {
            bail!("initial menu was invalid");
        }
        if let Some(existing) = mapping.as_ref() {
            ensure!(*existing == offered, "server changed its fixed menu");
        } else {
            *mapping = Some(offered);
            self.status.send_replace(MenuStatus::Ready);
        }
        Ok(())
    }
    pub fn fail_initial(&self) {
        if !self.is_server && *self.status.borrow() == MenuStatus::Pending {
            self.status.send_replace(MenuStatus::Invalid);
        }
    }
    pub fn validate_local_info(&self, info: &Value) -> Result<()> {
        let offered = menu(info)?;
        if self.is_server {
            ensure!(
                self.mapping.lock().unwrap().as_ref() == Some(&offered),
                "server menu cannot change"
            );
        }
        Ok(())
    }
    pub fn ready(&self) -> bool {
        *self.status.borrow() == MenuStatus::Ready
    }
    pub async fn wait(&self) -> Result<()> {
        let mut status = self.status.subscribe();
        loop {
            match *status.borrow_and_update() {
                MenuStatus::Ready => return Ok(()),
                MenuStatus::Invalid => bail!("custom menu unavailable"),
                MenuStatus::Pending => (),
            }
            status.changed().await?;
        }
    }
    pub fn contract(&self, channel: u32) -> Option<Contract> {
        self.mapping
            .lock()
            .unwrap()
            .as_ref()?
            .get(&channel)
            .and_then(|id| self.contracts.get(id))
            .cloned()
    }
    pub fn offers(&self) -> Vec<Offer> {
        self.mapping
            .lock()
            .unwrap()
            .as_ref()
            .map(|m| {
                m.iter()
                    .map(|(channel, contract)| Offer {
                        channel: *channel,
                        contract: contract.clone(),
                    })
                    .collect()
            })
            .unwrap_or_default()
    }
}
pub fn datagram(channel: u32, sequence: u64, payload: &[u8], limit: usize) -> Result<Vec<u8>> {
    ensure!(
        channel >= 1000 && payload.len().checked_add(12).is_some_and(|n| n <= limit),
        "invalid channel or oversized datagram"
    );
    let mut result = Vec::with_capacity(12 + payload.len());
    result.extend(channel.to_be_bytes());
    result.extend(sequence.to_be_bytes());
    result.extend(payload);
    Ok(result)
}
pub fn decode_datagram(bytes: &[u8]) -> Option<(u32, u64, &[u8])> {
    if bytes.len() < 12 {
        return None;
    }
    let channel = u32::from_be_bytes(bytes[..4].try_into().ok()?);
    (channel >= 1000).then(|| {
        (
            channel,
            u64::from_be_bytes(bytes[4..12].try_into().unwrap()),
            &bytes[12..],
        )
    })
}
#[derive(Default)]
pub(crate) struct DatagramQueue {
    queues: BTreeMap<u32, VecDeque<Vec<u8>>>,
    order: VecDeque<u32>,
    sequences: HashMap<u32, Option<u64>>,
}
impl DatagramQueue {
    pub fn push(&mut self, channel: u32, payload: &[u8], limit: usize) -> Result<()> {
        ensure!(
            self.queues.values().map(VecDeque::len).sum::<usize>() < 128,
            "datagram connection queue full"
        );
        let queue = self.queues.entry(channel).or_default();
        ensure!(queue.len() < 8, "datagram channel queue full");
        let next = self.sequences.entry(channel).or_insert(Some(0));
        let sequence = next.ok_or_else(|| anyhow::anyhow!("datagram sequence exhausted"))?;
        let packet = datagram(channel, sequence, payload, limit)?;
        if queue.is_empty() {
            self.order.push_back(channel);
        }
        queue.push_back(packet);
        *next = sequence.checked_add(1);
        Ok(())
    }
    pub fn pop(&mut self) -> Option<Vec<u8>> {
        let c = self.order.pop_front()?;
        let q = self.queues.get_mut(&c)?;
        let p = q.pop_front();
        if q.is_empty() {
            self.queues.remove(&c);
        } else {
            self.order.push_back(c);
        }
        p
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn menu_lexical_bounds_and_immutability() {
        for channel in ["1000.0", "1e3", "999", "4294967296", "\"1000\""] {
            let v:Value=serde_json::from_str(&format!("{{\"type\":\"info\",\"contracts\":[{{\"channel\":{channel},\"contract\":\"com.test-v1\"}}]}}")).unwrap();
            assert!(menu(&v).is_err());
        }
        let s = State::new(
            false,
            vec![Contract::new("com.test-v1", Format::Json)],
            &json!({"type":"info"}),
        )
        .unwrap();
        s.receive_info(
            &json!({"type":"info","contracts":[{"channel":1000,"contract":"com.test-v1"}]}),
        )
        .unwrap();
        assert!(s.receive_info(&json!({"type":"info"})).is_err());
        assert!(s.contract(1000).is_some());
        for id in ["A", "", "a/b", "é"] {
            assert!(!valid_identifier(id));
        }
        let invalid = State::new(false, Vec::new(), &json!({"type":"info"})).unwrap();
        assert!(invalid
            .receive_info(&json!({"type":"info","contracts":null}))
            .is_err());
        assert!(invalid.receive_info(&json!({"type":"info"})).is_err());
        assert!(!invalid.ready());
    }
    #[test]
    fn datagrams_are_bounded_fair_and_never_wrap() {
        let mut queue = DatagramQueue::default();
        for _ in 0..8 {
            queue.push(1000, b"a", 1200).unwrap();
        }
        assert!(queue.push(1000, b"a", 1200).is_err());
        queue.push(1001, b"b", 1200).unwrap();
        let first = queue.pop().unwrap();
        let second = queue.pop().unwrap();
        assert_eq!(decode_datagram(&first), Some((1000, 0, &b"a"[..])));
        assert_eq!(decode_datagram(&second), Some((1001, 0, &b"b"[..])));
        queue.sequences.insert(1002, Some(u64::MAX));
        queue.push(1002, b"last", 1200).unwrap();
        assert!(queue.push(1002, b"wrapped", 1200).is_err());
    }
}
