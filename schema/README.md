# Schemas

The top-level files track the unreleased draft-3 discussion in the
[RFC](../rfc/0001-common-golf-protocol.md). They are not published draft-2
schemas. Frozen copies remain unchanged:

- `draft-1/`: 0.1.0 and 0.1.1 (identical), ALPN `common-golf/draft-1`. Uses
  `shot.reduced_accuracy`.
- `draft-2/`: 0.2.0, ALPN `common-golf/draft-2`. Uses `shot.estimated` and
  `club.estimated`.

Choose the schema set by the negotiated ALPN, not `protocol_version`.

`tests/compatibility/schema_check.py` validates against the frozen copies, so
editing the working schemas doesn't change the checks for a published draft.
It also checks rules JSON Schema can't express, such as `estimated` naming
present numeric fields, and a zero total spin having a zero spin axis.

Schemas cover message shape only. Stream direction, channel ownership,
framing, and identity are covered by the implementation tests.

The working `shot`, `club`, `result`, and `flight` schemas use canonical
UUIDv7 `shot_uuid` identities. Explicit length and pattern constraints enforce
the UUID spelling and version even without a format checker. `measured_at`
is a separate optional UTC RFC3339 measurement time; it is not inferred from
the UUID. Whole snapshots replace earlier snapshots for the same UUID and
channel when a receiver accepts updates. There is no `final` field.

`open_connect.json` now describes the custom contract
`io.common-golf.open-connect-v1`, not channel 2. Its optional `shot_uuid`
extends request bodies only; replies remain unchanged. The listener's first
`info.contracts` fixes custom numbers for the connection. Schema validation
covers entry shape, identifier syntax, and bounds; runtime validation also
checks unique channel numbers, decimal-integer wire notation, endpoint role,
and menu immutability. `reads` and `reads_contracts` are optional hints.
