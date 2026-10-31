//! Rust implementation of the Common Golf Protocol.
//! Wire draft selection is exclusively ALPN; `info.protocol_version` is descriptive.
pub mod custom;
pub mod discovery;
pub mod draft3;
pub mod endpoint;
pub mod ffi;
pub mod framing;
pub mod identity;
pub mod message;
pub mod streams;
pub mod transport;
pub mod trust;
pub use message::Draft;
pub use transport::{Event, Peer, Role};

/// Apply status/state patches. A nested object replaces the old object whole.
pub fn apply_patch(
    snapshot: &mut serde_json::Map<String, serde_json::Value>,
    patch: &serde_json::Map<String, serde_json::Value>,
) {
    for (key, value) in patch {
        if value.is_null() {
            snapshot.remove(key);
        } else {
            snapshot.insert(key.clone(), value.clone());
        }
    }
}
