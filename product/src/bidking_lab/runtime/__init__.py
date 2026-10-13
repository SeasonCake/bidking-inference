"""Runtime-facing snapshot contracts.

This package is the boundary between inference/live parsing and future UI
frontends such as Streamlit, a desktop overlay, or a compact floating panel.
"""

from bidking_lab.runtime.snapshot import (
    ImportOverviewSnapshot,
    LayoutStageSnapshot,
    TacticalPanelSnapshot,
    TacticalSummaryRow,
    TacticalSnapshot,
    action_result_rows_from_results,
    import_overview_from_summary,
    layout_replay_rows_from_stages,
    packet_action_rows_from_sends,
    player_bid_candidate_rows_from_bids,
    tactical_panel_from_rows,
    tactical_summary_rows,
    tactical_snapshot_from_rows,
    ui_contract_from_artifact,
)

__all__ = (
    "ImportOverviewSnapshot",
    "LayoutStageSnapshot",
    "TacticalPanelSnapshot",
    "TacticalSummaryRow",
    "TacticalSnapshot",
    "action_result_rows_from_results",
    "import_overview_from_summary",
    "layout_replay_rows_from_stages",
    "packet_action_rows_from_sends",
    "player_bid_candidate_rows_from_bids",
    "tactical_panel_from_rows",
    "tactical_summary_rows",
    "tactical_snapshot_from_rows",
    "ui_contract_from_artifact",
)
