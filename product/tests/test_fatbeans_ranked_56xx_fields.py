"""Focused tests for the ranked-mode (56xx) rank-point fields.

Covers the additive, read-only ranked fields wired into ``fatbeans.py`` on
2026-06-25 (per ``data/reports/ranked_56xx_field_map.md``):

- Session-start ``0x21`` player block: ``rank_points`` (f16 float32), ``rank_tier``
  (f17 varint), ``rank_group`` (f18 varint).
- Settlement ``0x2d`` top-level ``f7``/``f8``: ``FatbeansRankScoreChange`` with
  before/delta/after (sub f1/f2/f3 float32).

Guard rails:
- New fields parse to the exact report values on real 56xx ranked samples.
- Old non-ranked maps (``*2401*``) leave every new field at ``None``/``()`` —
  proving zero impact on the non-ranked path.
- The new fields do NOT leak into ``live_batches_from_fatbeans_*`` output (they
  are not consumed by any value/count/grid/bid inference).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bidking_lab.live.fatbeans import (
    FatbeansRankScoreChange,
    live_batches_from_fatbeans_capture,
    parse_fatbeans_capture,
)

SAMPLE_ROOT = Path(__file__).resolve().parents[1] / "data" / "samples"
RANKED_56XX_DIR = SAMPLE_ROOT / "group_0625" / "56xx"
FATBEANS_DIR = SAMPLE_ROOT / "fatbeans"

# A 5608 ranked sample whose exact rank values are documented in the field-map
# report (§2a player 448050988650068 = 1187.0/tier5/group1; §2b f8=1187/-30/1157,
# f7=1248/-120/1128).
RANKED_5608_SAMPLE = (
    RANKED_56XX_DIR
    / "5608"
    / "npcap_live_2026-06-25_155700_5608_1530451467858177_reset.json"
)


def _player_rank_snapshots(events) -> dict[int, tuple[float | None, int | None, int | None]]:
    snapshots: dict[int, tuple[float | None, int | None, int | None]] = {}
    for state in events.states:
        if state.message_id != 0x0021:
            continue
        for bid in state.bids:
            snapshots[bid.player_id] = (
                bid.rank_points,
                bid.rank_tier,
                bid.rank_group,
            )
    return snapshots


@pytest.mark.skipif(
    not RANKED_5608_SAMPLE.exists(), reason="ranked 5608 sample not present"
)
def test_ranked_5608_player_block_rank_snapshot_matches_report() -> None:
    events = parse_fatbeans_capture(RANKED_5608_SAMPLE)
    snapshots = _player_rank_snapshots(events)

    # Report §2a: this player's pre-match snapshot.
    assert snapshots[448050988650068] == (1187.0, 5, 1)
    # All four ranked players carry the three fields (report §1a negative/positive).
    assert len(snapshots) == 4
    for rank_points, rank_tier, rank_group in snapshots.values():
        assert rank_points is not None
        assert rank_tier is not None
        assert rank_group is not None
        assert 1000.0 <= rank_points <= 1300.0  # observed 1064-1265 band
        assert rank_tier in (4, 5, 6, 7)
        assert rank_group in (1, 2)


@pytest.mark.skipif(
    not RANKED_5608_SAMPLE.exists(), reason="ranked 5608 sample not present"
)
def test_ranked_5608_settlement_rank_score_changes_match_report() -> None:
    events = parse_fatbeans_capture(RANKED_5608_SAMPLE)
    changes: list[FatbeansRankScoreChange] = []
    for state in events.states:
        changes.extend(state.rank_score_changes)

    by_slot = {c.slot: c for c in changes}
    # Report §2b: f8 = 1187 / -30 / 1157 ; f7 = 1248 / -120 / 1128.
    assert by_slot[8] == FatbeansRankScoreChange(slot=8, before=1187.0, delta=-30.0, after=1157.0)
    assert by_slot[7] == FatbeansRankScoreChange(slot=7, before=1248.0, delta=-120.0, after=1128.0)

    # Internal invariant for every parsed block: before + delta == after.
    for change in changes:
        assert change.before + change.delta == pytest.approx(change.after)

    # The settlement-frame before-score links back to that player's 0x21 snapshot
    # (report's f16 ↔ f8.f1 cross-check): 1187.0 appears in both paths.
    pre_match_points = {
        rp for rp, _t, _g in _player_rank_snapshots(events).values() if rp is not None
    }
    assert by_slot[8].before in pre_match_points


def _ranked_samples(limit: int = 2) -> list[Path]:
    found: list[Path] = []
    for path in sorted(RANKED_56XX_DIR.glob("56*/*reset.json")):
        found.append(path)
        if len(found) >= limit:
            break
    return found


def test_at_least_two_ranked_samples_expose_rank_fields() -> None:
    samples = _ranked_samples(limit=2)
    if len(samples) < 2:
        pytest.skip("fewer than 2 ranked reset samples present")
    for path in samples:
        events = parse_fatbeans_capture(path)
        player_rank_seen = any(
            bid.rank_points is not None
            for state in events.states
            if state.message_id == 0x0021
            for bid in state.bids
        )
        settle_rank_seen = any(state.rank_score_changes for state in events.states)
        assert player_rank_seen, f"no player rank_points in {path.name}"
        assert settle_rank_seen, f"no settlement rank_score_changes in {path.name}"


def _old_map_samples(limit: int = 3) -> list[Path]:
    return sorted(FATBEANS_DIR.glob("*2401*.json"))[:limit]


def test_old_non_ranked_maps_have_no_rank_fields() -> None:
    samples = _old_map_samples(limit=3)
    if not samples:
        pytest.skip("no 2401 old-map samples present")
    for path in samples:
        events = parse_fatbeans_capture(path)
        for state in events.states:
            assert state.rank_score_changes == ()
            for bid in state.bids:
                assert bid.rank_points is None
                assert bid.rank_tier is None
                assert bid.rank_group is None


@pytest.mark.skipif(
    not RANKED_5608_SAMPLE.exists(), reason="ranked 5608 sample not present"
)
def test_rank_fields_do_not_leak_into_live_batches() -> None:
    # The ranked fields are read-only metadata; they must not surface as any
    # field_update path or grid item attribute consumed by inference.
    batches = live_batches_from_fatbeans_capture(RANKED_5608_SAMPLE)
    for batch in batches:
        for update in batch.field_updates:
            joined = "/".join(str(part) for part in update.path)
            assert "rank" not in joined.lower()
        for item in batch.grid_items:
            assert not hasattr(item, "rank_points")
