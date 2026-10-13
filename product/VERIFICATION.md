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
| Native/GUI/live ZIP at initial export | Not run at initial export; see supplemental ordinary verification below |
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

An earlier exploratory run including `test_observation.py` reported 102 passed and
six failures. The supplemental check reproduced all six failures; their distinct
categories are recorded below rather than classifying all six as missing-input cases.

## Supplemental ordinary verification

Supplemental ordinary verification is pinned to public commit
`436374066acfeffbbe013bcc1d6a34c48026a812`. The isolated copy's 1,141 product files
matched that export before and after the checks; no source or test changes were made.

| Check | Result |
|---|---|
| Modern Python product wheel | `bidking_lab-1.0.0-py3-none-any.whl` built offline; 3 bounded public inference tests passed when importing directly from the wheel. This covers the Python package only. |
| Standalone native kernel | Offline build attempted; no native wheel produced. `link.exe` failed with `0xc0000142`; subsequent initialization of the existing MSVC environment exited with code 1 before another build started. Root cause remains unresolved. |
| Full Windows GUI/game acceptance and official encrypted live ZIP | Not run. All 21 files listed in `scripts/package_data_manifest.json` are absent from the isolated public-source checkout, along with the bootstrap icon and other required assets, runtime and release inputs. |

The six previously reported `test_observation.py` failures fall into two categories:
one static mode-set assertion differs because `expected_modes` omits `indiana` while
`STANDARD_LOADOUTS` includes it. The intended product support scope remains unconfirmed;
the assertion does not depend on an external game table. The other five tests require
the absent `data/raw/tables/Item.txt`; their database-backed regression remains
unverified. These results do not establish six algorithm bugs or full product correctness.
None of the six failures is relabeled as passed.

The locally built modern Python wheel is 648,711 bytes, with SHA-256
`b5c67f875dc9d34d4f9715f8e25b8fd180c171db682d56e10297b6f8861897b6`.
It is distinct from the existing `auction_inference` v0.1.0 toolkit wheel; its package
version is not a new Windows product release. These limited checks do not constitute
native/GUI/live ZIP acceptance or completion of the independent security review.

No production penetration testing, customer transaction, licence redemption, capture,
game execution, general native/GUI validation or overall security certification is claimed.
Deployment of source by an operator is a separate step.
