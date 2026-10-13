#!/usr/bin/env python3
"""Refresh value-normalization fields without changing distribution support.

The 0.3.4 package keeps the approved legacy map probabilities and map scope,
but its complete historical Item lookup is no longer allowed to define the
current normalization reference population.  This source-external step reads
the exact carried-forward 101-map payload, recomputes only each row's fitted
value from the current ``items_droppable`` population, then rebuilds the
derived quality priors and revealed-red lookup sidecar.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CALIBRATION = ROOT / "scripts" / "calibration"
for path in (CALIBRATION, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import aisha_calib as ac  # noqa: E402

LEGACY_DROP_WEIGHTS_BYTES = 2_484_864
LEGACY_DROP_WEIGHTS_SHA256 = (
    "23955de40db275d85ac6245896b3e4a8780c350f08b5ee1ea8f4281e0a5d7dac"
)
LEGACY_DROP_WEIGHTS_MAPS = 101
LEGACY_DROP_PRIORS_BYTES = 20_740
LEGACY_DROP_PRIORS_SHA256 = (
    "dad18c5102fb20a64404e1093164b321c79037d95b6b1e3b8ddc24a1c5ad5ed8"
)
LEGACY_DROP_PRIORS_MAPS = 72


class NormalizationRefreshError(ValueError):
    """A frozen input or distribution-preservation invariant failed."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise NormalizationRefreshError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def _load_json(raw: bytes, *, label: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise NormalizationRefreshError(f"invalid {label}") from exc


def _render(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _reset_calibration_inputs(*, items_json: Path, items_droppable: Path) -> None:
    ac.ITEMS_JSON = items_json
    ac.ITEMS_DROPPABLE_JSON = items_droppable
    ac._CATALOG = None
    ac._REFERENCE_ITEM_IDS = frozenset()
    ac._REFERENCE_ENABLE_RETIRED = None
    ac._SHAPE_FLOOR = None
    ac._REPLACEMENT = None
    ac._QMED_PERCELL = None


def refresh_outputs(
    *,
    legacy_drop_weights: bytes,
    legacy_drop_priors: bytes,
    items_json: Path,
    items_droppable: Path,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    if (
        len(legacy_drop_weights) != LEGACY_DROP_WEIGHTS_BYTES
        or _sha256(legacy_drop_weights) != LEGACY_DROP_WEIGHTS_SHA256
    ):
        raise NormalizationRefreshError("legacy drop weights identity mismatch")
    if (
        len(legacy_drop_priors) != LEGACY_DROP_PRIORS_BYTES
        or _sha256(legacy_drop_priors) != LEGACY_DROP_PRIORS_SHA256
    ):
        raise NormalizationRefreshError("legacy drop priors identity mismatch")
    weights = _load_json(legacy_drop_weights, label="legacy drop weights")
    previous_priors = _load_json(legacy_drop_priors, label="legacy drop priors")
    if weights.__class__ is not dict or len(weights) != LEGACY_DROP_WEIGHTS_MAPS:
        raise NormalizationRefreshError("legacy drop weights scope mismatch")
    if (
        previous_priors.__class__ is not dict
        or len(previous_priors) != LEGACY_DROP_PRIORS_MAPS
    ):
        raise NormalizationRefreshError("legacy drop priors scope mismatch")

    _reset_calibration_inputs(
        items_json=items_json,
        items_droppable=items_droppable,
    )
    catalog = ac._catalog()
    if not catalog or not ac._REFERENCE_ITEM_IDS:
        raise NormalizationRefreshError("current normalization reference unavailable")
    if ac.RETIRED_WORLD_CUP_Q6_ITEM_IDS & ac._REFERENCE_ITEM_IDS:
        raise NormalizationRefreshError("retired items entered default reference")

    changed_rows = 0
    row_count = 0
    for map_id, entry in weights.items():
        if (
            map_id.__class__ is not str
            or not map_id.isascii()
            or not map_id.isdigit()
            or entry.__class__ is not dict
            or entry.get("items").__class__ is not dict
        ):
            raise NormalizationRefreshError("legacy drop weights malformed")
        for raw_item_id, row in entry["items"].items():
            if (
                raw_item_id.__class__ is not str
                or not raw_item_id.isascii()
                or not raw_item_id.isdigit()
                or row.__class__ is not list
                or len(row) != 3
                or row[1].__class__ is not int
                or row[2].__class__ is not int
            ):
                raise NormalizationRefreshError("legacy item row malformed")
            item_id = int(raw_item_id)
            meta = catalog.get(item_id)
            if meta is None or int(meta[0]) != row[1]:
                raise NormalizationRefreshError("legacy/current item identity mismatch")
            fitted = int(ac.normalized_item_value(item_id))
            if fitted != row[2]:
                row[2] = fitted
                changed_rows += 1
            row_count += 1

    for previous in previous_priors.values():
        if (
            previous.__class__ is not dict
            or previous.get("quality_probs").__class__ is not dict
            or previous.get("quality_values").__class__ is not dict
            or previous.get("_value_src").__class__ is not dict
        ):
            raise NormalizationRefreshError("legacy prior malformed")
    # This update has no new probability or settled-value authority.  Preserve
    # the complete 72-map prior document exactly; deriving it again would be an
    # unrelated historical-prior recalibration even when deltas are tiny.
    priors = previous_priors
    red_longtail = {
        str(item_id): int(ac.normalized_item_value(item_id))
        for item_id, (_value, _shape, quality, _name) in catalog.items()
        if quality == 6 and ac.is_longtail(item_id)
    }
    alias_ids = {1046010, 1046011, 1046012}
    if alias_ids & {int(item_id) for item_id in red_longtail}:
        raise NormalizationRefreshError("localization alias entered red lookup")

    outputs = {
        "drop_weights_all_maps_0627.json": _render(weights),
        "drop_table_priors_0627.json": _render(priors),
        "red_longtail_normalized_0627.json": _render(red_longtail),
    }
    receipt = {
        "schema": "bidking_0_3_4_normalization_refresh_receipt_v1",
        "policy": (
            "probability_map_scope_and_priors_exactly_preserved;"
            "fitted_values_current_droppable_reference_only"
        ),
        "inputs": {
            "drop_priors": {
                "bytes": len(legacy_drop_priors),
                "maps": len(previous_priors),
                "sha256": LEGACY_DROP_PRIORS_SHA256,
            },
            "drop_weights": {
                "bytes": len(legacy_drop_weights),
                "maps": len(weights),
                "sha256": LEGACY_DROP_WEIGHTS_SHA256,
            },
        },
        "reference": {
            "items": len(ac._REFERENCE_ITEM_IDS),
            "retired_world_cup_included": False,
            "localization_aliases_are_item_ids": False,
        },
        "rows": {"changed_fitted_values": changed_rows, "total": row_count},
        "outputs": {
            name: {"bytes": len(raw), "sha256": _sha256(raw)}
            for name, raw in outputs.items()
        },
    }
    return outputs, receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-drop-weights", type=Path, required=True)
    parser.add_argument("--legacy-drop-priors", type=Path, required=True)
    parser.add_argument("--items-json", type=Path, required=True)
    parser.add_argument("--items-droppable", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--receipt-out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        outputs, receipt = refresh_outputs(
            legacy_drop_weights=args.legacy_drop_weights.read_bytes(),
            legacy_drop_priors=args.legacy_drop_priors.read_bytes(),
            items_json=args.items_json,
            items_droppable=args.items_droppable,
        )
    except (OSError, NormalizationRefreshError) as exc:
        print(f"input-assembly-not-ready: {exc}", file=sys.stderr)
        return 2
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
    for name, raw in outputs.items():
        (args.output_dir / name).write_bytes(raw)
    args.receipt_out.write_text(
        json.dumps(receipt, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        f"wrote normalization outputs={len(outputs)} "
        f"changed_rows={receipt['rows']['changed_fitted_values']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
