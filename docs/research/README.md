# Research: from a surprising estimate to data and runtime contracts

This source-checkout research layer connects real historical helpers, runnable synthetic
examples, historical aggregate charts, and unfinished engineering questions.
It does not change the maintained `auction_inference` v0.1.0 API.

Start with the [Chinese research guide](README.zh-CN.md). The code and command-line
examples can be used independently of the game:

```text
python -m research.aisha
python -m research.aisha.penalty_example
python -m research.transport
python -X utf8 -m research.text
python -m research.tables --help
python -m research.historical_data.generate_charts --csv-only
python scripts/verify.py
```

- [Aisha case study](AISHA_CASE_STUDY.zh-CN.md): inverted candidate windows, synthetic
  visibility, replay-field parity, and competing count/value metrics.
- [Historical atlas](HISTORICAL_DATA.zh-CN.md): aggregate quality weights, session
  coverage, catalog counts, and reproducible CSV/PNG generation.
- [Python tools](PYTHON_TOOLS.zh-CN.md): historical TCP reassembly, OCR normalization,
  decoding helpers, and a new bounded synthetic table-diff workflow.
- [Match10 lessons](MATCH10_LESSONS.zh-CN.md): real C# scheduling/trace helpers and
  standalone harnesses; local callback success is not native game integration.
- [Capture architecture](CAPTURE_ARCHITECTURE.zh-CN.md) and
  [runtime identity](RUNTIME_IDENTITY.zh-CN.md): substantive experiment specifications,
  not completed C++ adapters or published performance results.
- [Table change study](TABLE_CHANGE_STUDY.zh-CN.md): byte identity, structure, semantics,
  and consumer behavior are different claims.
- [Contribution ideas](CONTRIBUTION_IDEAS.zh-CN.md): bounded starting points.
- [Batch boundaries and roadmap](ROADMAP.zh-CN.md): what is included now, and what remains
  future work after more implementation evidence is available. The new nonlinear penalty
  example exposes fictional parameters and intermediate values, not a production model.

The [provenance manifest](PROVENANCE.json) separates adapted historical source, newly
written teaching code and approved historical summaries. Charts are not current loot
probability claims. The original 56 source files and seven data files remain frozen.
