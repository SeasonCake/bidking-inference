# Verification of this source export

Local checks used Python 3.13 on Windows. These results refer to the public source
candidate and fictional/local inputs, not a complete running-product security audit.

| Check | Actual result |
|---|---|
| Product inference demo | Completed; precision compatibility and one two-item support result; no network/game input |
| HTTP/public defaults plus existing security/dashboard checks | 124 passed |
| Portable product inference, capture parsing, ingest routing and upload privacy selection | 66 passed |
| Portable selection plus endpoint fallback fixtures | 92 passed (includes the 66 above) |
| Transport/config/payment fixtures and two monitor-identity cases | 63 passed |
| Python source syntax | All initially exported 1,061 Python files parsed; new support files are checked by the export verifier |
| Existing public baseline | 220 passed, 1 skipped; preserved legacy/data identities |
| Extended root verifier | 228 passed, 1 skipped, including eight export-checker controls |
| Full native build, live GUI/game, official encrypted ZIP reproduction | Not run |
| Full historical product test suite | Not run; external inputs are required |

Commands from product/:

```sh
python scripts/open_source_demo.py
python -m pytest -q tests/test_public_collector_boundaries.py tests/test_security_hardening_0701.py tests/test_private_dashboard_access.py
python -m pytest -q tests/test_public_offline_inference.py tests/test_posterior.py tests/test_live_capture.py tests/test_player_warehouse_ingest_routing.py tests/test_sample_upload_privacy.py
```

The HTTP tests cover invalid/negative/duplicate lengths before a body read, upper limits,
exact bounded reads, zero-body behavior, local defaults, empty/whitespace key rejection
for external binding and independent administrator/export checks. The new startup
positive cases substitute the server object; they do not prove real IPv6 binding.

Two old cache test doubles initially rejected the current identity_store keyword.
They were adapted without changing their cache assertions, and the 124-check run passed.

Upload-privacy tests isolate their configuration and status output under their temporary
application tree. Generated runtime logs are not source-manifest members. Endpoint and
account fixtures use documentation addresses and fictional identifiers.
The HTTP-envelope tests simulate the documentation IP's global-address classification
and fix their release-version metadata; socket tests use only loopback. This does not
validate a real public address. Config-helper subprocesses explicitly use UTF-8.

The endpoint fallback run initially reported 23 failures because the deployment-only
endpoint file was absent. A separately written `.invalid`-domain configuration now
supplies the real schema, without changing the loader or including production input;
all 92 selected cases subsequently passed. These are fixture/input adaptations, not
changes to production authentication or inference behavior.

An earlier exploratory run including test_observation.py reported 102 passed and six
failures in game-loadout/item-database-dependent cases. This export excludes that game
catalogue; the offline command uses dedicated synthetic inference controls instead.
Those six checks are not relabeled as passed. Supply their documented real inputs
before treating the full historical suite as representative.

No production penetration testing, customer transaction, licence redemption, capture,
game execution, general native/GUI validation or overall security certification is claimed.
Deployment of source by an operator is a separate step.
