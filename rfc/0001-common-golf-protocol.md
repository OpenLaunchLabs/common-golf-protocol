# Common Golf Protocol

| | |
|---|---|
| Document | 0001 |
| Status | Draft 3 discussion (unreleased) |
| Protocol version | 0.3.0 (proposed) |
| ALPN | `common-golf/draft-3` |
| Service type | `_common-golf._udp` |
| Date | Unreleased |

This working copy proposes draft 3 for discussion. It does not revise the
published draft-1 or draft-2 wire formats; their schema snapshots remain in
`schema/draft-1/` and `schema/draft-2/`. The
[draft-3 profile](../implementations/DRAFT3.md) tracks the proposal and its
implementation status.

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are to be read as
in RFC 2119. Everything else is explanation.

## 1. Why

The following protocols connect launch monitors and simulators.

| Protocol | Transport | Who listens | Framing | Units | Direction | Discovery | Security |
|---|---|---|---|---|---|---|---|
| GSPro Open Connect v1 | TCP 921 | Simulator | Unspecified | `Units: "Yards"` label; speed is mph, unstated | Device to sim, with coded replies | None | None |
| OpenGolfSim | TCP 3111 | Simulator | Newline-delimited JSON | `unit: imperial\|metric`; results always metres | Bidirectional | None | None |
| OpenLaunch Nova | TCP 2921 and WebSocket 2920 | Device | Newline-ish on TCP, one frame per message on WS | mph on TCP, m/s on WS, same device | Device to sim only | mDNS | None |
| [Pureplay Connect v1](https://github.com/PureplayGolf/Pureplay-Connect) | TCP 9760 | Simulator | Little-endian binary, CRC32C | Fixed m/s, degrees, rpm, mm | Device telemetry and simulator commands | None specified | None specified |

Every integrator writes an adapter per pair of products. The same device
reports ball speed in two units on two ports. Nobody authenticates anybody,
so a "launch monitor" can be a shell script, and every product ships an IP
field, a port field and a scan button that users have to understand.

This document replaces all of that with one connection that is encrypted
and authenticated by its transport, found by discovery alone, carries SI
numbers whose units are in their names, and lets any peer add private
channels without forking the protocol.

## 2. What QUIC gives you for free

If you know TCP sockets, here is what changes. QUIC is TCP's replacement,
standardised in RFC 9000, available as a library in every mainstream
language.

- **Encryption is built in.** TLS 1.3 is part of the handshake. There is no
  plaintext mode. Nothing on the wire is readable by a packet sniffer.
- **Both ends have a key.** TLS requires the listener to prove it holds a
  private key. This draft requires the connector to do the same. Both
  present Ed25519 raw public keys (RFC 7250), without X.509 certificates.
  That public key identifies the holder.
- **Version negotiation is built in.** The connector names the application
  protocol in the handshake (ALPN), and may offer more than one.
  `common-golf/draft-3` is this discussion draft;
  section 3.8 says how that changes.
- **Streams are cheap and independent.** One connection carries many
  streams. Each is reliable and ordered like a TCP socket, but a slow or
  stalled stream does not block the others. Either side can open streams,
  one-way or two-way.
- **Declining is built in.** A receiver that does not want a stream tells
  the sender to stop, with a code. No application message is needed.
- **Connections are cheap too.** A listener serves many connections on one
  port, each with its own handshake, authenticated key and streams. There is no
  reason to assume one client, and no reason to build a product that
  only talks to one thing.

This document adds four bytes at the start of each stream and four bytes in
front of each message. That is the entire framing.

## 3. Transport

### 3.1 Roles

The side closer to the hardware listens: a launch monitor, a swing radar, a
control box, or a desktop connector application standing in for one. The
simulator connects. Either side MAY take either QUIC role when a deployment
needs it; channel ownership in section 5 does not depend on who connected.

The direction serves the user at the screen: the simulator browses and the
user picks a device. A screenless device advertises and waits instead of
asking its owner to enter the simulator's address. Open Connect,
OpenGolfSim, and Pureplay put the listener at the simulator; Nova already
shows the device-listener alternative. Discovery removes the address-entry
step whichever peer is listening.

A listener MUST accept more than one connection at a time and MUST NOT
refuse or drop a connection because another exists. A connector MUST be
able to hold connections to more than one listener at a time. Every
connection is independent: its own authenticated peer key, its own tier, its own
open channels, and its own private-channel arrangements. A source shot keeps
its `shot_uuid` when sent to several peers or replayed after a reconnect.

This is normal, not exceptional. A launch monitor on a range is talking to
the simulator, the vendor's phone app, a coaching tool and a diagnostics
dashboard at the same time, and sends each of them what its tier allows. A
simulator is connected to a launch monitor and a control box, and to a
second launch monitor when a second player's device is on the same
network. Nothing in this document depends on there being exactly one of
anything.

### 3.2 Discovery, and nothing else

A listener MUST bind an ephemeral UDP port and MUST advertise itself with
DNS-SD over mDNS (RFC 6762, RFC 6763):

| Record | Value |
|---|---|
| Service type | `_common-golf._udp.local.` |
| Instance name | Human-readable device name |
| SRV | The host and the port the listener actually bound |
| TXT | `key` (required); `vendor`, `model`, `serial`, `alpn`, `caps` (optional); all strings |

`key` is the listener's Ed25519 public key written as a fingerprint
(section 4): 64 lowercase hex characters, the same key the listener
presents in the handshake. A listener MUST include it. A connector MUST
reject a discovery record whose `key` is missing or is not exactly 64
lowercase hex characters. A listener whose key changes MUST re-announce
its TXT record (RFC 6762, section 8.4) so that caches drop the old value.
Like `serial`, an advertised key lets anyone on the network recognise the
listener when it reappears. What a connector may do with it is in section 4.

The optional `alpn` key lists accepted ALPN strings with the `common-golf/`
prefix removed and comma-separated, for example `draft-2,draft-3`. The
optional `caps` key lists data capabilities, for example `ball,club,control`.
These are discovery hints, not negotiation or channel permissions. Their
values, update behavior, and handling of unknown entries are defined in the
[draft-3 profile](../implementations/DRAFT3.md#discovery-txt-keys). The hints
are additive and can also be advertised by earlier drafts without adopting
this draft's transport or channel requirements.

A connector MUST find listeners by browsing for that service type.
Discovery is REQUIRED on both ends; it is not a convenience layered over
manual addressing.

Implementations SHOULD NOT present an IP address, a port, or a scan control
anywhere in a user interface. Connection setup is: see the list of
discovered devices, pick one.

If you are about to add an address field anyway, read this paragraph
first. Every protocol in section 1 permitted manual addressing, and every
product built on them ended up with an IP field, a port field and a scan
button, because once the protocol allows it the first support ticket
produces it. That control cluster is the single most common support
problem in this space. It teaches users about ports, it breaks when DHCP
hands out a new address, and it is unnecessary: the user never has
information the connector cannot get from discovery. Remove the field and
the problem it solves disappears with it.

The deployment assumption is that both ends sit on one network segment
that passes multicast, which is every home and shop network. When
discovery fails the fix is the network: a phone hotspot works, venue guest
wifi that blocks multicast does not. If a vendor insists on a diagnostic
override for support staff, it belongs in a configuration file or a
support tool, never in the product.

### 3.3 Raw public keys

Both endpoints MUST authenticate with Ed25519 raw public keys using
[RFC 7250](https://www.rfc-editor.org/rfc/rfc7250.html). The TLS
`server_certificate_type` and `client_certificate_type` extensions MUST
negotiate `RawPublicKey` in both directions. Endpoints MUST NOT offer,
accept, or fall back to X.509 certificates for this draft.

The listener MUST request the connector's key during the TLS handshake,
and the connector MUST present it. Each endpoint MUST verify the peer's
proof of possession. A missing key, a key of another algorithm, or failed
proof of possession MUST fail the handshake; an anonymous connector is
not a maker. An authenticated but unregistered key is a maker (section 4).

RFC 7250 carries the key as DER-encoded SubjectPublicKeyInfo in the TLS
Certificate message, not as an X.509 certificate. The identity and discovery
fingerprint are the underlying 32 Ed25519 public-key bytes (section 4),
not that DER encoding. A key generated on first run and retained across
restarts is sufficient. No certificate authority, certificate name, or
certificate validity date is involved; a device with no clock can conform.
Use a QUIC/TLS library that supports Ed25519 raw public keys in both roles.

Listeners MUST NOT depend on TLS Server Name Indication (SNI). A listener
MUST accept an otherwise valid handshake with no SNI and MUST NOT require
a particular SNI value to select its key or the CGP service. Discovery
supplies the destination, ALPN selects the protocol, and the authenticated
key supplies identity.

This document takes no position on 0-RTT early data. Early data can be
replayed by someone on the path (RFC 9001, section 9.2), but nearly every
message here is a snapshot that replaces the previous one, so a replay
changes nothing. The exception is a `control` press, where a replayed
`mulligan` would be taken twice; a vendor who cares keeps control off
early data.

### 3.4 Framing

Every stream begins with the channel ID:

```
channel_id: 4 octets, unsigned, big-endian
```

Every message after that is:

```
length:  4 octets, unsigned, big-endian, counting payload only
payload: exactly length octets, one UTF-8 JSON object
```

A reader MUST handle a message split across reads and several messages in
one read.

Big-endian headers follow the traditional network-byte-order convention.
Pureplay chooses little-endian binary fields, which can be convenient on
little-endian microcontrollers. Neither order makes a protocol inherently
more suitable for low-level devices: portable implementations encode and
decode the specified order explicitly, rather than copying native structs
onto the wire. CGP's binary headers have a byte order; its JSON numbers do
not.

Every channel definition states its maximum message size, and a receiver
MUST accept any message up to that size on that channel. Channels 0, 1, and
3 to 8 are small JSON objects and their maximum is 65,535 bytes. This is the
DNS-over-QUIC rule (RFC 9250, section 4.6): one fixed number, nothing
negotiated. Private channels choose their own. A future channel carrying
images or video would state its own ceiling, and would likely be a byte
stream rather than JSON frames. Every message in this document is under
four kilobytes.

JSON numbers MUST be within the IEEE 754 double range (I-JSON, RFC 7493,
section 2.2). No golf value comes close; a number such as `1e999` means the
sender is broken.

A length above the channel's maximum, a payload that is not one valid JSON
object, a number outside the double range, or a FIN arriving inside a frame
is malformed. The receiver MUST
discard the rest of that stream, MUST send STOP_SENDING with application
error code 2 if the stream is still open (a stream that already ended with
FIN cannot be stopped, so it is simply discarded), and MUST keep the
connection open. The sender MAY reopen the channel on a new stream unless
it has been declined with code 1 on this connection (section 3.9).

Standard channels use reliable streams. Custom contracts may use raw
streams or datagrams under section 6 and the draft-3 profile.

### 3.5 Declining a channel

A receiver that does not read a channel MUST send STOP_SENDING with
application error code 1 on that stream. Simply never reading it is not
allowed: unread bytes hold connection-level flow-control credit (RFC 9000,
section 4) and would eventually stall the channels the receiver does want.
The sender MUST treat code 1 as "not on this connection" and MUST NOT
reopen that channel on this connection, treat the decline as an error, or
close the connection. Section 3.9 defines the lifecycle rules, including
the temporary wait for a custom-channel menu; that wait is not a decline.

A stream for a channel the opener does not own (section 5), or a second
stream for a channel the opener already has open, is declined the same way.

| Code | Name | Sent as | Meaning |
|---|---|---|---|
| 1 | `not_interested` | STOP_SENDING | Do not open this channel again on this connection |
| 2 | `malformed` | STOP_SENDING | Bad length, not one JSON object, or FIN inside a frame |

No other codes are defined. These stream errors do not close the
connection; the identity check in section 4 can require closing it.

### 3.6 Keeping the connection up

A golfer takes a minute between shots and longer between holes. QUIC
closes a connection that has carried nothing for the negotiated idle
timeout, and library defaults are 30 to 60 seconds, so a connection that
only speaks when a ball is struck will be gone before the next swing.

Endpoints MUST keep an idle connection alive with QUIC PING frames using a
5-second keep-alive interval. No application heartbeat message is defined
because QUIC already has one. Endpoints MUST advertise `max_idle_timeout`
of 30,000 milliseconds (30 seconds). Both sides use that value, so the
negotiated idle timeout is 30 seconds. Idle expiry and timer resets follow
[RFC 9000, section 10.1](https://www.rfc-editor.org/rfc/rfc9000.html#section-10.1),
including its minimum of three times the current probe timeout (PTO).
Keep-alive traffic preserves a healthy connection between shots; unanswered
PINGs do not keep an unreachable peer's connection alive indefinitely.

### 3.7 Streams and channels

QUIC orders bytes within a stream and not across streams (RFC 9000,
section 2). The rules below exist because of that.

- Each side MUST send its `info` (channel 0) before opening any other
  stream. A receiver MUST still accept standard-channel messages that arrive
  before `info`, because the network may reorder streams. Standard channels
  do not wait for the peer's `info`. Custom channels use the listener's menu
  under the lifecycle rules in section 3.9.
- One stream per channel per direction per connection. A second stream
  for a channel that is already open is declined with code 1.
- After a stream ends, by FIN or reset, the sender MAY reopen the channel
  on a new stream unless it has received code 1 for that channel on this
  connection (section 3.9). Code 2 still permits reopening. Reopening does
  not change shot identities: a message about the same shot retains its
  `shot_uuid`, and a new shot gets a new
  UUIDv7. The same-channel replacement rules in section 5 still apply.
- On a two-way custom stream only the opener writes the channel prefix.
  The responder writes on the same stream in the contract's format, without
  a second channel prefix.

### 3.8 Versions

The ALPN string names the wire format. While this document is a draft,
each draft has its own string, `common-golf/draft-1`, `common-golf/draft-2`,
and drafts MAY break each other.

A connector MAY offer several strings in one handshake, and a listener MAY
accept several. Ordinary TLS ALPN selection picks one: the listener chooses
from the connector's list, by its own preference. The chosen string governs
the whole connection; peers MUST NOT mix drafts within it. Two peers with no
string in common fail the QUIC handshake, which is the intended failure:
nothing half-works. An implementation that wants to support two drafts
offers or accepts both strings; it does not need any other mechanism.

The string moves only for a change that is not additive: a field or
channel removed or renamed, or a meaning changed. Adding an optional field
or a channel does not move it. Every string so far, and why it moved:

| ALPN | Protocol versions | Date | Why |
|---|---|---|---|
| `common-golf/draft-1` | 0.1.0, 0.1.1 | 2026-10-11 | First draft |
| `common-golf/draft-2` | 0.2.0 | 2026-10-17 | `reduced_accuracy` removed from channel 1, replaced by `estimated` on channels 1 and 3 (#2) |
| `common-golf/draft-3` | 0.3.0 (proposed) | Unreleased | Discussion: mandatory mutual raw public keys, required discovery key, no SNI dependence, new connection timers, UUIDv7 whole-snapshot shot identity without `final`, listener-menu custom channels replacing channel 2, and renamed state channels with capture/lie context |

At 1.0 the string becomes `common-golf/1`. From then on every change under
that string is additive: new optional fields, new channels. The rules that
receivers ignore unknown fields and decline unknown channels make that safe
without any further negotiation. A change that cannot be additive gets
`common-golf/2`.

`protocol_version` in `info` is for people and logs. Implementations MUST
NOT make decisions on it; the ALPN already did.

### 3.9 Connection lifecycle

The connection follows this sequence. “Use” is ordinary channel traffic,
not an additional exchange or a transition into a separate application
“established” state.

| Step | What happens |
|---|---|
| 1. QUIC handshake | ALPN picks one draft both sides offer (section 3.8). Both endpoints MUST present and authenticate Ed25519 raw public keys; X.509 and anonymous connectors are forbidden (section 3.3). |
| 2. Identity | The listener maps the connector's authenticated key to a tier. The connector MUST match the required advertised listener key to the handshake key or close before opening any channel (section 4). |
| 3. `info`, both ways | Each side's first stream is channel 0. The listener's first `info` carries the menu offered to that tier. |
| 4. Use | Each side opens channels it owns; each receiver keeps or declines each stream. Only a connector opening a custom channel needs the menu first. |

Four rules govern use:

1. **Declining is final for the connection.** STOP_SENDING code 1 means
   “not on this connection.” The sender MUST NOT reopen that channel on
   this connection. Code 2 (`malformed`) still allows reopening; ending a
   stream does not otherwise revoke its channel (sections 3.5 and 3.7).
2. **A missing menu is not a decline.** A receiver MUST NOT decline a
   custom stream just because the listener's menu has not arrived. It MUST
   hold the stream unread until the menu arrives and is validated, then
   keep or decline it under the contract and its own policy. This temporary
   wait is the exception to section 3.5's prohibition on leaving unwanted
   streams unread. An invalid initial menu leaves custom channels
   unavailable, so pending custom streams are declined with code 1.
3. **Identity determines the offer, fixed for the connection.** The
   listener knows the connector's handshake identity before sending `info`
   and chooses the menu by that identity's tier and its application policy.
   The menu MUST NOT change during the connection. To obtain a different
   menu, reconnect (section 6).
4. **No order, acknowledgement, or ready message.** Each side MUST send
   its own `info` before opening other streams. Standard traffic and
   listener-opened custom streams MAY start immediately after that, without
   waiting for the peer's `info` or hints such as `reads_contracts`. A
   connector MUST receive and validate the listener's menu before opening
   a custom channel or sending custom datagrams. Receivers MUST accept
   standard traffic that overtakes `info`; custom streams use rule 2.
   There is no additional cross-stream delivery order, selection exchange,
   acknowledgement, or readiness message. Capability hints remain optional
   and MUST NOT become a startup prerequisite.

## 4. Trust

Identity is the peer's Ed25519 public key: 32 bytes, written as 64
lowercase hex characters. Call it the fingerprint, since that is what
everyone will call it anyway. It is the key itself, not a hash of it and
not a hash of its SubjectPublicKeyInfo encoding.

The handshake proves possession of the key. Keying on the bare key means
one registration also covers an implementation that presents the same key
through a different transport or another protocol that identifies peers
by Ed25519 key.

Each endpoint MAY keep a list of fingerprints it knows, each with a tier:

| Tier | Meaning |
|---|---|
| `internal` | The vendor's own software |
| `developer` | A registered third party |
| `maker` | Every authenticated peer whose key is not registered |

An endpoint decides which channels to open toward a peer, and which to read
from it, by that peer's tier. Section 3.9 ties this policy to the connection's
fixed menu and channel use. A device might send plain
shots to makers and also club data and a private diagnostics channel to
developers. A tournament simulator might accept shots only from fingerprints it has
registered as real hardware.

An endpoint that keeps no list treats every peer as a maker and is
conforming. Registration is out of scope: a vendor can accept fingerprints
by email, a web form, or a file. Revocation is removing a line.

Be clear about what a fingerprint proves. The TLS handshake proves the
peer holds the private key for the public key it showed. A fingerprint on
your list therefore means "this is the holder I registered." A fingerprint
not on your list means only "this is not one I know"; it says nothing
about what the peer is. Accepting unknown peers as makers is a choice to
serve them anyway, not a judgement that they are devices. A device name
you recognise arriving with a fingerprint you do not is a new, unknown
device, and an implementation that keeps a list SHOULD surface that to the
user rather than trust it silently. This is the rule SSH uses for known
hosts, and it is sufficient.

The required advertised `key` (section 3.2) lets a connector apply its list before
connecting: label each discovered listener by tier, warn about a familiar
name with an unfamiliar key, or hide listeners it would not serve. The
advertisement is not authenticated; anyone on the network can advertise
any key, so it is a claim and not proof. A connector MUST NOT treat an
advertised key as authentication, and MUST compare it with the key the
handshake presents. If they differ, the connector MUST close the
connection before opening any channel and surface it as it would an
unknown device. It MAY rediscover and retry once, because a listener that
has just changed its key can leave a stale record in caches. Copying a
registered key into a fake advertisement therefore earns a misleading
label and a failed connection, never a session. A connector SHOULD keep
every advertisement that claims a given key and try each of them, so that
a fake cannot hide the real listener.

A key identifies whoever holds it, which may be an organisation or a
product line rather than a single unit. Several listeners can advertise
and present the same key at once; they are separate listeners, and a
connector MUST NOT merge them or choose between them by key alone. Keys
also change. A vendor rotates by issuing a new key and replacing the old
entry on the lists that hold it. Expiry, if a vendor wants it, is a date
kept beside the entry on the list. Raw public keys carry no expiry date
(section 3.3).

This is the whole security model. There are no signatures inside messages,
no nonces, no canonical JSON. Matching a fingerprint means talking to a
holder of that key. If the key lives in hardware in a single unit, that
holder is that unit.

## 5. Channels

A channel is an unsigned integer carried at the start of a stream. Each
standard channel has one owner, who opens the stream, and one direction.
One-way channels use QUIC unidirectional streams. To use a channel, open a
stream, write the channel ID, write messages. There is no negotiation.

| ID | Name | Contract | Opened by | Flows | Stream | Max message | Needed for play |
|---|---|---|---|---|---|---|---|
| 0 | `info` | `io.common-golf.info-v1` | either | opener to peer | one-way | 65,535 | yes, first message |
| 1 | `shot` | `io.common-golf.shot-v1` | device | device to sim | one-way | 65,535 | yes |
| 2 | reserved | | | | | | |
| 3 | `club` | `io.common-golf.club-v1` | device | device to sim | one-way | 65,535 | no |
| 4 | `device_status` | `io.common-golf.device-status-v1` | device | device to sim | one-way | 65,535 | no |
| 5 | `control` | `io.common-golf.control-v1` | controller | controller to sim | one-way | 65,535 | no |
| 6 | `simulator_state` | `io.common-golf.simulator-state-v1` | sim | sim to device | one-way | 65,535 | no |
| 7 | `result` | `io.common-golf.result-v1` | sim | sim to device | one-way | 65,535 | no |
| 8 | `flight` | `io.common-golf.flight-v1` | device | device to sim | one-way | 65,535 | no |
| 9 to 999 | reserved | | | | | | |
| 1000 and up | custom | per connection | contract-defined | any | any | own | no |

The Contract column gives documentation identifiers for the fixed channels.
The negotiated ALPN defines channels 0, 1, and 3–8, including their numbers,
ownership, framing, and message semantics. Endpoints do not send, parse, or negotiate
these identifiers to use a standard channel. The identifiers do not add any
wire fields or change which channels an endpoint must support.

Every simulator MUST read channel 1. A device MUST write channel 0 before
opening other streams. A launch monitor may publish native shots on channel
1, use the offered Open Connect custom contract in section 6.1, or use both
concurrently, including for the same shots. Other channels are optional and
independent.

Rules for every standard channel:

- One JSON object per message. Every object on channels 0, 1, and 3 to 8 has
  a `type` string equal to the channel name.
- Receivers MUST ignore fields they do not know.
- Senders MUST NOT send fields with the wrong unit under a known name.
- Nothing is acknowledged. A sender never waits for a reply. Where a
  simulator has something to say about a shot, it publishes a `result` on
  its own channel, and a device that cares correlates by `shot_uuid`.

JSON Schema for each channel is in `schema/`.

Shot identity on channels 1, 3, 7, and 8 uses the single field name
`shot_uuid`. The source generates a new UUIDv7 for each new shot, as defined
in [RFC 9562, section 5.7](https://www.rfc-editor.org/rfc/rfc9562.html#section-5.7).
All messages about that shot, including related club measurements, computed
flight, and simulator results, MUST reuse its `shot_uuid`. Updates and
replays MUST retain it, including across stream reopenings and reconnects.

A `shot_uuid` MUST use exactly 36 ASCII characters in lowercase,
hyphenated `8-4-4-4-12` form, with version 7 and the RFC UUID variant.
Its decoded JSON string MUST match this pattern and have length 36:

```text
^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$
```

For example, `017f22e2-79b0-7cc3-98c4-dc0c0c07398f` is valid. Uppercase or
mixed-case strings, braces, URN prefixes, missing hyphens, and other UUID
versions are invalid. Receivers compare the canonical strings byte for byte;
they MUST NOT normalize an invalid spelling into a valid one. An invalid
`shot_uuid` is malformed under section 3.4. The schemas enforce the length
and pattern explicitly; `format: "uuid"` alone is not sufficient.

`shot_uuid` is an identity, separate from `measured_at`. The timestamp
embedded in UUIDv7 is used by UUID generation; receivers MUST NOT use it
as the measurement time. `measured_at`, when known, carries the actual
measurement time independently. Neither timestamp determines which update
replaces another.

A later message with the same `shot_uuid` on the same channel replaces
the earlier message whole. Each message is a complete snapshot, not a
patch: an optional field omitted from the replacement is gone. Receivers
MAY accept these replacements, or keep the first message and ignore later
ones for that UUID on that channel. If a receiver accepts a replacement,
it MUST replace the whole earlier snapshot rather than merge fields or
count another shot. Here "later" means receive order on that channel,
including after the stream is reopened. A matching UUID on another
channel correlates related information; it does not replace that other
channel's snapshot. There is no `final` field or completion marker, and
receivers need not wait for another message before using a snapshot.

### 5.0 Channel 0: `info`

The first message each side sends, on the first stream it opens. Required.

| Field | Type | Required | Notes |
|---|---|---|---|
| `protocol_version` | string | yes | Semver of this discussion draft, `0.3.0` |
| `name` | string | | Display name |
| `vendor`, `model`, `serial`, `firmware_version` | string | | |
| `channels` | array of integer | | Channels this side intends to open |
| `reads` | array of integer | | Optional standard-channel read capability hint (0, 1, and 3–8) |
| `contracts` | array of objects | | Listener's immutable custom-channel menu; section 6 |
| `reads_contracts` | array of string | | Optional exact custom contract identifiers this peer reads; section 6 |

```json
{"type": "info", "protocol_version": "0.3.0", "name": "Garage LM", "vendor": "Garage Labs", "model": "GL-1", "channels": [0, 1, 4]}
```

In draft 3, `reads` lists standard channel numbers (0, 1, and 3–8).
An absent list makes no claim; an empty list advertises no standard readers.
This is a capability hint, not permission, preference, or acknowledgement.
A sender may use it to avoid unwanted streams or open a stream and accept a
decline. It MUST NOT become a startup prerequisite (section 3.9).
Use `reads_contracts` for custom contracts, including Open Connect: channel 2
is reserved in draft 3. Neither list requires choosing only one shot format.

### 5.1 Channel 1: `shot`

What the device measured at launch. Nothing computed about flight belongs
here; that is channel 8.

| Field | Type | Required | Notes |
|---|---|---|---|
| `shot_uuid` | string | yes | Canonical UUIDv7; see section 5 |
| `ball_speed_meters_per_second` | number | yes | |
| `vertical_launch_angle_degrees` | number | yes | Positive is up |
| `horizontal_launch_angle_degrees` | number | yes | Positive is right of target |
| `total_spin_revolutions_per_minute` | number | with axis | Positive is backspin, negative is topspin |
| `spin_axis_degrees` | number | with total | -90 to 90. 0 is no curve; positive curves right, whatever the sign of total |
| `backspin_revolutions_per_minute`, `sidespin_revolutions_per_minute` | number | | Only in addition to total and axis; positive sidespin curves right |
| `measured_at` | string | | UTC RFC 3339 measurement timestamp; independent of `shot_uuid`. Omit if unknown |
| `player_dexterity` | `right`, `left` | | Omit when unknown; see section 7 |
| `ball_lie` | `tee`, `fairway`, `putting_green`, `sand`, `rough` | | Known hitting-area lie, including simulated sand/rough; omit when unknown |
| `estimated` | array of string | | Names of numeric fields the device could not measure for this shot, or believes to have reduced accuracy. See section 7 |

`ball_lie` travels from the device or its connector application to the
simulator with the shot. It reports the known hitting-area lie: for example,
a tee, a normal fairway-like surface, a putting surface, simulated sand, or
simulated rough. It is not a request to move the virtual ball to that lie.
When a hitting surface has already affected the measured launch, this
context lets the simulator account for that instead of blindly applying
the same effect again. No numerical penalty or particular physics model is
encoded by this field. If the source does not know the lie, it omits the
field rather than inferring it from the shot's measurements. This replaces
`tee_position`; capture mode is reported separately, replacing `shot_kind`.

Spin has one canonical form: total and axis, sent together or not at all,
in the convention launch monitors and golfers already use. The sign of
total says backspin or topspin. The sign of the axis says which way the
ball curves, and it means that regardless of the sign of total. Zero total
carries axis 0. A consumer that wants components computes:

```
backspin = total × cos(axis)
sidespin = |total| × sin(axis)
```

A device that measures backspin and sidespin MUST convert before sending,
and MAY include the measured pair as well:

```
total = sign(backspin) × sqrt(backspin² + sidespin²)    (zero backspin counts as positive)
axis  = atan2(sidespin, |backspin|) in degrees
```

Consumers never convert.

For example, a putting device that measures speed and horizontal direction
but not launch angle can estimate the vertical angle as zero and mark that
field in `estimated`, following section 7. Optional unmeasured values, such
as spin, can be omitted. The example supplies all three required launch
measurements. Capture mode, when reported, is separate device state on
channel 4; there is no `shot_kind` field in this draft:

```json
{"type": "shot", "shot_uuid": "017f22e2-79b0-7cc3-98c4-dc0c0c07398f", "ball_speed_meters_per_second": 2.1, "horizontal_launch_angle_degrees": 0.4, "vertical_launch_angle_degrees": 0, "estimated": ["vertical_launch_angle_degrees"]}
```

A device MAY send more than one `shot` with the same `shot_uuid` as its
estimate refines. Each is a complete snapshot under section 5's optional
replacement rules. A simulator that accepts replacements updates that shot
without counting it again; one that does not may ignore subsequent snapshots.

```json
{"type": "shot", "shot_uuid": "017f22e2-79b0-7cc3-98c4-dc0c0c07398f", "ball_speed_meters_per_second": 67.3, "vertical_launch_angle_degrees": 12.1, "horizontal_launch_angle_degrees": -1.4, "total_spin_revolutions_per_minute": 2750, "spin_axis_degrees": -3.2, "player_dexterity": "right", "measured_at": "2022-02-22T19:22:21.950Z"}
```

### 5.2 Channel 2: reserved

Channel 2 is reserved in draft 3. Receivers MUST decline a stream on this
number with STOP_SENDING code 1. A custom menu entry assigning channel 2 is
malformed, like any menu entry below 1000. Open Connect is the custom
contract `io.common-golf.open-connect-v1` in section 6.1 and has no fixed
channel number. Drafts 1 and 2 retain their original channel-2 definition.

### 5.3 Channel 3: `club`

What the device measured about the club. Every field is optional. A
`shot_uuid` shared with a `shot` message correlates the club at impact with
that shot. A standalone swing from a device with no ball data, such as a
swing-speed radar, MAY have its own `shot_uuid` without any channel-1
message. If the identifier is omitted, no UUID-based correlation or
replacement is possible.

| Field | Notes |
|---|---|
| `shot_uuid` | Canonical UUIDv7; shared with a related shot or generated for a standalone swing |
| `club_speed_meters_per_second` | |
| `angle_of_attack_degrees` | Positive is upward |
| `club_path_degrees` | Positive is right of target |
| `face_angle_degrees` | Face to target. Positive is right of target |
| `face_to_path_degrees` | Face minus path |
| `dynamic_loft_degrees`, `spin_loft_degrees` | |
| `smash_factor` | Ball speed over club speed |
| `swing_direction_degrees` | Positive is right of target |
| `swing_plane_degrees` | |
| `swing_radius_meters` | |
| `d_plane_tilt_degrees` | Positive is right of target |
| `low_point_distance_meters`, `low_point_height_meters`, `low_point_side_meters` | Side positive is right of target |
| `lie_degrees` | |
| `speed_at_impact_meters_per_second` | |
| `face_impact_horizontal_millimeters`, `face_impact_vertical_millimeters` | Positive is toe and up |
| `closure_rate_degrees_per_second` | |
| `player_dexterity` | See section 7 |
| `estimated` | As on channel 1: names of numeric fields the device could not measure for this shot, or believes to have reduced accuracy |

Timing guidance (informative). For low latency, a simulator can start an
accepted ball shot from channel 1 without waiting for optional club data on
channel 3. Club data supports post-shot analysis. Sending available club
data promptly, and aiming to finish useful refinements within ten seconds
of the shot or before the next shot, whichever comes first, keeps that
analysis timely. This is
application guidance, not a protocol deadline or a receiver timeout. The
whole-snapshot replacement rules in section 5 apply; there is no final flag.

A standalone club tracker can generate its own `shot_uuid`. It is not
automatically correlated with another device's ball-shot UUID. Associating
a nearby swing with a ball shot for display is a local heuristic; it need
not delay ball simulation or change either source's shot identity.

```json
{"type": "club", "shot_uuid": "017f22e2-79b0-7cc3-98c4-dc0c0c07398f", "club_speed_meters_per_second": 47.1}
```

### 5.4 Channel 4: `device_status`

Device-owned state. The device sends a complete snapshot when the stream
opens, then only changed fields. An absent field is unchanged and `null`
clears a value. A nested object replaces the previous object whole rather
than merging its fields. All fields other than `type` are optional.

| Field | Notes |
|---|---|
| `ready` | Whether the device is ready to capture |
| `capture_mode` | Actual device mode: `full`, `short`, or `putting` |
| `ball_detected` | Whether the device detects a ball |
| `ball_zone` | `{x_meters, y_meters, width_meters, height_meters}`: the device's usable placement region; width and height are nonnegative |
| `ball_position` | `{x_meters, y_meters}`: the detected ball position, if known |
| `battery_percent` | Number from 0 to 100 |
| `uptime_seconds` | Nonnegative uptime |
| `message` | Human-readable device message |

The two position objects use the device's local placement frame: the origin
is defined by the device, positive x is right of its target line and positive
y is forward along it. They are capture-area coordinates, not coordinates
on the simulated course. A device offering them documents its origin.
Omitting them makes no claim that manual placement is necessary. Both are
reported by the device; a simulator does not command a ball position here.

`capture_mode` reports the mode actually in effect. It is independent of
whether the simulator requested that mode, and does not acknowledge a request.
A device that lacks a mode leaves it unsupported rather than reporting that
it applied it. The channel remains optional; receivers can decline it.

```json
{"type": "device_status", "ready": true, "capture_mode": "putting", "ball_detected": true, "ball_zone": {"x_meters": -0.1, "y_meters": 0, "width_meters": 0.2, "height_meters": 0.3}, "ball_position": {"x_meters": 0, "y_meters": 0.1}}
```

This replaces draft 2's `status` name on channel 4 and moves device placement
information from the old simulator-owned `state.ball_zone` to the device.
Published draft-2 names and schemas remain unchanged.

### 5.5 Channel 5: `control`

Virtual buttons, from a control box, a touch surface, or a device with
buttons on it, to the simulator.

| Field | Values |
|---|---|
| `command` | see table |
| `event` | `press` (default), `down`, `up` |

| Group | Commands |
|---|---|
| Aim | `left`, `right`, `up`, `down`, `reset_aim` |
| Club | `club_up`, `club_down`, `putter_toggle` |
| Shot | `mulligan`, `rehit`, `drop`, `tee_left`, `tee_right`, `switch_hand` |
| View | `flyover`, `shot_cam`, `scorecard`, `heatmap`, `map`, `green_grid`, `pin`, `hide_ui` |
| UI | `select`, `back` |

`down` and `up` are meaningful for the four aim commands, which repeat
while held. Everything else is a `press`. A simulator MUST ignore commands
it does not know, so a controller can send anything and only the supported
subset takes effect. Nothing is namespaced; a vendor with private buttons
uses a private channel.

```json
{"type": "control", "command": "left", "event": "down"}
```

### 5.6 Channel 6: `simulator_state`

Simulator-owned context. Sent in full when the stream opens, then as patches:
only the fields that changed. An absent field is unchanged, `null` clears it,
and nested objects replace their previous values whole. All fields other
than `type` are optional.

| Field | Notes |
|---|---|
| `ready` | The simulator will act on a shot right now; not a device arm/disarm command |
| `player` | `{name, dexterity}`; known dexterity is `right` or `left`, otherwise omit it |
| `distance_to_target_meters` | Nonnegative distance from the ball to the pin or current aim point, in meters; `null` clears it |
| `requested_capture_mode` | Requested device mode: `full`, `short`, or `putting`; `null` clears the request |

A requested capture mode is optional context, not a required control interface
or a readiness barrier. A device MAY use it in its capture policy or ignore
it if unsupported. The simulator MUST NOT wait for an acknowledgement.
There is no acknowledgement message: `device_status.capture_mode`, if offered,
is the device's actual state. Neither that state nor `simulator_state.ready`
is inferred from the request. Clearing a request does not select a default
mode; that remains device policy.

The old `state.club` and game `mode` fields are not carried in this profile.
Club identity/display can use a separately agreed custom contract; capture
mode describes device behavior instead of the simulator's game mode. Ball
placement is device-owned under section 5.4. No automatic translation from
a club abbreviation or game mode to a capture mode is defined.

```json
{"type": "simulator_state", "ready": true, "player": {"name": "Alex", "dexterity": "right"}, "distance_to_target_meters": 3.048, "requested_capture_mode": "putting"}
```

This replaces draft 2's `state` name on channel 6. Devices using the old name,
`club`, or `mode` continue to use the published draft-2 profile.

### 5.7 Channel 7: `result`

The simulator's computed outcome for a shot. Published when the simulator
has it. This is not an acknowledgement: a device MUST NOT treat silence as
either success or failure.

| Field | Notes |
|---|---|
| `shot_uuid` | required; canonical UUIDv7 |
| `accepted` | boolean |
| `reject_reason` | string, when not accepted |
| `carry_distance_meters`, `total_distance_meters` | |
| `carry_side_meters`, `total_side_meters` | Positive is right of target |
| `max_height_meters` | |

### 5.8 Channel 8: `flight`

The device's own computed ball flight, when it offers one. Kept apart from
channel 1 so a simulator that runs its own physics never confuses a
measurement with a derived value. `shot_uuid` required, rest optional.

| Field |
|---|
| `carry_distance_meters`, `carry_side_meters` |
| `total_distance_meters`, `total_side_meters` |
| `max_height_meters`, `curve_meters` |
| `landing_angle_degrees`, `hang_time_seconds` |

Trajectories as point arrays are deliberately not carried. They are library
output; see OpenGolfCoach.

## 6. Custom channels: 1000 and up

In draft 3, the listener assigns numbers 1000 and above to exact contract
identifiers in its first `info`. This menu is fixed for the connection and
contains only contracts it offers to that peer. The same number can name a
different contract on another connection. A missing `contracts` field or
an empty array offers no custom channels. All numbers below 1000 are
invalid in this menu, including reserved channel 2.

Section 3.9 defines when channels can open, how receivers handle streams
that overtake the menu, and why a decline lasts for the connection. The
contract defines who opens, which direction data flows, and
whether it uses framed JSON, raw streams, or datagrams. The opener writes
the four-byte channel ID followed directly by contract data; there is no
`{"type":"private"}` opening message. The [draft-3 profile](../implementations/DRAFT3.md#custom-channel-menu-in-info)
defines menu validation, immutability, and listener-opened streams.

A device SHOULD open custom channels only toward peers whose tier
warrants it (section 4).

Device-specific configuration, such as selecting a marked or markerless
ball, belongs in a vendor custom contract, usually on the vendor app's own
connection. It does not require a common `SetBallType` command. Simulator
context on channel 6 describes the game; it is not a general device settings
API. The simulator can observe measurement consequences through `estimated`
(section 7) and device status without understanding the vendor's settings.

### 6.1 Primary example: Open Connect

`io.common-golf.open-connect-v1` is the primary custom-channel example.
The listener offers it in its menu, for example:

```json
{"type":"info","protocol_version":"0.3.0","contracts":[{"channel":1000,"contract":"io.common-golf.open-connect-v1"}]}
```

Here 1000 is a listener-assigned handle for this connection, not a reserved
Open Connect number. The identifier names the CGP transport contract for
Open Connect v1 bodies, plus one optional CGP field. It is in the protocol's
reserved namespace.

| Contract property | Definition |
|---|---|
| Identifier | `io.common-golf.open-connect-v1` |
| Opened by | Device, regardless of which endpoint is the QUIC connector |
| Transport | One active bidirectional reliable stream on the assigned channel |
| Device to simulator | Open Connect v1 bodies, plus one optional CGP field: `shot_uuid` |
| Simulator to device | Open Connect `{Code, Message, Player}` replies, unchanged |
| Framing | Four-byte length-prefixed UTF-8 JSON objects in both directions |
| Maximum message | 65,535 bytes in either direction |

The device writes the assigned four-byte channel ID once when opening the
stream. The simulator's reply half has no second channel prefix. There is
no CGP `type` envelope and no private-channel opening message. The payload's
field names, casing, units, and `ShotNumber` remain as Open Connect defines
them. A device MAY add `shot_uuid` to a request body. When present, it MUST
use the UUIDv7 format, length, and pattern in section 5 and MUST retain the
same UUID for the same shot across updates, replays, and reconnects. If the
device also sends that shot on channel 1, both bodies MUST use the same
`shot_uuid`. `ShotNumber` remains a separate Open Connect field. Replies
remain unchanged and do not echo `shot_uuid`.

A bridge forwarding
requests to real GSPro on TCP 921 SHOULD strip `shot_uuid` unless acceptance
of that extra key has been tested against the target GSPro version. GSPro's
handling of unknown keys has not been confirmed; byte-for-byte forwarding
would also forward this CGP field.

In the usual device-listener setup, the simulator can advertise
`"reads_contracts":["io.common-golf.open-connect-v1"]` in its own `info`.
The device may use that hint or simply open the offered stream and accept
being declined with code 1. A simulator that accepts the stream MUST
provide the Open Connect replies. That requirement belongs to this custom
contract; standard channels remain unacknowledged.

Channel 1 and this custom channel are independent and MAY be used
concurrently, including for the same shots. CGP imposes no preference,
fallback ordering, or mutual-exclusion rule between them.

Device body, following the channel prefix and its message length:

```json
{"DeviceID": "Garage LM", "Units": "Yards", "ShotNumber": 12, "shot_uuid": "017f22e2-79b0-7cc3-98c4-dc0c0c07398f", "APIversion": "1", "BallData": {"Speed": 150.5, "SpinAxis": -3.2, "TotalSpin": 2750, "HLA": -1.4, "VLA": 12.1}, "ShotDataOptions": {"ContainsBallData": true, "ContainsClubData": false}}
```

Simulator reply, with its message length and no channel prefix:

```json
{"Code": 200, "Message": "Shot received"}
```

The [Open Connect payload schema](../schema/open_connect.json) describes
these bodies independently of the assigned channel number. Other custom
contracts can use different framing or transports without changing the
standard channels.

## 7. Units, frames and signs

Every numeric field carries its unit in its name, spelled out:
`_meters`, `_meters_per_second`, `_degrees`, `_revolutions_per_minute`,
`_millimeters`, `_seconds`, `_nanoseconds`, `_percent`. There are no unit
flags and no abbreviations. A field name is its documentation.

All lateral quantities share one frame, independent of the player: the
target line, viewed from behind the ball. Positive is right of target.
This applies to horizontal launch angle, spin axis, sidespin, carry side,
total side, club path, face angle, swing direction, low point side and
D-plane tilt. For spin, the axis sign is the curve direction even when
total spin is negative (topspin); section 5.1 has the decomposition.

`player_dexterity` travels alongside so a consumer can translate into the
player's terms: for a right-handed player, positive club path is in-to-out
and positive face angle is open; for a left-handed player each is the
opposite. When the device cannot tell, it omits `player_dexterity`; the
`unknown` string used by earlier drafts is not sent in this profile. The
wire never carries two handed variants of a number.

`measured_at` is an optional UTC RFC 3339 measurement timestamp, including
fractional seconds when available. Omit it if the measurement's UTC time is
unknown; do not substitute uptime or the timestamp embedded in `shot_uuid`.
The UUID identifies the shot independently of `measured_at`.

A device that could not measure a value for this shot and sends one
anyway, or that believes a value it sends has reduced accuracy, MUST name
that field in `estimated`, on channels 1 and 3. This is the number a
launch monitor shows in italics: indoor spin without a marked ball, a face
angle inferred from path and spin. A sender MUST name only fields present
in the message; a receiver ignores any other name. A consumer SHOULD
present estimated values distinctly. A value
computed exactly from fields that are themselves measured is not
estimated; it is derived.

## 8. Compatibility with what exists

### 8.1 GSPro Open Connect v1

Carried as Open Connect v1 bodies, plus optional `shot_uuid`, by the
`io.common-golf.open-connect-v1` custom contract (section 6.1). For a
simulator that wants to normalise
those bodies into channel 1 and 3 terms, with `Units: "Yards"` meaning mph,
rpm, degrees and yards:

| Open Connect | Common Golf | Factor |
|---|---|---|
| `shot_uuid` (when present) | `shot_uuid` | Preserve the supplied UUIDv7 |
| `ShotNumber` (when `shot_uuid` is absent) | `shot_uuid` | Allocate a UUIDv7 per source shot and retain the mapping for related messages and updates |
| `BallData.Speed` | `ball_speed_meters_per_second` | × 0.44704 |
| `BallData.VLA` | `vertical_launch_angle_degrees` | |
| `BallData.HLA` | `horizontal_launch_angle_degrees` | |
| `BallData.TotalSpin` | `total_spin_revolutions_per_minute` | negative topspin carries over as is |
| `BallData.SpinAxis` | `spin_axis_degrees` | |
| `BallData.BackSpin`, `SideSpin` | `backspin_…`, `sidespin_…` | |
| `BallData.CarryDistance` | `flight.carry_distance_meters` | × 0.9144 |
| `ClubData.Speed` | `club_speed_meters_per_second` | × 0.44704 |
| `ClubData.AngleOfAttack` | `angle_of_attack_degrees` | |
| `ClubData.Path` | `club_path_degrees` | |
| `ClubData.FaceToTarget` | `face_angle_degrees` | |
| `ClubData.Lie`, `Loft` | `lie_degrees`, `dynamic_loft_degrees` | |
| `ClubData.SpeedAtImpact` | `speed_at_impact_meters_per_second` | × 0.44704 |
| `ClubData.HorizontalFaceImpact`, `VerticalFaceImpact` | `face_impact_horizontal_millimeters`, `…_vertical_…` | unit undocumented; assume mm |
| `ClubData.ClosureRate` | `closure_rate_degrees_per_second` | |
| `ShotDataOptions.LaunchMonitorIsReady` | `device_status.ready` | |
| `ShotDataOptions.LaunchMonitorBallDetected` | `device_status.ball_detected` | |
| Reply `Code 200` | `result.accepted: true` | |
| Reply `Code 201` + `Player` | `simulator_state.player` | `RH`/`LH` to `right`/`left`; no direct club or capture-mode mapping |
| Reply `Player.DistanceToTarget` | `simulator_state.distance_to_target_meters` | Convert yards × 0.9144 when the source unit is established; the reply has no unit label |

### 8.2 OpenGolfSim

| OpenGolfSim | Common Golf |
|---|---|
| `shot.shot.ballSpeed` | `ball_speed_meters_per_second` (× 0.44704 when `unit` is imperial) |
| `shot.shot.verticalLaunchAngle`, `horizontalLaunchAngle` | launch angles |
| `shot.shot.spinSpeed` | `total_spin_revolutions_per_minute` |
| `shot.shot.spinAxis` | `spin_axis_degrees`, **negated** (OpenGolfSim positive is left-curving) |
| `device.status` | `device_status.ready` |
| `player.data.club.{id,name}` | No direct mapping; club display and capture-mode policy are application concerns |
| `result.data.result.{carry,total,lateral,height}` | `result.carry_distance_meters`, `total_distance_meters`, `carry_side_meters`, `max_height_meters` |
| `control.command` `left right up down club-up club-down drop mulligan rehit` | `control.command` with hyphens replaced by underscores; `event` passes through |

### 8.3 OpenLaunch Nova

Nova's WebSocket feed is already snake_case SI and maps by rename:
`ball_speed_meters_per_second`, `vertical_launch_angle_degrees`,
`horizontal_launch_angle_degrees` and `spin_axis_degrees` are identical;
`total_spin_rpm` becomes `total_spin_revolutions_per_minute`, negative
topspin included;
`timestamp_ns` can become `measured_at` only when its epoch is known and
can be converted to UTC RFC 3339; otherwise omit the measurement timestamp.
Assign the source shot a separate UUIDv7 and retain it for related messages
and updates. `status.uptime_seconds` and `status.firmware_version` go to
`device_status` and `info`. Nova's TCP API is
Open Connect and uses the `io.common-golf.open-connect-v1` custom contract. Nova's touch bar maps to `control`:
Left and Right to `left` and `right`, A to `reset_aim`, Y to `heatmap`.

### 8.4 OpenGolfCoach

OpenGolfCoach consumes channel 1 fields directly except that it spells
`total_spin_rpm`. Its `offline_distance_meters` is `carry_side_meters` here,
same sign. Its handed output variants are a presentation of section 7's
single frame plus `player_dexterity`.

### 8.5 Recording to MCAP

An optional recorder can save a session to [MCAP](https://mcap.dev/spec)
without changing the CGP wire format. For standard channels and private
channels carrying JSON, one recording convention is:

| MCAP | Value |
|---|---|
| Schema | `encoding: "jsonschema"`, with data from the schema set for the recorded ALPN (`schema/` for this proposal; frozen directories for earlier drafts); use channel `schema_id: 0` when no schema is available |
| Channel | A recorder-chosen `topic`, such as `<connection>/<sender>/shot`; `message_encoding: "json"`; string metadata such as `{"channel_id": "1", "alpn": "common-golf/draft-3"}` |
| Message data | Original JSON payload bytes, excluding the CGP channel prefix and length header |
| Message times | `log_time`: recorder receipt time in nanoseconds; `publish_time`: the same value when publication time is unavailable |
| Message sequence | A recorder counter per recorded channel, or zero when unused; distinct from `shot_uuid` |

These labels and metadata are local to the recording, not additional wire
fields. Including the connection and sender separates both directions of
`info` and Open Connect traffic, and identifies the source connection
independently of shot identity. MCAP channel IDs are file-local and need not match CGP channel numbers.

MCAP timestamps use an epoch understood by the recording's users. The
payload's `measured_at` stays unchanged: measurement time is not necessarily
publication or receipt time. Earlier-draft `measured_at_nanoseconds` also
stays in the payload, and its unspecified epoch may be uptime. The
[MCAP encoding registry](https://mcap.dev/spec/registry) describes the JSON
and JSON Schema encodings. Non-JSON private channels need their own recording
encoding; this convention does not reinterpret their bytes as JSON.

### 8.6 OpenGolfAPI / OpenShot

[OpenShot v0](https://opengolfapi.org/schemas/openshot/v0.json) describes a
shot event with ball measurements and optional club, device, and context
fields. Its ball units include mph, degrees, rpm, and yards, so an adapter
maps fields and converts units to CGP. The published schema requires
`api_version: "0"` and `ball`; an unchanged Open Connect object lacks those
required fields. This comparison defines no additional CGP channel or
transport binding.

### 8.7 Pureplay Connect v1

[Pureplay Connect's specification](https://github.com/PureplayGolf/Pureplay-Connect#readme)
defines fixed-unit binary telemetry. The following is an informative adapter
mapping. `ValidMask` distinguishes measurements from unused stored values:
a cleared field's zero is not a measurement. Unavailable optional fields can
be omitted; section 5.1 illustrates estimating a required launch measurement.

| Pureplay Connect | Common Golf |
|---|---|
| `ShotId` | Allocate a UUIDv7 `shot_uuid` per source shot; retain the source-session/`ShotId` mapping across connections, related ball/club messages, and updates |
| `BallSpeedMS`, `VLA`, `HLA` | `ball_speed_meters_per_second`, `vertical_launch_angle_degrees`, `horizontal_launch_angle_degrees` |
| `TotalSpinRPM`, `SpinAxisDeg` | Total and axis, after checking the source sign convention |
| `BackSpinRPM`, `SideSpinRPM` without total and axis | Convert with section 5.1, preserving topspin sign |
| `ClubHeadSpeedMS`, `SmashFactor` | `club_speed_meters_per_second`, `smash_factor` |
| `DynamicLoftDeg`, `FaceAngleDeg`, `ClubPathDeg`, `AttackAngleDeg` | `dynamic_loft_degrees`, `face_angle_degrees`, `club_path_degrees`, `angle_of_attack_degrees` |
| `ImpactPointXMM`, `ImpactPointYMM` | `face_impact_horizontal_millimeters`, `face_impact_vertical_millimeters`, same signs |
| `ReadyState` | An enum (`NotReady`, `Ready`, `BallDetected`, `Processing`); mapping it to `device_status.ready` and `device_status.ball_detected` needs adapter policy |
| `Heartbeat.Battery` | `device_status.battery_percent`; unknown (`0xFF`) is omitted initially or clears a prior value with `null` |
| `Hello.DeviceName`, `Firmware` | `info.name`, `info.firmware_version` |
| `SetClubInfo` | Handedness maps to `simulator_state.player.dexterity`; club selection has no direct capture-mode mapping |
| `SetDetect` | Arm/disarm command; `simulator_state.ready` is simulator readiness, so conversion needs adapter policy rather than a direct rename |
| `SetBallType` | Vendor configuration; section 6 |
| `Error.Message` | `device_status.message` |

Pureplay's component gap-fill formula takes an unsigned square root for
total spin and uses `atan2(side, back)` for axis. With negative backspin,
that loses CGP's topspin sign and can put the axis outside CGP's range.
Section 5.1 gives the CGP conversion from components. Pureplay's negative-total
semantics are not explicit, and its worked spin example has opposite signs
for sidespin and axis, so a direct total/axis copy depends on checking the
source device's convention.

## 9. Not in this version

No standard telemetry, media/video, or trajectory-array contract is defined.
Custom raw streams and datagrams can carry such application-defined data.
Per-message signatures. Cloud or remote lookup of devices. Simulator-to-
device configuration. Multiplayer, tournaments and scoring. CBOR or any
non-JSON encoding on standard channels. Manual addressing in user
interfaces, which section 3.2 argues against on purpose rather than
deferring.

USB and Bluetooth bindings are not defined. A tether near the hitting area
can be struck or pulled; wireless operation avoids that placement constraint.
For future Bluetooth work, BLE is worth exploring before Bluetooth Classic,
including a mapping of channels to GATT characteristics rather than assuming
a serial-stream tunnel. Discovery, authentication, message boundaries, and
payload limits would need their own specification. This is design guidance,
not a claim that QUIC framing or trust transfers unchanged.
