import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_settlement_payload_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_settlement_payload_audit",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _field_varint(field_no: int, value: int) -> bytes:
    return _varint((field_no << 3) | 0) + _varint(value)


def _field_bytes(field_no: int, value: bytes) -> bytes:
    return _varint((field_no << 3) | 2) + _varint(len(value)) + value


def _item(runtime_id: int, item_id: int, *, quality: int = 4, cells: int = 2) -> bytes:
    return b"".join(
        (
            _field_varint(1, runtime_id),
            _field_varint(2, item_id),
            *(_field_bytes(4, b"\x08\x01") for _ in range(cells)),
            _field_varint(9, quality),
        )
    )


def test_inventory_block_metrics_counts_slots_candidates_and_duplicates() -> None:
    module = _load_module()
    item = _item(101, 1001001, cells=2)
    occupied_slot = b"".join(
        (
            _field_varint(1, 7),
            _field_bytes(2, b"\x08\x01"),
            _field_bytes(3, item),
        )
    )
    duplicate_slot = _field_bytes(3, item)
    empty_slot = _field_bytes(2, b"\x10\x01")
    block = b"".join(
        (
            _field_varint(1, 1),
            _field_bytes(3, occupied_slot),
            _field_bytes(3, duplicate_slot),
            _field_bytes(3, empty_slot),
        )
    )

    result = module._inventory_block_metrics(block)

    assert result["inventory_slot_count"] == 3
    assert result["occupied_slot_count"] == 2
    assert result["raw_item_candidate_count"] == 2
    assert result["raw_duplicate_runtime_item_pair_count"] == 1
    assert result["occupied_slot_field_shapes"] == {
        "1:0:i,2:2:b,3:2:b": 1,
        "3:2:b": 1,
    }
    assert result["empty_slot_field_shapes"] == {"2:2:b": 1}
    assert result["occupied_slot_int_field_counts"] == {"1": 1}
    assert result["empty_slot_int_field_counts"] == {}
    assert result["occupied_slot_int_value_counts"] == {"1=7": 1}
    assert result["candidate_path_counts"] == {"3": 2}
    assert result["item_field_signatures"]


def test_settlement_wrapper_metrics_profiles_outer_fields() -> None:
    module = _load_module()
    body = b"".join(
        (
            _field_varint(1, 123),
            _field_bytes(2, b"payload"),
            _field_varint(3, 456),
            _field_varint(4, 456),
            _field_varint(5, 99),
            _field_bytes(6, b"left"),
            _field_bytes(6, b"right"),
        )
    )

    result = module._settlement_wrapper_metrics(body)

    assert result["settlement_outer_field_shape"] == (
        "1:0:ix1,2:2:bx1,3:0:ix1,4:0:ix1,5:0:ix1,6:2:bx2"
    )
    assert result["settlement_outer_field3_present"] is True
    assert result["settlement_outer_field4_present"] is True
    assert result["settlement_outer_field5_present"] is True
    assert result["settlement_outer_field3_values"] == (456,)
    assert result["settlement_outer_field4_values"] == (456,)
    assert result["settlement_outer_field5_values"] == (99,)
    assert result["settlement_outer_field6_count"] == 2
