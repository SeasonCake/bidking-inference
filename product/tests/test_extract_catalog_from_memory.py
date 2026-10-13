from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from extract_catalog_from_memory import (  # noqa: E402
    _localized_names_from_tsv,
    _select_record_candidate,
)


def test_record_candidate_requires_repeated_item_object_offsets() -> None:
    assert _select_record_candidate(
        [
            (44, 1, 3_276_848),  # unrelated single-offset false positive
            (36, 6, 320_000),
            (28, 6, 320_000),
        ]
    ) == (6, 320_000)


def test_record_candidate_rejects_single_offset_and_ambiguous_pairs() -> None:
    assert _select_record_candidate([(32, 4, 7_667_779)]) is None
    assert (
        _select_record_candidate(
            [
                (28, 5, 100_000),
                (36, 5, 100_000),
                (28, 6, 200_000),
                (36, 6, 200_000),
            ]
        )
        is None
    )


def test_localized_names_use_exact_item_key_and_unique_simplified_name(
    tmp_path: Path,
) -> None:
    table = tmp_path / "Language.tsv"
    table.write_text(
        "itemName_1004\t专家万用物品箱\t专家万用物品箱\tExpert Box\n"
        "itemName_1004\t专家万用物品箱\t专家万用物品箱\tExpert Box\n"
        "itemName_1005\t名称甲\n"
        "itemName_1005\t名称乙\n"
        "mail_text_1\titemName_1004\n",
        encoding="utf-8",
    )

    assert _localized_names_from_tsv(table) == {"1004": "专家万用物品箱"}
