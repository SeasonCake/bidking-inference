from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.parse_dropmap_tsv import (
    main,
    parse_dropmap_tsv,
    parse_rankmap_memory_tsv,
)


def _row(map_id: str = "2101") -> str:
    return "\t".join(
        [
            map_id,
            "未知快递",
            "说明",
            "[[11,15,1000],[16,20,1000]]",
            "[[101,50],[102,100]]",
            "[[1000,2000,200],[2001,5000,20]]",
            "[10,1,100,5]",
        ]
    )


def test_parse_rankmap_accepts_utf8_bom_and_preserves_fields(tmp_path: Path) -> None:
    source = tmp_path / "RankMap.tsv"
    source.write_text("\ufeff" + _row() + "\n", encoding="utf-8")

    parsed = parse_rankmap_memory_tsv(source)

    assert list(parsed) == ["2101"]
    assert parsed["2101"] == {
        "name": "未知快递",
        "range39": [[11, 15, 1000], [16, 20, 1000]],
        "area_w": [[101, 50], [102, 100]],
        "value_dist": [[1000, 2000, 200], [2001, 5000, 20]],
        "col6": [10, 1, 100, 5],
    }


def test_parse_rankmap_allows_ranked_rows_without_display_text(tmp_path: Path) -> None:
    source = tmp_path / "RankMap.tsv"
    source.write_text(_row("5601").replace("未知快递\t说明", "\t") + "\n", encoding="utf-8")

    parsed = parse_rankmap_memory_tsv(source)

    assert parsed["5601"]["name"] == ""


@pytest.mark.parametrize(
    "contents, message",
    [
        (_row() + "\n" + _row() + "\n", "duplicate map id"),
        (_row().replace("[10,1,100,5]", "[10,1,100]") + "\n", "value/weight pairs"),
        (_row().replace("[[101,50],[102,100]]", "[[101,50],[101,100]]") + "\n", "duplicate area_w id"),
        (_row().replace("[[1000,2000,200],[2001,5000,20]]", "[[1000,2000,200],[1999,5000,20]]") + "\n", "overlap"),
    ],
)
def test_parse_dropmap_rejects_ambiguous_or_invalid_rows(
    tmp_path: Path, contents: str, message: str
) -> None:
    source = tmp_path / "RankMap.tsv"
    source.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        parse_dropmap_tsv(source)


def test_cli_refuses_to_overwrite_existing_candidate(tmp_path: Path) -> None:
    source = tmp_path / "RankMap.tsv"
    output = tmp_path / "candidate.json"
    source.write_text(_row() + "\n", encoding="utf-8")
    output.write_text(json.dumps({"sentinel": True}), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        main(["parse_dropmap_tsv.py", str(source), "--out", str(output)])

    assert exc.value.code == 2
    assert json.loads(output.read_text(encoding="utf-8")) == {"sentinel": True}


def test_cli_rejects_legacy_mislabeled_dropmap_filename(tmp_path: Path) -> None:
    source = tmp_path / "DropMap.tsv"
    source.write_text(_row() + "\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        main(["parse_dropmap_tsv.py", str(source), "--out", str(tmp_path / "out.json")])

    assert exc.value.code == 2
    assert not (tmp_path / "out.json").exists()


def test_legacy_function_name_is_explicitly_rankmap_compatible(tmp_path: Path) -> None:
    source = tmp_path / "RankMap.tsv"
    source.write_text(_row() + "\n", encoding="utf-8")

    assert parse_dropmap_tsv(source) == parse_rankmap_memory_tsv(source)
