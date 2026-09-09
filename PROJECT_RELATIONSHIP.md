# Project relationship

`bidking-inference` is a public mathematical companion to the private BidKing calculator
project and its main public code/research entry. Its maintained package and synthetic fixtures were newly written for public
use. A separate `legacy/` directory preserves a reviewed subset of real early
`v0.2.0-hotfix1` code and pre-0.2.8 tables; it is historical reference material, not a
mirror of the current private product.

The separate `research/` layer adds explicitly selected historical helper adaptations,
new synthetic teaching models, and historical aggregate derivatives. Selected sources
include v0.3.0-era auxiliary code and a later Match10 scheduling experiment, not the
whole newer engine. See [research](docs/research/README.md) and its provenance manifest.

Reusable engineering workflows learned while building and maintaining BidKing are
published separately in
[`evidence-first-agent-skills`](https://github.com/SeasonCake/evidence-first-agent-skills).
That companion covers browser-edit recovery, intent checkpoints, evidence levels,
verification, CLI contracts, architecture surveys and fresh-agent compatibility.

The Windows product 0.3.5 is released, as described in the dated
[development snapshot](docs/DEVELOPMENT_STATUS.md). It does not change the public
package's separate v0.1.0 line. Research-source checks are not product release evidence.

Neither repository mirrors current private source history or contains production topology,
customer diagnostics, credentials, captured traffic, or third-party binary/game assets.
The code repository's explicitly labeled legacy tables contain old game-derived factual
metadata under the boundary and notice documented there.
