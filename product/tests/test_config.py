from pathlib import Path

from bidking_lab.config import get_game_root, project_root


def test_project_root_exists():
    root = project_root()
    assert (root / "pyproject.toml").is_file()


def test_project_root_can_use_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("BIDKING_PROJECT_ROOT", str(tmp_path))

    assert project_root() == tmp_path


def test_game_root_optional():
    # May be None on CI / machines without game — smoke only
    r = get_game_root()
    assert r is None or (isinstance(r, Path) and r.name == "BidKing")
