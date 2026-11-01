# Message schemas

This directory contains JSON Schemas for the messages defined in the
[protocol specification](../rfc/0001-common-golf-protocol.md).

| Directory | Protocol versions | ALPN |
|---|---|---|
| Top-level files | Draft 3, in development | `common-golf/draft-3` |
| `draft-1/` | 0.1.0 and 0.1.1, which share the same schemas | `common-golf/draft-1` |
| `draft-2/` | 0.2.0 | `common-golf/draft-2` |

Select the schema set using the negotiated ALPN. The `protocol_version`
field does not select the wire format. Published draft schemas are frozen.

## Validation scope

The schemas validate message structure and field values. Implementation
tests cover stream direction, channel ownership, framing, and identity.
The shared [schema checker](../tests/compatibility/schema_check.py) also
validates cross-field rules, including references in `estimated` and the
requirement for a zero spin axis when total spin is zero.

For draft 3:

- Shot identifiers use the canonical UUIDv7 spelling. Length and pattern
  constraints enforce this without a JSON Schema format checker.
- `device_status` (channel 4) and `simulator_state` (channel 6) contain
  optional state fields and are applied as patches.
- `open_connect.json` describes messages for the
  `io.common-golf.open-connect-v1` custom contract, including the optional
  `shot_uuid` on requests.
- `info.json` validates contract menu entries, identifier syntax, and bounds.
  Runtime checks enforce unique channel numbers, integer notation, endpoint
  roles, and menu immutability.
