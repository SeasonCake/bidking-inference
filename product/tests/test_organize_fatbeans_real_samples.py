import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from bidking_lab.live.fatbeans import FatbeansCaptureEvents

ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    path = ROOT / "scripts" / "organize_fatbeans_real_samples.py"
    spec = importlib.util.spec_from_file_location("organize_fatbeans_real_samples", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _events(session_id: str, *, map_id: int = 2401) -> FatbeansCaptureEvents:
    state = SimpleNamespace(
        sort_id=5,
        session_id=session_id,
        round_index=1,
        map_id=map_id,
        bids=(),
        public_infos=(),
        action_results=(),
        skill_reveals=(),
        inventory_items=(),
    )
    return FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(SimpleNamespace(sort_id=10, kind="bid", session_id=session_id, value=1000),),
        states=(state,),
        statuses=(),
    )


def test_plan_dedupes_sessions_and_copies_live_complete(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    archive = tmp_path / "archive"
    inbox = tmp_path / "inbox"
    live = tmp_path / "live"
    invalid = tmp_path / "invalid"
    archive.mkdir()
    inbox.mkdir()
    live.mkdir()
    main = archive / "old.json"
    duplicate = live / "dup.json"
    new_live = live / "new.json"
    main.write_text("main", encoding="utf-8")
    duplicate.write_text("duplicate", encoding="utf-8")
    new_live.write_text("new", encoding="utf-8")

    def parse(path):
        if Path(path).name == "new.json":
            return _events("2501:new", map_id=2501)
        return _events("2401:dup", map_id=2401)

    monkeypatch.setattr(module, "parse_fatbeans_capture", parse)
    monkeypatch.setattr(module, "_hero_from_events", lambda events: "ethan")

    plan = module.build_plan(
        [archive, live],
        archive_dir=archive,
        inbox_dir=inbox,
        live_dir=live,
        invalid_dir=invalid,
    )

    assert plan["summary"]["input_files"] == 3
    assert plan["summary"]["unique_files"] == 2
    assert plan["summary"]["duplicates"] == 1
    assert plan["summary"]["copy"] == 1
    assert plan["summary"]["skip_duplicate"] == 1


def test_plan_keeps_existing_archive_name_when_new_samples_shift_order(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    archive = tmp_path / "archive"
    inbox = tmp_path / "inbox"
    live = tmp_path / "live"
    invalid = tmp_path / "invalid"
    archive.mkdir()
    inbox.mkdir()
    live.mkdir()
    existing = (
        archive
        / "fatbeans_valid_ethan_2501_1rounds_2501_z_0001.json"
    )
    incoming = inbox / "manual_new.json"
    existing.write_text("existing", encoding="utf-8")
    incoming.write_text("incoming", encoding="utf-8")

    def parse(path):
        if Path(path).name == "manual_new.json":
            return _events("2401:a", map_id=2401)
        return _events("2501:z", map_id=2501)

    def hero(events):
        session_id = events.sends[0].session_id
        return "aisha" if session_id == "2401:a" else "ethan"

    monkeypatch.setattr(module, "parse_fatbeans_capture", parse)
    monkeypatch.setattr(module, "_hero_from_events", hero)

    plan = module.build_plan(
        [archive, inbox],
        archive_dir=archive,
        inbox_dir=inbox,
        live_dir=live,
        invalid_dir=invalid,
    )

    existing_action = next(
        action for action in plan["actions"] if action["source"].endswith(existing.name)
    )
    assert existing_action["action"] == "keep"
    assert existing_action["destination"].endswith(existing.name)
    assert plan["summary"]["move"] == 1


def test_plan_classifies_multi_session_capture_as_mixed(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    archive = tmp_path / "archive"
    inbox = tmp_path / "inbox"
    live = tmp_path / "live"
    invalid = tmp_path / "invalid"
    archive.mkdir()
    inbox.mkdir()
    live.mkdir()
    source = inbox / "multi.json"
    source.write_text("multi", encoding="utf-8")
    state_a = SimpleNamespace(
        sort_id=5,
        session_id="2401:a",
        round_index=1,
        map_id=2401,
        bids=(),
        public_infos=(),
        action_results=(),
        skill_reveals=(),
        inventory_items=(),
    )
    state_b = SimpleNamespace(
        sort_id=6,
        session_id="2402:b",
        round_index=1,
        map_id=2402,
        bids=(),
        public_infos=(),
        action_results=(),
        skill_reveals=(),
        inventory_items=(),
    )
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(SimpleNamespace(sort_id=10, kind="bid", session_id="2401:a", value=1000),),
        states=(state_a, state_b),
        statuses=(),
    )

    monkeypatch.setattr(module, "parse_fatbeans_capture", lambda path: events)
    monkeypatch.setattr(module, "_hero_from_events", lambda events: "aisha")

    plan = module.build_plan(
        [inbox],
        archive_dir=archive,
        inbox_dir=inbox,
        live_dir=live,
        invalid_dir=invalid,
    )

    action = plan["actions"][0]
    assert action["sample_class"] == "mixed"
    assert Path(action["destination"]).name.startswith("fatbeans_mixed_aisha_")


def test_plan_keeps_prior_invalid_filename_quarantined(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    archive = tmp_path / "archive"
    inbox = tmp_path / "inbox"
    live = tmp_path / "live"
    invalid = tmp_path / "invalid"
    archive.mkdir()
    inbox.mkdir()
    live.mkdir()
    source = archive / "fatbeans_invalid_parse_error_recovered.json"
    source.write_text("recovered", encoding="utf-8")

    monkeypatch.setattr(module, "parse_fatbeans_capture", lambda path: _events("2401:a"))
    monkeypatch.setattr(module, "_hero_from_events", lambda events: "aisha")

    plan = module.build_plan(
        [archive],
        archive_dir=archive,
        inbox_dir=inbox,
        live_dir=live,
        invalid_dir=invalid,
    )

    action = plan["actions"][0]
    assert action["sample_class"] == "invalid_quarantined_sample"
    assert action["action"] == "move"
    assert Path(action["destination"]).parts[-2] == "quarantined_sample"


def test_apply_moves_parse_errors_to_invalid_and_keeps_json(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    archive = tmp_path / "archive"
    inbox = tmp_path / "inbox"
    live = tmp_path / "live"
    invalid = tmp_path / "invalid"
    archive.mkdir()
    inbox.mkdir()
    live.mkdir()
    bad = archive / "bad.json"
    bad.write_text("{", encoding="utf-8")

    def parse(path):
        raise ValueError("bad")

    monkeypatch.setattr(module, "parse_fatbeans_capture", parse)

    plan = module.build_plan(
        [archive],
        archive_dir=archive,
        inbox_dir=inbox,
        live_dir=live,
        invalid_dir=invalid,
    )
    module.apply_plan(plan)

    assert not bad.exists()
    moved = list(invalid.rglob("*.json"))
    assert len(moved) == 1
    assert moved[0].read_text(encoding="utf-8") == "{"
