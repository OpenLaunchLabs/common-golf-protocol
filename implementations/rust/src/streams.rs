//! Shared cancellation and lifetime accounting for application-owned custom handles.
use anyhow::{bail, Result};
use std::sync::{Arc, Mutex, Weak};
use tokio::sync::{watch, Mutex as AsyncMutex, OwnedSemaphorePermit, Semaphore};

pub(crate) struct CustomIo {
    runtime: tokio::runtime::Handle,
    pub limit: Arc<Semaphore>,
    pub failed: watch::Sender<bool>,
    receives: Mutex<Vec<Weak<AsyncMutex<noq::RecvStream>>>>,
    sends: Mutex<Vec<Weak<AsyncMutex<noq::SendStream>>>>,
}
impl CustomIo {
    pub fn new() -> Self {
        Self {
            runtime: tokio::runtime::Handle::current(),
            limit: Arc::new(Semaphore::new(crate::custom::MAX_RAW_STREAMS)),
            failed: watch::channel(false).0,
            receives: Mutex::new(Vec::new()),
            sends: Mutex::new(Vec::new()),
        }
    }
    pub fn register_send(&self, stream: &Arc<AsyncMutex<noq::SendStream>>) {
        let mut sends = self.sends.lock().unwrap();
        sends.retain(|entry| entry.strong_count() != 0);
        sends.push(Arc::downgrade(stream));
        if *self.failed.borrow() {
            if let Ok(mut stream) = stream.try_lock() {
                let _ = stream.reset(2u32.into());
            }
        }
    }
    pub fn cancel(&self) {
        if self.failed.send_replace(true) {
            return;
        }
        let receives = std::mem::take(&mut *self.receives.lock().unwrap());
        let sends = std::mem::take(&mut *self.sends.lock().unwrap());
        // Every SDK I/O operation observes `failed` and releases its lock, so a
        // suspended application read/write cannot prevent stream cancellation.
        self.runtime.spawn(async move {
            for receive in receives {
                if let Some(receive) = receive.upgrade() {
                    let _ = receive.lock().await.stop(2u32.into());
                }
            }
            for send in sends {
                if let Some(send) = send.upgrade() {
                    let _ = send.lock().await.reset(2u32.into());
                }
            }
        });
    }
    pub fn wrap(
        &self,
        input: Option<noq::RecvStream>,
        output: Option<noq::SendStream>,
        permit: OwnedSemaphorePermit,
    ) -> (Option<RawRecvStream>, Option<RawSendStream>, Arc<Lease>) {
        let lease = Arc::new(Lease(permit));
        let input = input.map(|input| {
            let id = input.id();
            let inner = Arc::new(AsyncMutex::new(input));
            let mut receives = self.receives.lock().unwrap();
            receives.retain(|entry| entry.strong_count() != 0);
            receives.push(Arc::downgrade(&inner));
            RawRecvStream {
                inner,
                id,
                failed: self.failed.subscribe(),
                _lease: lease.clone(),
            }
        });
        let output = output.map(|output| {
            let id = output.id();
            let inner = Arc::new(AsyncMutex::new(output));
            self.register_send(&inner);
            RawSendStream {
                inner,
                id,
                failed: self.failed.subscribe(),
                _lease: lease.clone(),
            }
        });
        // Registration can race a driver failure on another executor thread.
        if *self.failed.borrow() {
            if let Some(input) = &input {
                if let Ok(mut input) = input.inner.try_lock() {
                    let _ = input.stop(2u32.into());
                }
            }
            if let Some(output) = &output {
                if let Ok(mut output) = output.inner.try_lock() {
                    let _ = output.reset(2u32.into());
                }
            }
        }
        (input, output, lease)
    }
}
#[allow(dead_code)]
pub(crate) struct Lease(OwnedSemaphorePermit);
/// Incremental custom input. It retains its connection permit even when moved
/// out of `CustomStream`, and connection failure interrupts pending reads.
pub struct RawRecvStream {
    inner: Arc<AsyncMutex<noq::RecvStream>>,
    id: noq::StreamId,
    failed: watch::Receiver<bool>,
    _lease: Arc<Lease>,
}
impl RawRecvStream {
    pub fn id(&self) -> noq::StreamId {
        self.id
    }
    pub async fn read_exact(&mut self, bytes: &mut [u8]) -> Result<()> {
        let mut inner = self.inner.lock().await;
        if *self.failed.borrow() {
            bail!("custom connection closed");
        }
        tokio::select! { result = inner.read_exact(bytes) => { result?; Ok(()) }, _ = self.failed.changed() => { let _ = inner.stop(2u32.into()); bail!("custom connection closed") } }
    }
    pub async fn read_to_end(&mut self, limit: usize) -> Result<Vec<u8>> {
        let mut inner = self.inner.lock().await;
        if *self.failed.borrow() {
            bail!("custom connection closed");
        }
        tokio::select! { result = inner.read_to_end(limit) => Ok(result?), _ = self.failed.changed() => { let _ = inner.stop(2u32.into()); bail!("custom connection closed") } }
    }
    pub async fn read_chunk(&mut self, maximum: usize) -> Result<Option<bytes::Bytes>> {
        let mut inner = self.inner.lock().await;
        if *self.failed.borrow() {
            bail!("custom connection closed");
        }
        tokio::select! { result = inner.read_chunk(maximum) => Ok(result?), _ = self.failed.changed() => { let _ = inner.stop(2u32.into()); bail!("custom connection closed") } }
    }
    pub fn stop(&mut self, code: noq::VarInt) -> Result<()> {
        self.inner.try_lock()?.stop(code)?;
        Ok(())
    }
}
/// Incremental custom output with shared permit ownership and cancellation.
pub struct RawSendStream {
    inner: Arc<AsyncMutex<noq::SendStream>>,
    id: noq::StreamId,
    failed: watch::Receiver<bool>,
    _lease: Arc<Lease>,
}
impl RawSendStream {
    pub fn id(&self) -> noq::StreamId {
        self.id
    }
    pub async fn write_all(&mut self, bytes: &[u8]) -> Result<()> {
        let mut inner = self.inner.lock().await;
        if *self.failed.borrow() {
            bail!("custom connection closed");
        }
        tokio::select! { result = inner.write_all(bytes) => { result?; Ok(()) }, _ = self.failed.changed() => { let _ = inner.reset(2u32.into()); bail!("custom connection closed") } }
    }
    pub async fn stopped(&mut self) -> Result<Option<noq::VarInt>> {
        let inner = self.inner.lock().await;
        if *self.failed.borrow() {
            bail!("custom connection closed");
        }
        tokio::select! { result = inner.stopped() => Ok(result?), _ = self.failed.changed() => bail!("custom connection closed") }
    }
    pub fn finish(&mut self) -> Result<()> {
        if *self.failed.borrow() {
            bail!("custom connection closed");
        }
        self.inner.try_lock()?.finish()?;
        Ok(())
    }
    pub fn reset(&mut self, code: noq::VarInt) -> Result<()> {
        self.inner.try_lock()?.reset(code)?;
        Ok(())
    }
}
