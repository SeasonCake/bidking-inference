"""Replay geometry-first grid-range prototype: baseline vs proposed (no engine edit).

For each sample/round we take the engine's current total_grid_range (baseline),
compute minimap geometry features, and apply aisha_geometry_floor.adjust_range to
get the proposed range. Emits one markdown comparison report so the user can
eyeball calibration (floor coefficients, deep-taper cap) before any engine change.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
AHMAD_SRC = ROOT / "hero_ref" / "src"
for path in (SCRIPTS, AHMAD_SRC, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from audit_aisha_gap import _map_id_from_events, _settlement_breakdown  # noqa: E402
from aisha_geometry_floor import adjust_range, compute_features  # noqa: E402
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture  # noqa: E402
from checkpoint_aisha_grid_sample20 import _evaluate_payload, _minimap_items  # noqa: E402
from organize_fatbeans_real_samples import sample_meta  # noqa: E402
from regenerate_checkpoint_matrices import (  # noqa: E402
    AISHA_SKILL_ROUNDS,
    EXCLUDED_PICKS,
    FEEL_PICKS,
    MatrixPick,
    _skill_ids,
    _snapshot_at_batch,
)

FATBEANS_DIR = ROOT / "data/samples/fatbeans"
REPORT = ROOT / "data/reports/geometry_prototype_before_after.md"

# New, previously un-annotated samples spanning depths/maps for generalization check.
NEW_PICKS: tuple[MatrixPick, ...] = (
    MatrixPick("2503_0154", "fatbeans_valid_aisha_2503_3rounds_2503_1295018669251860_0154.json", "新·沉船深仓 truth=116", True),
    MatrixPick("2509_0208", "fatbeans_valid_aisha_2509_5rounds_2509_1295018624542334_0208.json", "新·沉船 truth=94 五轮", True),
    MatrixPick("2402_0047", "fatbeans_valid_aisha_2402_5rounds_2402_1295018571743367_0047.json", "新·别墅 truth=95 五轮", True),
    MatrixPick("2410_0005", "fatbeans_valid_aisha_2410_5rounds_2410_1367517613200016_0005.json", "新·别墅浅仓 truth=65", True),
    MatrixPick("4410_0426", "fatbeans_valid_aisha_4410_3rounds_4410_1425860509979733_0426.json", "新·other深仓 truth=126", True),
    MatrixPick("2510_0209", "fatbeans_valid_aisha_2510_3rounds_2510_1274128128479934_0209.json", "新·沉船深仓 truth=100", True),
)


@dataclass
class RoundCompare:
    label: str
    truth: int
    raw_deepest: int | None
    known: int
    base: tuple[int | None, int | None, int | None]
    feat: Any
    proposed: tuple[int | None, int | None, int | None]
    notes: list[str]


def _hit(rng: tuple[int | None, int | None, int | None], truth: int) -> str:
    lo, _, hi = rng
    if lo is None or hi is None:
        return "?"
    return "Y" if lo <= truth <= hi else "N"


def _gap(rng: tuple[int | None, int | None, int | None], truth: int) -> str:
    mid = rng[1]
    if mid is None:
        return "—"
    return f"{mid - truth:+d}"


def _compare_sample(pick: MatrixPick) -> tuple[str, list[RoundCompare]]:
    recording = FATBEANS_DIR / pick.source_file
    if not recording.is_file():
        return f"missing: {pick.source_file}", []
    events = parse_fatbeans_capture(recording)
    pre_batches = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
    map_id = _map_id_from_events(events)
    settlement = _settlement_breakdown(events)
    truth = int(settlement["total_cells"])
    truth_payload = {"total_cells": truth, "total_items": int(settlement.get("total_items") or 0)}
    bid_rounds = sample_meta(recording).rounds
    prefix_snaps = [
        _snapshot_at_batch(events=events, pre_batches=pre_batches, batch_no=i, map_id=map_id)
        for i in range(1, len(pre_batches) + 1)
    ]

    rows: list[RoundCompare] = []
    for skill_round, skill_id, skill_label in AISHA_SKILL_ROUNDS:
        snap = next((s for s in prefix_snaps if skill_id in _skill_ids(s)), None)
        if snap is None:
            continue
        snap.setdefault("ui_contract", {}).setdefault("context", {})["round"] = skill_round
        payload = {
            "truth": truth_payload,
            "snapshot": snap,
            "eval_round": skill_round,
            "source_file": pick.source_file,
            "map_id": map_id,
            "source_kind": "fatbeans_batch_b",
        }
        rr = _evaluate_payload(payload)
        base = (rr.grid_low, rr.grid_mid, rr.grid_high)
        items = _minimap_items(snap)
        feat = compute_features(items, known_cells=rr.known_cells or 0)
        proposed, notes = adjust_range(base, feat, round_no=skill_round)
        rows.append(
            RoundCompare(
                label=f"R{skill_round}{skill_label}",
                truth=truth,
                raw_deepest=rr.raw_deepest,
                known=rr.known_cells or 0,
                base=base,
                feat=feat,
                proposed=proposed,
                notes=notes,
            )
        )
    header = f"map={map_id} · {pick.note} · {bid_rounds}轮出价 · truth={truth}"
    return header, rows


def _render(pick: MatrixPick, header: str, rows: list[RoundCompare]) -> str:
    out: list[str] = [f"## {pick.slug} — {pick.note}", "", header, ""]
    if not rows:
        out.append("_(无技能帧)_\n")
        return "\n".join(out)
    out.append("| 轮 | 最深行 | known | 占用行/连续度 | 宽度env | 深带最宽 | taper | f_low | baseline lo/mid/hi (hit/gap) | proposed lo/mid/hi (hit/gap) |")
    out.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        f = r.feat
        cont = f"{f.occupied_rows}/{f.deepest}={f.contiguity:.2f}" if f.deepest else "—"
        base_s = f"{r.base[0]}/{r.base[1]}/{r.base[2]} ({_hit(r.base, r.truth)}/{_gap(r.base, r.truth)})"
        prop_s = f"{r.proposed[0]}/{r.proposed[1]}/{r.proposed[2]} ({_hit(r.proposed, r.truth)}/{_gap(r.proposed, r.truth)})"
        out.append(
            f"| {r.label} | {f.deepest} | {r.known} | {cont} | {f.width_env} | "
            f"{f.deep_max_col} | {f.taper_loss} | {f.f_low:.2f} | {base_s} | {prop_s} |"
        )
    notes = [n for r in rows for n in r.notes]
    if notes:
        out.append("")
        out.append("应用: " + "; ".join(dict.fromkeys(notes)))
    out.append("")
    return "\n".join(out)


def main() -> int:
    groups: list[tuple[str, tuple[MatrixPick, ...]]] = [
        ("Feel 样本（用户已标注体感）", FEEL_PICKS),
        ("对照样本（有明确信息/已知问题）", EXCLUDED_PICKS),
        ("新样本（未标注，泛化检验）", NEW_PICKS),
    ]
    doc: list[str] = [
        "# 几何优先估格原型 · before/after 对比",
        "",
        "仅后处理 `total_grid_range`（不动件数/价值，不改引擎）。baseline=当前引擎输出；",
        "proposed=几何下限抬升 + 深带 taper 收口。系数为 v0 待校准（见 `aisha_geometry_floor.py`）。",
        "",
        "口径: 几何下限 ≈ 最深行×10×f_low（f_low 随连续度/深带宽度上调，孤立深件压低）；",
        "上限 ≈ 最深行×10 − 深带 taper×0.7。**不强制单调**（信息变清晰允许合理下修）。",
        "",
    ]
    for gtitle, picks in groups:
        doc.append(f"# {gtitle}\n")
        for pick in picks:
            header, rows = _compare_sample(pick)
            doc.append(_render(pick, header, rows))
    REPORT.write_text("\n".join(doc), encoding="utf-8")
    print(f"wrote {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
