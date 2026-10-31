//! Trust policy uses the authenticated raw Ed25519 public key, never a certificate hash.
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Tier {
    Internal,
    Developer,
    #[default]
    Maker,
}
pub type TrustStore = HashMap<String, Tier>;
pub fn tier(fingerprint: Option<&str>, trusted: &TrustStore) -> Tier {
    fingerprint
        .and_then(|key| trusted.get(key))
        .copied()
        .unwrap_or(Tier::Maker)
}
