# Contributing

Common Golf Protocol prioritizes a small, interoperable core. Extensions
should prove useful in deployed implementations before becoming standard.

## Where to contribute

| Contribution | Location |
|---|---|
| Questions, discussion, and demonstrations | Discord |
| Problems and proposed changes | GitHub issue |
| Typo corrections, schema fixes, and examples | Pull request |
| Specification changes | Proposal issue, followed by a pull request after acceptance |

Record decisions from Discord in a GitHub issue before acting on them.

## Requirements for changes

New standard channels in the range 9–999 must first ship on a private
channel (1000 or above), with at least two independent parties using them
consistently. Private channels require no approval.

Changes are evaluated by their impact on existing implementations:

| Change | Impact | Requirements |
|---|---|---|
| Optional field on an existing channel | Receivers ignore unknown fields | Two implementers; units included in the field name |
| Optional channel | Receivers can decline the channel | Deployed privately first; two independent adopters |
| Required field or new MUST requirement | Every implementation must change | A draft boundary and a written explanation of why the requirement cannot be optional |
| Change to an existing meaning, sign, or unit | Breaks deployed implementations | Use a new field or channel instead |

Custom contracts can remain private indefinitely. List them in
[KNOWN_PRIVATE_CHANNELS.md](KNOWN_PRIVATE_CHANNELS.md) so other vendors can
adopt them.

## Proposals

Open an issue using the proposal template and include:

1. A brief description of the problem.
2. Why existing channels or fields cannot address it.
3. The private implementations and channel numbers already in use.
4. Exact field names with units, plus an example message.
5. The behavior when a receiver ignores the extension.

Maintainers record one of three outcomes: acceptance for the next draft,
continued private use with a directory entry, or rejection with a reference
to the unmet requirement.

## Drafts and compatibility

Specification changes are released at draft boundaries, roughly every six
to eight weeks. Proposals submitted between releases are considered for
the next draft.

A change that removes or renames a field or channel, or changes its meaning,
requires a new ALPN string, such as `common-golf/draft-2`. This makes
incompatible endpoints fail during the handshake. Adding an optional field
or channel does not require a new ALPN string.

Each ALPN string must have an entry in section 3.8 of the
[RFC](rfc/0001-common-golf-protocol.md#38-versions) describing the reason
for the change and the issues it resolves.

## Pull requests

Pull requests may correct typos, fix schemas or examples, add examples, or
implement an accepted proposal. Specification changes without an accepted
proposal issue will be closed and referred to the proposal process.

Every pull request must keep `examples/sim.py` passing against every device
script.

## Authorship

Issues, pull requests, review comments, and Discord messages must be written
by a person. Language-model-generated, summarized, or expanded text is not
accepted. Spell checkers and grammar tools are permitted. Agents may run
examples against devices, but their transcripts must not be submitted as
comments.

Maintainers close submissions they judge to be generated without further
discussion. Repeated violations result in loss of access.

## Maintainers

Named maintainers decide which changes to accept and merge; decisions are
not made by vote. Rejections must cite a rule in this document. If no rule
applies, maintainers update these guidelines before deciding.
