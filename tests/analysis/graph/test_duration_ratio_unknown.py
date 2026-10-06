"""Public retained Duration quotients preserve their original Unknown Cells."""

import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Literal

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError, StatisticalRelationError
from marivo.analysis.methods.physical import ScalarType
from marivo.datasource.adapters import SourceSession
from tests.analysis.lifecycle.lifecycle_fixtures import END, START, build_lifecycle_public


@pytest.mark.runtime
@pytest.mark.parametrize("backend", ["duckdb", "sqlite"])
def test_public_duration_ratio_preserves_unknown(
    backend: Literal["duckdb", "sqlite"], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    rows = [
        (0, "started", 0, 1),
        (0, "paid", 5, 2),
        (0, "finished", 10, 3),
        (1, "started", 0, 1),
        (1, "paid", 5, 2),
        (2, "started", 0, 1),
        (2, "paid", 0, 2),
        (2, "finished", 0, 3),
    ]
    build_lifecycle_public(tmp_path, backend_name=backend, rows=rows)
    session = mv.session.get_or_create("unknown-duration-ratio", report_timezone="UTC")
    beginning = mv.step(
        participant=ms.participant_role(event=ms.ref.event("commerce.started"), name="subject"),
        key="start",
    )
    ending = mv.step(
        participant=ms.participant_role(event=ms.ref.event("commerce.finished"), name="subject"),
        key="end",
    )
    middle = mv.step(
        participant=ms.participant_role(event=ms.ref.event("commerce.paid"), name="subject"),
        key="paid",
    )
    journeys = session.events.match(
        mv.sequence(beginning, middle, ending),
        population=session.members(ms.ref.entity("commerce.subjects")),
        cohort_window=mv.time_scope(start=START, end=START + timedelta(seconds=1)),
        completion_through=END,
        matching=mv.first_per_subject(),
        business_order=ms.ref.business_order("commerce.order"),
    ).execute()

    def forbid(*args: object, **kwargs: object) -> None:
        raise AssertionError("Fixed Duration quotient accessed its source")

    monkeypatch.setattr(SourceSession, "batches", forbid)
    observed = journeys.time_to_event(from_step=beginning, to_step=ending).execute()
    durations = observed.observed_duration.execute()
    original = durations.to_pandas()
    assert original.value.iloc[0] == timedelta(seconds=10)
    assert original.value.iloc[2] == timedelta(0)
    assert original.value.iloc[1:2].isna().all()
    ratio = durations.ratio(durations).execute()
    assert ratio._node.root.value_type == ScalarType("float64")
    frame = ratio.to_pandas()
    assert frame.cell_tag.tolist() == ["defined", "unknown", "undefined"]
    assert frame.cell_reason.tolist() == [None, "coverage_censored", "zero_denominator"]
    assert frame.value.iloc[0] == 1.0
    assert frame.value.iloc[1:].isna().all()
    assert ratio._dataset is not None
    parts = {part.role: part.table for part in ratio._dataset.verified().parts}
    assert {"subject", "current_endpoint", "baseline_endpoint", "correspondence"} <= parts.keys()
    for role in ("current_endpoint", "baseline_endpoint"):
        assert parts[role][role + "__cell_tag"].to_pylist() == ["defined", "unknown", "defined"]
        assert parts[role][role + "__cell_reason"].to_pylist() == [None, "coverage_censored", None]
    assert durations.ratio(durations).execute().state.artifact_ref == ratio.state.artifact_ref
    with pytest.raises(AnalysisError):
        ratio.ratio(ratio).execute()
    fits: list[str] = []
    for method in ("zscore", "mad"):
        fit = ratio.deviation(method=method).execute()
        scores = fit.score.to_pandas()
        assert scores.cell_tag.tolist() == ["undefined", "unknown", "undefined"]
        assert scores.cell_reason.tolist() == [
            "insufficient_samples",
            "coverage_censored",
            "zero_denominator",
        ]
        assert fit._dataset is not None
        fit._dataset.verified()
        fits.append(method)
    shorter = journeys.time_to_event(from_step=beginning, to_step=middle).execute()
    short_duration = shorter.observed_duration.execute()
    other = short_duration.ratio(short_duration).execute()
    refusals: list[str] = []
    association_methods: tuple[Literal["pearson", "spearman", "kendall"], ...] = (
        "pearson",
        "spearman",
        "kendall",
    )
    for method in association_methods:
        before = set(tmp_path.rglob("*.parquet"))
        with pytest.raises(StatisticalRelationError) as refused:
            ratio.correlate(other, method=method).execute()
        assert refused.value.code == "r8.cell_policy"
        assert refused.value.received == repr(("unknown", "coverage_censored"))
        assert set(tmp_path.rglob("*.parquet")) == before
        refusals.append(method)
    assert session._runtime.store.resources(session._runtime.session_ref) == ()
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, "duration-ratio-unknown-" + backend + ".json").write_text(
            json.dumps(
                {
                    "backend": backend,
                    "producer": "Journey observed_duration.ratio(observed_duration)",
                    "physical_type": "float64",
                    "tags": frame.cell_tag.tolist(),
                    "reasons": frame.cell_reason.tolist(),
                    "unknown_preserved": True,
                    "endpoints_verified": True,
                    "fixed_source_reads_forbidden": True,
                    "fixed_exact_hit": True,
                    "resources": 0,
                    "deviation_unknown_preserved": fits,
                    "association_unknown_rejected": refusals,
                },
                sort_keys=True,
            )
        )


@pytest.mark.parametrize("unknown_side", ["current", "baseline", "both"])
@pytest.mark.parametrize("defined_value", [0, 10])
def test_duration_ratio_unknown_endpoint_integrity(unknown_side: str, defined_value: int) -> None:
    from marivo.analysis.methods.state_validation import difference_matches

    primary: dict[str, object] = {
        "value": None,
        "cell_tag": "unknown",
        "cell_reason": "coverage_censored",
    }
    endpoints: dict[str, dict[str, object]] = {}
    for side in ("current", "baseline"):
        unknown = unknown_side in (side, "both")
        endpoints[side] = {
            side + "_endpoint__value": None if unknown else defined_value,
            side + "_endpoint__cell_tag": "unknown" if unknown else "defined",
            side + "_endpoint__cell_reason": "coverage_censored" if unknown else None,
        }
    assert difference_matches(
        primary,
        endpoints["current"],
        endpoints["baseline"],
        method="cell.ratio@v1",
        duration_ratio=True,
    )
    assert not difference_matches(
        primary, endpoints["current"], endpoints["baseline"], method="cell.ratio@v1"
    )
    assert not difference_matches(
        {**primary, "value": 1.0, "cell_tag": "defined", "cell_reason": None},
        endpoints["current"],
        endpoints["baseline"],
        method="cell.ratio@v1",
        duration_ratio=True,
    )


@pytest.mark.parametrize(
    "method",
    ["forecast.naive", "forecast.drift", "forecast.seasonal_naive", "time.runs", "time.runs_read"],
)
def test_duration_quotient_does_not_qualify_full_grid_consumers(
    method: Literal[
        "forecast.naive", "forecast.drift", "forecast.seasonal_naive", "time.runs", "time.runs_read"
    ],
) -> None:
    from marivo.analysis.methods.builtin import implementations
    from marivo.analysis.methods.semantics import MethodKey

    assert all(
        "journey" not in item.key.input_domains for item in implementations(MethodKey(method))
    )
