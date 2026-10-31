//! Per-connection JSON channel lifecycle for drafts 1 and 2.
use crate::{framing, Draft};
use anyhow::{bail, Result};
use serde_json::Value;
use std::{
    collections::{HashMap, HashSet},
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{
    sync::{mpsc, Mutex as AsyncMutex, OwnedSemaphorePermit, Semaphore},
    task::{JoinHandle, JoinSet},
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Role {
    Device,
    Controller,
    Simulator,
    DeviceController,
}
impl Role {
    pub fn owns(self, channel: u32) -> bool {
        channel == 0
            || match self {
                Self::Device => matches!(channel, 1..=4 | 8),
                Self::Controller => channel == 5,
                Self::Simulator => matches!(channel, 6 | 7),
                Self::DeviceController => matches!(channel, 1..=5 | 8),
            }
    }
    fn receives(self, channel: u32) -> bool {
        channel == 0
            || match self {
                Self::Simulator => matches!(channel, 1..=5 | 8),
                _ => matches!(channel, 6 | 7),
            }
    }
}
#[derive(Clone)]
pub struct Reply(Arc<AsyncMutex<noq::SendStream>>);
impl Reply {
    /// Channel 2 replies have a length prefix, never another channel prefix.
    pub async fn send(&self, message: &Value) -> Result<()> {
        self.0
            .lock()
            .await
            .write_all(&framing::encode(message)?)
            .await?;
        Ok(())
    }
}
pub struct Event {
    pub channel: u32,
    pub message: Value,
    pub reply: Option<Reply>,
}
pub struct Peer {
    pub connection: noq::Connection,
    pub draft: Draft,
    pub fingerprint: Option<String>,
    pub incoming: mpsc::Receiver<Event>,
    role: Role,
    private: HashSet<u32>,
    outgoing: HashMap<u32, Arc<AsyncMutex<noq::SendStream>>>,
    events: mpsc::Sender<Event>,
    task: JoinHandle<()>,
    replies: JoinSet<()>,
}
impl Peer {
    /// Send info before opening any other stream. Private JSON channels must be explicitly arranged.
    pub async fn new(
        connection: noq::Connection,
        role: Role,
        info: Value,
        private: HashSet<u32>,
    ) -> Result<Self> {
        Self::build(connection, role, info, private, true).await
    }
    /// Connector ABI subset; Open Connect replies require the Rust Reply API.
    pub(crate) async fn without_open_connect(
        connection: noq::Connection,
        role: Role,
        info: Value,
    ) -> Result<Self> {
        Self::build(connection, role, info, HashSet::new(), false).await
    }
    async fn build(
        connection: noq::Connection,
        role: Role,
        info: Value,
        private: HashSet<u32>,
        open_connect: bool,
    ) -> Result<Self> {
        if private.iter().any(|c| *c < 1000) {
            bail!("private channel IDs start at 1000");
        }
        let draft = connection
            .handshake_data()
            .and_then(|data| data.downcast::<noq::crypto::rustls::HandshakeData>().ok())
            .and_then(|data| data.protocol.as_deref().and_then(Draft::from_alpn))
            .ok_or_else(|| anyhow::anyhow!("unsupported ALPN"))?;
        let fingerprint = crate::identity::peer_fingerprint(&connection)?;
        let mut stream = connection.open_uni().await?;
        stream.write_all(&0_u32.to_be_bytes()).await?;
        stream.write_all(&framing::encode(&info)?).await?;
        let outgoing = HashMap::from([(0, Arc::new(AsyncMutex::new(stream)))]);
        let (events, incoming) = mpsc::channel(128);
        let driver = connection.clone();
        let event_sender = events.clone();
        let arranged = private.clone();
        let task = tokio::spawn(async move {
            run(driver, role, arranged, event_sender, open_connect).await;
        });
        Ok(Self {
            connection,
            draft,
            fingerprint,
            incoming,
            role,
            private,
            outgoing,
            events,
            task,
            replies: JoinSet::new(),
        })
    }
    /// Reuse a channel's stream until explicitly finished; STOP_SENDING affects only that channel.
    pub async fn send(&mut self, channel: u32, message: &Value) -> Result<()> {
        while self.replies.try_join_next().is_some() {}
        if !(self.role.owns(channel) || self.private.contains(&channel)) {
            bail!("role does not own channel {channel}");
        }
        let bytes = framing::encode(&for_draft(self.draft, channel, message))?;
        if !self.outgoing.contains_key(&channel) {
            let mut stream = if channel == 2 {
                let (send, recv) = self.connection.open_bi().await?;
                let events = self.events.clone();
                self.replies.spawn(async move {
                    receive_messages(recv, 2, events, None).await;
                });
                send
            } else {
                self.connection.open_uni().await?
            };
            stream.write_all(&channel.to_be_bytes()).await?;
            self.outgoing
                .insert(channel, Arc::new(AsyncMutex::new(stream)));
        }
        let result = self
            .outgoing
            .get(&channel)
            .unwrap()
            .lock()
            .await
            .write_all(&bytes)
            .await;
        if result.is_err() {
            self.outgoing.remove(&channel);
        }
        result?;
        Ok(())
    }
    /// Release a channel; shot counters belong to the application and continue across reopen.
    pub fn finish(&mut self, channel: u32) -> Result<()> {
        if let Some(stream) = self.outgoing.remove(&channel) {
            stream.try_lock()?.finish()?;
        }
        Ok(())
    }
    pub fn close(&self) {
        self.connection.close(0_u32.into(), b"done");
    }
}
impl Drop for Peer {
    fn drop(&mut self) {
        self.task.abort();
        self.replies.abort_all();
    }
}

/// Draft-specific wire metadata is selected by ALPN. Unknown fields otherwise survive.
pub fn for_draft(draft: Draft, channel: u32, message: &Value) -> Value {
    let mut value = message.clone();
    if let (Some(fields), Some(excluded)) =
        (value.as_object_mut(), draft.excluded_metadata(channel))
    {
        fields.remove(excluded);
    }
    value
}
struct Active {
    channel: u32,
    channels: Arc<Mutex<HashSet<u32>>>,
}
impl Drop for Active {
    fn drop(&mut self) {
        self.channels.lock().unwrap().remove(&self.channel);
    }
}
async fn run(
    connection: noq::Connection,
    role: Role,
    private: HashSet<u32>,
    events: mpsc::Sender<Event>,
    open_connect: bool,
) {
    let active = Arc::new(Mutex::new(HashSet::new()));
    let receiving = Arc::new(Semaphore::new(64));
    let mut tasks = JoinSet::new();
    loop {
        tokio::select! {
            incoming = connection.accept_uni() => {
                let Ok(mut recv) = incoming else { break; };
                let Ok(permit) = receiving.clone().try_acquire_owned() else { let _ = recv.stop(1_u32.into()); continue; };
                tasks.spawn(receive(recv, None, role, private.clone(), active.clone(), events.clone(), permit, open_connect));
            }
            incoming = connection.accept_bi() => {
                let Ok((mut send, mut recv)) = incoming else { break; };
                let Ok(permit) = receiving.clone().try_acquire_owned() else { let _ = recv.stop(1_u32.into()); let _ = send.reset(1_u32.into()); continue; };
                tasks.spawn(receive(recv, Some(send), role, private.clone(), active.clone(), events.clone(), permit, open_connect));
            }
            _ = tasks.join_next(), if !tasks.is_empty() => (),
            _ = events.closed() => break,
        }
    }
    tasks.shutdown().await;
}
#[allow(clippy::too_many_arguments)]
async fn receive(
    mut recv: noq::RecvStream,
    mut send: Option<noq::SendStream>,
    role: Role,
    private: HashSet<u32>,
    active: Arc<Mutex<HashSet<u32>>>,
    events: mpsc::Sender<Event>,
    _receiving: OwnedSemaphorePermit,
    open_connect: bool,
) {
    let mut prefix = [0; 4];
    if !matches!(
        tokio::time::timeout(Duration::from_secs(10), recv.read_exact(&mut prefix)).await,
        Ok(Ok(()))
    ) {
        let _ = recv.stop(2_u32.into());
        if let Some(send) = send.as_mut() {
            let _ = send.reset(2_u32.into());
        }
        return;
    }
    let channel = u32::from_be_bytes(prefix);
    if !(role.receives(channel) || private.contains(&channel))
        || (channel == 2 && !open_connect)
        || (channel == 2) != send.is_some()
        || !active.lock().unwrap().insert(channel)
    {
        let _ = recv.stop(1_u32.into());
        if let Some(send) = send.as_mut() {
            let _ = send.reset(1_u32.into());
        }
        return;
    }
    let _guard = Active {
        channel,
        channels: active,
    };
    let reply = send.map(|s| Reply(Arc::new(AsyncMutex::new(s))));
    receive_messages(recv, channel, events, reply).await;
}
async fn receive_messages(
    mut recv: noq::RecvStream,
    channel: u32,
    events: mpsc::Sender<Event>,
    reply: Option<Reply>,
) {
    loop {
        match framing::read_message(&mut recv).await {
            Ok(Some(message)) => {
                if events
                    .send(Event {
                        channel,
                        message,
                        reply: reply.clone(),
                    })
                    .await
                    .is_err()
                {
                    break;
                }
            }
            Ok(None) => break,
            Err(_) => {
                let _ = recv.stop(2_u32.into());
                break;
            }
        }
    }
}
