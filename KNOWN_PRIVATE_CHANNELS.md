# Known private channels and placeholder examples

Channels 1000 and up belong to the two ends of a connection. Nothing here is
part of the protocol. This list lets vendors document private arrangements
so others can adopt them deliberately and proposals can identify existing
users.

## Placeholder examples

These rows illustrate documentation, not deployed implementations. The
names, owners, payloads, and specification names are fictional placeholders.
They are not registered channels or features that implementations are
expected to support.

| Name | Owner (placeholder) | What it carries (placeholder) | Specification (placeholder) |
|---|---|---|---|
| `com.example.foo` | Foo | Foo messages | Foo specification |
| `com.example.bar` | Bar | Bar messages | Bar specification |

## Adding a real arrangement

Open a pull request with the name, owner, payload description, and a link to
its specification or contact. Include who uses it and the framing and units
needed to interoperate.

In draft 2, the endpoints arrange the channel number and meaning before
using it. The `name` identifies that arrangement in the recommended opening
`{"type": "private", ...}` message described in
[section 6 of the RFC](rfc/0001-common-golf-protocol.md#6-private-channels-1000-and-up).
Channel numbers are scoped to a connection, so a documented number is an
example rather than a global assignment. This page is not a registry or a
negotiation mechanism.
