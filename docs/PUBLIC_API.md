# Public API

The supported API is exported from `auction_inference`. Inputs are strict and fail closed:
booleans are not integers, numbers must be finite, mappings reject missing or extra keys,
and invalid distributions raise `TypeError` or `ValueError`.

## Models

- `Interval(minimum, maximum)` defines an inclusive exact-integer interval.
- `Evidence(total_count, total_value)` groups the two observed intervals.
- `Hypothesis(label, total_count, total_value, prior)` defines a discrete candidate.

Use `Interval.from_mapping`, `Evidence.from_mapping`, and `Hypothesis.from_mapping` for
untrusted mappings. They require the exact documented key set.

## Constraints

- `evaluate_hypothesis(hypothesis, evidence)` returns one `ConstraintResult`.
- `filter_hypotheses(hypotheses, evidence)` returns `(accepted, rejected)` and requires at
  least one hypothesis.

Rejection reasons are stable machine strings:
`total_count_outside_interval`, `total_value_outside_interval`, and `zero_prior`.

## Posterior

`infer_posterior(hypotheses, evidence)` filters candidates and normalizes the accepted
priors. It raises when evidence rejects every hypothesis. The result contains rows,
expected count/value, and the rejected constraint results.

## Monte Carlo

`simulate_totals(values, probabilities, *, draws_per_trial, trials, seed)` samples a
synthetic discrete value pool. The integer seed makes a fixed input reproducible. The
summary includes min/p05/median/p95/max and mean; it is a simulation summary, not a
confidence interval or product forecast.

## Compatibility

Public names exported by `auction_inference.__all__` follow Semantic Versioning. Error
message prose may improve in a minor release, while machine rejection reason strings are
treated as public behavior.
