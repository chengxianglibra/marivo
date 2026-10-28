"""Closed terminal Finding values, scalar coordinates and bounded pages."""

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from marivo.analysis.errors import AnalysisError
from marivo.analysis.evidence._dataset_types import (
    AssociationFindingValueV1,
    AssociationLagV1,
    EvidencePage,
    Finding,
    FindingPage,
    ForecastPointFindingValueV1,
    NoAssociationLagV1,
    OutputCoordinate,
    OutputEvidence,
)
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.refs import ArtifactRef


def _finding(
    value: AssociationFindingValueV1 | ForecastPointFindingValueV1,
    coordinates: tuple[OutputCoordinate, ...] = (),
) -> Finding:
    return Finding(
        finding_id="finding-1",
        artifact_ref=ArtifactRef(ref="artifact-1"),
        session_id="session-1",
        value=value,
        coordinates=coordinates,
        committed_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )


def test_terminal_finding_keeps_exact_scalar_coordinate_types(
    capsys: pytest.CaptureFixture[str],
) -> None:
    values = (None, True, 2**80, -0.0, Decimal("1.2300"), date(2026, 9, 8), "CN")
    finding = _finding(
        ForecastPointFindingValueV1(
            model="naive@v1",
            interval_level=Decimal("0.95"),
            horizon_ordinal=1,
            forecast_value=Decimal("12.30"),
            interval_lower=Decimal("10.00"),
            interval_upper=Decimal("14.00"),
            training_row_count=5,
        ),
        tuple(OutputCoordinate(f"key.{index}", value) for index, value in enumerate(values)),
    )

    assert tuple(type(item.value) for item in finding.coordinates) == tuple(map(type, values))
    assert tuple(item.value for item in finding.coordinates) == values
    assert "Finding ref=finding-1" in repr(finding)
    assert "show()" in repr(finding)
    assert finding.show() is None
    assert "forecast_value" in capsys.readouterr().out
    with pytest.raises(FrozenInstanceError):
        finding.session_id = "other"  # type: ignore[misc]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), Decimal("NaN")])
def test_terminal_values_reject_non_finite_numbers(value: float | Decimal) -> None:
    with pytest.raises(AnalysisError, match="finite output scalar"):
        OutputCoordinate("value", value)


def test_association_finding_owns_pair_counts_and_lag_shape() -> None:
    value = AssociationFindingValueV1(
        method="pearson",
        coefficient=0.8,
        input_observation_count=11,
        null_pair_count=1,
        complete_pair_count=10,
        lag=AssociationLagV1(
            lag_offset=1,
            selected_for_pair=True,
            matched_observation_count=9,
            lag_boundary_drop_count=1,
        ),
    )
    finding = _finding(value)
    assert finding.finding_type == "association"

    with pytest.raises(IntegrityError, match="pair counts"):
        AssociationFindingValueV1(
            method="pearson",
            coefficient=0.8,
            input_observation_count=2,
            null_pair_count=1,
            complete_pair_count=2,
            lag=NoAssociationLagV1(),
        )
    with pytest.raises(IntegrityError, match="coefficient"):
        AssociationFindingValueV1(
            method="pearson",
            coefficient=1.1,
            input_observation_count=2,
            null_pair_count=0,
            complete_pair_count=2,
            lag=NoAssociationLagV1(),
        )


def test_record_pages_preserve_bounds_and_commit_identity() -> None:
    finding = _finding(
        ForecastPointFindingValueV1(
            model="naive@v1",
            interval_level=0.95,
            horizon_ordinal=1,
            forecast_value=12.0,
            interval_lower=10.0,
            interval_upper=14.0,
            training_row_count=20,
        ),
        (OutputCoordinate("key.0", "CN"),),
    )
    page = FindingPage(
        items=(finding,),
        limit=1,
        has_more=True,
        next_cursor="next",
        input_id="input-1",
        session_id="session-1",
        artifact_ref=ArtifactRef(ref="artifact-1"),
        total_count=2,
    )
    assert page.items == (finding,)
    assert page.next_cursor == "next"
    assert "FindingPage artifact=artifact-1 items=1" in repr(page)

    with pytest.raises(IntegrityError):
        FindingPage(
            items=(object(),),
            limit=1,
            has_more=False,
            next_cursor=None,
            input_id="input-1",
            session_id="session-1",
            artifact_ref=ArtifactRef(ref="artifact-1"),
            total_count=2,
        )  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="has_more"):
        FindingPage(
            items=(),
            limit=1,
            has_more=True,
            next_cursor=None,
            input_id="input-1",
            session_id="session-1",
            artifact_ref=ArtifactRef(ref="artifact-1"),
            total_count=2,
        )

    evidence_page = EvidencePage(
        input_id="input-1",
        session_id="session-1",
        artifact_ref=ArtifactRef(ref="artifact-1"),
        total_count=1,
        limit=20,
        has_more=False,
        next_cursor=None,
        items=(
            OutputEvidence(kind="read", summary="value", values=(("amount", Decimal("2.50")),)),
        ),
    )
    assert evidence_page.total_count == 1
    assert evidence_page.items[0].values == (("amount", Decimal("2.50")),)
    assert not hasattr(evidence_page, "evidence_digest")
