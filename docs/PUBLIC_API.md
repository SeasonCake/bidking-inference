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

## Composable observations

- `ExactObservation(expected)` accepts the same string value or a numerically equal
  integer/float. Booleans and string/number coercion are rejected.
- `IntervalObservation(minimum, maximum)` defines an inclusive finite numeric interval.
- `ApproximateObservation(center, scale, cutoff=None)` contributes a Gaussian-shaped
  relative log-likelihood. `cutoff` is measured in scale units and becomes a hard bound.
- `CategoricalObservation(likelihoods)` maps supported string values to positive relative
  likelihoods. The values do not need to sum to one; an omitted category is rejected.
- `observation_from_mapping(value)` parses the exact JSON shapes in `INPUT_SCHEMA.md`.

Each `evaluate` call returns `ObservationMatch(accepted, log_likelihood, reason)`.

## Generic candidate scoring

- `Candidate(label, attributes, prior)` stores a non-empty scalar attribute mapping.
- `EvidenceTerm(field, observation, weight=1.0)` binds one observation to one attribute.
- `score_candidate(candidate, terms)` returns the log weight and stable rejection reasons.
- `infer_weighted_posterior(candidates, terms)` normalizes accepted candidates in log
  space and preserves rejected candidates separately.

Candidate labels must be unique. Missing attributes, zero priors, unsupported categories,
and hard-bound misses reject a candidate. At least one candidate must survive.

## Joint variables

`VariableState`, `DiscreteVariable`, `enumerate_joint_candidates`, and
`infer_joint_posterior` convert independent discrete variables into a Cartesian candidate
set. State priors are relative weights; each variable needs a positive total. The default
`max_combinations=10000` guard prevents accidental combinatorial expansion.

## Diagnostics

- `posterior_diagnostics(probabilities)` returns support size, entropy in bits, effective
  sample size, and maximum probability for an already normalized distribution.
- `smallest_credible_set(labeled_probabilities, mass=0.95)` returns the smallest
  probability-ordered prefix whose cumulative mass reaches the requested threshold.

## Record adapters

`MappingCandidateAdapter` maps public record fields to `Candidate`; `adapt_records`
validates the adapter output and requires unique labels. Implement the `CandidateAdapter`
protocol to adapt another record type without coupling it to the inference engine.

## Monte Carlo

`simulate_totals(values, probabilities, *, draws_per_trial, trials, seed)` samples a
synthetic discrete value pool. The integer seed makes a fixed input reproducible. The
summary includes min/p05/median/p95/max and mean; it is a simulation summary, not a
confidence interval or product forecast.

`PoolItem` and `simulate_pool` add labeled values, relative weights, and categories. With
replacement, draws use the standard-library weighted sampler. Without replacement, each
selected item is removed and the remaining relative weights are sampled sequentially.
The result includes the total summary and mean draws per category.

## Compatibility

Public names exported by `auction_inference.__all__` follow Semantic Versioning. Error
message prose may improve in a minor release, while machine rejection reason strings are
treated as public behavior.
