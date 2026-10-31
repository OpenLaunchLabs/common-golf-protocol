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
open channels, its own private-channel arrangements, its own `shot_number`
sequence.

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
| TXT | `key` (required); `vendor`, `model`, `serial` (optional); all strings |

`key` is the listener's Ed25519 public key written as a fingerprint
(section 4): 64 lowercase hex characters, the same key the listener
presents in the handshake. A listener MUST include it. A connector MUST
reject a discovery record whose `key` is missing or is not exactly 64
lowercase hex characters. A listener whose key changes MUST re-announce
its TXT record (RFC 6762, section 8.4) so that caches drop the old value.
Like `serial`, an advertised key lets anyone on the network recognise the
listener when it reappears. What a connector may do with it is in section 4.

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
MUST accept any message up to that size on that channel. Channels 0 to 8
are small JSON objects and their maximum is 65,535 bytes. This is the
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
connection open. The sender MAY reopen the channel on a new stream.

Datagrams are not used in this version.

### 3.5 Declining a channel

A receiver that does not read a channel MUST send STOP_SENDING with
application error code 1 on that stream. Simply never reading it is not
allowed: unread bytes hold connection-level flow-control credit (RFC 9000,
section 4) and would eventually stall the channels the receiver does want.
The sender MUST treat code 1 as "not interested" and MUST NOT treat it as
an error or close the connection.

A stream for a channel the opener does not own (section 5), or a second
stream for a channel the opener already has open, is declined the same way.

| Code | Name | Sent as | Meaning |
|---|---|---|---|
| 1 | `not_interested` | STOP_SENDING | I do not read this channel, or you may not open it |
| 2 | `malformed` | STOP_SENDING | Bad length, not one JSON object, a number outside the double range, or FIN inside a frame |

No other codes are defined. Nothing closes the connection except the
transport itself.

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
  stream. A receiver MUST still accept messages that arrive before `info`,
  because the network may reorder streams. `info` is informational; nothing
  waits on it.
- One stream per channel per direction per connection. A second stream
  for a channel that is already open is declined with code 1.
- After a stream ends, by FIN or reset, the sender MAY reopen the channel
  on a new stream. Counters such as `shot_number` continue; they do not
  restart.
- On the two-way channel 2 only the opener writes the channel prefix. The
  responder writes framed messages on the same stream and nothing else.

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
| `common-golf/draft-3` | 0.3.0 (proposed) | Unreleased | Discussion: mandatory mutual raw public keys, required discovery key, no SNI dependence, and new connection timers |

At 1.0 the string becomes `common-golf/1`. From then on every change under
that string is additive: new optional fields, new channels. The rules that
receivers ignore unknown fields and decline unknown channels make that safe
without any further negotiation. A change that cannot be additive gets
`common-golf/2`.

`protocol_version` in `info` is for people and logs. Implementations MUST
NOT make decisions on it; the ALPN already did.

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
from it, by that peer's tier. A device might send plain
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

| ID | Name | Opened by | Flows | Stream | Max message | Needed for play |
|---|---|---|---|---|---|---|
| 0 | `info` | either | opener to peer | one-way | 65,535 | yes, first message |
| 1 | `shot` | device | device to sim | one-way | 65,535 | yes |
| 2 | `open_connect` | device | both ways | two-way | 65,535 | no, compatibility |
| 3 | `club` | device | device to sim | one-way | 65,535 | no |
| 4 | `status` | device | device to sim | one-way | 65,535 | no |
| 5 | `control` | controller | controller to sim | one-way | 65,535 | no |
| 6 | `state` | sim | sim to device | one-way | 65,535 | no |
| 7 | `result` | sim | sim to device | one-way | 65,535 | no |
| 8 | `flight` | device | device to sim | one-way | 65,535 | no |
| 9 to 999 | reserved | | | | | |
| 1000 and up | private | either | any | any | own | no |

Every simulator MUST read channel 1. A simulator MAY also offer channel 2.
A device MUST write channel 0 and then channel 1 or channel 2. Everything
else is optional and independent.

Channels 1 and 2 may both be open on one connection, but a device MUST NOT
send the same shot on both: a simulator cannot reliably match an Open
Connect `ShotNumber` to a `shot_number` and would count it twice. A device
SHOULD use one shot channel per connection, channel 1 by default. Section
5.2 says what channel 2 is for.

Rules for every standard channel:

- One JSON object per message. Every object on channels 0, 1, 3 to 8 has a
  `type` string equal to the channel name. Channel 2 carries foreign JSON
  with no envelope.
- Receivers MUST ignore fields they do not know.
- Senders MUST NOT send fields with the wrong unit under a known name.
- Nothing is acknowledged. A sender never waits for a reply. Where a
  simulator has something to say about a shot, it publishes a `result` on
  its own channel, and a device that cares correlates by `shot_number`.

JSON Schema for each channel is in `schema/`.

### 5.0 Channel 0: `info`

The first message each side sends, on the first stream it opens. Required.

| Field | Type | Required | Notes |
|---|---|---|---|
| `protocol_version` | string | yes | Semver of this discussion draft, `0.3.0` |
| `name` | string | | Display name |
| `vendor`, `model`, `serial`, `firmware_version` | string | | |
| `channels` | array of integer | | Channels this side intends to open |

```json
{"type": "info", "protocol_version": "0.3.0", "name": "Garage LM", "vendor": "Garage Labs", "model": "GL-1", "channels": [0, 1, 4]}
```

### 5.1 Channel 1: `shot`

What the device measured at launch. Nothing computed about flight belongs
here; that is channel 8.

| Field | Type | Required | Notes |
|---|---|---|---|
| `shot_number` | integer | yes | Increments per device per connection |
| `ball_speed_meters_per_second` | number | yes | |
| `vertical_launch_angle_degrees` | number | yes | Positive is up |
| `horizontal_launch_angle_degrees` | number | yes | Positive is right of target |
| `total_spin_revolutions_per_minute` | number | with axis | Positive is backspin, negative is topspin |
| `spin_axis_degrees` | number | with total | -90 to 90. 0 is no curve; positive curves right, whatever the sign of total |
| `backspin_revolutions_per_minute`, `sidespin_revolutions_per_minute` | number | | Only in addition to total and axis; positive sidespin curves right |
| `measurement_id` | string | | Globally unique; lets a simulator dedupe across reconnects |
| `measured_at_nanoseconds` | integer | | Nanoseconds since an unspecified epoch; monotonic is fine |
| `player_dexterity` | `right`, `left`, `unknown` | | See section 7 |
| `tee_position` | `tee`, `mat`, `ground`, `unknown` | | Where the ball was struck from |
| `estimated` | array of string | | Names of numeric fields the device could not measure for this shot, or believes to have reduced accuracy. See section 7 |
| `final` | boolean | | See below. Absent means final |
| `shot_kind` | `full`, `putt`, `chip` | | |

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
measurements and uses the existing `shot_kind` field:

```json
{"type": "shot", "shot_number": 13, "ball_speed_meters_per_second": 2.1, "horizontal_launch_angle_degrees": 0.4, "vertical_launch_angle_degrees": 0, "estimated": ["vertical_launch_angle_degrees"], "shot_kind": "putt"}
```

A device MAY send more than one `shot` for the same `shot_number` as its
estimate refines, each with `final: false` until the last, which carries
`final: true`. Each message is a complete snapshot that replaces the
previous one for that `shot_number`; nothing is merged, so a field present
in the preliminary and absent in the final is gone. A simulator that does
not want preliminaries ignores messages with `final: false`.

```json
{"type": "shot", "shot_number": 12, "ball_speed_meters_per_second": 67.3, "vertical_launch_angle_degrees": 12.1, "horizontal_launch_angle_degrees": -1.4, "total_spin_revolutions_per_minute": 2750, "spin_axis_degrees": -3.2, "player_dexterity": "right", "measurement_id": "2f1c…"}
```

### 5.2 Channel 2: `open_connect`

GSPro Open Connect v1 JSON, carried verbatim in both directions on one
two-way stream. The device writes the launch monitor body; the simulator
writes the `{Code, Message, Player}` reply. Field names, casing and units
are exactly as GSPro documents them. Nothing is translated.

This channel is a bridge for firmware that only emits Open Connect JSON,
so that such a device changes only its socket. It is not the baseline;
channel 1 is. A device that can speak channel 1 SHOULD, and a new device
has no reason to implement channel 2 at all.

A simulator MAY offer this channel. One that does MUST reply to each body
the way GSPro would, because Open Connect devices expect it. That is the
one place in this document where a reply is required, and it is inherited.
A simulator that does not offer it declines the stream with code 1, and
the device has no fallback: it reports that this simulator does not
support Open Connect compatibility, which is the same experience as a
refused TCP socket today. A device that can speak both MAY try channel 2
first and fall back to channel 1 when declined, but never runs both for
the same shots.

The device writes the channel prefix when it opens the stream. The
simulator writes only framed replies.

```json
{"DeviceID": "Garage LM", "Units": "Yards", "ShotNumber": 12, "APIversion": "1", "BallData": {"Speed": 150.5, "SpinAxis": -3.2, "TotalSpin": 2750, "HLA": -1.4, "VLA": 12.1}, "ShotDataOptions": {"ContainsBallData": true, "ContainsClubData": false}}
```

```json
{"Code": 200, "Message": "Shot received"}
```

### 5.3 Channel 3: `club`

What the device measured about the club. Every field is optional. With
`shot_number` it describes the club at impact for that shot. Without it,
and with `swing_number`, it is a standalone swing from a device with no
ball data, such as a swing-speed radar.

| Field | Notes |
|---|---|
| `shot_number` or `swing_number` | integer |
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
| `final` | As on channel 1: each message for a `shot_number` replaces the previous one whole |

Timing guidance (informative). For low latency, a simulator can start an
accepted ball shot from channel 1 without waiting for optional club data on
channel 3. Club data supports post-shot analysis. Sending available club
data promptly, and aiming to finish useful refinements within ten seconds
of the shot or before the next shot, whichever comes first, keeps that
analysis timely. This is
application guidance, not a protocol deadline or a receiver timeout. The
existing `final` flag and whole-snapshot replacement rules still apply.

A standalone club tracker has its own connection and `swing_number`
sequence. It is not automatically correlated with another device's
`shot_number`. Associating a nearby swing with a ball shot for display is a
local heuristic; it need not delay ball simulation or change either
device's counters.

```json
{"type": "club", "swing_number": 3, "club_speed_meters_per_second": 47.1}
```

### 5.4 Channel 4: `status`

Device state. Sent in full when the stream opens, then as patches: only
the fields that changed. An absent field is unchanged, a `null` field is
cleared. All optional.

| Field | Type |
|---|---|
| `ready` | boolean |
| `ball_detected` | boolean |
| `battery_percent` | number |
| `uptime_seconds` | number |
| `message` | string, human readable |

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

### 5.6 Channel 6: `state`

Simulator context. Sent in full when the stream opens, then as patches:
only the fields that changed. An absent field is unchanged, a `null` field
is cleared, and a nested object such as `player` replaces the previous
object whole rather than merging into it. All fields optional.

| Field | Notes |
|---|---|
| `ready` | The simulator will act on a shot right now |
| `player` | `{name, dexterity}` |
| `club` | `{id, name}`; `id` uses the common abbreviations `DR`, `3W`, `7I`, `PW`, `PT` |
| `mode` | `range`, `course`, `putting` |
| `ball_zone` | `{x_meters, y_meters, width_meters, height_meters}` where the ball should be placed, in the device's frame. Optional, and clunky for users; prefer devices that do not need it |

```json
{"type": "state", "ready": true, "mode": "course", "player": {"name": "Chad", "dexterity": "right"}, "club": {"id": "PT"}}
```

### 5.7 Channel 7: `result`

The simulator's computed outcome for a shot. Published when the simulator
has it. This is not an acknowledgement: a device MUST NOT treat silence as
either success or failure.

| Field | Notes |
|---|---|
| `shot_number` | required |
| `accepted` | boolean |
| `reject_reason` | string, when not accepted |
| `carry_distance_meters`, `total_distance_meters` | |
| `carry_side_meters`, `total_side_meters` | Positive is right of target |
| `max_height_meters` | |

### 5.8 Channel 8: `flight`

The device's own computed ball flight, when it offers one. Kept apart from
channel 1 so a simulator that runs its own physics never confuses a
measurement with a derived value. `shot_number` required, rest optional.

| Field |
|---|
| `carry_distance_meters`, `carry_side_meters` |
| `total_distance_meters`, `total_side_meters` |
| `max_height_meters`, `curve_meters` |
| `landing_angle_degrees`, `hang_time_seconds` |
| `final`, as on channel 1: each message for a `shot_number` replaces the previous one whole |

Trajectories as point arrays are deliberately not carried. They are library
output; see OpenGolfCoach.

## 6. Private channels: 1000 and up

Channels 1000 and above belong to the two endpoints of a connection and to
nobody else. Their meaning is whatever those two endpoints arranged, and
the arrangement is scoped to that connection. Channel 1000 on a device's
connection to vendor A's software can mean something unrelated to channel
1000 on the same device's connection to vendor B's software. There is no
registry and nothing to register.

A private channel MAY carry JSON in this document's framing, or anything
else; the receiver knows what to expect because it arranged it. Typical
uses: diagnostics, licence and unlock traffic, firmware, raw sensor dumps,
anything proprietary. All of it is already encrypted by the transport.

The opener SHOULD send, as the first message on a private channel, a JSON
object naming the arrangement so the peer can decline what it does not
recognise:

```json
{"type": "private", "name": "com.example.diagnostics", "version": "1.2.0"}
```

A device SHOULD open private channels only toward peers whose tier
warrants it (section 4).

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
opposite. `unknown` is a legitimate value when the device cannot tell. The
wire never carries two handed variants of a number.

Timestamps are nanoseconds since an epoch the sender does not have to
define. Monotonic since boot is fine.

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

Carried unchanged on channel 2. For a simulator that wants to normalise
those bodies into channel 1 and 3 terms, with `Units: "Yards"` meaning mph,
rpm, degrees and yards:

| Open Connect | Common Golf | Factor |
|---|---|---|
| `ShotNumber` | `shot_number` | |
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
| `ShotDataOptions.LaunchMonitorIsReady` | `status.ready` | |
| `ShotDataOptions.LaunchMonitorBallDetected` | `status.ball_detected` | |
| Reply `Code 200` | `result.accepted: true` | |
| Reply `Code 201` + `Player` | `state.player`, `state.club` | `RH`/`LH` to `right`/`left` |

### 8.2 OpenGolfSim

| OpenGolfSim | Common Golf |
|---|---|
| `shot.shot.ballSpeed` | `ball_speed_meters_per_second` (× 0.44704 when `unit` is imperial) |
| `shot.shot.verticalLaunchAngle`, `horizontalLaunchAngle` | launch angles |
| `shot.shot.spinSpeed` | `total_spin_revolutions_per_minute` |
| `shot.shot.spinAxis` | `spin_axis_degrees`, **negated** (OpenGolfSim positive is left-curving) |
| `device.status` | `status.ready` |
| `player.data.club.{id,name}` | `state.club` |
| `result.data.result.{carry,total,lateral,height}` | `result.carry_distance_meters`, `total_distance_meters`, `carry_side_meters`, `max_height_meters` |
| `control.command` `left right up down club-up club-down drop mulligan rehit` | `control.command` with hyphens replaced by underscores; `event` passes through |

### 8.3 OpenLaunch Nova

Nova's WebSocket feed is already snake_case SI and maps by rename:
`ball_speed_meters_per_second`, `vertical_launch_angle_degrees`,
`horizontal_launch_angle_degrees` and `spin_axis_degrees` are identical;
`total_spin_rpm` becomes `total_spin_revolutions_per_minute`, negative
topspin included;
`timestamp_ns` becomes `measured_at_nanoseconds`; `status.uptime_seconds`
and `status.firmware_version` go to `status` and `info`. Nova's TCP API is
Open Connect and goes on channel 2. Nova's touch bar maps to `control`:
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
| Schema | `encoding: "jsonschema"`, with data from the matching file under `schema/draft-2/`; use channel `schema_id: 0` when no schema is available |
| Channel | A recorder-chosen `topic`, such as `<connection>/<sender>/shot`; `message_encoding: "json"`; string metadata such as `{"channel_id": "1", "alpn": "common-golf/draft-2"}` |
| Message data | Original JSON payload bytes, excluding the CGP channel prefix and length header |
| Message times | `log_time`: recorder receipt time in nanoseconds; `publish_time`: the same value when publication time is unavailable |
| Message sequence | A recorder counter per recorded channel, or zero when unused; distinct from `shot_number` |

These labels and metadata are local to the recording, not additional wire
fields. Including the connection and sender separates both directions of
`info` and Open Connect traffic, and keeps different devices' shot counters
apart. MCAP channel IDs are file-local and need not match CGP channel numbers.

MCAP timestamps use an epoch understood by the recording's users. The
payload's `measured_at_nanoseconds` stays unchanged: its epoch may be uptime,
and measurement time is not necessarily publication or receipt time. The
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
| `ShotId` | Allocate increasing `shot_number` values on each CGP connection; retain the source-session/`ShotId` mapping for related ball/club messages and updates |
| `BallSpeedMS`, `VLA`, `HLA` | `ball_speed_meters_per_second`, `vertical_launch_angle_degrees`, `horizontal_launch_angle_degrees` |
| `TotalSpinRPM`, `SpinAxisDeg` | Total and axis, after checking the source sign convention |
| `BackSpinRPM`, `SideSpinRPM` without total and axis | Convert with section 5.1, preserving topspin sign |
| `ClubHeadSpeedMS`, `SmashFactor` | `club_speed_meters_per_second`, `smash_factor` |
| `DynamicLoftDeg`, `FaceAngleDeg`, `ClubPathDeg`, `AttackAngleDeg` | `dynamic_loft_degrees`, `face_angle_degrees`, `club_path_degrees`, `angle_of_attack_degrees` |
| `ImpactPointXMM`, `ImpactPointYMM` | `face_impact_horizontal_millimeters`, `face_impact_vertical_millimeters`, same signs |
| `ReadyState` | An enum (`NotReady`, `Ready`, `BallDetected`, `Processing`); mapping it to `status.ready` and `status.ball_detected` needs adapter policy |
| `Heartbeat.Battery` | `status.battery_percent`; unknown (`0xFF`) is omitted initially or clears a prior value with `null` |
| `Hello.DeviceName`, `Firmware` | `info.name`, `info.firmware_version` |
| `SetClubInfo` | Handedness maps to `state.player.dexterity`; club selection maps to `state.club` using CGP's club abbreviations |
| `SetDetect` | Arm/disarm command; `state.ready` is simulator readiness, so conversion needs adapter policy rather than a direct rename |
| `SetBallType` | Vendor configuration; section 6 |
| `Error.Message` | `status.message` |

Pureplay's component gap-fill formula takes an unsigned square root for
total spin and uses `atan2(side, back)` for axis. With negative backspin,
that loses CGP's topspin sign and can put the axis outside CGP's range.
Section 5.1 gives the CGP conversion from components. Pureplay's negative-total
semantics are not explicit, and its worked spin example has opposite signs
for sidespin and axis, so a direct total/axis copy depends on checking the
source device's convention.

## 9. Not in this version

Datagrams. Telemetry streams. Media and video. Trajectory point arrays.
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
