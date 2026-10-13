# BidKing product source

This tree opens the author-owned product implementation behind the 0.3.6 line:
inference, capture adapters, desktop UI, activation and renewal services, data readers,
native source, build tools and tests. It is a sanitized source snapshot, not the private
repository's history or a copy of the hosted service.

The repository-root `auction_inference` package remains its independent v0.1.0 API.
This product checkout keeps its original Python packaging metadata; that metadata,
the source snapshot and the downloadable Windows application's version are separate.

## Try an offline example

From this directory, with Python 3.10+ (the local verification used Python 3.13):

```sh
python -m pip install -e ".[dev]"
python scripts/open_source_demo.py
```

The example uses the actual product inference modules with two fictional item types.
It checks decimal-display compatibility and count/value support. It uses no game,
activation key, captured traffic, account or network service.

For the independent standard-library-only toolkit, use the [repository quick start](../README.en.md#quick-start).

## Source map

| Component | Location | What is included |
|---|---|---|
| Inference and data interfaces | `src/bidking_lab/inference`, `data`, `simulation`, `extract` | Constraints, posterior/quality models, mean support, simulation, local table parsing |
| Live capture and state | `src/bidking_lab/live`, `capture`, `runtime` | Packet/OCR adapters, state, upload policy and process lifecycle |
| Desktop surfaces | `hero_ref`, `app`, `apps` | Reference engine, Tk overlay, analytical interface and launch scripts |
| Client authority | `src/bidking_lab/secure` | Activation, transport and local runtime contracts |
| Services | `scripts/activation_*`, `membership_*`, `sample_collector_server.py`, related readers | Authentication, renewal, indexing, records, dashboards and collector |
| Native source | `native/bidking_kernel`, `native/bidking_trust_bootstrap` | Rust/PyO3 and bootstrap source plus lockfiles |
| Build and maintenance | `scripts` | Packaging, input contracts and operator tools; read required arguments before use |
| Tests | `tests` | Original test source plus public-export regression controls |

## Run a collector you own

See [SELF_HOSTING.md](SELF_HOSTING.md). The public default binds to `127.0.0.1`.
Explicit non-loopback binding requires a nonempty ingest key. A shared upload key
does not grant dashboard, export, activation-administration or hosted-service access.
Production endpoints and personal contact/profile values are replaced with examples;
configure your own endpoints and keys instead of expecting the hosted BidKing system.

## Full application and build inputs

The complete source is useful without granting redistribution rights to the game's
assets or packaging every local development input. Supply your own legally obtained
game tables, model/calibration inputs, UI/pet assets, runtime dependencies and toolchain
as required by the selected feature. The existing `apps/hero_ref/Import-LocalTables.ps1`
and extraction scripts describe local table import. Imports and manifests are code and
metadata, not bundled current game data.

The Windows path uses Tk, optional packet/OCR dependencies, and the selected release
builder under `scripts`. The native crates use Rust edition 2024; the PyO3 extension
must match your Python ABI and Windows toolchain. Runtime/vendor binaries, generated
`target`/stage directories, signed manifests, signing keys and encrypted release inputs
are not included. A maintainer building the encrypted profile must generate their own
keys and use their own data and endpoints. Do not reuse production credentials or copy
an installed customer's configuration into a public build.

Source availability is not a promise that this checkout reproduces the official
Windows ZIP byte for byte. [Source coverage](SOURCE_COVERAGE.md) and
[third-party boundaries](THIRD_PARTY.md) list what remains external.

## Verification

From this directory:

```sh
python -m pytest -q tests/test_public_collector_boundaries.py tests/test_security_hardening_0701.py tests/test_private_dashboard_access.py
python -m pytest -q tests/test_public_offline_inference.py tests/test_posterior.py tests/test_live_capture.py tests/test_player_warehouse_ingest_routing.py tests/test_sample_upload_privacy.py
```

These are bounded local checks. The complete historical test directory contains tests
that require excluded data, Windows APIs or generated release inputs; a blanket pytest
run is not a portable smoke test. Missing inputs are not passing tests. No tests should
target the author's hosted services. Consult [the recorded results](VERIFICATION.md)
for actual coverage, not an implied whole-product certification.

## Contributing

Keep mathematical behavior and source-relative imports intact. Add fictional fixtures
for changes, preserve numerical/identity boundaries, and separate a source fix from
deployment. The root MIT license covers author-owned source; external content retains
its own rights. Operational examples are for systems you own or are authorized to run.
