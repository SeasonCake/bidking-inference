from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from bidking_lab.live.fatbeans import (
    FatbeansActionResult,
    FatbeansCaptureEvents,
    FatbeansSendEvent,
    FatbeansStateEvent,
)
from bidking_lab.live.monitor import _action_result_rows


SESSION = "2601:9000000000000027"
OTHER_SESSION = "2602:9000000000000028"
ACTION_ID = 100110
ITEMS = {ACTION_ID: SimpleNamespace(name="普品均格")}


def _events(
    *,
    send_session: Any = SESSION,
    ack_session: Any = SESSION,
    send_sort: int = 10,
    ack_sort: int = 11,
    ack_message_id: int = 0x0027,
    ack_action_id: Any = ACTION_ID,
    ack_result: int | float | None = None,
    ack_result_field: int | None = None,
    later_target_state: bool = False,
) -> FatbeansCaptureEvents:
    states = [
        FatbeansStateEvent(
            sort_id=ack_sort,
            capture_time="2026-07-27 21:40:22.379",
            message_id=ack_message_id,
            session_id=ack_session,
            map_id=2601,
            round_index=2,
            action_results=(
                FatbeansActionResult(
                    action_id=ack_action_id,
                    result=ack_result,
                    result_field=ack_result_field,
                ),
            ),
        )
    ]
    if later_target_state:
        states.append(
            FatbeansStateEvent(
                sort_id=max(send_sort, ack_sort) + 10,
                capture_time="2026-07-27 21:40:23.000",
                message_id=0x0021,
                session_id=SESSION,
                map_id=2601,
                round_index=2,
            )
        )
    return FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(
            FatbeansSendEvent(
                sort_id=send_sort,
                capture_time="2026-07-27 21:40:22.216",
                message_id=0x0026,
                session_id=send_session,
                value=ACTION_ID,
            ),
        ),
        statuses=(),
        states=tuple(states),
    )


def _rows(events: FatbeansCaptureEvents, *, target_session: Any = SESSION) -> list[dict]:
    return _action_result_rows(
        events,
        ITEMS,
        session_id=target_session,
    )


def _assert_no_inferred_zero(rows: list[dict]) -> None:
    assert not any(
        row.get("result") == 0 and row.get("inferred_zero") is True
        for row in rows
    )


def test_valid_same_session_same_action_later_empty_ack_still_infers_zero() -> None:
    rows = _rows(_events())

    assert rows == [
        {
            "sort": 10,
            "time": "2026-07-27 21:40:22.216",
            "action_id": ACTION_ID,
            "tool": "普品均格",
            "result": 0,
            "result_field": None,
            "inferred_zero": True,
            "result_inference": "sent_action_without_result_after_later_state",
            "revealed_items": 0,
            "revealed_items_detail": [],
            "revealed_summary": "",
        }
    ]


@pytest.mark.parametrize("target_session", [None, "", "bad", True])
def test_missing_or_malformed_target_session_never_infers_zero(
    target_session: Any,
) -> None:
    _assert_no_inferred_zero(_rows(_events(), target_session=target_session))


@pytest.mark.parametrize("send_session", [None, "", "bad", True])
def test_missing_or_malformed_send_session_never_infers_zero(
    send_session: Any,
) -> None:
    _assert_no_inferred_zero(_rows(_events(send_session=send_session)))


@pytest.mark.parametrize("ack_session", [None, "", "bad", True])
def test_missing_or_malformed_ack_session_never_infers_zero(
    ack_session: Any,
) -> None:
    _assert_no_inferred_zero(_rows(_events(ack_session=ack_session)))


def test_all_matching_but_malformed_sessions_never_infer_zero() -> None:
    _assert_no_inferred_zero(
        _rows(
            _events(send_session="bad", ack_session="bad"),
            target_session="bad",
        )
    )


def test_cross_session_ack_cannot_borrow_a_later_target_state() -> None:
    rows = _rows(
        _events(
            ack_session=OTHER_SESSION,
            later_target_state=True,
        )
    )

    _assert_no_inferred_zero(rows)


def test_reordered_empty_ack_cannot_borrow_a_later_target_state() -> None:
    rows = _rows(
        _events(
            send_sort=12,
            ack_sort=11,
            later_target_state=True,
        )
    )

    _assert_no_inferred_zero(rows)


def test_missing_empty_ack_cannot_use_an_unrelated_later_state() -> None:
    events = _events(later_target_state=True)
    events = FatbeansCaptureEvents(
        packets=events.packets,
        frames=events.frames,
        sends=events.sends,
        statuses=events.statuses,
        states=events.states[1:],
    )

    _assert_no_inferred_zero(_rows(events))


def test_wrong_action_empty_ack_cannot_use_an_unrelated_later_state() -> None:
    rows = _rows(
        _events(
            ack_action_id=100111,
            later_target_state=True,
        )
    )

    _assert_no_inferred_zero(rows)


@pytest.mark.parametrize(
    ("ack_message_id", "ack_result", "ack_result_field"),
    [
        (0x0021, None, None),
        (0x0027, None, 11),
    ],
)
def test_non_ack_or_malformed_empty_result_never_infers_zero(
    ack_message_id: int,
    ack_result: int | None,
    ack_result_field: int | None,
) -> None:
    rows = _rows(
        _events(
            ack_message_id=ack_message_id,
            ack_result=ack_result,
            ack_result_field=ack_result_field,
        )
    )

    _assert_no_inferred_zero(rows)


def test_normal_nonzero_response_remains_authoritative() -> None:
    rows = _rows(
        _events(
            ack_result=14,
            ack_result_field=11,
        )
    )

    assert rows[0]["result"] == 14
    assert "inferred_zero" not in rows[0]
