//! Draft 3 shot identities and JSON snapshots. Legacy typed messages remain in `message`.
use anyhow::{ensure, Result};
use serde_json::Value;
/// Canonical UUIDv7 with RFC variant. Identity is independent of measurement time.
pub fn valid_shot_uuid(value: &str) -> bool {
    let bytes = value.as_bytes();
    bytes.len() == 36
        && bytes.iter().enumerate().all(|(i, b)| {
            if [8, 13, 18, 23].contains(&i) {
                *b == b'-'
            } else {
                b.is_ascii_digit() || (b'a'..=b'f').contains(b)
            }
        })
        && bytes[14] == b'7'
        && b"89ab".contains(&bytes[19])
}
pub fn new_shot_uuid() -> Result<String> {
    let millis = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)?
        .as_millis();
    ensure!(millis < 1u128 << 48, "UUIDv7 timestamp overflow");
    let mut bytes = [0u8; 16];
    rustls::crypto::aws_lc_rs::default_provider()
        .secure_random
        .fill(&mut bytes)
        .map_err(|_| anyhow::anyhow!("secure random unavailable"))?;
    bytes[..6].copy_from_slice(&(millis as u64).to_be_bytes()[2..]);
    bytes[6] = (bytes[6] & 15) | 0x70;
    bytes[8] = (bytes[8] & 63) | 0x80;
    let h = crate::identity::hex(&bytes);
    Ok(format!(
        "{}-{}-{}-{}-{}",
        &h[..8],
        &h[8..12],
        &h[12..16],
        &h[16..20],
        &h[20..]
    ))
}
/// Validate identity and channel names at the transport boundary; application fields
/// remain ordinary JSON and schemas are repository conformance tools.
pub fn validate(channel: u32, message: &Value) -> Result<()> {
    crate::framing::encode(message)?;
    ensure!(message.is_object(), "expected JSON object");
    if channel < 1000 {
        let name = match channel {
            0 => "info",
            1 => "shot",
            3 => "club",
            4 => "device_status",
            5 => "control",
            6 => "simulator_state",
            7 => "result",
            8 => "flight",
            _ => anyhow::bail!("reserved channel"),
        };
        ensure!(
            message.get("type").and_then(Value::as_str) == Some(name),
            "channel/message type mismatch"
        );
    }
    if matches!(channel, 1 | 7 | 8) || (channel == 3 && message.get("shot_uuid").is_some()) {
        ensure!(
            message
                .get("shot_uuid")
                .and_then(Value::as_str)
                .is_some_and(valid_shot_uuid),
            "invalid or missing canonical UUIDv7"
        );
    }
    Ok(())
}
/// Whole snapshots replace whole snapshots. Correlation across channels belongs
/// to the caller; an absent optional field is not merged from an older message.
#[derive(Clone, Debug, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(transparent)]
pub struct Message(pub Value);
impl Message {
    pub fn decode(channel: u32, bytes: &[u8]) -> Result<Self> {
        ensure!(
            bytes.len() <= crate::framing::MAX_MESSAGE_BYTES,
            "message too large"
        );
        let value = serde_json::from_slice(bytes)?;
        validate(channel, &value)?;
        Ok(Self(value))
    }
    pub fn channel(&self) -> u32 {
        match self.0.get("type").and_then(Value::as_str).unwrap_or("") {
            "info" => 0,
            "shot" => 1,
            "club" => 3,
            "device_status" => 4,
            "control" => 5,
            "simulator_state" => 6,
            "result" => 7,
            "flight" => 8,
            _ => u32::MAX,
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn canonical_uuid() {
        let id = new_shot_uuid().unwrap();
        assert!(valid_shot_uuid(&id));
        assert!(!valid_shot_uuid(&id.to_uppercase()));
        assert!(!valid_shot_uuid("017f22e2-79b0-4cc3-98c4-dc0c0c07398f"));
    }
}
