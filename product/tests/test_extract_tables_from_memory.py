from __future__ import annotations

from scripts.extract_tables_from_memory import _looks_like_table


def _bidmap_row() -> bytes:
    fields = ["2101", "未知快递"] + [f"field_{index}" for index in range(2, 23)]
    return ("\ufeff" + "\t".join(fields) + "\n").encode("utf-8")


def test_bidmap_shape_rejects_localization_block() -> None:
    localization = (
        "bid_map_N_2101\t未知快递\tUnknown Delivery\n"
        "itemName_1004\t专家万用物品箱\tExpert Box\n"
    ).encode("utf-8")

    assert _looks_like_table("BidMap", _bidmap_row())
    assert not _looks_like_table("BidMap", localization)
    assert _looks_like_table("Language", localization)


def test_rankmap_shape_requires_exact_seven_column_semantics() -> None:
    valid = (
        "2101\tname\tdesc\t[[11,15,1000]]\t[[101,50]]\t"
        "[[1000,2000,20]]\t[10,1,100,5]\n"
    ).encode("utf-8")
    wrong_columns = "2101\tname\tdesc\t[]\n".encode("utf-8")
    wrong_id = (
        "bid_map_N_2101\tname\tdesc\t[[11,15,1000]]\t[[101,50]]\t"
        "[[1000,2000,20]]\t[10,1,100,5]\n"
    ).encode("utf-8")
    duplicate_area = valid.replace(b"[[101,50]]", b"[[101,50],[101,20]]")
    boolean_weight = valid.replace(b"[[101,50]]", b"[[101,true]]")

    assert _looks_like_table("RankMap", valid)
    assert not _looks_like_table("DropMap", valid)
    assert not _looks_like_table("RankMap", wrong_columns)
    assert not _looks_like_table("RankMap", wrong_id)
    assert not _looks_like_table("RankMap", duplicate_area)
    assert not _looks_like_table("RankMap", boolean_weight)
