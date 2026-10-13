# Self-hosting

Start with the offline example in [README](README.md). For local collector exploration,
from this checkout:

```sh
PYTHONPATH=src python scripts/sample_collector_server.py --host 127.0.0.1 --port 18900 --out data/local/samples.jsonl
```

On PowerShell set `$env:PYTHONPATH="src"` before the Python command. Without credentials,
only the local development collection surface is available; activation remains
unconfigured and export remains closed. Use fictional local samples for initial tests.

For client-side development, the checked-in `activation_service_endpoints.json` is a
fictional schema example: both origins use the reserved `.invalid` domain. It carries
no operator credentials or production addresses. Configure your own HTTPS origins and
same-origin telemetry paths in your private build/deployment copy before enabling the
client's online features. The normal loader still rejects missing or malformed input;
the offline inference example needs no endpoint service.

For a service others can reach:

- Set an explicit bind address and a fresh ingest key. Empty and whitespace-only keys
  do not permit non-loopback binding. Generate keys using a cryptographically secure
  generator; do not reuse constants from tests or examples.
- Put the service behind an HTTPS reverse proxy with request-size, connection and
  timeout limits. Limit direct backend exposure using your own network controls.
- Use separate activation/master, dashboard and export credentials. The shared ingest
  key is compatibility admission, not a trusted user identity or administrator role.
- The signed-upload/lease implementation is included. Enabling it needs explicit
  authority-epoch and identity-store configuration. Source presence does not mean a
  remote hosted service has enabled that policy.
- Use a dedicated low-privilege service account and your own private state directory.
  Provision databases using the included initialization commands for the selected
  component. Keep keys, membership records, device bindings and collected records out
  of the checkout and its Git history.
- Read `--help` for the selected service and pass your own file paths. Operator and
  packaging scripts may restart or replace your configured deployment; they are not
  quick-start commands for someone else's infrastructure.

For browser access, the dashboard and personal-record views retain their own access
checks. Export requires its separate key, and missing activation authority is a service
configuration error, not a reason to bypass checks. The historical `pay_service.py`
adapter is reference code; its old price table is not a current store or a connected
payment provider. Configure and verify your own payment integration before real use.

This source license gives no access to the BidKing operator's servers, member benefits,
tokens, customer records or databases. Do not test against those hosted endpoints.
