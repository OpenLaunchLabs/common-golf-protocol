//! Per-connection channel lifecycle, including draft 3 server menus.
use crate::{
    custom::{self, Contract, Format},
    framing,
    streams::{CustomIo, Lease, RawRecvStream, RawSendStream},
    Draft,
};
use anyhow::{bail, Result};
use serde_json::Value;
use std::{
    collections::{HashMap, HashSet},
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{
    sync::{mpsc, Mutex as AsyncMutex, Notify, OwnedSemaphorePermit, Semaphore},
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
    /// Bidirectional JSON replies have a length prefix, never another channel prefix.
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
pub struct CustomStream {
    pub channel: u32,
    pub input: Option<RawRecvStream>,
    pub output: Option<RawSendStream>,
    _lease: Arc<Lease>,
}
pub struct Datagram {
    pub channel: u32,
    pub sequence: u64,
    pub payload: Vec<u8>,
}
/// A queued datagram rejected by QUIC at dispatch (for example after a path change).
#[derive(Debug)]
pub struct DatagramSendError {
    pub channel: u32,
    pub sequence: u64,
    pub error: noq::SendDatagramError,
}
pub struct Peer {
    pub connection: noq::Connection,
    pub draft: Draft,
    pub fingerprint: Option<String>,
    pub incoming: mpsc::Receiver<Event>,
    pub custom_streams: mpsc::Receiver<CustomStream>,
    pub datagrams: mpsc::Receiver<Datagram>,
    pub datagram_errors: mpsc::Receiver<DatagramSendError>,
    custom: Option<Arc<custom::State>>,
    custom_io: Arc<CustomIo>,
    datagram_queue: Arc<Mutex<custom::DatagramQueue>>,
    datagram_ready: Arc<Notify>,
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
    /// Client ABI subset; Open Connect replies require the Rust Reply API.
    pub(crate) async fn without_open_connect(
        connection: noq::Connection,
        role: Role,
        info: Value,
    ) -> Result<Self> {
        Self::build(connection, role, info, HashSet::new(), false).await
    }
    /// Draft 3 contract policy and optional server menu supplied in `info.contracts`.
    /// Clients provide the advertised server key, checked before any channel opens.
    pub async fn with_contracts(
        connection: noq::Connection,
        role: Role,
        info: Value,
        contracts: Vec<Contract>,
        expected_key: Option<&str>,
    ) -> Result<Self> {
        Self::build_inner(
            connection,
            role,
            info,
            HashSet::new(),
            false,
            contracts,
            expected_key,
        )
        .await
    }
    async fn build(
        connection: noq::Connection,
        role: Role,
        info: Value,
        private: HashSet<u32>,
        open_connect: bool,
    ) -> Result<Self> {
        Self::build_inner(
            connection,
            role,
            info,
            private,
            open_connect,
            Vec::new(),
            None,
        )
        .await
    }
    #[allow(clippy::too_many_arguments)]
    async fn build_inner(
        connection: noq::Connection,
        role: Role,
        info: Value,
        private: HashSet<u32>,
        open_connect: bool,
        contracts: Vec<Contract>,
        expected_key: Option<&str>,
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
        let custom = if draft == Draft::Three {
            if !private.is_empty() {
                bail!("draft 3 requires a server menu, not static private bindings");
            }
            if connection.side() == noq::Side::Client {
                crate::identity::check_expected_key(
                    &connection,
                    expected_key
                        .ok_or_else(|| anyhow::anyhow!("draft 3 requires advertised server key"))?,
                )?;
            }
            crate::draft3::validate(0, &info)?;
            Some(Arc::new(custom::State::new(
                connection.side() == noq::Side::Server,
                contracts,
                &info,
            )?))
        } else {
            if !contracts.is_empty() {
                bail!("menus require draft 3");
            }
            None
        };
        let custom_io = Arc::new(CustomIo::new());
        let datagram_queue = Arc::new(Mutex::new(custom::DatagramQueue::default()));
        let datagram_ready = Arc::new(Notify::new());
        let (custom_sender, custom_streams) = mpsc::channel(16);
        let (datagram_sender, datagrams) = mpsc::channel(128);
        let (datagram_error_sender, datagram_errors) = mpsc::channel(128);
        let mut stream = connection.open_uni().await?;
        stream.write_all(&0_u32.to_be_bytes()).await?;
        stream.write_all(&framing::encode(&info)?).await?;
        let mut replies = JoinSet::new();
        if let Some(state) = &custom {
            let stopped = stream.stopped();
            let state = state.clone();
            replies.spawn(async move {
                if matches!(stopped.await, Ok(Some(code)) if code == noq::VarInt::from_u32(1)) {
                    state.declined.lock().unwrap().insert(0);
                }
            });
        }
        let outgoing = HashMap::from([(0, Arc::new(AsyncMutex::new(stream)))]);
        let (events, incoming) = mpsc::channel(128);
        let driver = connection.clone();
        let event_sender = events.clone();
        let arranged = private.clone();
        let task_custom = custom.clone();
        let task_io = custom_io.clone();
        let task_queue = datagram_queue.clone();
        let task_ready = datagram_ready.clone();
        let task = tokio::spawn(async move {
            if let Some(custom) = task_custom {
                run3(
                    driver,
                    role,
                    custom,
                    event_sender,
                    custom_sender,
                    datagram_sender,
                    task_io,
                    task_queue,
                    task_ready,
                    datagram_error_sender,
                )
                .await;
            } else {
                run(driver, role, arranged, event_sender, open_connect).await;
            }
        });
        Ok(Self {
            connection,
            draft,
            fingerprint,
            incoming,
            role,
            private,
            custom,
            custom_io,
            datagram_queue,
            datagram_ready,
            custom_streams,
            datagrams,
            datagram_errors,
            outgoing,
            events,
            task,
            replies,
        })
    }
    /// Reuse a channel's stream until explicitly finished; STOP_SENDING affects only that channel.
    pub async fn send(&mut self, channel: u32, message: &Value) -> Result<()> {
        while self.replies.try_join_next().is_some() {}
        if self.custom.is_some() {
            return self.send3(channel, message).await;
        }
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
    /// Release a channel; shot identities belong to the application and survive reopen.
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
        self.custom_io.cancel();
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

impl Peer {
    pub fn menu(&self) -> Vec<custom::Offer> {
        self.custom.as_ref().map(|s| s.offers()).unwrap_or_default()
    }
    pub async fn wait_for_menu(&self) -> Result<()> {
        let state = self
            .custom
            .as_ref()
            .ok_or_else(|| anyhow::anyhow!("menus require draft 3"))?;
        tokio::select! { result=state.wait()=>result, _=self.connection.closed()=>bail!("connection closed before menu") }
    }
    fn watch_decline(&mut self, channel: u32, send: &noq::SendStream) {
        let stopped = send.stopped();
        let state = self.custom.as_ref().unwrap().clone();
        self.replies.spawn(async move {
            if let Ok(Some(code)) = stopped.await {
                if code == noq::VarInt::from_u32(1) {
                    state.declined.lock().unwrap().insert(channel);
                }
            }
        });
    }
    async fn send3(&mut self, channel: u32, message: &Value) -> Result<()> {
        if channel >= 1000 {
            self.wait_for_menu().await?;
        }
        let state = self.custom.as_ref().unwrap().clone();
        if state.declined.lock().unwrap().contains(&channel) {
            bail!("channel declined for this connection");
        }
        let bidi = if channel >= 1000 {
            let contract = state
                .contract(channel)
                .ok_or_else(|| anyhow::anyhow!("unsupported custom channel"))?;
            if contract.format != Format::Json || !contract.opens(self.role) {
                bail!("contract does not permit JSON sending");
            }
            if contract.identifier == custom::OPEN_CONNECT {
                if let Some(id) = message.get("shot_uuid") {
                    if !id.as_str().is_some_and(crate::draft3::valid_shot_uuid) {
                        bail!("invalid Open Connect UUIDv7");
                    }
                }
            }
            contract.bidirectional
        } else {
            if channel == 2 || channel > 8 || !self.role.owns(channel) {
                bail!("reserved or unowned channel");
            }
            crate::draft3::validate(channel, message)?;
            if channel == 0 {
                state.validate_local_info(message)?;
            }
            false
        };
        let bytes = framing::encode(message)?;
        if !self.outgoing.contains_key(&channel) {
            let mut send = if bidi {
                let (send, recv) = self.connection.open_bi().await?;
                let events = self.events.clone();
                let state = state.clone();
                self.replies.spawn(async move {
                    receive_messages3(recv, channel, events, None, state, false).await;
                });
                send
            } else {
                self.connection.open_uni().await?
            };
            self.watch_decline(channel, &send);
            send.write_all(&channel.to_be_bytes()).await?;
            self.outgoing
                .insert(channel, Arc::new(AsyncMutex::new(send)));
        }
        let result = self
            .outgoing
            .get(&channel)
            .unwrap()
            .lock()
            .await
            .write_all(&bytes)
            .await;
        if let Err(noq::WriteError::Stopped(code)) = &result {
            if *code == noq::VarInt::from_u32(1) {
                state.declined.lock().unwrap().insert(channel);
            }
        }
        if result.is_err() {
            self.outgoing.remove(&channel);
        }
        result?;
        Ok(())
    }
    pub async fn open_raw(&mut self, channel: u32, bidirectional: bool) -> Result<CustomStream> {
        while self.replies.try_join_next().is_some() {}
        self.wait_for_menu().await?;
        let state = self.custom.as_ref().unwrap();
        if state.declined.lock().unwrap().contains(&channel) {
            bail!("channel declined for this connection");
        }
        let contract = state
            .contract(channel)
            .ok_or_else(|| anyhow::anyhow!("unbound custom channel"))?;
        if !matches!(contract.format, Format::Raw | Format::RawDatagram)
            || !contract.opens(self.role)
            || bidirectional != contract.bidirectional
        {
            bail!("contract does not permit this raw stream");
        }
        let permit = self.custom_io.limit.clone().try_acquire_owned()?;
        let (mut send, recv) = if bidirectional {
            let (s, r) = self.connection.open_bi().await?;
            (s, Some(r))
        } else {
            (self.connection.open_uni().await?, None)
        };
        self.watch_decline(channel, &send);
        send.write_all(&channel.to_be_bytes()).await?;
        let (input, output, lease) = self.custom_io.wrap(recv, Some(send), permit);
        Ok(CustomStream {
            channel,
            input,
            output,
            _lease: lease,
        })
    }
    pub fn max_datagram_payload(&self) -> Option<usize> {
        self.connection.max_datagram_size()?.checked_sub(12)
    }
    /// Enqueue a sample. Queue/size errors are immediate; dispatch failures are
    /// delivered through `datagram_errors` with their channel and sequence.
    pub fn send_datagram(&mut self, channel: u32, payload: &[u8]) -> Result<()> {
        let state = self
            .custom
            .as_ref()
            .ok_or_else(|| anyhow::anyhow!("datagrams require draft 3"))?;
        if !state.ready() {
            bail!("server menu not available");
        }
        let contract = state
            .contract(channel)
            .ok_or_else(|| anyhow::anyhow!("unbound datagram channel"))?;
        if !matches!(contract.format, Format::Datagram | Format::RawDatagram)
            || !contract.opens(self.role)
        {
            bail!("contract does not permit datagrams");
        }
        let limit = self
            .connection
            .max_datagram_size()
            .ok_or_else(|| anyhow::anyhow!("peer lacks datagrams"))?;
        self.datagram_queue
            .lock()
            .unwrap()
            .push(channel, payload, limit)?;
        self.datagram_ready.notify_one();
        Ok(())
    }
}
#[allow(clippy::too_many_arguments)]
async fn run3(
    connection: noq::Connection,
    role: Role,
    state: Arc<custom::State>,
    events: mpsc::Sender<Event>,
    raw: mpsc::Sender<CustomStream>,
    datagrams: mpsc::Sender<Datagram>,
    io: Arc<CustomIo>,
    queue: Arc<Mutex<custom::DatagramQueue>>,
    ready: Arc<Notify>,
    errors: mpsc::Sender<DatagramSendError>,
) {
    let active = Arc::new(Mutex::new(HashSet::new()));
    let receiving = Arc::new(Semaphore::new(64));
    let mut tasks = JoinSet::new();
    tasks.spawn(send_datagrams(connection.clone(), queue, ready, errors));
    loop {
        tokio::select! {
            incoming=connection.accept_uni()=>{let Ok(mut recv)=incoming else{break};let Ok(permit)=receiving.clone().try_acquire_owned() else{let _=recv.stop(1u32.into());continue};tasks.spawn(receive3(recv,None,role,state.clone(),active.clone(),events.clone(),raw.clone(),io.clone(),permit,connection.clone()));}
            incoming=connection.accept_bi()=>{let Ok((mut send,mut recv))=incoming else{break};let Ok(permit)=receiving.clone().try_acquire_owned() else{let _=recv.stop(1u32.into());let _=send.reset(1u32.into());continue};tasks.spawn(receive3(recv,Some(send),role,state.clone(),active.clone(),events.clone(),raw.clone(),io.clone(),permit,connection.clone()));}
            incoming=connection.read_datagram()=>{let Ok(bytes)=incoming else{break};if state.ready(){if let Some((channel,sequence,payload))=custom::decode_datagram(&bytes){if state.contract(channel).is_some_and(|c|matches!(c.format,Format::Datagram|Format::RawDatagram)&&c.reads(role)){let _=datagrams.try_send(Datagram{channel,sequence,payload:payload.to_vec()});}}}}
            _=tasks.join_next(),if !tasks.is_empty()=>(),
            _=events.closed()=>break,
        }
    }
    tasks.shutdown().await;
    io.cancel();
}

async fn send_datagrams(
    connection: noq::Connection,
    queue: Arc<Mutex<custom::DatagramQueue>>,
    ready: Arc<Notify>,
    errors: mpsc::Sender<DatagramSendError>,
) {
    loop {
        let packet = queue.lock().unwrap().pop();
        let Some(packet) = packet else {
            tokio::select! {
                _ = ready.notified() => continue,
                _ = connection.closed() => return,
            }
        };
        let (channel, sequence, _) = custom::decode_datagram(&packet).unwrap();
        // The non-waiting QUIC API evicts older queued samples under congestion.
        // Waiting preserves them and propagates pressure to our bounded fair queue.
        if let Err(error) = connection.send_datagram_wait(packet.into()).await {
            if errors
                .send(DatagramSendError {
                    channel,
                    sequence,
                    error,
                })
                .await
                .is_err()
            {
                return;
            }
        }
    }
}
#[allow(clippy::too_many_arguments)]
async fn receive3(
    mut recv: noq::RecvStream,
    mut send: Option<noq::SendStream>,
    role: Role,
    state: Arc<custom::State>,
    active: Arc<Mutex<HashSet<u32>>>,
    events: mpsc::Sender<Event>,
    raw: mpsc::Sender<CustomStream>,
    io: Arc<CustomIo>,
    _permit: OwnedSemaphorePermit,
    connection: noq::Connection,
) {
    let mut prefix = [0; 4];
    if !matches!(
        tokio::time::timeout(Duration::from_secs(10), recv.read_exact(&mut prefix)).await,
        Ok(Ok(()))
    ) {
        let _ = recv.stop(2u32.into());
        if let Some(s) = send.as_mut() {
            let _ = s.reset(2u32.into());
        }
        return;
    }
    let channel = u32::from_be_bytes(prefix);
    if channel >= 1000 {
        // Only the prefix is consumed until the server's info installs the menu.
        let ready = tokio::select! {result=state.wait()=>result,_=connection.closed()=>return};
        if ready.is_ok() {
            if let Some(contract) = state.contract(channel) {
                if contract.reads(role) && contract.bidirectional == send.is_some() {
                    if matches!(contract.format, Format::Raw | Format::RawDatagram) {
                        if let Ok(permit) = io.limit.clone().try_acquire_owned() {
                            let (input, output, lease) = io.wrap(Some(recv), send, permit);
                            let _ = raw
                                .send(CustomStream {
                                    channel,
                                    input,
                                    output,
                                    _lease: lease,
                                })
                                .await;
                            return;
                        }
                    } else if contract.format == Format::Json
                        && active.lock().unwrap().insert(channel)
                    {
                        let _guard = Active {
                            channel,
                            channels: active,
                        };
                        let reply = send.map(|s| Reply(Arc::new(AsyncMutex::new(s))));
                        receive_messages3(recv, channel, events, reply, state, false).await;
                        return;
                    }
                }
            }
        }
    } else if channel != 2
        && channel <= 8
        && role.receives(channel)
        && send.is_none()
        && active.lock().unwrap().insert(channel)
    {
        let _guard = Active {
            channel,
            channels: active,
        };
        receive_messages3(recv, channel, events, None, state, true).await;
        return;
    }
    let _ = recv.stop(1u32.into());
    if let Some(s) = send.as_mut() {
        let _ = s.reset(1u32.into());
    }
}
async fn receive_messages3(
    mut recv: noq::RecvStream,
    channel: u32,
    events: mpsc::Sender<Event>,
    reply: Option<Reply>,
    state: Arc<custom::State>,
    standard: bool,
) {
    loop {
        match framing::read_message(&mut recv).await {
            Ok(Some(message)) => {
                let validation = if channel == 0 {
                    state.receive_info(&message)
                } else if standard {
                    crate::draft3::validate(channel, &message)
                } else {
                    if state
                        .contract(channel)
                        .is_some_and(|c| c.identifier == custom::OPEN_CONNECT)
                        && message.get("shot_uuid").is_some_and(|v| {
                            !v.as_str().is_some_and(crate::draft3::valid_shot_uuid)
                        })
                    {
                        Err(anyhow::anyhow!("invalid Open Connect UUIDv7"))
                    } else {
                        Ok(())
                    }
                };
                if validation.is_err() {
                    let _ = recv.stop(2u32.into());
                    break;
                }
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
                if channel == 0 {
                    state.fail_initial();
                }
                let _ = recv.stop(2u32.into());
                break;
            }
        }
    }
}
