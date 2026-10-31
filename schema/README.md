# Schema versions

The JSON files in this directory are the working draft 2 schemas, matching the
[published RFC](../rfc/0001-common-golf-protocol.md).

- `draft-1/` preserves the schemas from `b3eaf37` (0.1.1). Versions 0.1.0 and
  0.1.1 have identical schemas and share the `common-golf/draft-1` ALPN.
- `draft-2/` preserves the schemas from `1c343a8` (0.2.0), using
  `common-golf/draft-2`. The working schemas match this snapshot.

Select schemas by the negotiated ALPN, never by the informational
`protocol_version` field. Draft 1 uses `shot.reduced_accuracy`; draft 2 uses
`shot.estimated` and `club.estimated`. Both drafts use numeric `shot_number`
identifiers, `status` on channel 4, `state` on channel 6, and Open Connect on
channel 2. Receivers ignore unknown fields.

`tests/compatibility/schema_check.py` validates emitted messages against the
explicit snapshots, so edits to working schemas cannot change checks for a
published draft. It also checks sender rules that JSON Schema alone does not
express, such as `estimated` naming present numeric fields.

Schemas check message shapes in tests and CI. Stream direction, ownership,
duplicate channels, framing, identity, and sender-only rules require the
implementation tests too.
