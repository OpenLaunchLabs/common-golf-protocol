# Common Golf Protocol

| | |
|---|---|
| Document | 0001 |
| Status | Draft 1 |
| Protocol version | 0.1.1 |
| ALPN | `common-golf/draft-1` |
| Service type | `_common-golf._udp` |
| Date | 2026-10-11 |

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are to be read as
in RFC 2119. Everything else is explanation.

## 1. Why

Three protocols are in common use between launch monitors and simulators.

| Protocol | Transport | Who listens | Framing | Units | Direction | Discovery | Security |
|---|---|---|---|---|---|---|---|
| GSPro Open Connect v1 | TCP 921 | Simulator | Unspecified | `Units: "Yards"` label; speed is mph, unstated | Device to sim, with coded replies | None | None |
| OpenGolfSim | TCP 3111 | Simulator | Newline-delimited JSON | `unit: imperial\|metric`; results always metres | Bidirectional | None | None |
| OpenLaunch Nova | TCP 2921 and WebSocket 2920 | Device | Newline-ish on TCP, one frame per message on WS | mph on TCP, m/s on WS, same device | Device to sim only | mDNS | None |

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
  private key and can ask the connector to do the same, usually by
  presenting a certificate, which is a public key with a name around it.
  That public key identifies the holder. This document uses nothing else
  for identity.
- **Version negotiation is built in.** The connector names the application
  protocol in the handshake (ALPN). `common-golf/draft-1` is this draft;
  section 3.8 says how that changes.
- **Streams are cheap and independent.** One connection carries many
  streams. Each is reliable and ordered like a TCP socket, but a slow or
  stalled stream does not block the others. Either side can open streams,
  one-way or two-way.
- **Declining is built in.** A receiver that does not want a stream tells
  the sender to stop, with a code. No application message is needed.
- **Connections are cheap too.** A listener serves many connections on one
  port, each with its own handshake, certificate and streams. There is no
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

A listener MUST accept more than one connection at a time and MUST NOT
refuse or drop a connection because another exists. A connector MUST be
able to hold connections to more than one listener at a time. Every
connection is independent: its own peer certificate, its own tier, its own
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
| TXT | `vendor`, `model`, `serial` (all optional, all strings) |

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

### 3.3 Certificates

A listener MUST present a certificate; TLS requires it. A connector SHOULD
present one, and a listener SHOULD request it, so that both sides can be
identified. A connector that presents none is treated as a maker (section
4). A self-signed certificate generated on first run is sufficient and
expected, and so is a raw public key (RFC 7250) where the library supports
it. The key MUST be Ed25519, so that every identity is the same 32 bytes.
Certificate validity dates MUST be ignored, so a device with no clock is
conforming. No certificate authority is involved anywhere. Use a QUIC
library that brings its own TLS; the TLS built into Windows cannot do
Ed25519 and is the wrong choice here for other reasons too.

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

Every channel definition states its maximum message size, and a receiver
MUST accept any message up to that size on that channel. Channels 0 to 8
are small JSON objects and their maximum is 65,535 bytes. This is the
DNS-over-QUIC rule (RFC 9250, section 4.6): one fixed number, nothing
negotiated. Private channels choose their own. A future channel carrying
images or video would state its own ceiling, and would likely be a byte
stream rather than JSON frames. Every message in this document is under
four kilobytes.

A length above the channel's maximum, a payload that is not one valid JSON
object, or a FIN arriving inside a frame is malformed. The receiver MUST
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
| 2 | `malformed` | STOP_SENDING | Bad length, not one JSON object, or FIN inside a frame |

No other codes are defined. Nothing closes the connection except the
transport itself.

### 3.6 Keeping the connection up

A golfer takes a minute between shots and longer between holes. QUIC
closes a connection that has carried nothing for the negotiated idle
timeout, and library defaults are 30 to 60 seconds, so a connection that
only speaks when a ball is struck will be gone before the next swing.

Endpoints MUST keep an idle connection alive with QUIC PING frames. Every
library exposes this as a keep-alive interval; no application heartbeat
message is defined because QUIC already has one. Endpoints MUST advertise
an idle timeout of at least 60 seconds. The effective timeout is the
smaller of the two peers' values, so both sides need the floor. A
keep-alive every 15 seconds with an idle timeout of 90 seconds is
RECOMMENDED.

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
and drafts MAY break each other. Two peers on different drafts fail the
QUIC handshake, which is the intended failure: nothing half-works.

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
not a hash of the certificate.

That is on purpose. A certificate also carries a serial number, dates and
a name, so the same key wrapped twice would differ, and regenerating a
certificate would change a device's identity. The handshake proved
possession of the key; the certificate was only the envelope. Keying on
the bare key also means one registration covers an implementation that
presents the same key through a different transport, through a raw
public key handshake, or in another protocol that identifies peers by
Ed25519 key, of which there are several.

Each endpoint MAY keep a list of fingerprints it knows, each with a tier:

| Tier | Meaning |
|---|---|
| `internal` | The vendor's own software |
| `developer` | A registered third party |
| `maker` | Everyone else, including peers that presented no certificate |

An endpoint decides which channels to open toward a peer, and which to read
from it, by that peer's tier. A device might send plain shots to makers and
also club data and a private diagnostics channel to developers. A
tournament simulator might accept shots only from fingerprints it has
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

This is the whole security model. There are no signatures inside messages,
no nonces, no canonical JSON. If a device keeps its key in hardware,
matching its fingerprint means talking to that device.

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
| `protocol_version` | string | yes | Semver of this document, `0.1.1` |
| `name` | string | | Display name |
| `vendor`, `model`, `serial`, `firmware_version` | string | | |
| `channels` | array of integer | | Channels this side intends to open |

```json
{"type": "info", "protocol_version": "0.1.1", "name": "Garage LM", "vendor": "Garage Labs", "model": "GL-1", "channels": [0, 1, 4]}
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
| `reduced_accuracy` | boolean | | The device is less confident than usual |
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
| `final` | As on channel 1: each message for a `shot_number` replaces the previous one whole |

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
measurement with an estimate. `shot_number` required, rest optional.

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

## 9. Not in this version

Datagrams. Telemetry streams. Media and video. Trajectory point arrays.
Per-message signatures. Cloud or remote lookup of devices. Simulator-to-
device configuration. Multiplayer, tournaments and scoring. CBOR or any
non-JSON encoding on standard channels. Manual addressing in user
interfaces, which section 3.2 argues against on purpose rather than
deferring.
