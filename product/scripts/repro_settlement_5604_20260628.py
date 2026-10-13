"""Reproduce settlement gap for 5604:1630026028987444 (2026-06-28)."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIAG = ROOT / "data/samples/hero_ref/investigations/settlement_missing_5604_20260628"
RAW = ROOT / "tests/fixtures/live/settlement_5604_f3_split_npcap_live.jsonl"
USER_SETTLEMENT_VALUE = 1_134_728
USER_DECLARED_ITEMS = 33


def _load_events():
    from bidking_lab.live.fatbeans import (
        load_fatbeans_packets_from_rows,
        parse_fatbeans_packets,
    )

    rows = [
        json.loads(line)
        for line in RAW.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return parse_fatbeans_packets(load_fatbeans_packets_from_rows(rows)), rows


def _scan_settlement_frame(rows: list[dict]) -> None:
    from bidking_lab.live.fatbeans import (
        _parse_fields,
        _state_payload,
        load_fatbeans_packets_from_rows,
        reconstruct_fatbeans_frames,
    )

    pkts = load_fatbeans_packets_from_rows(rows)
    frames = reconstruct_fatbeans_frames(pkts, "REV")
    target = USER_SETTLEMENT_VALUE
    print("\n=== 0x002D frame scan (ints near user settlement value) ===")
    for fr in frames:
        if fr.message_id != 0x002D:
            continue
        print(f"sort_id={fr.sort_id} raw_len={len(fr.raw)} body_len={len(fr.body)}")
        hits: list[tuple[str, int]] = []

        def walk(data: bytes, prefix: str, depth: int = 0) -> None:
            if depth > 6:
                return
            for fn, _wt, val in _parse_fields(data):
                path = f"{prefix}f{fn}"
                if isinstance(val, int):
                    if val == target or abs(val - target) < 5000:
                        hits.append((path, val))
                    if val > 50_000:
                        hits.append((path, val))
                elif isinstance(val, bytes):
                    walk(val, path + ".", depth + 1)

        walk(fr.body, "")
        payload, loss = _state_payload(fr)
        print(f"inventory payload bytes={len(payload) if payload else 0} loss_units={loss}")
        for path, val in sorted(set(hits), key=lambda x: -x[1])[:40]:
            mark = " <-- USER VALUE" if val == target else ""
            print(f"  {path}: {val:,}{mark}")


def _scan_item_value_fields(rows: list[dict]) -> None:
    """Look for per-item rolled values inside inventory protobuf."""
    from bidking_lab.live.fatbeans import (
        _parse_fields,
        _state_payload,
        load_fatbeans_packets_from_rows,
        reconstruct_fatbeans_frames,
    )

    pkts = load_fatbeans_packets_from_rows(rows)
    frames = reconstruct_fatbeans_frames(pkts, "REV")
    print("\n=== Per-item protobuf fields (first 3 items) ===")
    for fr in frames:
        if fr.message_id != 0x002D:
            continue
        payload, _ = _state_payload(fr)
        if not payload:
            continue

        item_nodes: list[bytes] = []

        def find_items(data: bytes, depth: int = 0) -> None:
            if depth > 8:
                return
            ints: dict[int, list[int]] = {}
            bfs: dict[int, list[bytes]] = {}
            for fn, _wt, val in _parse_fields(data):
                if isinstance(val, int):
                    ints.setdefault(fn, []).append(val)
                elif isinstance(val, bytes):
                    bfs.setdefault(fn, []).append(val)
            rid = ints.get(1, [None])[0]
            iid = ints.get(2, [None])[0]
            if (
                isinstance(rid, int)
                and isinstance(iid, int)
                and 1_000_000 <= iid < 2_000_000
            ):
                item_nodes.append(data)
                return
            for vals in bfs.values():
                for v in vals:
                    find_items(v, depth + 1)

        find_items(payload)
        print(f"item nodes found: {len(item_nodes)}")
        for idx, node in enumerate(item_nodes[:3]):
            print(f"--- item[{idx}] ---")
            for fn, wt, val in _parse_fields(node):
                if isinstance(val, int):
                    print(f"  f{fn} int={val}")
                elif isinstance(val, bytes):
                    print(f"  f{fn} bytes len={len(val)} subfields={len(list(_parse_fields(val)))}")


def main() -> int:
    from bidking_lab.live.monitor import (
        _settlement_inventory_items,
        build_monitor_artifact_from_events,
        load_monitor_tables,
    )

    ev, rows = _load_events()
    tables = load_monitor_tables()
    art = build_monitor_artifact_from_events(
        ev,
        file="npcap_live.jsonl",
        tables=tables,
        n_trials=20,
    )

    print("=== REPLAY (source HEAD) ===")
    print("session_id:", art.get("session_id"))
    print("phase:", art.get("phase"))
    print("inventory_count:", art.get("inventory_count"))
    print("known_value_sum:", art.get("known_value_sum"))
    print("final_decision_value:", art.get("final_decision_value"))
    print("settlement_incomplete:", art.get("settlement_incomplete"))
    print("settlement_incomplete_detail:", art.get("settlement_incomplete_detail"))
    tic = (art.get("inference_input_constraints") or {}).get("total_item_count") or {}
    print("declared total_item_count:", tic)
    skill = (art.get("skill_reveal_rows") or [{}])[0]
    print("ahmed R1 skill result:", skill.get("result"), skill.get("tool"))
    captured = art.get("known_value_sum") or 0
    print(
        f"user settlement: {USER_SETTLEMENT_VALUE:,}  "
        f"gap: {USER_SETTLEMENT_VALUE - captured:,}  "
        f"ratio: {captured / USER_SETTLEMENT_VALUE:.3f}"
    )

    inv = _settlement_inventory_items(ev)
    print(f"\n=== Captured inventory ({len(inv)} items, catalog pricing) ===")
    total = 0
    q_counter: Counter[int] = Counter()
    lines = []
    for inv_item in inv:
        cat = tables.items.get(inv_item.item_id)
        value = cat.value if cat else 0
        total += value
        quality = inv_item.quality or (cat.quality if cat else 0)
        q_counter[int(quality or 0)] += 1
        name = cat.name if cat else "?"
        lines.append((value, quality, inv_item.item_id, name, inv_item.cells))
    for value, quality, item_id, name, cells in sorted(lines, reverse=True):
        print(f"  q{quality} {item_id} {name} cells={cells} catalog={value:,}")
    print("catalog sum:", f"{total:,}")
    print("quality counts:", dict(sorted(q_counter.items())))

    # Scale estimate: if missing items proportional
    missing = USER_DECLARED_ITEMS - len(inv)
    linear_est = int(total * USER_DECLARED_ITEMS / max(len(inv), 1))
    print(f"\nIf value scaled linearly {len(inv)}->{USER_DECLARED_ITEMS} items: {linear_est:,}")
    print(f"Still short of user value by: {USER_SETTLEMENT_VALUE - linear_est:,}")

    _scan_settlement_frame(rows)
    _scan_item_value_fields(rows)

    # Compare with packaged snapshot from live build
    snap_path = DIAG / "latest_snapshot.json"
    if snap_path.exists():
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        print("\n=== Packaged v0.2.3 snapshot ===")
        print("known_value_sum:", snap.get("known_value_sum"))
        print("inventory_count:", snap.get("inventory_count"))
        print("settlement_incomplete:", snap.get("settlement_incomplete"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
