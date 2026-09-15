"""Independent native concentration arithmetic, exact digests and frozen source inputs."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import ibis
import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis.compiler import compile_dataset
from marivo.analysis.compiler.driver_candidate import (
    _coordinate,
    decode_driver_candidate_proof,
    driver_candidate_output_proof,
    lower_driver_candidate,
)
from marivo.analysis.compiler.driver_numeric import install_driver_numeric_functions
from marivo.analysis.compiler.nodes import CompiledRelationFence, CompiledValidation
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.operators.driver_contracts import (
    DriverCandidateEvaluationSummary,
    DriverCandidatePayload,
    DriverCandidateSpecV1,
)
from marivo.analysis.operators.row import PartFrame
from marivo.analysis.operators.row_values import frame_keys, row_key_names
from marivo.refs import ref
from tests.lazy_attribute_fixtures import CHANNEL, REGION, inputs
from tests.lazy_execution_fixtures import assert_compiled_validations, execution_fixture
from tests.lazy_observation_fixtures import make_sources


def _spec(
    *, axes: int = 1, search_all: bool = True, limit: int = 50, name: str = "revenue"
) -> DriverCandidateSpecV1:
    dimensions = (REGION,) if axes == 1 else (REGION, CHANNEL)
    metric = (
        make_sources().observe(ref.metric(f"sales.{name}")).with_dimensions(*dimensions).aggregate()
    )
    candidate = metric.compare(metric).discover.driver_axes(
        search_space=dimensions if search_all else (REGION,), limit=limit
    )
    assert isinstance(candidate._root, LogicalRootHandle)
    assert isinstance(candidate._root.payload, DriverCandidatePayload)
    return candidate._root.payload.spec


def _wide(
    frame: pd.DataFrame, spec: DriverCandidateSpecV1, parts: tuple[PartFrame, ...]
) -> pd.DataFrame:
    result = frame.copy()
    keys = row_key_names(spec.input_row)
    expected = frame_keys(frame, keys)
    for part in parts:
        positions = dict(zip(frame_keys(part.frame, keys), range(len(part.frame)), strict=True))
        for name in part.frame.columns:
            if name not in keys:
                result[name] = (
                    part.frame[name]
                    .iloc[pd.Index([positions[key] for key in expected])]
                    .reset_index(drop=True)
                )
    return result


@pytest.mark.parametrize(
    "changes, expected",
    [
        ([6.0, 4.0, 0.0], (3, 1, 0.6)),
        ([5.0, -5.0, 0.0], (3, 1, 0.5)),
        ([4.0, 3.0, 3.0], (3, 2, 0.7)),
        ([1.0, -1.0 + 1e-12, 0.0], (3, 1, 1.0 / (2.0 - 1e-12))),
    ],
)
def test_native_minimal_half_mass_includes_zero_and_null_members(
    changes: list[float], expected: tuple[int, int, float]
) -> None:
    frame, _, parts = inputs(
        "revenue", [("a",), ("b",), (None,)], [(v, 1) for v in changes], [(0, 1)] * 3
    )
    spec = _spec()
    backend = ibis.duckdb.connect()
    install_driver_numeric_functions(backend)
    try:
        table = backend.create_table(
            "delta", pa.Table.from_pandas(_wide(frame, spec, parts), preserve_index=False)
        )
        output, checks, proof = lower_driver_candidate(table, spec)
        assert_compiled_validations(checks)
        result = backend.execute(output)
        assert result.axis_cardinality.tolist() == [expected[0]]
        assert result.concentration_member_count.tolist() == [expected[1]]
        assert result.concentration_share.tolist() == pytest.approx([expected[2]])
        assert result.score.tolist() == pytest.approx([1 / (expected[1] + expected[0] / 1000)])
        summary = decode_driver_candidate_proof(
            backend.to_pyarrow(proof).to_pylist()[0], spec.definition
        )
        assert isinstance(summary.evaluation, DriverCandidateEvaluationSummary)
        assert summary.evaluation.evaluated_axis_count == 1
        assert (
            backend.execute(
                driver_candidate_output_proof(
                    output, spec.output_row, spec.definition, summary.evaluation
                )
            ).iloc[0, 0]
            == 0
        )
    finally:
        backend.disconnect()


def test_native_folds_other_search_axes_before_absolute_contribution() -> None:
    frame, _, parts = inputs(
        "revenue", [("a", "x"), ("a", "y"), ("b", "x")], [(100, 1), (-99, 1), (5, 1)], [(0, 1)] * 3
    )
    spec = _spec(axes=2)
    backend = ibis.duckdb.connect()
    install_driver_numeric_functions(backend)
    try:
        table = backend.create_table(
            "delta", pa.Table.from_pandas(_wide(frame, spec, parts), preserve_index=False)
        )
        output, checks, proof = lower_driver_candidate(table, spec)
        assert_compiled_validations(checks)
        rows = {value["axis_ref"]: value for value in backend.to_pyarrow(output).to_pylist()}
        assert rows[REGION.path]["concentration_share"] == pytest.approx(5 / 6)
        assert rows[CHANNEL.path]["concentration_share"] == pytest.approx(105 / 204)
        assert backend.to_pyarrow(proof).to_pylist()[0]["evaluated_axis_count"] == 2
    finally:
        backend.disconnect()


def test_native_zero_partition_evaluates_without_emitting_candidate() -> None:
    frame, _, parts = inputs("revenue", [("a",), (None,)], [(3, 1), (2, 1)], [(3, 1), (2, 1)])
    spec = _spec()
    backend = ibis.duckdb.connect()
    install_driver_numeric_functions(backend)
    try:
        table = backend.create_table(
            "delta", pa.Table.from_pandas(_wide(frame, spec, parts), preserve_index=False)
        )
        output, checks, proof = lower_driver_candidate(table, spec)
        assert_compiled_validations(checks)
        assert backend.execute(output).empty
        summary = decode_driver_candidate_proof(
            backend.to_pyarrow(proof).to_pylist()[0], spec.definition
        )
        assert isinstance(summary.evaluation, DriverCandidateEvaluationSummary)
        assert summary.evaluation.zero_contribution_axis_count == 1
        assert summary.evaluation.pre_limit_candidate_count == 0
    finally:
        backend.disconnect()


def test_native_digest_encoding_has_independent_scalar_vectors() -> None:
    backend = ibis.duckdb.connect()
    install_driver_numeric_functions(backend)
    try:
        cases: tuple[tuple[object, str, str], ...] = (
            (None, "string", "N"),
            ("snow|:[]", "string", "V736E6F777C3A5B5D"),
            (-0.0, "float64", "V307830702B30"),
            (1.5, "float64", "V3078312E38702B30"),
            (True, "boolean", "V74727565"),
            (Decimal("100.1200"), "decimal(15,4)", "V3130302E3132"),
            (Decimal("100.0000"), "decimal(15,4)", "V313030"),
            (date(1970, 1, 2), "date", "V313937302D30312D3032"),
            (datetime(1970, 1, 1, 0, 0, 0, 1), "timestamp", "V31"),
        )
        for value, kind, expected in cases:
            assert backend.execute(_coordinate(ibis.literal(value, type=kind))) == expected
    finally:
        backend.disconnect()


@pytest.mark.parametrize("collision", [False, True])
def test_native_keys_and_digest_checked_before_limit(
    monkeypatch: pytest.MonkeyPatch, collision: bool
) -> None:
    frame, _, parts = inputs(
        "revenue",
        [("a", "x"), ("b", "x"), ("a", "y"), ("b", "y")],
        [(5, 1), (2, 1), (4, 1), (3, 1)],
        [(0, 1)] * 4,
    )
    spec = _spec(axes=2, search_all=False, limit=1)
    wide = _wide(frame, spec, parts)
    if collision:
        monkeypatch.setattr(
            "marivo.analysis.compiler.driver_candidate.driver_item_id",
            lambda *_: ibis.literal("sha256:collision"),
        )
    else:
        wide = pd.concat((wide, wide.iloc[[0]]), ignore_index=True)
    backend = ibis.duckdb.connect()
    install_driver_numeric_functions(backend)
    try:
        table = backend.create_table("delta", pa.Table.from_pandas(wide, preserve_index=False))
        _, checks, _ = lower_driver_candidate(table, spec)
        name = (
            "candidate.driver_item_id_unique"
            if collision
            else "candidate.driver_coordinates_unique"
        )
        selected = [check for check in checks if check.name == name]
        assert any(backend.execute(check.expression).iloc[0, 0] > 0 for check in selected)
    finally:
        backend.disconnect()


def test_native_scoring_and_proof_use_frozen_delta(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        install_driver_numeric_functions(fixture.backend)
        metric = (
            fixture.sources.observe(ref.metric("sales.revenue")).with_dimensions(REGION).aggregate()
        )
        candidate = metric.compare(metric).discover.driver_axes(search_space=(REGION,))
        compiled = compile_dataset(candidate, fixture.tables(candidate))
        fences = [step for step in compiled.preparations if isinstance(step, CompiledRelationFence)]
        assert len(fences) == 1
        for preparation in compiled.preparations:
            if isinstance(preparation, CompiledRelationFence):
                fixture.backend.create_table(
                    preparation.relation_name, preparation.expression, temp=True
                )
            else:
                assert isinstance(preparation, CompiledValidation)
                assert fixture.backend.execute(preparation.expression).iloc[0, 0] == 0
        assert compiled.candidate_proof is not None
        assert all("identity" not in name for name in compiled.candidate_proof.columns)
        before = fixture.backend.execute(compiled.expression)
        fixture.backend.raw_sql("DROP TABLE orders")
        assert fixture.backend.execute(compiled.expression).equals(before)
        assert fixture.backend.execute(compiled.candidate_proof).iloc[0].evaluated_axis_count == 1


def test_native_integer_half_mass_boundary_above_float_precision() -> None:
    n = 2**53
    frame, _, parts = inputs(
        "order_count", [("a",), ("b",), (None,)], [(n, 1), (n, 1), (1, 1)], [(0, 1)] * 3
    )
    spec = _spec(name="order_count")
    backend = ibis.duckdb.connect()
    install_driver_numeric_functions(backend)
    try:
        table = backend.create_table(
            "delta", pa.Table.from_pandas(_wide(frame, spec, parts), preserve_index=False)
        )
        output, checks, _ = lower_driver_candidate(table, spec)
        assert_compiled_validations(checks)
        result = backend.execute(output)
        assert result.concentration_member_count.tolist() == [2]
        assert result.score.tolist() == pytest.approx([1 / 2.003])
    finally:
        backend.disconnect()


def test_native_driver_entity_digest_is_source_only_and_tampering_fails() -> None:
    from marivo.analysis.datasets import descriptors as d
    from marivo.analysis.operators.driver_values import driver_item_id as local_item_id

    metric = make_sources().observe(ref.metric("sales.revenue"))
    candidate = metric.compare(metric).discover.driver_axes(search_space=(REGION,))
    assert isinstance(candidate._root, LogicalRootHandle)
    assert isinstance(candidate._root.payload, DriverCandidatePayload)
    spec = candidate._root.payload.spec
    backend = ibis.duckdb.connect()
    install_driver_numeric_functions(backend)
    try:
        raw = backend.create_table("ids", {"id": [123], "axis_ref": [REGION.path]})
        table = raw.select(
            entity_identity=ibis.struct({"id": raw.id}),
            axis_ref=raw.axis_ref,
            score=ibis.literal(1.0 / 1.001),
            reason_codes=ibis.literal(["axis_concentration"]),
            axis_cardinality=ibis.literal(1, type="int64"),
            concentration_member_count=ibis.literal(1, type="int64"),
            concentration_share=ibis.literal(1.0),
        )
        from marivo.analysis.compiler.driver_candidate import driver_item_id

        table = table.mutate(item_id=driver_item_id(table, spec.output_row, spec.definition))
        row = backend.to_pyarrow(table).to_pylist()[0]
        from marivo.analysis.operators.errors import CandidateError

        with pytest.raises(CandidateError, match="local identity computation is not admitted"):
            local_item_id(spec.definition, spec.output_row, row)
        signature = tuple(
            (field.field_id.value, field.logical_type_id)
            for field in spec.output_row.schema.columns
            if field.field_id in spec.output_row.key_field_ids
        )
        import hashlib

        encoded = "S[V313233]|V" + REGION.path.encode().hex().upper()
        preimage = (
            "candidate_driver_item@v1:"
            + d._canonical_digest(spec.definition.identity_payload())
            + ":"
            + d._canonical_digest(signature)
            + ":"
            + encoded
        )
        assert row["item_id"] == "sha256:" + hashlib.sha256(preimage.encode()).hexdigest()
        proof = driver_candidate_output_proof(table, spec.output_row, spec.definition)
        assert proof.columns == ("violations",)
        assert backend.execute(proof).iloc[0, 0] == 0
        corrupt = table.mutate(entity_identity=ibis.struct({"id": ibis.literal(456, type="int64")}))
        assert (
            backend.execute(
                driver_candidate_output_proof(corrupt, spec.output_row, spec.definition)
            ).iloc[0, 0]
            == 1
        )
    finally:
        backend.disconnect()


def test_exact_native_endpoint_reconciliation_does_not_round_large_integers() -> None:
    from marivo.analysis.compiler.attribution import _reconciles

    backend = ibis.duckdb.connect()
    install_driver_numeric_functions(backend)
    try:
        left = ibis.literal(Decimal(2**53 + 1), type="decimal(38,0)")
        right = ibis.literal(2**53, type="int64")
        assert not backend.execute(_reconciles(left, right))
        assert backend.execute(_reconciles(left, ibis.literal(2**53 + 1, type="int64")))
    finally:
        backend.disconnect()


def test_native_unavailable_partition_and_no_evaluation_never_become_zero() -> None:
    frame, _, parts = inputs("revenue", [("a",), ("b",)], [(3, 1), (2, 1)], [(3, 1), (2, 1)])
    spec = _spec()
    wide = _wide(frame, spec, parts)
    backend = ibis.duckdb.connect()
    install_driver_numeric_functions(backend)
    try:
        table = backend.create_table("delta", pa.Table.from_pandas(wide, preserve_index=False))
        _, empty_checks, _ = lower_driver_candidate(table.filter(ibis.literal(False)), spec)
        evaluated = next(
            check for check in empty_checks if check.name == "candidate.driver_evaluated"
        )
        assert backend.execute(evaluated.expression).iloc[0, 0] == 1
        corrupt = table.mutate(current_value=ibis.null().cast("float64"))
        _, corrupt_checks, _ = lower_driver_candidate(corrupt, spec)
        finite = next(
            check
            for check in corrupt_checks
            if check.name == "attribution.current.raw_partition_finite"
        )
        assert backend.execute(finite.expression).iloc[0, 0] == 2
    finally:
        backend.disconnect()


def test_native_expansion_rejects_unavailable_original_entity_endpoint(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        install_driver_numeric_functions(fixture.backend)
        metric = fixture.sources.observe(
            ref.metric("sales.revenue"),
            population=fixture.sources.population(ref.entity("sales.customers")),
        )
        candidate = metric.compare(metric).discover.driver_axes(search_space=(CHANNEL,))
        compiled = compile_dataset(candidate, fixture.tables(candidate))
        violations: dict[str, int] = {}
        for preparation in compiled.preparations:
            if isinstance(preparation, CompiledRelationFence):
                fixture.backend.create_table(
                    preparation.relation_name, preparation.expression, temp=True
                )
            else:
                assert isinstance(preparation, CompiledValidation)
                violations[preparation.name] = int(
                    fixture.backend.execute(preparation.expression).iloc[0, 0]
                )
        assert violations["attribution.expanded_current_endpoint"] == 1
        assert violations["attribution.expanded_baseline_endpoint"] == 1


@pytest.mark.parametrize(
    "values",
    [
        [1e16, 1.0, -1e16],
        [0.1, 0.2, 0.3],
        [5e-324, 5e-324],
        [1e308, -1e308, 0.75],
        [1e16, 1.0, 1.0, -1e16, 0.75],
    ],
)
def test_native_exact_float_sum_and_window_match_fsum(values: list[float]) -> None:
    import math

    from marivo.analysis.compiler.driver_numeric import exact_float_sum

    backend = ibis.duckdb.connect()
    install_driver_numeric_functions(backend)
    try:
        table = backend.create_table("exact_sum", {"value": values, "position": range(len(values))})
        result = exact_float_sum(table.value)
        assert backend.execute(result) == math.fsum(values)
        window = ibis.window(order_by=table.position, preceding=None, following=0)
        cumulative = table.select("position", total=result.over(window)).order_by("position")
        assert backend.execute(cumulative).total.tolist() == [
            math.fsum(values[: i + 1]) for i in range(len(values))
        ]
        assert result.type().is_float64()
        assert cumulative.schema()["total"].is_float64()
    finally:
        backend.disconnect()


def test_native_cancellation_concentration_matches_independent_and_local_results() -> None:
    from marivo.analysis.operators.driver_values import execute_driver
    from tests.lazy_driver_fixtures import driver_inputs

    frame, spec, parts = driver_inputs(
        [("a", "x"), ("a", "y"), ("a", "z"), ("b", "u"), ("c", "v")],
        [(1e16, 1), (1.0, 1), (-1e16, 1), (0.75, 1), (0.75, 1)],
        [(0.0, 1)] * 5,
    )
    local, _ = execute_driver(frame, spec, parts=parts)
    backend = ibis.duckdb.connect()
    install_driver_numeric_functions(backend)
    try:
        table = backend.create_table(
            "cancelled_delta", pa.Table.from_pandas(_wide(frame, spec, parts), preserve_index=False)
        )
        expression, checks, _ = lower_driver_candidate(table, spec)
        assert_compiled_validations(checks)
        source = backend.execute(expression)
        source_region = source[source.axis_ref == REGION.path].iloc[0]
        local_region = local[local.axis_ref == REGION.path].iloc[0]
        assert (
            source_region.concentration_member_count == local_region.concentration_member_count == 2
        )
        assert source_region.concentration_share == local_region.concentration_share == 0.7
        assert source_region.score == local_region.score == 1 / 2.003
    finally:
        backend.disconnect()


@pytest.mark.parametrize(
    "values, expected_count",
    [
        ([0.1, 0.2, 0.3], 1),
        ([0.1] * 16, 8),
        ([0.30000000000000004] * 2, 1),
        ([5e-324] * 5, 3),
        ([8e307] * 2, 1),
    ],
)
def test_native_decimal_fraction_half_boundary_matches_local(
    values: list[float], expected_count: int
) -> None:
    from marivo.analysis.operators.driver_values import execute_driver
    from tests.lazy_driver_fixtures import driver_inputs

    frame, spec, parts = driver_inputs(
        [(str(index),) for index in range(len(values))],
        [(value, 1) for value in values],
        [(0.0, 1)] * len(values),
    )
    local, _ = execute_driver(frame, spec, parts=parts)
    backend = ibis.duckdb.connect()
    install_driver_numeric_functions(backend)
    try:
        table = backend.create_table(
            "fraction_delta", pa.Table.from_pandas(_wide(frame, spec, parts), preserve_index=False)
        )
        expression, checks, _ = lower_driver_candidate(table, spec)
        assert_compiled_validations(checks)
        source = backend.execute(expression)
        assert (
            source.concentration_member_count.tolist()
            == local.concentration_member_count.tolist()
            == [expected_count]
        )
        assert source.concentration_share.tolist() == local.concentration_share.tolist()
    finally:
        backend.disconnect()


def test_exact_native_sum_macros_follow_connection_lifetime(tmp_path: Path) -> None:
    from marivo.analysis.compiler.driver_numeric import exact_float_sum

    database = tmp_path / "numeric.duckdb"
    backend = ibis.duckdb.connect(database)
    try:
        table = backend.create_table("connection_values", {"value": [1e16, 1.0, -1e16]})
        install_driver_numeric_functions(backend)
        assert backend.execute(exact_float_sum(table.value)) == 1.0
    finally:
        backend.disconnect()
    fresh = ibis.duckdb.connect(database)
    try:
        functions = fresh.sql(
            "SELECT count(*) AS macro_count FROM duckdb_functions() "
            "WHERE function_name IN ('__marivo_driver_float_units', '__marivo_driver_float_from_units')"
        )
        assert fresh.execute(functions).iloc[0, 0] == 0
    finally:
        fresh.disconnect()


@pytest.mark.parametrize("coordinate", [float("nan"), float("inf"), float("-inf"), None])
def test_native_numeric_dimension_keys_reject_nonfinite_but_keep_legal_null(
    coordinate: float | None,
) -> None:
    from dataclasses import replace

    from marivo.analysis.compiler.driver_candidate import _coordinate_checks
    from marivo.analysis.datasets import descriptors as d
    from marivo.analysis.observation.contracts import make_ids

    row = _spec(axes=2, search_all=False).output_row
    fields = tuple(
        replace(
            field,
            _token=d._CORE_TOKEN,
            logical_type_id="float64",
            physical_type_state=d._deferred_type("float64", ids=make_ids(())),
        )
        if field.role_id == "dimension"
        else field
        for field in row.schema.columns
    )
    row = replace(row, _token=d._CORE_TOKEN, schema=d._make_schema(fields))
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table(
            "coordinate_keys",
            pa.table(
                {"channel": pa.array([coordinate], type=pa.float64()), "axis_ref": [REGION.path]}
            ),
        )
        checks = _coordinate_checks(table, row)
        count = sum(int(backend.execute(check.expression).iloc[0, 0]) for check in checks)
        assert count == (0 if coordinate is None else 1)
    finally:
        backend.disconnect()


@pytest.mark.parametrize(
    "values",
    [[1e308, 1e308, -1e308], [1e308, -1e308, 1e308], [-1e308, 1e308, 1e308]],
)
def test_native_and_local_exact_sum_survive_finite_intermediate_overflow(
    values: list[float],
) -> None:
    from fractions import Fraction

    from marivo.analysis.compiler.driver_numeric import exact_float_sum
    from marivo.analysis.operators.attribute_values import _sum

    backend = ibis.duckdb.connect()
    install_driver_numeric_functions(backend)
    try:
        table = backend.create_table("overflowing_intermediate", {"value": values})
        expected = float(sum(Fraction(value) for value in values))
        assert expected == 1e308
        assert backend.execute(exact_float_sum(table.value)) == _sum(list(values)) == expected
    finally:
        backend.disconnect()


@pytest.mark.parametrize("reverse", [False, True])
def test_driver_finite_overflowing_partitions_preserve_native_local_parity(reverse: bool) -> None:
    from marivo.analysis.operators.driver_values import execute_driver
    from tests.lazy_driver_fixtures import driver_inputs

    keys: list[tuple[str | None, ...]] = [
        ("a", "x"),
        ("a", "y"),
        ("a", "z"),
        ("b", "x"),
        ("b", "y"),
        ("b", "z"),
    ]
    values = [1e308, 1e308, -1e308, -1e308, 0.0, 1e308]
    if reverse:
        keys.reverse()
        values.reverse()
    frame, spec, parts = driver_inputs(
        keys, [(value, 1) for value in values], [(0.0, 1)] * len(values)
    )
    local, _ = execute_driver(frame, spec, parts=parts)
    backend = ibis.duckdb.connect()
    install_driver_numeric_functions(backend)
    try:
        table = backend.create_table(
            "overflowing_partitions",
            pa.Table.from_pandas(_wide(frame, spec, parts), preserve_index=False),
        )
        expression, checks, _ = lower_driver_candidate(table, spec)
        assert_compiled_validations(checks)
        source = backend.execute(expression)
        assert source.axis_ref.tolist() == local.axis_ref.tolist()
        assert (
            source.concentration_member_count.tolist()
            == local.concentration_member_count.tolist()
            == [1, 1]
        )
        assert (
            source.concentration_share.tolist() == local.concentration_share.tolist() == [1.0, 1.0]
        )
        assert source.score.tolist() == local.score.tolist()
    finally:
        backend.disconnect()
