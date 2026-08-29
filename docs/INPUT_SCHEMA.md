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

## Multidimensional weighted mode

Set `"mode": "weighted"` to score arbitrary scalar fields. The complete synthetic
example is `examples/synthetic_multidimensional.json`:

```json
{
  "synthetic": true,
  "mode": "weighted",
  "credible_mass": 0.8,
  "candidates": [
    {
      "label": "balanced",
      "attributes": {"count": 11, "value": 1200, "region": "north"},
      "prior": 0.6
    },
    {
      "label": "wide",
      "attributes": {"count": 13, "value": 1500, "region": "south"},
      "prior": 0.4
    }
  ],
  "evidence": [
    {
      "field": "count",
      "observation": {"kind": "interval", "minimum": 9, "maximum": 13}
    },
    {
      "field": "value",
      "observation": {"kind": "approximate", "center": 1300, "scale": 200}
    },
    {
      "field": "region",
      "weight": 0.7,
      "observation": {
        "kind": "categorical",
        "likelihoods": {"north": 1.0, "south": 0.5}
      }
    }
  ]
}
```

Candidates require exactly `label`, `attributes`, and `prior`. Attribute values are
finite JSON numbers or strings; booleans are rejected. Evidence terms accept exactly:

- exact: `{"kind": "exact", "expected": <scalar>}`;
- interval: `{"kind": "interval", "minimum": <number>, "maximum": <number>}`;
- approximate: `{"kind": "approximate", "center": <number>, "scale": <positive>,
  "cutoff": <optional-positive>}`;
- categorical: `{"kind": "categorical", "likelihoods": {"label": <positive>}}`.

`weight` is an optional positive multiplier for an evidence term. Categorical values and
candidate priors are relative weights, not calibrated probabilities. Unknown document,
candidate, evidence, or observation keys fail closed.
