# Qualify observations before computing a posterior

[简体中文](EVIDENCE_LIFECYCLE.zh-CN.md) · [Runnable example](../examples/evidence_lifecycle.py)

Distilled from maintaining BidKing `0.3.4-hotfix1`, this example connects observation
completeness, revision identity, and posterior computation. A synthetic six-item
population shows why seeing the same two items can support different conclusions.

## The same number can carry different evidence

An invented six-item population has three candidates: one, two, or three blue items,
with priors 1/4, 1/2, and 1/4.

| Observation | Constraint before the first inference | Result |
| --- | --- | --- |
| Complete count: two blue items | exact(2) | Only the two-item candidate survives |
| Partial inspection: two blue items seen | interval(2, 6), not exact(2) | Two/three items have probabilities 2/3 and 1/3 |
| Count unavailable | No constraint for that field; do not substitute zero | All three priors remain |
| Complete count: zero | exact(0), contradicting every toy candidate | Explicit error, not an ordinary empty posterior or reused result |

Partial counts here are duplicate-free and error-free observations, so they establish
a lower bound. Noisy measurements need a different contract. Six is the stated toy
capacity, not a calibrated threshold.

## Shape is not identity

Each observation carries a session and revision. The example checks these first:

- A different session cannot contribute to the current run.
- An unknown revision produces a withheld result; it does not inherit the last known revision.
- A revision mismatch withholds the result and returns no current posterior.
- A new matching snapshot can resume inference.

Identifiers are caller-supplied. The example does not inspect processes, discover
versions, read disk identity, compute hashes, or collect network traffic. It cannot
establish that identifiers are truthful or stable. Integrators must supply reliable
identity and replace the previous view with the new state, not merely relabel old values.

## Reproduce in a minute

From the repository root, with Python 3.10+ and no game, credentials, GUI, or dependencies:

```powershell
python examples/evidence_lifecycle.py
python -m unittest discover -s tests -p test_evidence_lifecycle.py -v
```

The deterministic JSON output is explicitly synthetic and covers complete, partial,
missing, stale-revision, unknown-revision, and wrong-session cases. Tests also cover the
first result, invalid count types, contradictory evidence, replacing a ready view, and
recovery with a matching snapshot. Run `python scripts/verify.py` for repository checks.

Apply this pattern to versioned catalogs, inventory batches, or experiment snapshots
only after defining the population, count semantics, and authoritative revision source.
Replace the candidates with your own population and test identity sourcing, duplicate
counts, and recovery. Record synthetic tests separately from real-system verification.

See the [public boundary](../OPEN_SOURCE_BOUNDARY.md) and the
[companion claim-review case study](https://github.com/SeasonCake/evidence-first-agent-skills/blob/main/docs/HOTFIX1_CLAIM_REVIEW.md).
