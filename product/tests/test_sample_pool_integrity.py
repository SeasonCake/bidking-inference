"""样本池守门：候选必须登记、内容哈希未漂移、严格 truth gate 判为 valid.

╔════════════════════════════════════════════════════════════════════════════════════╗
║ 旧 refresh_sample_pool._screen 有 84% 漏网率，--apply 仍保持封锁。                  ║
║ registry 改由 build_sample_truth_registry.py 调用严格 sample_truth_gate 生成；       ║
║ 它覆盖主池、活动池和隔离样本。active 必须全 valid；隔离还可含 mixed/provenance。     ║
╚════════════════════════════════════════════════════════════════════════════════════╝

## 盲区(0.2.8 修): 我的门只查「声明件数 > 实抓件数」, 漏掉了正交的物理铁律

    **揭示 ⊆ 真值**:  breakdown_total_cells < R4 实际揭示格  ⇒  结算真值残缺

实测: 拿 31 个真实坏样本(覆盖全部 9 个英雄)喂给我的门 —— **抓到 5, 放过 26**。
🔴 而我原本只用【一个】坏样本做证伪门, 它**碰巧被抓到了** ⇒ 我差点认为门是好的。
   是作者坚持"一个不够, 要十几个、不同地图、不同英雄"才把 84% 的漏网逼出来。
   **⇒ 证伪门的【多样性】和它的【存在】同等重要。一个样本的通过, 证明不了任何事。**

────────────────────────────────────────────────────────────────────────────────────

样本池守门 —— 无效样本【不可能】悄悄进入标定/调参.

作者 0712: "需要核对好, 以免把错误和无效样本也归入"

## 为什么这道门必须存在

坏真值(结算不完整 / 声明件数 > 实抓件数)会把**欠估读成过估 —— 符号相反**
(见记忆 `fatbeans-mixed-samples-bad-truth`: 血色 2501 实案, 残缺真值算出"过估 +148,953",
完整真值算出"欠估 −299,607")。**用这种样本调参 = 朝着错误方向调, 而且是【静默】的。**

历史上这只靠"纪律"守着(有人记得跑一遍筛查)。纪律会失效, 测试不会。

## 三条不变量(任何一条破了都该红)

1. **池子里的每一个样本都必须【登记在案且判为 valid】.**
   有人往 data/samples/fatbeans/ 里丢一个文件却没跑 refresh_sample_pool.py => 红.
2. **排除清单里的样本, 一个都不许出现在池子里.**
3. **隔离区(ZZZ_INVALID_DO_NOT_USE_*/)里的样本, 一个都不许被 clean_eval_files 扫到.**
   (glob 不递归, 所以这条平时自然成立 —— 但如果有人改了 glob 或挪了文件, 这里会喊.)

外加一道**分英雄抽检**: 每个英雄取 N 个池内样本，现场重跑严格 truth gate。
(登记表可能过期 —— 比如解析器修好后旧判决失效; 抽检是对登记表本身的对照.)

## 出错了怎么办
    python scripts/build_sample_truth_registry.py --write --full
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in ("scripts", "hero_ref/src", "hero_ref/tools", "src", "."):
    _f = str(ROOT / _p)
    if _f not in sys.path:
        sys.path.insert(0, _f)

FATBEANS = ROOT / "data" / "samples" / "fatbeans"
MANIFESTS = ROOT / "data" / "sample_manifests"
REGISTRY = MANIFESTS / "sample_pool_registry.json"
EXCLUDE = MANIFESTS / "declared_mismatch_exclude.json"

HEROES = ("ahmed", "aisha", "ethan", "gabriela", "raven", "sophie", "tatiana")
SPOT_CHECK_PER_HERO = 2
KNOWN_CLEAN_POLLUTION = {
    "fatbeans_valid_ahmed_2301_1rounds_2301_1575875119893486_0098.json",
    "fatbeans_valid_ahmed_2306_1rounds_2306_1575875110259334_0195.json",
    "fatbeans_valid_ahmed_2401_3rounds_2401_1575875207639318_0301.json",
    "fatbeans_valid_ahmed_2405_2rounds_2405_1575875107245532_0424.json",
    "fatbeans_valid_ahmed_2408_6rounds_2408_1575875135187976_0498.json",
    "fatbeans_valid_ahmed_2501_3rounds_2501_1630025996898264_0595.json",
    "fatbeans_valid_ahmed_5601_5rounds_5601_1544538959251891_1406.json",
    "fatbeans_valid_ahmed_5606_5rounds_5606_1544676403181648_1585.json",
    "fatbeans_valid_ahmed_5607_1rounds_5607_1575875095277749_1595.json",
    "fatbeans_valid_aisha_5603_4rounds_5603_1575875051838984_1849.json",
    "fatbeans_valid_aisha_5606_5rounds_5606_1575875052431622_1865.json",
    "fatbeans_valid_aisha_5606_5rounds_5606_1575875054479420_1866.json",
}


def _pool_files() -> list[Path]:
    from build_clean_eval_set import clean_eval_files
    out: list[Path] = []
    for hero in HEROES:
        out += [Path(f) for f in clean_eval_files(hero)]
    return sorted(set(out))


def test_missing_truth_registry_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import build_clean_eval_set as clean_set

    monkeypatch.setattr(clean_set, "TRUTH_REGISTRY", tmp_path / "missing_registry.json")
    with pytest.raises(clean_set.SampleTruthRegistryError, match="registry is missing"):
        clean_set._load_truth_registry()


def test_truth_gate_context_hash_is_stable_across_checkout_newlines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import build_clean_eval_set as clean_set

    gate = tmp_path / "sample_truth_gate.py"
    monitor = tmp_path / "monitor.py"
    gate.write_bytes(b"alpha\nbeta\n")
    monitor.write_bytes(b"one\r\ntwo\rthree\n")
    monkeypatch.setattr(clean_set, "ROOT", tmp_path)
    monkeypatch.setattr(clean_set, "TRUTH_GATE_SOURCE", gate)
    monkeypatch.setattr(clean_set, "TRUTH_GATE_CONTEXT_PATHS", (gate, monitor))
    monkeypatch.setattr(clean_set, "TRUTH_GATE_REQUIRED_RAW_SHA256", {})

    raw_before = clean_set._sha256_file(gate)
    code_before = clean_set._truth_gate_code_sha256()
    context_before = clean_set._truth_gate_context_sha256()

    gate.write_bytes(b"alpha\r\nbeta\r\n")
    monitor.write_bytes(b"one\ntwo\nthree\n")

    assert clean_set._sha256_file(gate) != raw_before
    assert clean_set._truth_gate_code_sha256() == code_before
    assert clean_set._truth_gate_context_sha256() == context_before


def test_truth_gate_context_covers_final_release_transitive_inputs() -> None:
    import build_clean_eval_set as clean_set

    actual = {
        path.relative_to(ROOT).as_posix()
        for path in clean_set.TRUTH_GATE_CONTEXT_PATHS
    }
    required = {
        "scripts/mitm_bidking_wss_addon.py",
        "scripts/run_fatbeans_webhook_monitor.py",
        "scripts/run_wss_live_monitor.py",
        "hero_ref/src/ahmad_ref_engine.py",
        "hero_ref/src/layout_depth_policy.py",
        "hero_ref/tools/ahmad_live_panel_server.py",
        "hero_ref/tools/ahmad_tk_overlay.py",
        "hero_ref/tools/detail_composition_view.py",
        "src/bidking_lab/data/item_category_authority.py",
        "src/bidking_lab/inference/mean_multiset_support.py",
        "src/bidking_lab/inference/size_avg_evidence.py",
        "src/bidking_lab/inference/v2.py",
        "src/bidking_lab/live/live_refresh_signatures.py",
        "src/bidking_lab/live/monitor.py",
        "src/bidking_lab/runtime/snapshot.py",
        "data/processed/drop_weights_all_maps_0627.json",
        "data/processed/item_category_authority_0_3_2.json",
        "data/processed/items_droppable.json",
        "external_references/AuctionAnalyzer4.13.3/_decompiled/MapBidCalculator/MapBidCalculator/Models/StaticData.cs",
    }
    assert required <= actual


def test_hotfix2_layout_depth_policy_mutation_invalidates_source_context_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import build_clean_eval_set as clean_set

    layout_relative = "hero_ref/src/layout_depth_policy.py"
    parent_order = (
        "scripts/sample_truth_gate.py",
        "scripts/summarize_fatbeans_sample_manifest.py",
        "scripts/mitm_bidking_wss_addon.py",
        "scripts/run_fatbeans_webhook_monitor.py",
        "scripts/run_wss_live_monitor.py",
        "hero_ref/src/ahmad_ref_engine.py",
        "hero_ref/tools/ahmad_live_panel_server.py",
        "hero_ref/tools/ahmad_tk_overlay.py",
        "hero_ref/tools/detail_composition_view.py",
        "src/bidking_lab/data/item_category_authority.py",
        "src/bidking_lab/inference/mean_multiset_support.py",
        "src/bidking_lab/inference/size_avg_evidence.py",
        "src/bidking_lab/inference/v2.py",
        "src/bidking_lab/live/fatbeans.py",
        "src/bidking_lab/live/live_refresh_signatures.py",
        "src/bidking_lab/live/monitor.py",
        "src/bidking_lab/extract/item_table.py",
        "src/bidking_lab/runtime/snapshot.py",
        "data/raw/tables/BidMap.txt",
        "data/raw/tables/Drop.txt",
        "data/raw/tables/Item.txt",
        "data/processed/drop_weights_all_maps_0627.json",
        "data/processed/item_category_authority_0_3_2.json",
        "data/processed/items_droppable.json",
        "data/processed/items.json",
        "external_references/AuctionAnalyzer4.13.3/_decompiled/MapBidCalculator/"
        "MapBidCalculator/Models/StaticData.cs",
    )
    actual = tuple(
        path.relative_to(ROOT).as_posix()
        for path in clean_set.TRUTH_GATE_CONTEXT_PATHS
    )
    assert len(parent_order) == 26
    assert actual == parent_order[:6] + (layout_relative,) + parent_order[6:]

    gate = tmp_path / "sample_truth_gate.py"
    engine = tmp_path / "ahmad_ref_engine.py"
    layout = tmp_path / "layout_depth_policy.py"
    registry = tmp_path / "sample_pool_registry.json"
    gate.write_text("gate-v2\n", encoding="utf-8")
    engine.write_text("engine-v2\n", encoding="utf-8")
    layout.write_text("layout-v1\n", encoding="utf-8")
    monkeypatch.setattr(clean_set, "ROOT", tmp_path)
    monkeypatch.setattr(clean_set, "TRUTH_GATE_SOURCE", gate)
    monkeypatch.setattr(clean_set, "TRUTH_GATE_REQUIRED_RAW_SHA256", {})
    monkeypatch.setattr(clean_set, "TRUTH_REGISTRY", registry)

    parent_paths = (gate, engine)
    source_paths = (gate, engine, layout)
    monkeypatch.setattr(clean_set, "TRUTH_GATE_CONTEXT_PATHS", parent_paths)
    parent_context_before = clean_set._truth_gate_context_sha256()
    monkeypatch.setattr(clean_set, "TRUTH_GATE_CONTEXT_PATHS", source_paths)
    source_context_before = clean_set._truth_gate_context_sha256()

    layout.write_text("layout-v2\n", encoding="utf-8")
    monkeypatch.setattr(clean_set, "TRUTH_GATE_CONTEXT_PATHS", parent_paths)
    assert clean_set._truth_gate_context_sha256() == parent_context_before
    registry.write_text(
        json.dumps(
            {
                "schema_version": clean_set.TRUTH_REGISTRY_SCHEMA_VERSION,
                "gate_code_sha256": clean_set._truth_gate_code_sha256(),
                "gate_context_sha256": parent_context_before,
                "samples": [],
            }
        ),
        encoding="utf-8",
    )
    assert clean_set._load_truth_registry() == {}

    monkeypatch.setattr(clean_set, "TRUTH_GATE_CONTEXT_PATHS", source_paths)
    assert clean_set._truth_gate_context_sha256() != source_context_before
    registry.write_text(
        json.dumps(
            {
                "schema_version": clean_set.TRUTH_REGISTRY_SCHEMA_VERSION,
                "gate_code_sha256": clean_set._truth_gate_code_sha256(),
                "gate_context_sha256": source_context_before,
                "samples": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        clean_set.SampleTruthRegistryError,
        match="parser/engine/table context has changed",
    ):
        clean_set._load_truth_registry()


@pytest.mark.parametrize(
    "relative",
    (
        "hero_ref/tools/ahmad_live_panel_server.py",
        "hero_ref/tools/detail_composition_view.py",
        "src/bidking_lab/live/live_refresh_signatures.py",
    ),
)
def test_hotfix1_transitive_context_change_invalidates_registry(
    relative: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import build_clean_eval_set as clean_set

    gate = tmp_path / "sample_truth_gate.py"
    context = tmp_path / relative
    registry = tmp_path / "sample_pool_registry.json"
    gate.write_text("gate-v2\n", encoding="utf-8")
    context.parent.mkdir(parents=True, exist_ok=True)
    context.write_text("current-context\n", encoding="utf-8")
    monkeypatch.setattr(clean_set, "ROOT", tmp_path)
    monkeypatch.setattr(clean_set, "TRUTH_GATE_SOURCE", gate)
    monkeypatch.setattr(clean_set, "TRUTH_GATE_CONTEXT_PATHS", (context,))
    monkeypatch.setattr(clean_set, "TRUTH_GATE_REQUIRED_RAW_SHA256", {})
    monkeypatch.setattr(clean_set, "TRUTH_REGISTRY", registry)
    registry.write_text(
        json.dumps(
            {
                "schema_version": clean_set.TRUTH_REGISTRY_SCHEMA_VERSION,
                "gate_code_sha256": clean_set._truth_gate_code_sha256(),
                "gate_context_sha256": clean_set._truth_gate_context_sha256(),
                "samples": [],
            }
        ),
        encoding="utf-8",
    )

    assert clean_set._load_truth_registry() == {}
    context.write_text("changed-context\n", encoding="utf-8")
    with pytest.raises(
        clean_set.SampleTruthRegistryError,
        match="parser/engine/table context has changed",
    ):
        clean_set._load_truth_registry()


def test_tracked_e07_artifacts_are_materialized_as_lf() -> None:
    attributes = {
        line.strip()
        for line in (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    }
    required = {
        "data/sample_manifests/sample_pool_registry.json text eol=lf",
        "data/sample_manifests/sample_capability_summary_20260715.json text eol=lf",
    }
    assert required <= attributes
    for rule in required:
        relative = rule.removesuffix(" text eol=lf")
        assert b"\r\n" not in (ROOT / relative).read_bytes()


def test_required_static_data_context_identity_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import build_clean_eval_set as clean_set

    static_data = tmp_path / "StaticData.cs"
    expected = "7543b4aa16addc73dfaaaf3c6df9bc0f6662bd83458de110a16d04ff9f2f9676"
    monkeypatch.setattr(clean_set, "ROOT", tmp_path)
    monkeypatch.setattr(clean_set, "TRUTH_GATE_CONTEXT_PATHS", (static_data,))
    monkeypatch.setattr(
        clean_set,
        "TRUTH_GATE_REQUIRED_RAW_SHA256",
        {static_data: expected},
    )

    with pytest.raises(clean_set.SampleTruthRegistryError, match="context is missing"):
        clean_set._truth_gate_context_sha256()

    static_data.write_bytes(b"wrong StaticData")
    with pytest.raises(clean_set.SampleTruthRegistryError, match="identity mismatch"):
        clean_set._truth_gate_context_sha256()


def test_old_registry_context_identity_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import build_clean_eval_set as clean_set

    gate = tmp_path / "sample_truth_gate.py"
    context = tmp_path / "engine.py"
    registry = tmp_path / "sample_pool_registry.json"
    gate.write_text("gate-v2\n", encoding="utf-8")
    context.write_text("engine-v2\n", encoding="utf-8")
    registry.write_text(
        json.dumps(
            {
                "schema_version": clean_set.TRUTH_REGISTRY_SCHEMA_VERSION,
                "gate_code_sha256": clean_set._sha256_normalized_text_file(gate),
                "gate_context_sha256": "0" * 64,
                "samples": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(clean_set, "ROOT", tmp_path)
    monkeypatch.setattr(clean_set, "TRUTH_GATE_SOURCE", gate)
    monkeypatch.setattr(clean_set, "TRUTH_GATE_CONTEXT_PATHS", (gate, context))
    monkeypatch.setattr(clean_set, "TRUTH_GATE_REQUIRED_RAW_SHA256", {})
    monkeypatch.setattr(clean_set, "TRUTH_REGISTRY", registry)

    with pytest.raises(
        clean_set.SampleTruthRegistryError,
        match="parser/engine/table context has changed",
    ):
        clean_set._load_truth_registry()


def test_clean_era_has_no_upper_date_bound_for_normal_maps() -> None:
    import build_clean_eval_set as clean_set

    assert clean_set._is_clean_era_map((2026, 7, 15, 23, 59, 59), 5601)
    assert clean_set._is_clean_era_map((2026, 7, 15, 23, 59, 59), 2501)


def test_clean_era_still_excludes_old_and_activity_maps() -> None:
    import build_clean_eval_set as clean_set

    assert not clean_set._is_clean_era_map((2026, 6, 25, 11, 59, 59), 5601)
    assert not clean_set._is_clean_era_map((2026, 7, 15, 23, 59, 59), 2511)
    assert not clean_set._is_clean_era_map((2026, 7, 17, 0, 0, 0), 2541)
    assert not clean_set._is_clean_era_map((2026, 7, 17, 0, 0, 0), 4550)
    assert not clean_set._is_clean_era_map(None, 5601)


def test_desktop_ingest_routes_activity_generations_to_distinct_pools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ingest_desktop_samples as ingest

    main = tmp_path / "main"
    activity_0702 = tmp_path / "activity_0702"
    activity_0716 = tmp_path / "activity_0716"
    monkeypatch.setattr(ingest, "MAIN_POOL", main)
    monkeypatch.setattr(ingest, "ACTIVITY_POOL", activity_0702)
    monkeypatch.setattr(ingest, "RED_CRATE_ACTIVITY_POOL", activity_0716)

    assert ingest._destination_for_map(2501) == (main, "accept_main")
    assert ingest._destination_for_map(2511) == (activity_0702, "accept_activity")
    assert ingest._destination_for_map(2541) == (
        activity_0716,
        "accept_red_crate_activity",
    )
    assert ingest._destination_for_map(4550) == (
        activity_0716,
        "accept_red_crate_activity",
    )
    assert ingest._destination_for_map(2551) == (main, "accept_main")


def test_desktop_ingest_routes_truth_only_by_the_same_activity_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ingest_desktop_samples as ingest

    normal = tmp_path / "truth" / "normal"
    activity_0702 = tmp_path / "truth" / "activity_0702"
    activity_0716 = tmp_path / "truth" / "activity_0716"
    monkeypatch.setattr(ingest, "TRUTH_ONLY_MAIN_POOL", normal)
    monkeypatch.setattr(ingest, "TRUTH_ONLY_ACTIVITY_POOL", activity_0702)
    monkeypatch.setattr(ingest, "TRUTH_ONLY_RED_CRATE_ACTIVITY_POOL", activity_0716)

    assert ingest._truth_only_destination_for_map(2501) == (normal, "accept_truth_only")
    assert ingest._truth_only_destination_for_map(2511) == (
        activity_0702,
        "accept_activity_truth_only",
    )
    assert ingest._truth_only_destination_for_map(2541) == (
        activity_0716,
        "accept_red_crate_activity_truth_only",
    )
    assert ingest._truth_only_destination_for_map(4550) == (
        activity_0716,
        "accept_red_crate_activity_truth_only",
    )


def test_desktop_ingest_truth_valid_workflow_incomplete_is_not_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ingest_desktop_samples as ingest

    sid = "1234567890123456"
    source = tmp_path / f"npcap_live_2026-07-17_010203_2501_{sid}_reset.json"
    source.write_text("[]", encoding="utf-8")
    truth_pool = tmp_path / "truth" / "normal"
    monkeypatch.setattr(ingest, "ROOT", tmp_path)
    monkeypatch.setattr(ingest, "TRUTH_ONLY_MAIN_POOL", truth_pool)
    truth_valid_record = {
        "source": source.name,
        "session_id": sid,
        "session_token": f"2501:{sid}",
        "hero": "aisha",
        "map_id": 2501,
        "rounds": 3,
        "bytes": source.stat().st_size,
        "state_count": 4,
        "ready_windows": 0,
        "claims_present": 2,
        "verdict": "INVALID",
        "reason": "workflow_invalid_no_bid",
        "reasons": ["workflow_invalid_no_bid"],
        "replay_verdict": "INVALID",
        "replay_reasons": ["workflow_invalid_no_bid"],
        "truth_verdict": "valid",
        "truth_reasons": [],
        "truth": {"collectible_count": 41, "total_cells": 134, "total_value": 900000},
        "truth_signature": {"collectible_count": 41, "total_cells": 134},
    }
    monkeypatch.setattr(
        ingest,
        "_candidate_record",
        lambda _candidate, *, tables: (truth_valid_record, []),
    )

    decision, chosen, rows = ingest._choose_session(
        sid,
        [ingest.Candidate("fixture", source, sid)],
        tables=None,
    )

    assert decision["decision"] == "accept_truth_only"
    assert decision["destination"].startswith("truth/normal/fatbeans_truth_aisha_2501_")
    assert decision["selected"]["replay_verdict"] == "INVALID"
    assert decision["selected"]["truth_verdict"] == "valid"
    assert chosen is not None
    assert rows == []

    truth_invalid_record = {
        **truth_valid_record,
        "truth_verdict": "INVALID",
        "truth_reason": "count_mismatch",
        "truth_reasons": ["count_mismatch"],
        "reasons": ["workflow_invalid_no_bid", "count_mismatch"],
    }
    monkeypatch.setattr(
        ingest,
        "_candidate_record",
        lambda _candidate, *, tables: (truth_invalid_record, []),
    )
    rejected, _, _ = ingest._choose_session(
        sid,
        [ingest.Candidate("fixture", source, sid)],
        tables=None,
    )
    assert rejected["decision"] == "reject"


def test_truth_only_registry_metadata_is_truth_eligible_but_not_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import build_sample_truth_registry as registry

    truth_root = tmp_path / "data" / "samples" / "fatbeans_truth_only_settlement"
    normal = truth_root / "normal"
    activity_0702 = truth_root / "activity_20260702_shipwreck"
    activity_0716 = truth_root / "activity_20260716_red_crate_shipwreck"
    for directory in (normal, activity_0702, activity_0716):
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(registry, "ROOT", tmp_path)
    monkeypatch.setattr(registry, "TRUTH_ONLY_MAIN_POOL", normal)
    monkeypatch.setattr(registry, "TRUTH_ONLY_ACTIVITY_POOL", activity_0702)
    monkeypatch.setattr(registry, "TRUTH_ONLY_RED_CRATE_ACTIVITY_POOL", activity_0716)

    expected_pools = (
        (normal, "truth_only_settlement"),
        (activity_0702, "truth_only_activity_shipwreck"),
        (activity_0716, "truth_only_activity_red_crate_shipwreck"),
    )
    for directory, pool in expected_pools:
        path = directory / "fatbeans_truth_aisha_2501_3rounds_2501_1234567890123456_3456.json"
        path.write_text("[]", encoding="utf-8")
        metadata = registry._path_metadata(path)
        assert metadata["pool"] == pool
        assert metadata["active"] is False
        assert metadata["replay_eligible"] is False
        assert metadata["truth_eligible"] is True


def test_capture_date_fallback_reads_bom_packet_time(tmp_path: Path) -> None:
    import build_clean_eval_set as clean_set

    capture = tmp_path / "capture.json"
    capture.write_text(
        json.dumps(
            [
                {"CaptureTime": "2026/06/26 12:34:57.999"},
                {"CaptureTime": "2026-06-26T12:34:56.123"},
            ]
        ),
        encoding="utf-8-sig",
    )

    assert clean_set._capture_date_from_path(capture) == "2026-06-26 12:34:56"


def test_unregistered_clean_candidate_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import build_clean_eval_set as clean_set

    candidate = tmp_path / "fatbeans_valid_ahmed_2501_1rounds_2501_1234567890123456_0001.json"
    candidate.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(clean_set, "candidate_eval_files", lambda _hero="ahmed": [str(candidate)])
    monkeypatch.setattr(clean_set, "_load_truth_registry", lambda: {})
    with pytest.raises(clean_set.SampleTruthRegistryError, match="unregistered=1"):
        clean_set.clean_eval_files("ahmed")


def test_registered_invalid_clean_candidate_is_excluded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import build_clean_eval_set as clean_set

    candidate = tmp_path / "fatbeans_valid_aisha_2501_1rounds_2501_1234567890123456_0001.json"
    candidate.write_text("[]", encoding="utf-8")
    registry = {
        candidate.name: {
            "file": candidate.name,
            "sha256": clean_set._sha256_file(candidate),
            "verdict": "INVALID",
        }
    }
    monkeypatch.setattr(clean_set, "candidate_eval_files", lambda _hero="aisha": [str(candidate)])
    monkeypatch.setattr(clean_set, "_load_truth_registry", lambda: registry)
    assert clean_set.clean_eval_files("aisha") == []


def test_truth_prior_selector_keeps_only_eligible_records(tmp_path: Path) -> None:
    import build_clean_eval_set as clean_set

    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "records": [
                    {"session_hash": "a", "hero": "raven", "truth_eligible": True},
                    {"session_hash": "b", "hero": "raven", "truth_eligible": False},
                    {"session_hash": "c", "hero": "gabriela", "truth_eligible": True},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert [row["session_hash"] for row in clean_set.truth_prior_records(catalog)] == ["a", "c"]
    assert [row["session_hash"] for row in clean_set.truth_prior_records(catalog, hero="raven")] == ["a"]


def test_selection_metadata_distinguishes_main_activity_and_unknown_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import build_clean_eval_set as clean_set

    main = tmp_path / "main"
    activity = tmp_path / "activity"
    red_crate_activity = tmp_path / "red_crate_activity"
    main.mkdir()
    activity.mkdir()
    red_crate_activity.mkdir()
    main_file = main / "fatbeans_valid_aisha_2501_1rounds_2501_1234567890123456_0001.json"
    activity_file = activity / "fatbeans_valid_raven_2511_1rounds_2511_1234567890123457_0002.json"
    red_crate_file = red_crate_activity / "fatbeans_valid_aisha_2541_1rounds_2541_1234567890123458_0003.json"
    external_file = tmp_path / "external.json"
    for path in (main_file, activity_file, red_crate_file, external_file):
        path.write_text("[]", encoding="utf-8")
    registry = {
        main_file.name: {"hero": "aisha", "capture_date": "2026-07-01 01:02:03"},
        activity_file.name: {"hero": "raven", "capture_date": "2026-07-03 04:05:06"},
        red_crate_file.name: {"hero": "aisha", "capture_date": "2026-07-17 01:02:03"},
    }
    monkeypatch.setattr(clean_set, "MAIN_POOL", main)
    monkeypatch.setattr(clean_set, "ACTIVITY_POOL", activity)
    monkeypatch.setattr(clean_set, "RED_CRATE_ACTIVITY_POOL", red_crate_activity)
    monkeypatch.setattr(clean_set, "_load_truth_registry", lambda: registry)
    monkeypatch.setattr(clean_set, "_repo_head", lambda: "deadbeef")

    metadata = clean_set.sample_selection_metadata(
        (main_file, activity_file, red_crate_file, external_file), selector="explicit_paths"
    )

    assert metadata == {
        "selector": "explicit_paths",
        "sources": {
            "explicit_external_or_other": 1,
            "local_activity_20260702_raw": 1,
            "local_activity_20260716_red_crate_raw": 1,
            "local_main_raw": 1,
        },
        "sample_count": 4,
        "registered_count": 3,
        "capture_time_min": "2026-07-01 01:02:03",
        "capture_time_max": "2026-07-17 01:02:03",
        "unknown_time_count": 1,
        "commit": "deadbeef",
        "activity": False,
        "activity_count": 2,
        "heroes": {"aisha": 2, "raven": 1},
    }


@pytest.mark.skipif(not FATBEANS.is_dir(), reason="样本库不在(gitignored, 本地才有)")
def test_every_pool_sample_is_registered_valid() -> None:
    """不变量 ①: 每个 active raw 样本都必须登记在案、内容未漂移且判为 valid.

    这是"自动加样本"的安全底座: 新样本【必须】走 refresh_sample_pool.py 的质量门才能进池,
    直接往目录里丢文件是进不来的 —— 因为它没登记, 这条会红.
    """
    assert REGISTRY.is_file(), (
        "严格样本 registry 缺失；运行 "
        "python scripts/build_sample_truth_registry.py --write --full"
    )
    from build_clean_eval_set import _load_truth_registry, _sha256_file
    from build_sample_truth_registry import ACTIVITY_POOL, RED_CRATE_ACTIVITY_POOL

    by_name = _load_truth_registry()
    active = sorted(
        {
            *FATBEANS.glob("fatbeans_valid_*.json"),
            *FATBEANS.glob("fatbeans_mixed_*.json"),
            *ACTIVITY_POOL.glob("fatbeans_valid_*.json"),
            *RED_CRATE_ACTIVITY_POOL.glob("fatbeans_valid_*.json"),
        }
    )
    unregistered = [p.name for p in active if p.name not in by_name]
    stale = [
        p.name
        for p in active
        if p.name in by_name and by_name[p.name].get("sha256") != _sha256_file(p)
    ]
    assert not unregistered, (
        f"{len(unregistered)} 个候选未登记: {unregistered[:5]}；"
        "重建 sample truth registry"
    )
    assert not stale, f"{len(stale)} 个候选内容已漂移: {stale[:5]}；重建 registry"
    invalid = [
        p.name
        for p in active
        if p.name in by_name and by_name[p.name].get("verdict") != "valid"
    ]
    assert not invalid, (
        f"{len(invalid)} 个 active raw 样本仍被判 INVALID: {invalid[:5]}；"
        "先运行严格隔离工具，再重建 registry"
    )

    pool = _pool_files()
    if not pool:
        pytest.skip("池子为空(样本未恢复?)")
    bad = [p.name for p in pool if by_name[p.name]["verdict"] != "valid"]
    assert not bad, (
        f"{len(bad)} 个【被判为 INVALID】的样本仍在池子里:\n"
        f"  {[(n, by_name[n]['reason']) for n in bad[:5]]}\n"
        "  修: 重建 registry 并检查 clean_eval_files 的 registry 过滤"
    )


@pytest.mark.skipif(not FATBEANS.is_dir(), reason="样本库不在")
def test_truth_only_samples_are_registered_valid_and_excluded_from_replay() -> None:
    """结算 truth-only 可进先验，但绝不能混入 active replay 或 clean-eval。"""
    from build_clean_eval_set import _load_truth_registry, _sha256_file
    from build_sample_truth_registry import (
        TRUTH_ONLY_ACTIVITY_POOL,
        TRUTH_ONLY_MAIN_POOL,
        TRUTH_ONLY_RED_CRATE_ACTIVITY_POOL,
    )

    truth_only = sorted(
        path
        for directory in (
            TRUTH_ONLY_MAIN_POOL,
            TRUTH_ONLY_ACTIVITY_POOL,
            TRUTH_ONLY_RED_CRATE_ACTIVITY_POOL,
        )
        for path in directory.glob("fatbeans_truth_*.json")
    )
    if not truth_only:
        pytest.skip("尚未恢复历史 truth-only 样本")

    by_name = _load_truth_registry()
    replay_names = {path.name for path in _pool_files()}
    problems = []
    for path in truth_only:
        row = by_name.get(path.name)
        if (
            row is None
            or row.get("sha256") != _sha256_file(path)
            or row.get("verdict") != "valid"
            or row.get("replay_eligible") is not False
            or row.get("truth_eligible") is not True
            or path.name in replay_names
        ):
            problems.append(path.name)
    assert not problems, f"truth-only 资格或隔离错误: {problems[:5]}"


@pytest.mark.skipif(not FATBEANS.is_dir(), reason="样本库不在")
def test_clean_eval_manifests_match_the_live_selector() -> None:
    """Tracked manifests must not keep pre-ingest paths or omit newly admitted samples."""
    from build_clean_eval_set import clean_eval_files

    drift: list[str] = []
    for hero in HEROES:
        manifest = MANIFESTS / f"clean_eval_{hero}.json"
        if not manifest.is_file():
            drift.append(f"{hero}: manifest missing")
            continue
        stored = json.loads(manifest.read_text(encoding="utf-8"))
        current = [
            Path(path).resolve().relative_to(ROOT).as_posix()
            for path in clean_eval_files(hero)
        ]
        if stored != current:
            drift.append(f"{hero}: manifest={len(stored)} selector={len(current)}")
    assert not drift, (
        "clean-eval manifests are stale: "
        + "; ".join(drift)
        + "; rerun `python scripts/build_clean_eval_set.py <hero>`"
    )


@pytest.mark.skipif(not FATBEANS.is_dir(), reason="样本库不在")
def test_every_quarantine_sample_is_registered_and_excluded() -> None:
    """隔离不是黑洞：真值阳性和 mixed/provenance 隔离都按内容哈希登记。"""
    from build_clean_eval_set import _load_truth_registry, _sha256_file

    by_name = _load_truth_registry()
    quarantine = sorted(
        path
        for directory in FATBEANS.iterdir()
        if directory.is_dir()
        and (
            directory.name.startswith("ZZZ_INVALID_DO_NOT_USE")
            or directory.name.startswith("_quarantine")
        )
        for path in directory.glob("fatbeans_*.json")
    )
    assert quarantine, "没有隔离阳性样本"
    missing = [path.name for path in quarantine if path.name not in by_name]
    stale = [
        path.name
        for path in quarantine
        if path.name in by_name and by_name[path.name].get("sha256") != _sha256_file(path)
    ]
    truth_positive = [path for path in quarantine if by_name.get(path.name, {}).get("verdict") != "valid"]
    structural = [path for path in quarantine if by_name.get(path.name, {}).get("verdict") == "valid"]
    assert not missing, f"隔离阳性未登记: {missing[:5]}"
    assert not stale, f"隔离阳性内容漂移: {stale[:5]}"
    assert truth_positive, "隔离区没有可触发 truth gate 的阳性"
    assert structural, "历史 mixed/provenance 隔离口径意外消失"


@pytest.mark.skipif(not FATBEANS.is_dir(), reason="样本库不在")
def test_known_clean_pollution_is_registered_invalid_and_excluded() -> None:
    """2026-07-15 独立审计发现的 12 个反例必须留作 selector 阳性对照。"""
    assert REGISTRY.is_file(), "sample truth registry 缺失"
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    by_name = {r["file"]: r for r in reg["samples"]}
    missing = sorted(KNOWN_CLEAN_POLLUTION - by_name.keys())
    wrongly_valid = sorted(
        name
        for name in KNOWN_CLEAN_POLLUTION
        if name in by_name and by_name[name].get("verdict") == "valid"
    )
    pool_names = {path.name for path in _pool_files()}
    leaked = sorted(KNOWN_CLEAN_POLLUTION & pool_names)
    assert not missing, f"12 份历史污染样本有 {len(missing)} 份未登记: {missing}"
    assert not wrongly_valid, f"历史污染样本被误判 valid: {wrongly_valid}"
    assert not leaked, f"历史污染样本仍进入 clean pool: {leaked}"


@pytest.mark.skipif(not FATBEANS.is_dir(), reason="样本库不在")
def test_excluded_samples_never_reach_the_pool() -> None:
    """不变量 ②: 排除清单里的样本, 一个都不许出现在池子里.

    这是第三层防线 —— 即使有人把无效样本挪回根目录、改回 `valid` 名字, 排除清单仍按【文件名】拦它。
    """
    if not EXCLUDE.exists():
        pytest.skip("排除清单不存在")
    excluded = {Path(r["file"]).name for r in json.loads(EXCLUDE.read_text(encoding="utf-8"))
                if isinstance(r, dict) and r.get("file")}
    leaked = [p.name for p in _pool_files() if p.name in excluded]
    assert not leaked, (
        f"{len(leaked)} 个【排除清单里的坏样本】漏进了池子: {leaked[:5]}\n"
        f"  clean_eval_files 的排除逻辑被绕过了 —— 这会让调参朝错误方向走。"
    )


@pytest.mark.skipif(not FATBEANS.is_dir(), reason="样本库不在")
def test_quarantine_is_never_globbed_into_the_pool() -> None:
    """不变量 ③: 隔离区里的样本一个都不许被扫进池子.

    `clean_eval_files` 用的是【非递归】glob, 所以隔离区(子目录)天然扫不到。
    但如果有人把 glob 改成递归、或把隔离样本挪回根目录, 这条会立刻喊。
    """
    quar = [d for d in FATBEANS.glob("ZZZ_INVALID_DO_NOT_USE*") if d.is_dir()]
    if not quar:
        pytest.skip("还没有隔离区")
    quar_names = {p.name for d in quar for p in d.glob("*.json")}
    if not quar_names:
        pytest.skip("隔离区是空的")
    leaked = [p.name for p in _pool_files() if p.name in quar_names]
    assert not leaked, f"隔离区的样本被扫进了池子: {leaked[:5]}"


@pytest.mark.skipif(not FATBEANS.is_dir(), reason="样本库不在")
def test_quarantine_positive_controls_still_fail_truth_gate() -> None:
    """known-fail 必须触发 checker，防止 registry/gate 变成统一放行的坏仪器。"""
    manifests = sorted(MANIFESTS.glob("desktop_ingest_*.json"))
    manifests.extend(sorted(MANIFESTS.glob("archive_ingest_*.json")))
    rows = []
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        rows.extend(manifest.get("quarantine_positive_controls") or [])
    paths = [ROOT / row["destination"] for row in rows]
    missing = [str(path.relative_to(ROOT)) for path in paths if not path.is_file()]
    assert rows, "ingest manifests 没有 quarantine positive controls"
    assert not missing, f"{len(missing)} 个 known-fail 文件缺失: {missing[:5]}"

    from bidking_lab.live.fatbeans import parse_fatbeans_capture
    from bidking_lab.live.monitor import load_monitor_tables
    from sample_truth_gate import validate_events

    tables = load_monitor_tables()
    wrongly_valid = []
    for path in paths:
        result = validate_events(parse_fatbeans_capture(path), tables=tables)
        if result["verdict"] == "valid":
            wrongly_valid.append(path.name)
    assert not wrongly_valid, (
        f"{len(wrongly_valid)} 个 known-fail 未被 checker 抓住: {wrongly_valid[:5]}"
    )


@pytest.mark.skipif(not FATBEANS.is_dir(), reason="样本库不在")
def test_spot_check_pool_samples_still_pass_the_gate() -> None:
    """分英雄抽检 pool GOOD，现场重跑当前严格 truth gate。

    为什么需要它: 登记表是【缓存】, 可能过期 —— 例如解析器修好后(BUG-E `34ea6f1`),
    旧判决就失效了。抽检是对登记表本身的对照, 防止"登记表说好, 其实已经不好了"。
    """
    from bidking_lab.live.fatbeans import parse_fatbeans_capture
    from build_clean_eval_set import clean_eval_files
    from sample_truth_gate import validate_events

    random.seed(20260712)
    failures = []
    checked = 0
    for hero in HEROES:
        paths = [Path(path) for path in clean_eval_files(hero)]
        if not paths:
            continue
        for p in random.sample(paths, min(SPOT_CHECK_PER_HERO, len(paths))):
            rec = validate_events(parse_fatbeans_capture(p))
            checked += 1
            if rec["verdict"] != "valid":
                failures.append((p.name, rec["reason"]))
    assert checked > 0, "没有可抽检的 clean 样本"
    assert not failures, (
        f"抽检发现池内有【现在判为无效】的样本 —— 登记表已过期:\n"
        f"  {failures}\n"
        "  修: python scripts/build_sample_truth_registry.py --write --full"
    )
