import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from bidking_lab.live.fatbeans import FatbeansCaptureEvents

ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    path = ROOT / "scripts" / "rename_manual_fatbeans_samples.py"
    spec = importlib.util.spec_from_file_location("rename_manual_fatbeans_samples", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_plan_renames_manual_exports_with_metadata(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    source = tmp_path / "fatbeans_export.json"
    source.write_text("[]", encoding="utf-8")
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(
            SimpleNamespace(kind="bid", session_id="2506:abc", value=100),
            SimpleNamespace(kind="bid", session_id="2506:abc", value=200),
        ),
        states=(
            SimpleNamespace(sort_id=1, session_id="2506:abc", map_id=2506, round_index=1),
            SimpleNamespace(sort_id=2, session_id="2506:abc", map_id=2506, round_index=2),
        ),
        statuses=(),
    )

    monkeypatch.setattr(module, "parse_fatbeans_capture", lambda path: events)
    monkeypatch.setattr(module, "_hero_from_events", lambda parsed: "aisha")

    plans = module.build_rename_plan(tmp_path)

    assert len(plans) == 1
    assert plans[0].status == "rename"
    assert plans[0].destination is not None
    assert plans[0].destination.name == "manual_2026-06-04_001_aisha_2506_2rounds_2506_abc.json"


def test_build_plan_skips_already_named_files(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    source = tmp_path / "manual_2026-06-04_001_aisha_2506_5rounds.json"
    source.write_text("[]", encoding="utf-8")

    plans = module.build_rename_plan(tmp_path)

    assert len(plans) == 1
    assert plans[0].status == "skip"
    assert plans[0].reason == "already_named"


def test_build_plan_renames_placeholder_names(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    source = tmp_path / "manual_2026-06-04_001_hero_map_rounds.json"
    source.write_text("[]", encoding="utf-8")
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(SimpleNamespace(kind="bid", session_id="2401:abc", value=100),),
        states=(SimpleNamespace(sort_id=1, session_id="2401:abc", map_id=2401, round_index=1),),
        statuses=(),
    )

    monkeypatch.setattr(module, "parse_fatbeans_capture", lambda path: events)
    monkeypatch.setattr(module, "_hero_from_events", lambda parsed: "ethan")

    plans = module.build_rename_plan(tmp_path)

    assert plans[0].status == "rename"
    assert plans[0].destination is not None
    assert plans[0].destination.name == "manual_2026-06-04_001_ethan_2401_1rounds_2401_abc.json"


def test_apply_plan_renumber_all_uses_two_phase_rename(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    first = tmp_path / "manual_2026-06-04_001_hero_map_rounds.json"
    second = tmp_path / "manual_2026-06-04_001_aisha_2401_1rounds_2401_old.json"
    first.write_text("[]", encoding="utf-8")
    second.write_text("[]", encoding="utf-8")
    os.utime(first, (1_000, 1_000))
    os.utime(second, (2_000, 2_000))
    first_events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(SimpleNamespace(kind="bid", session_id="2506:new", value=100),),
        states=(SimpleNamespace(sort_id=1, session_id="2506:new", map_id=2506, round_index=1),),
        statuses=(),
    )
    second_events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(SimpleNamespace(kind="bid", session_id="2401:old", value=100),),
        states=(SimpleNamespace(sort_id=1, session_id="2401:old", map_id=2401, round_index=1),),
        statuses=(),
    )

    def parse(path):
        return first_events if Path(path).name == first.name else second_events

    def hero(events):
        return "ethan" if events is first_events else "aisha"

    monkeypatch.setattr(module, "parse_fatbeans_capture", parse)
    monkeypatch.setattr(module, "_hero_from_events", hero)

    plans = module.build_rename_plan(tmp_path, renumber_all=True)
    module.apply_plan(plans)

    assert not first.exists()
    assert not second.exists()
    assert (tmp_path / "manual_2026-06-04_001_ethan_2506_1rounds_2506_new.json").exists()
    assert (tmp_path / "manual_2026-06-04_002_aisha_2401_1rounds_2401_old.json").exists()


def test_build_plan_reports_parse_errors(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    source = tmp_path / "bad.json"
    source.write_text("{", encoding="utf-8")

    def fail(path):
        raise ValueError("bad export")

    monkeypatch.setattr(module, "parse_fatbeans_capture", fail)

    plans = module.build_rename_plan(tmp_path)

    assert len(plans) == 1
    assert plans[0].status == "error"
    assert plans[0].reason == "ValueError"
