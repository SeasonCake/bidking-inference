# -*- coding: utf-8 -*-
"""验证引擎提速优化(compose-cache + fit_grids backptr)对一批样本【字节级输出一致】+ 计时。

优化均为行为保持型(确定性纯函数缓存 / 回指 DP)。本 harness 对【同一 snapshot】分别在
  baseline = AHMAD_DISABLE_COMPOSE_CACHE=1 + AHMAD_DISABLE_FITGRIDS_BACKPTR=1
  optimized = 两旗标清除(默认)
两种环境下跑引擎, 比较 RefResult.as_dict() 是否完全相同, 并累计 wall-clock。

用法: python scripts/verify_engine_opt_identity.py <out.json> <sample1.json> [sample2.json ...]
输出 JSON: {n_samples,n_snaps,n_mismatch,mismatches,t_baseline,t_opt,speedup,errors}
Read-only(不改样本/不写引擎)。供 workflow 分片并行调用。"""
from __future__ import annotations
import sys, json, os, time, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT/"scripts", ROOT/"scripts/calibration", ROOT/"hero_ref/src", ROOT/"hero_ref/tools", ROOT/"src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import aisha_calib as C  # noqa: E402
from audit_aisha_gap import _map_id_from_events  # noqa: E402
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events  # noqa: E402
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from regenerate_checkpoint_matrices import _snapshot_at_batch  # noqa: E402

BASELINE_ENV = {"AHMAD_DISABLE_COMPOSE_CACHE": "1", "AHMAD_DISABLE_FITGRIDS_BACKPTR": "1"}


def _hero_from_name(path: str) -> str:
    m = re.search(r"fatbeans_(?:valid|mixed)_([a-z]+)_", Path(path).name)
    return m.group(1) if m else "aisha"


def _set_env(on: bool) -> None:
    for k, v in BASELINE_ENV.items():
        if on:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


def _run(snap, hero, rnd):
    cap = hero_max_combos_for_round(hero, rnd)
    return run_reference_engine(prepare_reference_engine_snapshot(snap), max_combos=cap).as_dict()


def main(argv: list[str]) -> int:
    out_path = argv[1]
    rest = argv[2:]
    if rest and rest[0] == "--list":
        files = json.loads(Path(rest[1]).read_text(encoding="utf-8"))
    else:
        files = rest
    n_snaps = 0
    n_mismatch = 0
    mismatches: list[str] = []
    errors: list[str] = []
    t_base = 0.0
    t_opt = 0.0
    for f in files:
        hero = _hero_from_name(f)
        try:
            ev = C._events(Path(f))
            pre = [b for b in live_batches_from_fatbeans_events(ev) if b.phase != "settled"]
            mp = _map_id_from_events(ev)
            snaps = [_snapshot_at_batch(events=ev, pre_batches=pre, batch_no=i, map_id=mp)
                     for i in range(1, len(pre) + 1)]
        except Exception as e:  # noqa: BLE001
            errors.append(f"{Path(f).name}: load {type(e).__name__}: {e}")
            continue
        for idx, snap in enumerate(snaps, start=1):
            rnd = min(idx, 5)
            try:
                _set_env(True)
                t0 = time.perf_counter(); base = _run(snap, hero, rnd); t_base += time.perf_counter() - t0
                _set_env(False)
                t0 = time.perf_counter(); opt = _run(snap, hero, rnd); t_opt += time.perf_counter() - t0
            except Exception as e:  # noqa: BLE001
                errors.append(f"{Path(f).name}#b{idx}: run {type(e).__name__}: {e}")
                continue
            n_snaps += 1
            if base != opt:
                n_mismatch += 1
                if len(mismatches) < 20:
                    diff_keys = sorted(k for k in set(base) | set(opt) if base.get(k) != opt.get(k))
                    mismatches.append(f"{Path(f).name}#b{idx} keys={diff_keys[:8]}")
    _set_env(False)
    result = {
        "n_samples": len(files), "n_snaps": n_snaps, "n_mismatch": n_mismatch,
        "mismatches": mismatches, "errors": errors[:20], "n_errors": len(errors),
        "t_baseline": round(t_base, 3), "t_opt": round(t_opt, 3),
        "speedup": round(t_base / t_opt, 3) if t_opt > 0 else None,
    }
    Path(out_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("n_samples","n_snaps","n_mismatch","n_errors","speedup")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
