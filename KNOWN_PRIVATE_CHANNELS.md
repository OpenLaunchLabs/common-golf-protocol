# Known private channels

Channels 1000 and up belong to the two ends of a connection. Nothing here is
part of the protocol. This list exists so that a private channel one vendor
built can be adopted by another on purpose instead of by accident, and so a
proposal to standardize one can point at who already uses it.

To add a row, open a pull request. The `name` is what the opener sends in
its first `{"type": "private", ...}` message. Channel numbers are per
connection and are listed only as the number the owner happens to use.

| Name | Owner | Usual channel | What it carries | Spec or contact |
|---|---|---|---|---|
| `com.example.diagnostics` | the examples in this repo | 1000 | Radar temperature and uptime, for illustration | `examples/device_full.py` |
