from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import subprocess
import sys
import threading
import zlib
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest

from ahmad_live_panel_server import ServerConfig, make_handler, summarize_snapshot
from ahmad_ref_engine import (
    _known_quality_value_total_floor,
    extract_evidence,
    run_reference_engine,
)
from ahmad_tk_overlay import _summary_diagnostic_row
from test_aisha_2521_q5q6_exact_residual_count_support_hotfix2 import (
    _require_frozen_package_inputs,
)
from test_ahmed_2404_q4_arm_risk_ceiling_0_3_1 import _snapshot_2404


ROLLBACK = "BIDKING_DISABLE_KNOWN_QUALITY_RESIDUAL_QUOTE_TRANSFORM"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def _frozen_package_inputs() -> None:
    _require_frozen_package_inputs(ROOT)


REAL_SNAPSHOT_ARCHIVES = {
    "2601:1862572829855581": (
        "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",
    ),
    "2601:1862572830469675": (
        "66edad23887dc0a9a59b370d69b27111b89d84a7aa4766afe933ee1131ade44e",
        "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",
    ),
    "2522:1862572860602704": (
        "9c23d100d74cee1ade4981bfd17a19b0cfc611ef56432bff247724185071fb16",
        "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",
    ),
    "2529:1862572863153195": (
        "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",
    ),
}


REAL_SNAPSHOT_EXTRAS = {
    "2601:1862572829855581": ("2d989d2f724c8c02242273d252e4d1dc76497c0d78a145db1c6420e49432c33e", "eNrtlt9uwiAUxt+Fay4KlEL7KoshrGVKhq2jrZkxvvtajVFrYnY2cCbzsuRAfv3Od/5sUak7M2/8Rs29rZTtzLJFxcsMo/bdOqe8WRvtxqMtWhjfKFuhgiYSo+a1NX5tTne2qDRuDK175zAaj/fRZ5+1XhpUIISRa0rtlK0r84kKkWP00Wtnu80x3Pd1Z5dm/wCRGeWCSppLnpGUDWwLvTKqbKrhNUIwWmvXm8PVHf45BxECACLzSxBKg4HkGUQQMuFg4QShHAAiImaGJAAQTqJlhlBIagSPpgiHCMImgrBwFjlEfJuEiEsSHtCsPAWAZDyaJKAuwrJ4XoW0VRrPqoRBQLJJWyXhBJGQbpaKeIJAOGgSrb1DfEriTTuIGkROMAJmJYfokctoNiUcAiJEtMSkEtLIaMTMABoITWi8ws0gU1fktxUZluyV9qbu1GHXPjF40/auu/xSb9a4U0TT19URix239T1Dkgz7+TjwG9+N7WL84Wire1hFflU0kCEjp9MuDTdkQIuZjLaF5Awy7JLbHMGsSq6tSp9W/SOrCsjkZTyaII9SMo+yHz5m6dLr0mV3Kt2nUf+BUfGztaf3WxDTs9Kd7b4Ad5QvnQ=="),
    "2601:1862572830469675": ("fc53e97db591e511203d435db86bfad501da1e615f3b4df1c572e7be678f2612", "eNrtlktuwjAQQO/idRb+xPldpaosN5mCVZNQJ0FFiLs3ASFI2HRaG7WUZayx9TJ+M+MdKXUHi8Zt1cKZSpkOVi0pnp4j0r4Za5WDDWg7Lu3IElyjTEUKTrOINC8tuA2c9+xICXYMrXtrIzIuH6IvPmu9AlIQEhHblNoqU1fwQQrJI/Lea2u67Snc9XVnVnA4gGUJlynPBI2TPM3lwLbUa1BlUw2nxcPmjbY9HLfuo+9zJBgMOsXg/jA4xXCwKYcQ3jhYKr4OknERDCRF+ZHPOKS/hGDyQWeCMOaPg0oMSBasYJjMMYbIYBnhAmMIn3IkHk3F5IPNOJjHi6EUY0garJflKFOTKYf0WDI8wdxMPEvIDGQYj2vtoO7UcUqeGRy0ve2mX+rVgD1HNH1dnbDEac4eGCgdJuvYzxs3HMHl+MPBhu5vaaoZylQZzNQUZaoIxsEExlROb2QquzaV38hUxlGK5MHeIQ9VQ6r6E5AY1d3zv/FS9Va7/Lp2xa2mzEPV/6DqHTzd73PKhHypxhc95Hn/CZkO+H4="),
    "2522:1862572860602704": ("92d50049e341db7f6e1357474f32030cc5b7175ccaf223c298ffc599ee1e824e", "eNrtmM1u00AQx9/F5xz2w7t2/CqostxkSaxs7OCPlKrKoQfEgR44tBQQVIKqKKqoQEggIRA8jZP0LVjHEbEdZ+MuJOLAKcl6PTu/+c/szuZIazkR6/jBod0J3LbtRqwfata9o9/jmgWB3lhO484+45qlTW7eJS/GmnjCOBevUPHNFw/E3C5zO91Is3BDS+3ZbntuhAKAFiOe02fCBgBG8vRk8v7H7fNPyfevs6ufs8tHwiT3Ww63Xa/NHgqDwsyD2OFudDhfJfAPNIuIz9iL3D7LrJsUEQOZFFCAERCzwq4zYHaPiXc0hIXN0I+DVrrowGn1WCRGDtx21NUsNGrcndZc0MIlLSrSEpC6kaedjN8kr8+Tk2fT08/Ty+Pp9XHy+Nvk5mpycT37cDY9HScXT8rwEOTgyQIeSeBhExbhdSSD11Xg4QIeL+FhDt6LOS+Sl6kMVEFlyiRFuEgFoYwKlqlwFdX5l+Tjy1UqvZoKpokFmkWw5O349uxVGS+fsGhBB2WamXTrdGhBR6oTdrNmUM9RZdNrJGNZNrQ12YhqMq7hkgtm7CwdlYsMqXCRrXNR+c65mYuAajAqPRLMIhiWJiL+E8GoqmCUVIMZMjC4u41RucLwmhLTpWDg3y8xiNYVGZahEbozNOVkhHhNNkKpapTs7CRLE0TxKIPNNWxIKpu+9cOsYnO8o2wQqKChZhGNSmWjoz3xtOdybgdsyBye3Ri6LPDnthEQTvv7IQuGrHClyCBzFDVP6PINoEbn3/LbwlTaNg4dHrNsmbkkik6sdOI1OvDMCR39NSdWGucaDXPmBIRFJ4SCAydgXmRnQi7XD1gY86j4y77vMr6c4cdeO9fsLi1AccVLxQ/9IK1KkrLWTQtzJ/cnFdUIMQxQI2JmOVzzvTIfLLgarP/RWhsttBotpBYtup1/IlT2IdhsArNOfOjG+FSUHs7FZ2/0C3L690U="),
    "2529:1862572863153195": ("fd536f27c3e5d11db1c43b51deb90539fc35e9135e4d9021511ea0cde1dc60e3", "eNrNmEtP20AQx7+Lzznsy698h0q9VygyyZZYmJg6DhQhJKSqqKISXJBKQSo9VKUPVa04tKfybRzKt+jajhsnazbrzRp6S/xYz2/+szM7s2t0vZiuhdFOZy3yex0/phtDo/1k9991ow0BaU0fC7xVGhhtY/ztY/L2k8Hu0CBgr1jsV8huwJbRp/5aPzbauGWk63X8ntEejIJg8nfgbVC2AHs1CLte0PEHPfqcvWiClvFs5AV+vJMtF4Xb7Gr6YzSI/Q2arQMdC5k2ciwMTWyld4d9b5N21il7yUCYrToMR1E3/cKm112nMbuy7ffivtFGe636XA7PhepxOWUus+ByRVzYneUiSMRFVLgKvUxlvUiFXEhAZdpu42qRQi0wxSK1sEy3hEUmWKIgNJ05LEhEWFAFC02w3CkVrCcWdEpYuAhCkVwWwHNyQe1yQcTvrnpyIVAhFxbKBWexsFAurIQ1oSLKcuEKKvHecuaCEGoPwqWpEK6IQbFWVuNURX63VfM7tiq0IkIq3Hh2d3it6lGZuHYaJI1TFRGIqyOQLYJBmvnLYMmHs+Tw4s+L38nBefJqn9tpqCImxTvNfOidpsKJq/aeOEqbzyiWeO9lnIDMcj56bCYHV7cnr8fvvsxDulZVkQPCIjdfDZD+arCgyLFVCADunJzH38eHp8nx+9uvp7cXl+P9y9vTq/Hhh5vza466qgQKqU3XWaoG4irqNz+TH2dTanfZfgCBqnMzgiI1zbl6gbF2rmJvOuq13S5xoUIv0V60iLvUXqzDZaty2VYFliOMQvP/p3LKVPnjGZiwfQP2vYGVG516ZK5bTSbOl9C5N7Q7qzyGAOBZuJujz8nLXzfXRzcnV1wiKXHCAlIUl9b97TblszS+QzzhyQU0zoWWnZZYoBrMFiaSuSIOkRhshd1d94OgE9Et6gX5wKtPozBbG6VngnB1SKMtOjMRyxFLDIptj0S70w17NO3aW8aWF4xo/plMEEUj+CmUxPApt4IgbVZwXYVEN6HdCG5wJTGwyo1IldRlBJINimm/oT0osHxkOo0ZwQ3cJAZtEyOINiO4zkGiYciNwBoDk5uASwy+tUcmN8+QmGPo36PcvFJiTDlxhb7Q5DoriYZqEhX6QhNJG+HAxoyA0onbXpApWO3d9CI6iDt5CZ5+P6LDURDP/us89WkwfSIcDXqFSaQo4vmhEGRlexhGaaOZdXayBR2iJptxlZBhra+DJDyVze9n/JS1kWUvQd5Lqm7SPYFSKTasecYyjlnoF8T7BT2MX7ScBLT5BfN+wWp+sbRO8lTqMUYukXCKtdApFamGlJyysvcXTQI3Mg=="),
}

REAL_SNAPSHOT_CREATED_AT = {
    "2601:1862572829855581": 1785645199.7819862,
    "2601:1862572830469675": 1785646228.920063,
    "2522:1862572860602704": 1785691106.0775716,
    "2529:1862572863153195": 1785696694.7318406,
}

REAL_EXPECTED_8K = {
    "2601:1862572829855581": {
        "raw": [3_985_251, 4_059_550, 4_132_518],
        "rollback": [3_387_463, 3_450_617, 3_512_640],
        "default": [3_862_472, 3_925_626, 3_987_649],
    },
    "2601:1862572830469675": {
        "raw": [4_098_160, 4_134_766, 4_249_322],
        "rollback": [3_693_221, 3_693_221, 3_693_221],
        "default": [4_037_419, 4_068_535, 4_165_907],
    },
    "2522:1862572860602704": {
        "raw": [2_034_150, 2_067_659, 2_104_856],
        "rollback": [1_883_477, 1_883_477, 1_883_477],
        "default": [2_011_549, 2_040_031, 2_071_649],
    },
    "2529:1862572863153195": {
        "raw": [2_388_101, 2_444_388, 2_502_727],
        "rollback": [2_029_886, 2_077_730, 2_127_318],
        "default": [2_320_632, 2_368_476, 2_418_064],
    },
}

FRESH_EXPECTED = {
    8_000: {
        "raw": [3_932_681, 4_003_894, 4_078_402],
        "rollback": [3_342_778, 3_403_310, 3_466_642],
        "default": [3_817_787, 3_878_319, 3_941_650],
        "combo_count": 8_000,
    },
    50_000: {
        "raw": [3_943_331, 4_015_921, 4_093_462],
        "rollback": [3_351_831, 3_413_533, 3_479_443],
        "default": [3_826_840, 3_888_541, 3_954_451],
        "combo_count": 50_000,
    },
}

_FRESH_PROCESS_RUNNER = r"""
import hashlib
import json
import sys
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root / "hero_ref" / "src"))
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine

snapshot = json.load(sys.stdin)
result = run_reference_engine(
    prepare_reference_engine_snapshot(snapshot),
    max_combos=int(sys.argv[1]),
).as_dict()
stable = {
    key: result[key]
    for key in (
        "combo_count",
        "quality_count_ranges",
        "quality_cells_ranges",
        "quality_count_candidates",
        "quality_cells_candidates",
        "quality_count_cells_candidates",
        "quality_lock",
        "red_value_range",
        "evidence",
    )
}
print(json.dumps({
    "quotes": [result[k] for k in ("conservative", "balanced", "aggressive")],
    "raw": [result[k] for k in ("value_p25", "value_p50", "value_p75")],
    "combo_count": result["combo_count"],
    "stable_sha256": hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest(),
}, sort_keys=True))
"""


def _real_snapshot(session_id: str) -> dict:
    expected_sha256, encoded = REAL_SNAPSHOT_ARCHIVES[session_id]
    payload = zlib.decompress(base64.b64decode(encoded))
    assert hashlib.sha256(payload).hexdigest() == expected_sha256
    snapshot = json.loads(payload.decode("utf-8"))
    extra_sha256, extra_encoded = REAL_SNAPSHOT_EXTRAS[session_id]
    extra_payload = zlib.decompress(base64.b64decode(extra_encoded))
    assert hashlib.sha256(extra_payload).hexdigest() == extra_sha256
    snapshot.update(json.loads(extra_payload.decode("utf-8")))
    snapshot["created_at"] = REAL_SNAPSHOT_CREATED_AT[session_id]
    assert "truth" not in snapshot.get("ui_contract", {})
    return snapshot


def _fresh_process(snapshot: dict, *, max_combos: int, rollback: bool) -> dict:
    env = os.environ.copy()
    if rollback:
        env[ROLLBACK] = "1"
    else:
        env.pop(ROLLBACK, None)
    completed = subprocess.run(  # noqa: S603 - fixed local interpreter and code
        [sys.executable, "-c", _FRESH_PROCESS_RUNNER, str(max_combos)],
        cwd=ROOT,
        env=env,
        input=json.dumps(snapshot),
        text=True,
        capture_output=True,
        check=True,
        timeout=180,
    )
    assert completed.stderr == ""
    return json.loads(completed.stdout)


def _snapshot(*, quality_values: dict[str, int] | None = None) -> dict:
    return {
        "ui_contract": {
            "context": {
                "hero": "ethan",
                "map_id": 2601,
                "phase": "bidding",
                "round": 4,
            },
            "source": {
                "qualification": "postupdate_unknown_warehouse_source",
                "official_distribution_eligible": False,
                "quote_policy": "advisory_only_no_clamp",
            },
        },
        "structured_ref_inputs": {
            "total_count": 20,
            "fixed_counts": {"q1": 4, "q3": 4, "q4": 4, "q5": 4, "q6": 4},
            "quality_cells": {"q1": 5, "q3": 8, "q4": 12, "q5": 16, "q6": 20},
            "quality_values": dict(quality_values or {}),
        },
    }


def _ahmed_zero_red_snapshot_with_known_q4() -> dict:
    source = {
        "qualification": "postupdate_unknown_warehouse_source",
        "official_distribution_eligible": False,
        "quote_policy": "advisory_only_no_clamp",
    }
    return {
        "hero": "ahmed",
        "map_id": 2510,
        "phase": "bidding",
        "round": 4,
        "warehouse_source": copy.deepcopy(source),
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 2510,
                "phase": "bidding",
                "round": 4,
                "warehouse_source": copy.deepcopy(source),
            },
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": {
            "source": "monitor_pre_settlement_field_updates",
            "total_count": 35,
            "counts": {"q1": 5, "q3": 10, "q4": 8},
            "quality_cells": {"q1": 15},
            "avg_cells": {
                "q1": 3.0,
                "q3": 3.299999952316284,
                "q4": 3.5,
                "q5": 3.1666667461395264,
            },
            "quality_values": {"q4": 100_000},
        },
    }


def _quotes(result: dict) -> list[int]:
    return [result[key] for key in ("conservative", "balanced", "aggressive")]


def _raw_values(result: dict) -> list[int]:
    return [result[key] for key in ("value_p25", "value_p50", "value_p75")]


@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_fresh_process_default_and_rollback_8k_50k(max_combos: int) -> None:
    snapshot = _real_snapshot("2601:1862572829855581")
    expected = FRESH_EXPECTED[max_combos]

    default = _fresh_process(snapshot, max_combos=max_combos, rollback=False)
    rollback = _fresh_process(snapshot, max_combos=max_combos, rollback=True)

    assert default["raw"] == rollback["raw"] == expected["raw"]
    assert default["combo_count"] == rollback["combo_count"] == expected[
        "combo_count"
    ]
    assert default["quotes"] == expected["default"]
    assert rollback["quotes"] == expected["rollback"]
    assert default["stable_sha256"] == rollback["stable_sha256"]


@pytest.mark.parametrize("session_id", list(REAL_EXPECTED_8K))
def test_four_truth_free_bidding_snapshots_fixed_default_rollback_8k(
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _real_snapshot(session_id)
    expected = REAL_EXPECTED_8K[session_id]

    monkeypatch.setenv(ROLLBACK, "1")
    rollback_summary = summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=Path("missing-known-value-rollback.json"),
    )
    monkeypatch.delenv(ROLLBACK)
    default_summary = summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=Path("missing-known-value-default.json"),
    )
    rollback = rollback_summary["ahmed_ref"]
    current = default_summary["ahmed_ref"]

    assert rollback_summary["context"]["phase"] == "bidding"
    assert default_summary["context"]["phase"] == "bidding"
    assert _raw_values(rollback) == _raw_values(current) == expected["raw"]
    assert _quotes(rollback) == expected["rollback"]
    assert _quotes(current) == expected["default"]
    for key in (
        "combo_count",
        "quality_count_ranges",
        "quality_cells_ranges",
        "quality_count_candidates",
        "quality_cells_candidates",
        "quality_count_cells_candidates",
        "quality_lock",
        "red_value_range",
    ):
        assert current[key] == rollback[key]
    assert (
        default_summary["context"]["warehouse_source"]
        == rollback_summary["context"]["warehouse_source"]
    )


def test_exact_q6_total_is_kept_before_unknown_residual_discount(monkeypatch) -> None:
    snapshot = _snapshot(quality_values={"q6": 1_000_000})

    monkeypatch.setenv(ROLLBACK, "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.delenv(ROLLBACK)
    current = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert _quotes(rollback) == [1_028_886, 1_040_972, 1_053_058]
    assert _quotes(current) == [1_178_886, 1_190_972, 1_203_058]
    assert current["value_p25"] == rollback["value_p25"] == 1_210_454
    assert current["value_p50"] == rollback["value_p50"] == 1_224_673
    assert current["value_p75"] == rollback["value_p75"] == 1_238_892
    assert current["combo_count"] == rollback["combo_count"] == 1


def test_no_known_value_is_byte_identical_to_rollback(monkeypatch) -> None:
    snapshot = _snapshot()

    monkeypatch.setenv(ROLLBACK, "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.delenv(ROLLBACK)
    current = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert current == rollback


@pytest.mark.parametrize(
    ("quality_values", "known_value"),
    (
        ({"q4": 250_000}, 250_000),
        ({"q5": 500_000}, 500_000),
        ({"q4": 250_000, "q5": 500_000, "q6": 1_000_000}, 1_750_000),
    ),
)
def test_exact_quality_totals_are_additive_and_only_unknown_residual_is_discounted(
    quality_values: dict[str, int],
    known_value: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(quality_values=quality_values)

    monkeypatch.setenv(ROLLBACK, "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.delenv(ROLLBACK)
    current = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert _raw_values(current) == _raw_values(rollback)
    assert current["quality_count_cells_candidates"] == rollback[
        "quality_count_cells_candidates"
    ]
    expected = [
        round(
            known_value
            + max(0, raw - known_value) * (transformed / raw)
        )
        for raw, transformed in zip(
            _raw_values(rollback),
            _quotes(rollback),
            strict=True,
        )
    ]
    # Published quantiles/quotes are integer projections of the internal weighted
    # floats, so reconstructing the conjugation from those published integers is
    # approximate when K is close to the raw total.
    assert _quotes(current) == pytest.approx(expected, abs=2_000)
    assert all(value >= known_value for value in _quotes(current))
    assert any(
        str(note).startswith(
            f"known_quality_value_residual_quote_transform:{known_value}:"
        )
        for note in current["notes"]
    )


def test_exact_and_revealed_catalog_floors_dedupe_within_quality_and_add_across() -> None:
    evidence = extract_evidence(
        _snapshot(quality_values={"q5": 100_000})
    )
    evidence = replace(
        evidence,
        quality_value_floors={"q5": 40_000, "q6": 30_000},
        quality_catalog_min_value_floors={"q5": 70_000, "q6": 20_000},
        quality_min_total_value_floors={"q5": 80_000, "q6": 60_000},
    )

    # q5=max(exact 100k, revealed+catalog 110k, aggregate floor 80k)
    # q6=max(revealed+catalog 50k, aggregate floor 60k); qualities are disjoint.
    assert _known_quality_value_total_floor(evidence) == 170_000


def test_explicit_zero_quality_total_has_no_discount_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(quality_values={"q6": 0})

    monkeypatch.setenv(ROLLBACK, "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.delenv(ROLLBACK)
    current = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert current == rollback


def test_settled_path_is_byte_identical_to_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(quality_values={"q6": 1_000_000})
    snapshot["ui_contract"]["context"]["phase"] = "settled"

    monkeypatch.setenv(ROLLBACK, "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.delenv(ROLLBACK)
    current = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert current == rollback


@pytest.mark.parametrize("phase", ("reading", "manual"))
def test_nonsettled_active_paths_keep_the_same_residual_contract(
    phase: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(quality_values={"q6": 1_000_000})
    snapshot["ui_contract"]["context"]["phase"] = phase

    monkeypatch.setenv(ROLLBACK, "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.delenv(ROLLBACK)
    current = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert _raw_values(current) == _raw_values(rollback)
    assert _quotes(current) == [1_178_886, 1_190_972, 1_203_058]


def _set_source(snapshot: dict, qualification: str) -> None:
    source = {
        "qualification": qualification,
        "official_distribution_eligible": qualification == "official_random_warehouse",
        "quote_policy": "advisory_only_no_clamp",
        "quarantine_required": False,
    }
    snapshot["warehouse_source_qualification"] = qualification
    snapshot["warehouse_source"] = copy.deepcopy(source)
    snapshot["ui_contract"]["context"]["warehouse_source_qualification"] = (
        qualification
    )
    snapshot["ui_contract"]["context"]["warehouse_source"] = copy.deepcopy(source)
    snapshot["ui_contract"]["source"] = {
        "warehouse_source_qualification": qualification,
        "warehouse_source": copy.deepcopy(source),
    }


@pytest.mark.parametrize(
    "qualification",
    (
        "official_random_warehouse",
        "postupdate_unknown_warehouse_source",
        "player_custom_warehouse",
    ),
)
def test_source_pool_is_preserved_while_residual_transform_is_applied(
    qualification: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(quality_values={"q6": 1_000_000})
    _set_source(snapshot, qualification)
    original = copy.deepcopy(snapshot)

    monkeypatch.delenv(ROLLBACK, raising=False)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / f"{qualification}.json",
    )

    assert snapshot == original
    assert summary["context"]["warehouse_source_qualification"] == qualification
    assert summary["context"]["warehouse_source"] == original["warehouse_source"]
    assert _quotes(summary["ahmed_ref"]) == [1_178_886, 1_190_972, 1_203_058]


def test_summary_loopback_api_and_tk_consume_the_same_quotes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(quality_values={"q6": 1_000_000})
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    monkeypatch.delenv(ROLLBACK, raising=False)

    summary = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(  # noqa: S310 - loopback product contract
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=10,
        ) as response:
            assert response.status == 200
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)

    tk_row = _summary_diagnostic_row(
        payload,
        snapshot_path=snapshot_path,
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )
    expected = [1_178_886, 1_190_972, 1_203_058]
    assert _quotes(summary["ahmed_ref"]) == expected
    assert _quotes(payload["ahmed_ref"]) == expected
    assert [
        tk_row["reference"][key]
        for key in ("conservative", "balanced", "aggressive")
    ] == [f"{value:,}" for value in expected]


def test_structured_zero_red_tail_risk_quotes_also_transform_only_residual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _ahmed_zero_red_snapshot_with_known_q4()

    monkeypatch.setenv(ROLLBACK, "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.delenv(ROLLBACK)
    current = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert current["quality_count_cells_candidates"] == rollback[
        "quality_count_cells_candidates"
    ]
    assert current["quality_lock"] == rollback["quality_lock"]
    assert current["risk_floor"]["probability"] == rollback["risk_floor"][
        "probability"
    ]
    for key in ("conservative", "balanced", "aggressive"):
        assert current["risk_floor"]["tail_quotes"][key] > rollback[
            "risk_floor"
        ]["tail_quotes"][key]
        assert current["risk_floor"]["tail_quotes"][key] >= 100_000


def test_structured_q4_arm_risk_ceiling_also_transforms_only_residual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2404()
    for owner in ("structured_ref_inputs", "ahmad_ref_inputs"):
        snapshot[owner]["quality_values"] = {"q5": 100_000}
    snapshot["ui_contract"]["constraints"]["structured_ref_inputs"][
        "quality_values"
    ] = {"q5": 100_000}

    monkeypatch.setenv(ROLLBACK, "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.delenv(ROLLBACK)
    current = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert current["quality_count_cells_candidates"] == rollback[
        "quality_count_cells_candidates"
    ]
    assert current["quality_lock"] == rollback["quality_lock"]
    assert current["risk_ceiling"]["value"] > rollback["risk_ceiling"]["value"]
    selected = current["risk_ceiling"]["selected_branch"]
    matching = next(
        branch
        for branch in current["risk_ceiling"]["branches"]
        if branch["count"] == selected["count"]
        and branch["cells"] == selected["cells"]
    )
    assert selected == matching
    assert current["risk_ceiling"]["value"] == selected["aggressive"]
