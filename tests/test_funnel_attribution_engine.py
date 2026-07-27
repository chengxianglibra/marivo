"""Pure funnel loss-rate attribution tests without a session."""

from __future__ import annotations

import pandas as pd
import pytest

from marivo.analysis.frames.attribution import (
    FUNNEL_ATTRIBUTION_COLUMNS,
    FunnelAttributionFrameMeta,
    FunnelAttributionReconciliation,
)
from marivo.analysis.intents._funnel_attribution import decompose_loss_rate


def _components() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "channel": "paid",
                "current_lost_count": 30,
                "current_resolved_entry_count": 50,
                "baseline_lost_count": 10,
                "baseline_resolved_entry_count": 50,
            },
            {
                "channel": "organic",
                "current_lost_count": 10,
                "current_resolved_entry_count": 50,
                "baseline_lost_count": 14,
                "baseline_resolved_entry_count": 30,
            },
        ]
    )


def test_funnel_attribution_contract_and_reconciliation_guard() -> None:
    assert FUNNEL_ATTRIBUTION_COLUMNS[0] == "contribution_kind"
    assert "target" in FunnelAttributionFrameMeta.model_fields
    assert "target_step_key" not in FunnelAttributionFrameMeta.model_fields
    with pytest.raises(ValueError, match="residual"):
        FunnelAttributionReconciliation(
            target_loss_rate_delta=0.1,
            contribution_sum=0.2,
            positive_pool=0.2,
            negative_pool=0.0,
            residual=-0.1,
            max_abs_residual=0.1,
        )


def test_contributions_reconcile_exactly_and_emit_both_components() -> None:
    result = decompose_loss_rate(components=_components(), axis_columns=("channel",))
    assert result.total_delta == pytest.approx(0.1)
    assert result.rows["contribution"].sum() == pytest.approx(result.total_delta)
    assert abs(result.residual) <= 1e-9
    assert set(result.rows["contribution_kind"]) == {"loss", "denominator_mix"}
    assert len(result.rows) == 4


def test_pool_shares_use_explicit_sign_denominators() -> None:
    result = decompose_loss_rate(components=_components(), axis_columns=("channel",))
    positive = result.rows[result.rows["contribution"] > 0]
    negative = result.rows[result.rows["contribution"] < 0]
    assert positive["share_of_positive_pool"].sum() == pytest.approx(1.0)
    assert negative["share_of_negative_pool"].sum() == pytest.approx(1.0)
    assert positive["share_of_negative_pool"].isna().all()
