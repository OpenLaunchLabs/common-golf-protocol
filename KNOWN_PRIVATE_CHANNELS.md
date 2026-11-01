# Known private channels

Channels 1000 and above carry custom contracts agreed between the endpoints.
This directory allows vendors to share those contracts and records adoption
for future standardization proposals. Channel numbers are assigned per
connection and are not registered here.

No vendor contracts are currently listed.

## Register a contract

Open a pull request with the following information:

- Contract identifier.
- Owner or maintainer.
- Description of the data exchanged.
- Link to a specification or a contact.

In draft 3, the identifier is the `contract` value in the server's
`info.contracts` menu. See [custom channels in the RFC](rfc/0001-common-golf-protocol.md#6-custom-channels-1000-and-up).
In drafts 1 and 2, it is the `name` in the stream initiator's first
`{"type": "private", ...}` message.
