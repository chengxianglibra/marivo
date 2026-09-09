"""Independent numerical references for native complete-cohort Entity screening."""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from decimal import Decimal
from statistics import median

import ibis
import ibis.expr.types as ir
import pytest
from ibis.backends.duckdb import Backend

from marivo.analysis.compiler.entity_candidate import (
    decode_candidate_proof,
    entity_candidate_output_proof,
    lower_entity_candidate,
)
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.operators.candidate_contracts import (
    CandidatePayload,
    CandidateSpecV1,
    EntityCandidateEvaluationSummary,
)
from marivo.refs import ref
from tests.lazy_observation_fixtures import make_sources


@pytest.fixture
def backend() -> Iterator[Backend]:
    connection = ibis.duckdb.connect()
    try:
        yield connection
    finally:
        connection.disconnect()


def _spec(*, threshold: float = 0.1, limit: int = 50) -> CandidateSpecV1:
    dataset = (
        make_sources()
        .observe(ref.metric("sales.revenue"))
        .discover.entity_outliers(threshold=threshold, limit=limit)
    )
    assert isinstance(dataset._root, LogicalRootHandle)
    assert isinstance(dataset._root.payload, CandidatePayload)
    return dataset._root.payload.spec


def _table(backend: Backend, values: Sequence[float | int | Decimal | None]) -> ir.Table:
    table = backend.create_table(
        "observations", {"id": list(range(len(values))), "revenue": list(values)}, overwrite=True
    )
    return table.select(entity_identity=ibis.struct({"id": table.id}), revenue=table.revenue)


@pytest.mark.parametrize(
    "values",
    [
        [-7.0, -1.0, 0.0, 1.0, 9.0],
        [1.0, 1.0, 1.0, 10.0],
        [-10.0, -1.0, -1.0, -1.0],
        [-9.0, -2.0, 2.0, 9.0],
        [None, -7.0, -1.0, 0.0, 1.0, 9.0, None],
    ],
)
def test_native_mad_matches_independent_reference(
    backend: Backend, values: list[float | None]
) -> None:
    complete = [value for value in values if value is not None]
    center = median(complete)
    deviations = [abs(value - center) for value in complete]
    mad = median(deviations)
    scale = 1.4826 * mad if mad else sum(deviations) / len(complete)
    expected = sorted(
        [
            (index, value, abs(value - center) / scale)
            for index, value in enumerate(values)
            if value is not None and abs(value - center) / scale >= 0.1
        ],
        key=lambda item: (-item[2], item[0]),
    )
    spec = _spec()
    result, checks, proof = lower_entity_candidate(_table(backend, values), spec)
    assert all(backend.execute(check.expression).iloc[0, 0] == 0 for check in checks)
    frame = backend.execute(result)
    assert frame.score.tolist() == pytest.approx([score for _, _, score in expected])
    assert frame.entity_identity.tolist() == [{"id": index} for index, _, _ in expected]
    assert frame.observed_value.tolist() == [value for _, value, _ in expected]
    assert frame.baseline_value.tolist() == [center] * len(expected)
    assert frame.signed_deviation.tolist() == [value - center for _, value, _ in expected]
    assert frame.direction.tolist() == [
        "high" if value > center else "low" for _, value, _ in expected
    ]
    summary = decode_candidate_proof(backend.to_pyarrow(proof).to_pylist()[0], spec.definition)
    assert isinstance(summary.evaluation, EntityCandidateEvaluationSummary)
    assert summary.evaluation.center == center
    assert summary.evaluation.scale == pytest.approx(scale)
    assert summary.evaluation.non_null_value_count == len(complete)
    assert summary.evaluation.null_value_count == len(values) - len(complete)
    assert summary.evaluation.scale_method == ("mad" if mad else "mean_absolute_deviation")
    assert (
        backend.execute(
            entity_candidate_output_proof(
                result, spec.output_row, spec.definition, summary.evaluation
            )
        ).iloc[0, 0]
        == 0
    )


def test_fallback_boundary_is_four_and_empty_is_valid(backend: Backend) -> None:
    table = _table(backend, [1.0, 1.0, 1.0, 10.0])
    for threshold, expected in ((4.0, [4.0]), (math.nextafter(4.0, math.inf), [])):
        spec = _spec(threshold=threshold)
        result, checks, proof = lower_entity_candidate(table, spec)
        assert all(backend.execute(check.expression).iloc[0, 0] == 0 for check in checks)
        assert backend.execute(result).score.tolist() == expected
        summary = decode_candidate_proof(backend.to_pyarrow(proof).to_pylist()[0], spec.definition)
        assert isinstance(summary.evaluation, EntityCandidateEvaluationSummary)
        assert summary.evaluation.scale == 2.25
        assert summary.evaluation.pre_limit_candidate_count == len(expected)


def test_original_summary_covers_pre_limit_hits(backend: Backend) -> None:
    spec = _spec(limit=1)
    result, _, proof = lower_entity_candidate(_table(backend, [-8.0, -1.0, 0.0, 1.0, 9.0]), spec)
    summary = decode_candidate_proof(backend.to_pyarrow(proof).to_pylist()[0], spec.definition)
    assert isinstance(summary.evaluation, EntityCandidateEvaluationSummary)
    assert len(backend.execute(result)) == summary.evaluation.emitted_candidate_count == 1
    assert summary.evaluation.pre_limit_candidate_count == 4
    assert summary.evaluation.reason_counts == (("entity_mad_threshold_met", 4),)
    assert summary.evaluation.score_range == pytest.approx((1 / 1.4826, 9 / 1.4826))


@pytest.mark.parametrize(
    "values,check_name",
    [
        ([1.0, 2.0, None], "candidate.entity_scale_valid"),
        ([1.0, 1.0, 1.0], "candidate.entity_scale_valid"),
        ([1.0, 2.0, float("inf")], "candidate.entity_input_finite"),
        ([1.0, 2.0, float("nan")], "candidate.entity_input_finite"),
        ([-1e308, 1e308, 1e308], "candidate.entity_scores_finite"),
        ([2**60 + 1, 2**60 + 3, 2**60 + 5], "candidate.entity_input_representable"),
        (
            [Decimal("1.00000000000000001"), Decimal("2.0"), Decimal("3.0")],
            "candidate.entity_input_representable",
        ),
    ],
)
def test_invalid_values_fail_source_assertions(
    backend: Backend,
    values: list[float | int | Decimal | None],
    check_name: str,
) -> None:
    table = _table(backend, values)
    if any(isinstance(value, float) and math.isnan(value) for value in values):
        # Arrow ingestion interprets NaN as null; native NaN is a distinct non-null input.
        table = table.mutate(
            revenue=(table.entity_identity.id == 2).ifelse(float("nan"), table.revenue)
        )
    _, checks, _ = lower_entity_candidate(table, _spec())
    check = next(check for check in checks if check.name == check_name)
    assert backend.execute(check.expression).iloc[0, 0] > 0


def test_native_publication_proof_detects_numeric_tampering(backend: Backend) -> None:
    spec = _spec()
    result, _, proof = lower_entity_candidate(_table(backend, [1.0, 1.0, 1.0, 10.0]), spec)
    summary = decode_candidate_proof(backend.to_pyarrow(proof).to_pylist()[0], spec.definition)
    assert isinstance(summary.evaluation, EntityCandidateEvaluationSummary)
    for corrupted in (
        result.mutate(score=100.0),
        result.mutate(baseline_value=2.0),
        result.mutate(scale_method=ibis.literal("unknown")),
        result.mutate(item_id=ibis.literal("sha256:invalid")),
        result.mutate(reason_codes=ibis.literal(["wrong_reason"])),
    ):
        assert (
            backend.execute(
                entity_candidate_output_proof(
                    corrupted, spec.output_row, spec.definition, summary.evaluation
                )
            ).iloc[0, 0]
            > 0
        )
