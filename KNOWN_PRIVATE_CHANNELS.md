# Known private channels

Channels 1000 and up belong to the two ends of a connection. Nothing here is
part of the protocol. This list exists so that a private channel one vendor
built can be adopted by another on purpose instead of by accident, and so a
proposal to standardize one can point at who already uses it.

To add a row, open a pull request with the name, owner, what the channel
carries, and a link to its specification or a contact. In drafts 1/2, the
`name` is what the opener sends in its first `{"type": "private", ...}`
message. In the draft 3 proposal it is the contract identifier in the
listener's first `info.contracts` menu (see
[section 6 of the RFC](rfc/0001-common-golf-protocol.md#6-custom-channels-1000-and-up)).
Channel numbers are assigned per connection, so none are listed.

The rows below are placeholders showing the format.

| Name | Owner | What it carries | Spec or contact |
|---|---|---|---|
| `com.example.foo` | Foo | Foo messages | Foo specification |
| `com.example.bar` | Bar | Bar messages | Bar specification |
