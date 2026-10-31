//! Version 1 connector ABI. See include/cgp_rust.h for the safety contract.
#![allow(clippy::missing_safety_doc)]
use crate::{discovery, endpoint, identity::Identity, Draft, Peer, Role};
use anyhow::{bail, ensure, Result};
use serde::Deserialize;
use std::{
    cell::RefCell,
    ffi::{c_char, c_void, CStr, CString},
    panic::{catch_unwind, AssertUnwindSafe},
    path::Path,
    time::Duration,
};

/// Bound on waiting for CONNECTION_CLOSE to leave the socket. Without the
/// wait, an application that exits right after a close can drop the packet,
/// and the peer only notices at the negotiated idle timeout.
const CLOSE_FLUSH: Duration = Duration::from_secs(1);

thread_local! { static ERROR: RefCell<CString> = RefCell::new(CString::default()); }
fn guard(f: impl FnOnce() -> Result<i32>) -> i32 {
    match catch_unwind(AssertUnwindSafe(f)) {
        Ok(Ok(code)) => code,
        result => {
            let message = match result {
                Ok(Err(e)) => format!("{e:#}"),
                _ => "Rust panic".into(),
            };
            ERROR.with(|e| *e.borrow_mut() = CString::new(message.replace('\0', "?")).unwrap());
            -1
        }
    }
}
unsafe fn string<'a>(p: *const c_char) -> Result<&'a str> {
    ensure!(!p.is_null(), "null string");
    Ok(CStr::from_ptr(p).to_str()?)
}
#[derive(Deserialize)]
struct Config {
    identity_directory: String,
    role: String,
    #[serde(default = "default_draft")]
    draft: u32,
    info: serde_json::Value,
}
fn default_draft() -> u32 {
    3
}
pub struct CgpEndpoint {
    runtime: tokio::runtime::Runtime,
    identity: Identity,
    role: Role,
    draft: Draft,
    info: serde_json::Value,
    connected: Option<endpoint::Connected>,
    peer: Option<Peer>,
}
impl CgpEndpoint {
    fn disconnect(&mut self) {
        if let Some(peer) = self.peer.take() {
            peer.close();
        }
        if let Some(connected) = self.connected.take() {
            let endpoint = connected.endpoint;
            let _ = self
                .runtime
                .block_on(async { tokio::time::timeout(CLOSE_FLUSH, endpoint.wait_idle()).await });
        }
    }
}
pub type Callback = unsafe extern "C" fn(*mut c_void, u32, *const u8, u32);

#[no_mangle]
pub extern "C" fn cgp_abi_version() -> u32 {
    1
}
#[no_mangle]
pub extern "C" fn cgp_last_error() -> *const c_char {
    ERROR.with(|e| e.borrow().as_ptr())
}
#[no_mangle]
pub unsafe extern "C" fn cgp_create(config: *const c_char, out: *mut *mut CgpEndpoint) -> i32 {
    guard(|| {
        ensure!(!out.is_null(), "null output");
        *out = std::ptr::null_mut();
        let config: Config = serde_json::from_str(string(config)?)?;
        ensure!(config.info.is_object(), "info must be an object");
        crate::framing::encode(&config.info)?;
        let role = match config.role.as_str() {
            "simulator" => Role::Simulator,
            "device" => Role::Device,
            "controller" => Role::Controller,
            "device_controller" => Role::DeviceController,
            _ => bail!("invalid role"),
        };
        let draft = match config.draft {
            2 => Draft::Two,
            3 => Draft::Three,
            _ => bail!("ABI v1 supports drafts 2 and 3"),
        };
        let identity = Identity::load_or_create(Path::new(&config.identity_directory))?;
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .enable_all()
            .build()?;
        *out = Box::into_raw(Box::new(CgpEndpoint {
            runtime,
            identity,
            role,
            draft,
            info: config.info,
            connected: None,
            peer: None,
        }));
        Ok(0)
    })
}
unsafe fn handle<'a>(p: *mut CgpEndpoint) -> Result<&'a mut CgpEndpoint> {
    p.as_mut().ok_or_else(|| anyhow::anyhow!("null endpoint"))
}
fn duration(ms: u32) -> Result<Duration> {
    ensure!((1..=60_000).contains(&ms), "timeout must be 1..60000 ms");
    Ok(Duration::from_millis(ms.into()))
}
#[no_mangle]
pub unsafe extern "C" fn cgp_discover(
    p: *mut CgpEndpoint,
    ms: u32,
    callback: Option<Callback>,
    context: *mut c_void,
) -> i32 {
    guard(|| {
        let h = handle(p)?;
        let callback = callback.ok_or_else(|| anyhow::anyhow!("null callback"))?;
        let devices = h.runtime.block_on(discovery::browse(duration(ms)?))?;
        for device in devices {
            let bytes = serde_json::to_vec(&device)?;
            callback(context, 0, bytes.as_ptr(), bytes.len().try_into()?);
        }
        Ok(0)
    })
}
#[no_mangle]
pub unsafe extern "C" fn cgp_connect(
    p: *mut CgpEndpoint,
    device: *const c_char,
    fingerprint: *const c_char,
) -> i32 {
    guard(|| {
        let h = handle(p)?;
        ensure!(h.peer.is_none(), "already connected");
        let device: discovery::Discovered = serde_json::from_str(string(device)?)?;
        ensure!(
            !device.addresses.is_empty() && device.addresses.len() <= 16,
            "expected 1..16 addresses"
        );
        let expected = string(fingerprint)?;
        ensure!(
            expected.len() == 64
                && expected
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
            "expected lowercase Ed25519 public key hex"
        );
        let (connected, peer) = h.runtime.block_on(async {
            tokio::time::timeout(Duration::from_secs(10), async {
                let connected = endpoint::connect(&h.identity, &[h.draft], &device).await?;
                if crate::identity::peer_fingerprint(&connected.connection)?.as_deref()
                    != Some(expected)
                {
                    connected.connection.close(1u32.into(), b"untrusted peer");
                    let _ = tokio::time::timeout(CLOSE_FLUSH, connected.endpoint.wait_idle()).await;
                    bail!("peer fingerprint mismatch");
                }
                let peer = if h.draft == Draft::Three {
                    Peer::with_contracts(
                        connected.connection.clone(),
                        h.role,
                        h.info.clone(),
                        Vec::new(),
                        Some(expected),
                    )
                    .await?
                } else {
                    Peer::without_open_connect(connected.connection.clone(), h.role, h.info.clone())
                        .await?
                };
                Ok::<_, anyhow::Error>((connected, peer))
            })
            .await?
        })?;
        h.connected = Some(connected);
        h.peer = Some(peer);
        Ok(0)
    })
}
#[no_mangle]
pub unsafe extern "C" fn cgp_send(
    p: *mut CgpEndpoint,
    channel: u32,
    data: *const u8,
    len: u32,
    ms: u32,
) -> i32 {
    guard(|| {
        let h = handle(p)?;
        let timeout = duration(ms)?;
        ensure!(
            !data.is_null() && (1..=65_535).contains(&len),
            "invalid payload"
        );
        ensure!(
            channel <= 8 && channel != 2,
            "ABI v1 supports standard channels only"
        );
        let value: serde_json::Value =
            serde_json::from_slice(std::slice::from_raw_parts(data, len as usize))?;
        crate::framing::encode(&value)?;
        let peer = h
            .peer
            .as_mut()
            .ok_or_else(|| anyhow::anyhow!("not connected"))?;
        let result = h.runtime.block_on(async {
            tokio::time::timeout(timeout, async { peer.send(channel, &value).await }).await
        });
        // A cancelled write may have emitted a partial frame; never reuse that connection.
        match result {
            Ok(result) => {
                result?;
                Ok(0)
            }
            Err(_) => {
                h.disconnect();
                bail!("send timed out; disconnected")
            }
        }
    })
}
#[no_mangle]
pub unsafe extern "C" fn cgp_poll(
    p: *mut CgpEndpoint,
    ms: u32,
    callback: Option<Callback>,
    context: *mut c_void,
) -> i32 {
    guard(|| {
        let h = handle(p)?;
        let timeout = duration(ms)?;
        let callback = callback.ok_or_else(|| anyhow::anyhow!("null callback"))?;
        let peer = h
            .peer
            .as_mut()
            .ok_or_else(|| anyhow::anyhow!("not connected"))?;
        let event = h.runtime.block_on(async {
            tokio::select! {
                event = peer.incoming.recv() => event.ok_or_else(|| anyhow::anyhow!("receive closed")).map(Some),
                reason = peer.connection.closed() => Err(anyhow::anyhow!("connection closed: {reason}")),
                _ = tokio::time::sleep(timeout) => Ok(None),
            }
        })?;
        let Some(event) = event else {
            return Ok(1);
        };
        let bytes = serde_json::to_vec(&event.message)?;
        callback(
            context,
            event.channel,
            bytes.as_ptr(),
            bytes.len().try_into()?,
        );
        Ok(0)
    })
}
#[no_mangle]
pub unsafe extern "C" fn cgp_peer_fingerprint(
    p: *mut CgpEndpoint,
    out: *mut c_char,
    capacity: u32,
) -> i32 {
    guard(|| {
        let h = handle(p)?;
        ensure!(
            !out.is_null() && capacity >= 65,
            "fingerprint buffer needs 65 bytes"
        );
        let fingerprint = h
            .peer
            .as_ref()
            .and_then(|p| p.fingerprint.as_ref())
            .ok_or_else(|| anyhow::anyhow!("not connected"))?;
        std::ptr::copy_nonoverlapping(fingerprint.as_ptr(), out.cast(), 64);
        *out.add(64) = 0;
        Ok(0)
    })
}
#[no_mangle]
pub unsafe extern "C" fn cgp_disconnect(p: *mut CgpEndpoint) -> i32 {
    guard(|| {
        handle(p)?.disconnect();
        Ok(0)
    })
}
#[no_mangle]
pub unsafe extern "C" fn cgp_destroy(p: *mut CgpEndpoint) -> i32 {
    guard(|| {
        if !p.is_null() {
            let mut h = Box::from_raw(p);
            h.disconnect();
            drop(h);
        }
        Ok(0)
    })
}
