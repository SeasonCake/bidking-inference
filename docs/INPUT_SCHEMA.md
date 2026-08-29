# Synthetic CLI input schema

The CLI accepts one UTF-8 JSON document and rejects inputs that do not declare
`"synthetic": true`.

```json
{
  "synthetic": true,
  "evidence": {
    "total_count": {"minimum": 10, "maximum": 14},
    "total_value": {"minimum": 900, "maximum": 1600}
  },
  "hypotheses": [
    {"label": "balanced", "total_count": 12, "total_value": 1200, "prior": 0.7}
  ],
  "monte_carlo": {
    "values": [50, 100, 250],
    "probabilities": [0.5, 0.35, 0.15],
    "draws_per_trial": 12,
    "trials": 2000,
    "seed": 7
  }
}
```

All count/value/draw/trial/seed fields are exact JSON integers. `prior` and probability
weights are finite non-negative numbers; each weight set must have a positive sum. Array
lengths and ranges must be meaningful. Unknown or missing model keys fail closed.

The output is JSON containing accepted posterior rows, expected values, rejected labels
with reason strings, and the deterministic Monte Carlo summary.
