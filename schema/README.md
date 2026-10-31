# Schemas

The top-level JSON files are the working schemas for draft 2, matching the
[RFC](../rfc/0001-common-golf-protocol.md). Frozen copies:

- `draft-1/`: 0.1.0 and 0.1.1 (identical schemas), ALPN `common-golf/draft-1`.
  Uses `shot.reduced_accuracy`.
- `draft-2/`: 0.2.0, ALPN `common-golf/draft-2`. Uses `shot.estimated` and
  `club.estimated`.

Pick a schema set by the negotiated ALPN, not by `protocol_version`.

`tests/compatibility/schema_check.py` validates messages against the frozen
copies, so editing the working schemas never changes checks for a published
draft. It also checks rules JSON Schema can't express, such as `estimated`
naming fields that are present and numeric.

Schemas cover message shape only. Stream direction, channel ownership, framing,
and identity are covered by the implementation tests.
