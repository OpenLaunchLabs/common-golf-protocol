# Common Golf Protocol

| | |
|---|---|
| Document | 0001 |
| Status | Draft 3, unreleased |
| Protocol version | 0.3.0 |
| ALPN | `common-golf/draft-3` |
| Service type | `_common-golf._udp` |

This specification defines draft 3. Published drafts 1 and 2 retain their
schemas in `schema/draft-1/` and `schema/draft-2/`. Section 3.8 lists the
supported protocol versions and their ALPN identifiers.

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are interpreted as
described in RFC 2119.

## 1. Overview

Common Golf Protocol (CGP) connects launch monitors, swing radars, control
boxes and simulator software over QUIC. It provides local device discovery,
mutual authentication, explicit measurement units and independent channels
for measurements, controls and simulator state.

Standard channels define shared message formats. Custom channels support
vendor features and existing protocols without changing those formats.

## 2. Transport overview

QUIC (RFC 9000) provides encrypted connections with TLS 1.3, application
protocol selection through ALPN, and independent reliable streams. Either
endpoint can open a stream, and a stalled stream does not block other
streams. Receivers can decline streams they do not use.

CGP requires both endpoints to authenticate with Ed25519 raw public keys
(RFC 7250). Each connection authenticates its peers and has its own access
policy and streams. Standard messages use JSON with a four-byte channel
prefix per stream and a four-byte length prefix per message. Custom
contracts can also use raw streams or QUIC datagrams.

## 3. Transport

### 3.1 Roles

Connection roles use QUIC terminology ([RFC 9000, section
1.2](https://www.rfc-editor.org/rfc/rfc9000.html#section-1.2)): the **client**
initiates a QUIC connection, and the **server** accepts it. An **endpoint**
is either participant; its **peer** is the other endpoint. Client and server
below always refer to these QUIC roles.

Device, controller and simulator are application roles. Normally, the device
acts as the QUIC server, and the simulator acts as the QUIC client. The
simulator displays discovered devices for the user to select. Either side
MAY take either QUIC role when a deployment needs it; channel ownership
(section 5) is independent of the QUIC role.

A **bridge application** translates between CGP and another interface or
protocol. A desktop bridge for a device acts on the device's behalf and
normally accepts CGP connections as a QUIC server. The term bridge describes
the application's purpose; its QUIC client or server role is determined
separately for each connection.

The **stream initiator** is the endpoint that opens a particular stream
([RFC 9000, section 2.1](https://www.rfc-editor.org/rfc/rfc9000.html#section-2.1)).
Either the QUIC client or server can initiate streams, subject to channel
ownership and contract rules. On a bidirectional stream, the initiator's
peer can send data on the same stream without becoming its initiator.

A server MUST accept more than one connection at a time and MUST NOT
refuse or drop a connection because another exists. A client MUST be able
to hold connections to more than one server at a time. Every connection
has its own authenticated key, tier, open channels and custom-channel menu.
A shot retains its `shot_uuid` when sent to multiple peers or replayed after
reconnection.

### 3.2 Discovery

A server MUST bind an ephemeral UDP port and MUST advertise itself with
DNS-SD over mDNS (RFC 6762, RFC 6763):

| Record | Value |
|---|---|
| Service type | `_common-golf._udp.local.` |
| Instance name | Human-readable device name |
| SRV | The host and the port the server actually bound |
| TXT | `key` (required); `vendor`, `model`, `serial`, `alpn`, `caps` (optional); all strings |

`key` is the server's fingerprint (section 4): the Ed25519 public key it
presents in the handshake, encoded as 64 lowercase hex characters. A
server MUST include it. A client MUST reject a record whose `key` is
missing or is not exactly 64 lowercase hex characters. The fingerprint is
visible to the local network and can identify a returning server.

`alpn` and `caps` provide information for the discovery interface. The
negotiated ALPN and the `info` received after connection are authoritative.
Clients MUST ignore unknown TXT keys.

`alpn` lists every ALPN string the server accepts, comma-separated, each
with the `common-golf/` prefix removed (`draft-3` for
`common-golf/draft-3`). It MUST match what the handshake accepts. Clients
compare entries exactly after restoring the prefix and MUST NOT parse them
for version numbers. When no entry matches a supported string, the client
MAY skip the handshake and report the incompatibility. An unrecognized
identifier can indicate that the client needs an update; a known but
unsupported older identifier can indicate that the server needs one.

`caps` is a comma-separated list of lowercase ASCII capabilities available
to at least one tier:

| Value | Meaning | Channels |
|---|---|---|
| `ball` | Sends ball data | 1 |
| `club` | Sends club data | 3 |
| `flight` | Sends a computed flight | 8 |
| `control` | Sends control input | 5 |
| `simulation` | Runs the game and sends state and results | 6, 7 |

Clients MUST ignore values they do not know; values may be added without
changing the ALPN. Clients MAY use `caps` to group, label or filter
servers in a user interface, and MUST NOT use it to decide which channels
to open or read. A server does not necessarily send every capability to
every tier.

A server whose `key`, `alpn` or `caps` value changes MUST re-announce its
TXT record (RFC 6762, section 8.4).

```text
vendor=Garage Labs
model=GL-1
key=<64 hex characters>
alpn=draft-2,draft-3
caps=ball,club,control
```

A client MUST find servers by browsing for the service type.
Implementations SHOULD NOT present an IP address, a port, or a scan control
in a user interface. Users connect by selecting a discovered device.

Both endpoints must share a network segment that permits multicast. Networks
that block multicast prevent discovery. Diagnostic address overrides belong
in support tools or configuration files, outside the product's connection
interface.

### 3.3 Raw public keys

Both endpoints MUST authenticate with Ed25519 raw public keys ([RFC
7250](https://www.rfc-editor.org/rfc/rfc7250.html)). The TLS
`server_certificate_type` and `client_certificate_type` extensions MUST
negotiate `RawPublicKey`. Endpoints MUST NOT offer, accept, or fall back to
X.509 certificates.

The server MUST request the client's key and the client MUST present
it. Each endpoint MUST verify the peer's proof of possession. A missing key,
a key of another algorithm, or failed proof of possession MUST fail the
handshake. Authenticated peers with unregistered keys receive the `maker`
tier (section 4).

RFC 7250 carries the key as DER-encoded SubjectPublicKeyInfo. The
fingerprint is the underlying 32 public-key bytes. An implementation can
generate a key on first run and retain it across restarts. Authentication
does not require a certificate authority or a clock.

Servers MUST NOT depend on TLS Server Name Indication (SNI). A server
MUST accept an otherwise valid handshake with no SNI and MUST NOT require a
particular SNI value. Discovery supplies the destination, ALPN selects the
protocol, and the key supplies identity.

Implementations MUST disable QUIC 0-RTT early data for CGP connections.
CGP application data MUST NOT be sent or accepted in 0-RTT. This avoids
early-data replay (RFC 9001, section 9.2), which can repeat actions
triggered by `control` messages.

### 3.4 Framing

Every reliable stream begins with a channel ID written by the stream initiator.
Replies on the same bidirectional stream omit this prefix.

```
channel_id: 4 octets, unsigned, big-endian
```

Standard channels and framed-JSON custom contracts then encode each message
as:

```
length:  4 octets, unsigned, big-endian, counting payload only
payload: exactly length octets, one UTF-8 JSON object
```

A reader MUST handle a message split across reads and several messages in
one read. Implementations encode the byte order explicitly rather than
copying native structs onto the wire.

Each channel defines a maximum message size. A receiver MUST accept any
message up to that size. Channels 0, 1 and 3 to 8 have a fixed limit of
65,535 bytes; custom contracts define their own limits.

JSON numbers MUST be within the IEEE 754 double range (I-JSON, RFC 7493,
section 2.2).

A length above the channel's maximum, a payload that is not one valid JSON
object, a number outside the double range, or a FIN inside a frame is
malformed. The receiver MUST discard the rest of that stream, MUST send
STOP_SENDING with code 2 if the stream is still open (a stream already ended
by FIN is discarded), and MUST keep the connection open. The sender
MAY reopen the channel on a new stream unless it has been declined with code
1 on this connection.

### 3.5 Declining a channel

A receiver that does not read a stream MUST send STOP_SENDING with code 1.
Unread streams consume connection-level flow-control credit (RFC 9000,
section 4) and can stall other channels. Custom streams awaiting the menu
are the only exception (section 3.9). The sender MUST treat code 1 as a
refusal for the rest of the connection: it MUST NOT reopen that channel on
this connection, treat the decline as an error, or close the connection.

A stream for a channel the initiator does not own (section 5), or a second
stream for a framed-JSON channel the initiator already has open, is declined
the same way.

| Code | Name | Sent as | Meaning |
|---|---|---|---|
| 1 | `not_interested` | STOP_SENDING | Do not open this channel again on this connection |
| 2 | `malformed` | STOP_SENDING | Malformed stream; the channel may be reopened |

No other codes are defined. Stream errors leave the connection open.
Identity failures can close it as described in section 4.

### 3.6 Connection keep-alive

Endpoints MUST send QUIC PING frames at a 5-second keep-alive interval and
MUST advertise `max_idle_timeout` of 30,000 milliseconds. CGP does not use
an application heartbeat. Idle expiry follows [RFC 9000, section
10.1](https://www.rfc-editor.org/rfc/rfc9000.html#section-10.1), including
its minimum of three probe timeouts, so unanswered PINGs do not keep a dead
peer's connection alive.

### 3.7 Streams and channels

QUIC orders bytes within a stream, not across streams (RFC 9000, section 2).

- Each side MUST send its `info` (channel 0) before opening any other
  stream. Receivers MUST accept standard-channel messages that arrive
  before `info`. Standard channels do not wait for the peer's `info`.
- Standard channels and framed-JSON custom contracts allow one stream per
  channel per direction. A second one while the first is open is declined
  with code 1. Raw contracts may use concurrent streams (section 6.3).
- After a stream ends by FIN or reset, the sender MAY reopen the channel
  unless it received code 1 for that channel on this connection. Reopening
  does not change shot identity.
- On a bidirectional stream, only the stream initiator writes the channel
  prefix. Its peer writes directly in the contract's format.

### 3.8 Versions

The ALPN identifier selects the wire format. Before version 1.0, each draft
has its own identifier, and drafts MAY introduce incompatible changes.

A client MAY offer several identifiers and a server MAY accept several.
TLS ALPN selects one according to the server's preference. The selected
identifier applies to the whole connection; peers MUST NOT mix drafts within
it. If no identifier is shared, the handshake fails.

A new identifier is required when a field or channel is removed, renamed or
given a different meaning. Adding an optional field or channel does not
change the identifier.

| ALPN | Protocol versions | Release date | Changes |
|---|---|---|---|
| `common-golf/draft-1` | 0.1.0, 0.1.1 | 2026-10-11 | First draft |
| `common-golf/draft-2` | 0.2.0 | 2026-10-17 | `reduced_accuracy` on channel 1 replaced by `estimated` on channels 1 and 3 (#2) |
| `common-golf/draft-3` | 0.3.0 | Unreleased | Mutual raw public keys, required discovery key, no SNI dependence, new timers, UUIDv7 shot identity without `final`, server-menu custom channels replacing channel 2, renamed state channels with capture mode and lie |

Version 1.0 uses `common-golf/1` and permits only additive changes under
that identifier. Incompatible changes require `common-golf/2`.

`protocol_version` in `info` is informational and intended for display and
logging. Implementations MUST NOT use it to select protocol behavior.

### 3.9 Connection lifecycle

| Step | What happens |
|---|---|
| 1. Handshake | ALPN selects one draft. Both endpoints present and authenticate Ed25519 raw public keys (section 3.3). |
| 2. Identity | The server maps the client's key to a tier. The client MUST match the advertised server key to the handshake key, or close before opening any channel (section 4). |
| 3. `info` | Each side's first stream is channel 0. The server's first `info` carries the custom-channel menu chosen for that peer (section 6.2). |
| 4. Use | Each side opens the channels it owns; each receiver keeps or declines each stream. |

After sending its own `info`, an endpoint MAY send standard traffic, and the
server MAY open offered custom channels, without waiting for the peer's
`info`. There is no additional selection, acknowledgement or ready exchange.
A client MUST receive and validate the server's menu before opening a
custom channel or sending a custom datagram. Hints such as `reads` and
`reads_contracts` MUST NOT become a startup prerequisite.

A receiver MUST NOT decline a custom stream only because the server's menu
has not arrived. It MUST hold the stream unread until the menu arrives and
is validated, then keep or decline it. If the initial menu is invalid,
custom channels are unavailable and held streams are declined with code 1.
Custom datagrams that arrive before the menu are discarded.

## 4. Trust

A peer's identity is its Ed25519 public key. Its fingerprint is the 32-byte
key encoded as 64 lowercase hex characters, rather than a hash of the key or
its SubjectPublicKeyInfo encoding. The same fingerprint identifies the key
across transports and protocols.

Each endpoint MAY keep a list of fingerprints, each with a tier:

| Tier | Meaning |
|---|---|
| `internal` | The vendor's own software |
| `developer` | A registered third party |
| `maker` | Every authenticated peer whose key is not registered |

An endpoint uses the peer's tier to decide which channels to send or read
and which custom contracts to offer. For example, a device can provide shots
to makers and additional club data to developers. An endpoint without a
registration list treats all authenticated peers as makers and is
conforming. Registration procedures are outside this specification; removing
a fingerprint revokes its registered tier.

Authentication proves possession of a private key. It does not establish
that an unregistered peer is a physical device. A familiar device name with
an unrecognized fingerprint represents a new identity. An implementation
with a registration list SHOULD notify the user of this change.

The advertised `key` (section 3.2) allows clients to label or filter
discovered servers by tier before connecting. The advertisement is
unauthenticated: a client MUST NOT treat it as authentication and MUST
compare it with the handshake key. If the keys differ, the client MUST
close the connection before opening any channel and report the server as
an unknown device. It MAY rediscover and retry once to account for stale
discovery records after a key change. A client SHOULD retain and try
every advertisement claiming a given key so that a false advertisement
cannot hide a legitimate server.

A key may identify an organization or product line rather than a single
device. Multiple servers can present the same key; a client MUST NOT
merge them or choose between them by key alone. Key rotation requires
replacing the registered fingerprint. Expiration is a local registration
policy.

Message authentication relies on the transport. CGP adds no per-message
signatures, nonces or JSON canonicalization.

## 5. Channels

A channel is identified by an unsigned 32-bit integer at the start of a
stream. Each standard channel has one owner and one direction. The owner
opens a QUIC unidirectional stream, writes the channel ID, then sends
messages.

| ID | Name | Contract | Opened by | Flows | Stream | Max message | Needed for play |
|---|---|---|---|---|---|---|---|
| 0 | `info` | `io.common-golf.info-v1` | either | stream initiator to peer | one-way | 65,535 | yes, first message |
| 1 | `shot` | `io.common-golf.shot-v1` | device | device to sim | one-way | 65,535 | yes |
| 2 | reserved | | | | | | |
| 3 | `club` | `io.common-golf.club-v1` | device | device to sim | one-way | 65,535 | no |
| 4 | `device_status` | `io.common-golf.device-status-v1` | device | device to sim | one-way | 65,535 | no |
| 5 | `control` | `io.common-golf.control-v1` | controller | controller to sim | one-way | 65,535 | no |
| 6 | `simulator_state` | `io.common-golf.simulator-state-v1` | sim | sim to device | one-way | 65,535 | no |
| 7 | `result` | `io.common-golf.result-v1` | sim | sim to device | one-way | 65,535 | no |
| 8 | `flight` | `io.common-golf.flight-v1` | device | device to sim | one-way | 65,535 | no |
| 9 to 999 | reserved | | | | | | |
| 1000 and up | custom | per connection | contract | contract | contract | contract | no |

The Contract column identifies the documented message contracts. ALPN
defines channels 0, 1 and 3 to 8; their contract identifiers are not sent or
negotiated, and a custom-channel menu cannot redefine them. Receivers MUST
decline streams on channel 2 and channels 9 to 999 with code 1. These are
reserved for future standard channels.

Every simulator MUST read channel 1. A launch monitor may send shots on
channel 1, on the Open Connect contract (section 6.5), or both, including
the same shots. Other channels are optional.

Rules for every standard channel:

- One JSON object per message, with a `type` string equal to the channel
  name.
- Receivers MUST ignore fields they do not know.
- Senders MUST NOT send a value in a unit other than the one its field name
  states.
- Standard messages require no acknowledgement, and senders do not wait
  for replies. Simulators publish shot outcomes as `result` messages,
  correlated by `shot_uuid`.

JSON Schemas for each channel are in `schema/`.

**Shot identity.** Channels 1, 3, 7 and 8 identify a shot by `shot_uuid`.
The source generates a new UUIDv7
([RFC 9562, section 5.7](https://www.rfc-editor.org/rfc/rfc9562.html#section-5.7))
for each shot. Every message about that shot, including club data, flight
and results, MUST reuse it, as MUST updates and replays across stream
reopenings and reconnects.

A `shot_uuid` MUST be exactly 36 lowercase ASCII characters in hyphenated
`8-4-4-4-12` form with version 7 and the RFC variant. The decoded JSON
string MUST match:

```text
^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$
```

For example, `017f22e2-79b0-7cc3-98c4-dc0c0c07398f`. Uppercase, braces, URN
prefixes, missing hyphens and other versions are invalid. Receivers compare
strings byte for byte and MUST NOT normalize an invalid spelling. An invalid
`shot_uuid` is malformed (section 3.4). The schemas enforce both length and
pattern independently of format validation.

The timestamp inside a UUIDv7 is not the measurement time; receivers MUST
NOT use it as one, or to order snapshots. `measured_at` carries the
measurement time when known (section 7).

**Snapshots.** Messages with the same `shot_uuid` on the same channel are
successive snapshots, ordered by receipt, including across stream
reopenings. Receivers MAY accept replacements or retain the first snapshot
and ignore later ones. A receiver that accepts a replacement MUST replace
the whole snapshot without merging fields or counting an additional shot.
Omitted optional fields are removed. The same UUID on another channel
correlates messages without replacing them. Snapshots can be used
immediately; there is no completion marker.

### 5.0 Channel 0: `info`

Each endpoint sends `info` as its first message on its first stream.

| Field | Type | Required | Notes |
|---|---|---|---|
| `protocol_version` | string | yes | `0.3.0` |
| `name` | string | | Display name |
| `vendor`, `model`, `serial`, `firmware_version` | string | | |
| `channels` | array of integer | | Channels this side intends to open |
| `reads` | array of integer | | Standard channels (0, 1, 3 to 8) this side reads |
| `contracts` | array of objects | | Server's custom-channel menu; section 6.2 |
| `reads_contracts` | array of string | | Custom contract identifiers this side reads; section 6.2 |

```json
{"type": "info", "protocol_version": "0.3.0", "name": "Garage LM", "vendor": "Garage Labs", "model": "GL-1", "channels": [0, 1, 4]}
```

`reads` and `reads_contracts` describe receive capabilities, not permissions,
preferences or acknowledgements. An absent list provides no information.
An empty `reads` list indicates that no standard channels are
read; an empty `reads_contracts` list indicates that no custom contracts are
read. A sender can use the hints to avoid unwanted streams or open a stream
and accept a decline.

### 5.1 Channel 1: `shot`

Ball measurements at launch. Computed flight is sent on channel 8.

| Field | Type | Required | Notes |
|---|---|---|---|
| `shot_uuid` | string | yes | UUIDv7; section 5 |
| `ball_speed_meters_per_second` | number | yes | |
| `vertical_launch_angle_degrees` | number | yes | Positive is up |
| `horizontal_launch_angle_degrees` | number | yes | Positive is right of target |
| `total_spin_revolutions_per_minute` | number | with axis | Positive is backspin, negative is topspin |
| `spin_axis_degrees` | number | with total | -90 to 90. 0 is no curve; positive curves right, regardless of total spin sign |
| `backspin_revolutions_per_minute`, `sidespin_revolutions_per_minute` | number | | Only in addition to total and axis; positive sidespin curves right |
| `measured_at` | string | | UTC RFC 3339 measurement time; omit if unknown |
| `player_dexterity` | `right`, `left` | | Omit when unknown; section 7 |
| `ball_lie` | `tee`, `fairway`, `putting_green`, `sand`, `rough` | | Known hitting-area lie; omit when unknown |
| `estimated` | array of string | | Numeric fields not measured or of reduced accuracy; section 7 |

`ball_lie` identifies the hitting surface, including simulated sand or
rough, so the simulator can avoid applying an effect already present in the
measured launch. It does not request a ball position or specify a penalty.
Sources omit unknown lies rather than infer them from measurements.

Spin uses a canonical total-and-axis representation. Both fields are sent
together or omitted together. Total spin is positive for backspin and
negative for topspin. The axis sign indicates curve direction regardless of
the total spin sign. Zero total spin requires an axis of 0. Components can
be calculated as:

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

Receivers can use the canonical representation directly.

A putting device that measures speed and direction but not launch angle can
send a vertical angle of zero and mark it estimated. Optional unmeasured
values, such as spin, are omitted:

```json
{"type": "shot", "shot_uuid": "017f22e2-79b0-7cc3-98c4-dc0c0c07398f", "ball_speed_meters_per_second": 2.1, "horizontal_launch_angle_degrees": 0.4, "vertical_launch_angle_degrees": 0, "estimated": ["vertical_launch_angle_degrees"]}
```

A device MAY send updated measurements using the same `shot_uuid`. Each
update is a complete snapshot (section 5):

```json
{"type": "shot", "shot_uuid": "017f22e2-79b0-7cc3-98c4-dc0c0c07398f", "ball_speed_meters_per_second": 67.3, "vertical_launch_angle_degrees": 12.1, "horizontal_launch_angle_degrees": -1.4, "total_spin_revolutions_per_minute": 2750, "spin_axis_degrees": -3.2, "player_dexterity": "right", "measured_at": "2022-02-22T19:22:21.950Z"}
```

### 5.2 Channel 2: reserved

Receivers MUST decline channel 2 with code 1. A menu entry assigning it is
malformed. Open Connect is a custom contract (section 6.5).

### 5.3 Channel 3: `club`

Club measurements. All fields other than `type` are optional. A `shot_uuid`
shared with a `shot` associates the club measurements with that impact. A
standalone swing from a device without ball data, such as a swing-speed
radar, MAY carry its own `shot_uuid`. Messages without a UUID cannot be
correlated or replaced.

| Field | Notes |
|---|---|
| `shot_uuid` | UUIDv7, shared with the related shot or generated for a standalone swing |
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
| `player_dexterity` | Section 7 |
| `estimated` | As on channel 1 |

```json
{"type": "club", "shot_uuid": "017f22e2-79b0-7cc3-98c4-dc0c0c07398f", "club_speed_meters_per_second": 47.1}
```

A simulator can process channel 1 without waiting for club data. A
standalone club tracker's `shot_uuid` does not correlate with another
device's ball shot; any pairing for display is a local heuristic.

### 5.4 Channel 4: `device_status`

Device state uses patches. The device sends a complete snapshot when the
stream opens, followed by changed fields only. An omitted field remains
unchanged, `null` clears a value, and a nested object replaces the previous
object in full. All fields other than `type` are optional.

| Field | Notes |
|---|---|
| `ready` | Whether the device is ready to capture |
| `capture_mode` | Mode in effect: `full`, `short`, or `putting` |
| `ball_detected` | Whether the device detects a ball |
| `ball_zone` | `{x_meters, y_meters, width_meters, height_meters}`: the usable placement region; width and height are nonnegative |
| `ball_position` | `{x_meters, y_meters}`: the detected ball position |
| `battery_percent` | 0 to 100 |
| `uptime_seconds` | Nonnegative |
| `message` | Human-readable device message |

Positions use a local capture-area coordinate system with an origin defined
and documented by the device. Positive x points right of the target line,
and positive y points forward along it. These coordinates report device
state; they do not set course positions or command ball placement. Omitted
coordinates provide no information about placement requirements.

`capture_mode` reports the mode actually in effect, whether or not the
simulator requested it (section 5.6). It is not an acknowledgement.

```json
{"type": "device_status", "ready": true, "capture_mode": "putting", "ball_detected": true, "ball_zone": {"x_meters": -0.1, "y_meters": 0, "width_meters": 0.2, "height_meters": 0.3}, "ball_position": {"x_meters": 0, "y_meters": 0.1}}
```

### 5.5 Channel 5: `control`

Button events sent to the simulator by a control box, touch surface or
device controls.

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

`down` and `up` apply to the four aim commands, which repeat while held. All
other commands use `press`. A simulator MUST ignore commands it does not
know. Vendor-specific buttons use a custom channel.

```json
{"type": "control", "command": "left", "event": "down"}
```

### 5.6 Channel 6: `simulator_state`

Simulator state uses the same patch rules as channel 4. All fields other
than `type` are optional.

| Field | Notes |
|---|---|
| `ready` | The simulator will act on a shot now; not a device arm/disarm command |
| `player` | `{name, dexterity}`; dexterity is `right` or `left`, omitted when unknown |
| `distance_to_target_meters` | Nonnegative distance from the ball to the pin or aim point |
| `requested_capture_mode` | `full`, `short`, or `putting`; `null` clears the request |

A device MAY use or ignore the requested capture mode. The simulator MUST
NOT wait for an acknowledgement. The request does not control readiness;
`device_status.capture_mode` reports the active mode. Clearing a request
returns mode selection to device policy.

Club selection and game mode require custom contracts. This specification
defines no mapping from either to a capture mode.

```json
{"type": "simulator_state", "ready": true, "player": {"name": "Alex", "dexterity": "right"}, "distance_to_target_meters": 3.048, "requested_capture_mode": "putting"}
```

### 5.7 Channel 7: `result`

The simulator publishes a `result` when a shot outcome is available. Results
are optional; a device MUST NOT treat the absence of a result as success or
failure.

| Field | Notes |
|---|---|
| `shot_uuid` | Required |
| `accepted` | boolean |
| `reject_reason` | string, when not accepted |
| `carry_distance_meters`, `total_distance_meters` | |
| `carry_side_meters`, `total_side_meters` | Positive is right of target |
| `max_height_meters` | |

### 5.8 Channel 8: `flight`

Ball flight computed by the device. Channel 8 separates derived flight
values from the launch measurements on channel 1. `shot_uuid` is required;
all other fields except `type` are optional.

| Field |
|---|
| `carry_distance_meters`, `carry_side_meters` |
| `total_distance_meters`, `total_side_meters` |
| `max_height_meters`, `curve_meters` |
| `landing_angle_degrees`, `hang_time_seconds` |

Trajectory point arrays require a custom contract.

## 6. Custom channels: 1000 and up

Channel numbers below 1000 are defined or reserved by this specification.
Numbers from 1000 onward are assigned per connection by the server's menu.
In its first `info`, the server binds each number to an exact contract
identifier. The mapping applies in both directions.

A contract defines who opens streams, which way data flows, its maximum
message size, and whether it uses framed JSON, raw streams, datagrams, or
raw streams and datagrams. Endpoints implement contract definitions locally.
Contract names do not negotiate behavior or determine whether the server
sends or receives.

An endpoint SHOULD offer and open custom channels only toward peers whose
tier warrants it (section 4). Device configuration, such as selecting a
marked or markerless ball, belongs in a vendor contract, typically on the
vendor application's own connection. Channel 6 carries simulator state.

### 6.1 Contract identifiers

An identifier names an application contract and its version, for example
`com.example.diagnostics-v1`. It is independent of the software release.

Identifiers MUST be 1 to 128 ASCII bytes of lowercase `a`–`z`, digits, `.`,
`-` and `_`, that is `[a-z0-9._-]{1,128}`, measured after JSON string
decoding. Receivers MUST validate this, MUST NOT repair invalid values by
lowercasing or other normalization, and then MUST treat the identifier as
opaque and compare it byte for byte. They MUST NOT parse domain labels or
version suffixes, perform DNS lookups, or infer compatibility or
authorization from the name.

Identifiers SHOULD use a reversed domain controlled by the author and
include a contract version, for example `com.garagelabs.diagnostics-v2`. An
incompatible revision requires a new identifier. There is no separate
version field or version-range matching.

Identifiers beginning `io.common-golf.` are reserved for contracts this
protocol defines; vendors MUST NOT assign them. Standardized vendor
contracts receive a new identifier in this namespace and continue to use
custom channel numbers. The new identifier does not alias the original.

### 6.2 The menu

The server's first `info` carries the menu as a `contracts` array of
`{channel, contract}` entries:

```json
{"type":"info","protocol_version":"0.3.0","contracts":[{"channel":1000,"contract":"io.common-golf.open-connect-v1"},{"channel":1001,"contract":"com.example.auth-v2"},{"channel":1002,"contract":"com.example.auth-v1"}]}
```

- `channel` MUST be an unsigned decimal integer literal from 1000 to
  4294967295. Strings, fractions and exponents (`"1000"`, `1000.0`, `1e3`)
  are invalid, as is any number below 1000.
- Each number appears at most once. An identifier MAY appear on several
  numbers. Array order has no meaning.
- The menu contains at most 16 entries. The `info` message retains its
  65,535-byte limit.
- An absent `contracts` field or an empty array defines an empty menu.

The server chooses the menu according to the authenticated peer's tier and
local policy before sending `info`. The menu is fixed for the connection:
any later server `info` MUST describe the same mappings, regardless of
order. Closing a stream does not release its channel number. Changing the
menu requires reconnection.

A receiver MUST validate the whole menu before installing it. An invalid
menu, or a later `info` that changes it, is malformed `info` (section 3.4,
code 2). No partial menu is installed and an established one is never
replaced. An invalid initial menu leaves custom channels unavailable for the
connection; standard channels are unaffected.

The client matches identifiers against the contracts it supports and uses
any subset of the offered channels in the directions each contract allows.
Selection is local and requires no acknowledgement. In the example above, a
client selecting `com.example.auth-v2` uses channel 1001.

A client MAY list the identifiers it reads in `reads_contracts` in its
own `info`. This list does not assign channel numbers or change the menu.
The server can use it to choose streams or open an offered stream and
accept a decline.

### 6.3 Reliable streams

The stream initiator writes the four-byte channel ID once, then data in the
contract's format. Framed-JSON contracts use section 3.4 framing and allow
one stream per direction. Raw contracts add no envelope or length prefix,
and MAY allow several concurrent streams on one channel. A bidirectional
reply never repeats the prefix. A stream on an unbound channel, or one that
violates its contract, is declined without closing the connection.

A separate raw stream per record supports large or indivisible payloads,
such as camera frames or trajectories. Senders can reset obsolete streams;
receivers can use STOP_SENDING to cancel them. This confines head-of-line
blocking to one record. Cancelling a record MUST use a code other than 1,
which declines the entire channel for the connection.

### 6.4 Datagrams

QUIC DATAGRAM frames ([RFC
9221](https://www.rfc-editor.org/rfc/rfc9221.html))
are unreliable. Most endpoints never need them. A lost packet in QUIC
delays only the streams whose data it carried, and every standard channel
uses a stream. Use a datagram only for a sample that is useless once the
next one exists, such as an aim reading, a high-rate sensor sample or an
LED frame. If unsure, use a stream.

Every endpoint MUST advertise `max_datagram_frame_size` of at least 1,200.
All datagram channels share the connection's DATAGRAM facility. Each
datagram is:

| Offset | Length | Meaning |
|---|---|---|
| 0 | 4 | Channel ID, unsigned, big-endian |
| 4 | 8 | Sequence number, unsigned, big-endian |
| 12 | Remaining | Contract payload, possibly empty |

Sequence numbers start at zero for each channel, direction and connection.
They do not wrap: sends fail after 2^64 − 1. The counter is independent of
`shot_uuid`. Datagrams may be lost, duplicated or reordered and have no
ordering relative to streams. Receivers expose the sequence number for
handling by the contract. Datagrams shorter than the header, or sent on
unbound or unread channels, are discarded.

A datagram occupies one QUIC packet and is never fragmented. Before path MTU
discovery completes, the packet limit is 1,200 bytes. A contract MUST fit
each datagram, including its header, within that packet; approximately 1,100
payload bytes leaves room for overhead. Larger records use raw streams.
Implementations expose the current payload limit and reject oversized sends.
A sender MUST treat this rejection as a contract error, not packet loss.

Datagrams are not flow controlled. Send queues are bounded per connection
and per channel and served round-robin alongside reliable traffic. When a
queue is full, the sender is notified and chooses which data to discard.
Receivers discard datagrams that exceed their capacity.

### 6.5 Open Connect

`io.common-golf.open-connect-v1` carries GSPro Open Connect v1 bodies over
CGP. The server assigns its channel number in the custom-channel menu; no
number is reserved for Open Connect.

| Property | Definition |
|---|---|
| Opened by | Device, regardless of its QUIC role |
| Transport | One bidirectional reliable stream |
| Device to simulator | Open Connect v1 bodies, plus optional `shot_uuid` |
| Simulator to device | Open Connect `{Code, Message, Player}` replies, unchanged |
| Framing | Section 3.4 length-prefixed JSON in both directions, no `type` envelope |
| Maximum message | 65,535 bytes |

Field names, casing, units and `ShotNumber` are as Open Connect defines
them. A device MAY add `shot_uuid`; it MUST then follow section 5's format
and retention rules, and MUST equal the UUID of the same shot on channel 1.
Replies do not echo the UUID. A simulator that accepts the stream MUST send
the Open Connect replies. A simulator acting as the QUIC client can list
the identifier in `reads_contracts`.

A bridge receiving this contract and forwarding its bodies to GSPro on TCP
921 takes the simulator application role on its CGP connection, independently
of its QUIC client/server role. It SHOULD strip `shot_uuid` unless the target
GSPro version is known to accept unknown keys.

Channel 1 and this contract are independent and MAY be used together for the
same shots. CGP defines no preference between them.

Device body, after the channel prefix and length:

```json
{"DeviceID": "Garage LM", "Units": "Yards", "ShotNumber": 12, "shot_uuid": "017f22e2-79b0-7cc3-98c4-dc0c0c07398f", "APIversion": "1", "BallData": {"Speed": 150.5, "SpinAxis": -3.2, "TotalSpin": 2750, "HLA": -1.4, "VLA": 12.1}, "ShotDataOptions": {"ContainsBallData": true, "ContainsClubData": false}}
```

Simulator reply, after its length:

```json
{"Code": 200, "Message": "Shot received"}
```

The bodies are described by
[`schema/open_connect.json`](../schema/open_connect.json).

## 7. Units, frames and signs

Measurement fields spell out units in their names: `_meters`,
`_meters_per_second`, `_degrees`, `_revolutions_per_minute`, `_millimeters`,
`_seconds`, `_nanoseconds`, `_percent`. There are no unit flags and no
abbreviations.

All lateral quantities share one frame, independent of the player: the
target line viewed from behind the ball, positive right of target. This
covers horizontal launch angle, spin axis, sidespin, carry side, total side,
club path, face angle, swing direction, low point side and D-plane tilt. The
spin axis sign is the curve direction even when total spin is negative
(section 5.1).

`player_dexterity` lets a consumer translate into the player's terms: for a
right-handed player, positive club path is in-to-out and positive face angle
is open; for a left-handed player each is the opposite. When the device
cannot tell, it omits the field. The wire format uses the same values for
both handednesses.

`measured_at` is a UTC RFC 3339 timestamp, with fractional seconds when
available. Omit it when the UTC time is unknown; never substitute uptime or
the UUID's timestamp.

A device that sends an unmeasured or reduced-accuracy value for a shot MUST
name that field in `estimated` on channels 1 and 3. A sender MUST name only
fields present in the message; receivers ignore other names. Consumers
SHOULD distinguish estimated values in their displays. Exact calculations
from measured fields are derived values and do not require an estimate
marker.

## 8. Protocol mappings

These mappings are informative guidance for adapters.

### 8.1 GSPro Open Connect v1

The contract in section 6.5 carries Open Connect bodies directly. Adapters
can instead map them to standard channels using the conversions below. For
`Units: "Yards"`, source units are mph, rpm, degrees and yards.

| Open Connect | Common Golf | Factor |
|---|---|---|
| `shot_uuid` (when present) | `shot_uuid` | Preserve |
| `ShotNumber` (when `shot_uuid` is absent) | `shot_uuid` | Allocate a UUIDv7 per source shot and keep the mapping for related messages and updates |
| `BallData.Speed` | `ball_speed_meters_per_second` | × 0.44704 |
| `BallData.VLA` | `vertical_launch_angle_degrees` | |
| `BallData.HLA` | `horizontal_launch_angle_degrees` | |
| `BallData.TotalSpin` | `total_spin_revolutions_per_minute` | Negative topspin carries over |
| `BallData.SpinAxis` | `spin_axis_degrees` | |
| `BallData.BackSpin`, `SideSpin` | `backspin_…`, `sidespin_…` | |
| `BallData.CarryDistance` | `flight.carry_distance_meters` | × 0.9144 |
| `ClubData.Speed` | `club_speed_meters_per_second` | × 0.44704 |
| `ClubData.AngleOfAttack` | `angle_of_attack_degrees` | |
| `ClubData.Path` | `club_path_degrees` | |
| `ClubData.FaceToTarget` | `face_angle_degrees` | |
| `ClubData.Lie`, `Loft` | `lie_degrees`, `dynamic_loft_degrees` | |
| `ClubData.SpeedAtImpact` | `speed_at_impact_meters_per_second` | × 0.44704 |
| `ClubData.HorizontalFaceImpact`, `VerticalFaceImpact` | `face_impact_horizontal_millimeters`, `…_vertical_…` | Unit undocumented; assume mm |
| `ClubData.ClosureRate` | `closure_rate_degrees_per_second` | |
| `ShotDataOptions.LaunchMonitorIsReady` | `device_status.ready` | |
| `ShotDataOptions.LaunchMonitorBallDetected` | `device_status.ball_detected` | |
| Reply `Code 200` | `result.accepted: true` | |
| Reply `Code 201` + `Player` | `simulator_state.player` | `RH`/`LH` to `right`/`left`; no club or capture-mode mapping |
| Reply `Player.DistanceToTarget` | `simulator_state.distance_to_target_meters` | × 0.9144 when the unit is known to be yards; the reply has no unit label |

### 8.2 OpenGolfSim

| OpenGolfSim | Common Golf |
|---|---|
| `shot.shot.ballSpeed` | `ball_speed_meters_per_second` (× 0.44704 when `unit` is imperial) |
| `shot.shot.verticalLaunchAngle`, `horizontalLaunchAngle` | Launch angles |
| `shot.shot.spinSpeed` | `total_spin_revolutions_per_minute` |
| `shot.shot.spinAxis` | `spin_axis_degrees`, **negated** (OpenGolfSim positive curves left) |
| `device.status` | `device_status.ready` |
| `player.data.club.{id,name}` | No mapping |
| `result.data.result.{carry,total,lateral,height}` | `result.carry_distance_meters`, `total_distance_meters`, `carry_side_meters`, `max_height_meters` |
| `control.command` `left right up down club-up club-down drop mulligan rehit` | `control.command` with hyphens as underscores; `event` passes through |

### 8.3 OpenLaunch Nova

Nova's WebSocket feed uses SI units and snake_case field names.
`ball_speed_meters_per_second`, `vertical_launch_angle_degrees`,
`horizontal_launch_angle_degrees` and `spin_axis_degrees` map directly.
Rename `total_spin_rpm` to `total_spin_revolutions_per_minute`, preserving
the topspin sign. Convert `timestamp_ns` to `measured_at` only when its
epoch is known to be convertible to UTC. Assign each source shot a UUIDv7.

Map `status.uptime_seconds` to `device_status` and `status.firmware_version`
to `info`. Nova's TCP API uses Open Connect (section 6.5). Touch bar
controls map as follows: Left to `left`, Right to `right`, A to `reset_aim`,
and Y to `heatmap`.

### 8.4 OpenGolfCoach

OpenGolfCoach consumes channel 1 fields directly except for
`total_spin_revolutions_per_minute`, which it names `total_spin_rpm`. Its
`offline_distance_meters` maps to `carry_side_meters` with the same sign.
Translate handed output variants using the coordinate system in section 7
and `player_dexterity`.

### 8.5 Recording to MCAP

Sessions can be recorded in [MCAP](https://mcap.dev/spec). The following
convention stores JSON channels without their transport framing:

| MCAP | Value |
|---|---|
| Schema | `encoding: "jsonschema"`, from the schema set for the connection's ALPN; `schema_id: 0` when none is available |
| Channel | A recorder-chosen `topic` such as `<connection>/<sender>/shot`; `message_encoding: "json"`; metadata such as `{"channel_id": "1", "alpn": "common-golf/draft-3"}` |
| Message data | The JSON payload, without the CGP channel prefix and length |
| Message times | `log_time`: receipt time in nanoseconds; `publish_time`: the same when unknown |
| Message sequence | A per-channel recorder counter, or zero; unrelated to `shot_uuid` |

Including connection and sender in the topic separates both directions of
`info` and Open Connect traffic. MCAP channel IDs are file-local. The
payload's `measured_at` is left unchanged. Non-JSON custom channels need
their own recording encoding. See the [MCAP encoding
registry](https://mcap.dev/spec/registry).

### 8.6 OpenGolfAPI / OpenShot

[OpenShot v0](https://opengolfapi.org/schemas/openshot/v0.json) describes a
shot with ball measurements and optional club, device and context fields,
using mph, degrees, rpm and yards. Adapters map fields and convert units.
The schema requires `api_version: "0"` and `ball`; an Open Connect body
requires conversion before it can be used as OpenShot.

### 8.7 Pureplay Connect v1

[Pureplay Connect](https://github.com/PureplayGolf/Pureplay-Connect#readme)
is fixed-unit binary telemetry. `ValidMask` distinguishes measurements from
unused fields; a cleared field's zero is not a measurement.

| Pureplay Connect | Common Golf |
|---|---|
| `ShotId` | Allocate a UUIDv7 per source shot; keep the session/`ShotId` mapping across connections, related messages and updates |
| `BallSpeedMS`, `VLA`, `HLA` | `ball_speed_meters_per_second`, `vertical_launch_angle_degrees`, `horizontal_launch_angle_degrees` |
| `TotalSpinRPM`, `SpinAxisDeg` | Total and axis, after checking the source sign convention |
| `BackSpinRPM`, `SideSpinRPM` without total and axis | Convert with section 5.1, preserving topspin sign |
| `ClubHeadSpeedMS`, `SmashFactor` | `club_speed_meters_per_second`, `smash_factor` |
| `DynamicLoftDeg`, `FaceAngleDeg`, `ClubPathDeg`, `AttackAngleDeg` | `dynamic_loft_degrees`, `face_angle_degrees`, `club_path_degrees`, `angle_of_attack_degrees` |
| `ImpactPointXMM`, `ImpactPointYMM` | `face_impact_horizontal_millimeters`, `face_impact_vertical_millimeters`, same signs |
| `ReadyState` | `NotReady`, `Ready`, `BallDetected`, `Processing`; mapping to `device_status.ready` and `ball_detected` is adapter policy |
| `Heartbeat.Battery` | `device_status.battery_percent`; unknown (`0xFF`) is omitted, or `null` to clear |
| `Hello.DeviceName`, `Firmware` | `info.name`, `info.firmware_version` |
| `SetClubInfo` | Handedness to `simulator_state.player.dexterity`; no club or capture-mode mapping |
| `SetDetect` | An arm/disarm command; `simulator_state.ready` is simulator readiness, so mapping is adapter policy |
| `SetBallType` | Vendor configuration (section 6) |
| `Error.Message` | `device_status.message` |

Use the conversion in section 5.1 for component spin values. Pureplay's
gap-fill formula can lose the topspin sign or produce an axis outside CGP's
range. Verify the source device's sign convention before copying total spin
and axis directly; Pureplay's documentation is ambiguous about negative
total spin and the relationship between sidespin and axis.

## 9. Scope

This specification does not define:

- Standard telemetry, media or trajectory contracts. Custom streams and
  datagrams can carry these payloads.
- Per-message signatures.
- Cloud or remote device discovery.
- Standard device-configuration commands.
- Multiplayer, tournaments or scoring.
- Non-JSON encodings on standard channels.
- USB or Bluetooth transports.

Local discovery and the connection interface are defined in section 3.2.
