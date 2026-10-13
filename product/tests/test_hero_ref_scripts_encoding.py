from pathlib import Path


def test_hero_ref_powershell_scripts_are_utf8_bom_encoded() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    roots = [
        repo_root / "apps" / "hero_ref",
        repo_root / "hero_ref",
    ]
    bom = b"\xef\xbb\xbf"
    scripts: list[Path] = []
    for root in roots:
        scripts.extend(sorted(root.glob("*.ps1")))
    assert scripts, "expected PowerShell scripts to check"
    for path in scripts:
        assert path.read_bytes().startswith(bom), f"{path} is missing UTF-8 BOM"


def test_hero_ref_non_ascii_batch_files_are_cmd_safe() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    batch_files = sorted((repo_root / "apps" / "hero_ref").glob("*.bat"))
    assert batch_files, "expected batch launchers to check"

    checked = 0
    for path in batch_files:
        data = path.read_bytes()
        if not any(byte >= 0x80 for byte in data):
            continue
        checked += 1
        assert not data.startswith(b"\xef\xbb\xbf"), f"{path} must not use UTF-8 BOM"
        assert b"\r\n" in data, f"{path} must use CRLF line endings for cmd.exe"
        assert b"\n" not in data.replace(b"\r\n", b""), f"{path} has mixed or LF-only line endings"

    assert checked, "expected at least one non-ASCII batch launcher"
