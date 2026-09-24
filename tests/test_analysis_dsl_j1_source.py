"""First private J1 Ibis route against the independent DuckDB fixture."""

from __future__ import annotations

import hashlib

import duckdb
import ibis
import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.materialization.contracts import schema_fingerprint
from marivo.analysis.materialization.dsl_j1_receipt import (
    j1_observation_binding,
    j1_read_receipt,
    load_j1_observation,
    load_j1_read,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.local_stage import run_j1_local
from marivo.analysis.materialization.source_stage import run_j1_source
from marivo.analysis.materialization.storage import (
    PartWriteSpec,
    _realized_schema,
    write_local_dataset,
)
from marivo.analysis.observation.dsl_j1 import (
    J1_SUM_PARTS,
    J1Context,
    J1Group,
    J1Members,
    J1Observed,
    J1Statistic,
    j1_row_contracts,
)
from marivo.analysis.operators.dsl_j1_values import J1ExecutionResult
from tests.shared_fixtures import DslCaseFactory


def test_j1_ibis_source_matches_independent_oracle(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    state = case.catalog._state
    context = J1Context(state.registry, state.sidecar, "j1-source", "j1-source")
    domain = case.names.domain
    customer = ms.ref.entity(f"{domain}.customer")
    region = ms.ref.dimension(f"{domain}.customer.region")
    channel = ms.ref.dimension(f"{domain}.order.channel")
    revenue = ms.ref.metric(f"{domain}.revenue")
    buyer = ms.ref.relationship(f"{domain}.order_buyer")
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")
    members = context.members(customer)
    backend = ibis.duckdb.connect(str(case.database_path))
    try:
        tables = {
            f"{domain}.customer": backend.table("customer"),
            f"{domain}.order": backend.table("order"),
        }

        def read(value: J1Members | J1Group | J1Observed | J1Statistic) -> list[tuple[object, ...]]:
            result = run_j1_source(context, value.root, backend, tables)
            return [tuple(row.values()) for row in result.primary.to_pylist()]

        total = members.observe(revenue, during=august, via=buyer).rollup()
        assert read(total)[0][0] == 1000
        direct = members.group_by(region)
        read_group = members.read(region).group_by()
        direct_result = direct.observe(revenue, during=august, via=buyer)
        read_result = read_group.observe(revenue, during=august, via=buyer)
        assert read(direct_result) == read(read_result)
        assert {(row[0], row[1], row[2]) for row in read(direct_result)} == {
            ("east", 600, "defined"),
            ("south", 400, "defined"),
            ("west", None, "null"),
        }
        assert (
            read(members.observe(revenue, during=august, via=buyer).summarize("count"))[0][0] == 4
        )
        chosen = members.read(region)
        east = chosen.where(chosen.value.eq("east")).members()
        channels = east.observe(revenue, during=august, via=buyer, coordinates=(channel,)).group_by(
            channel
        )
        assert {(row[0], row[1]) for row in read(channels)} == {
            ("mobile", 150),
            ("web", 450),
        }
        detailed = run_j1_source(
            context,
            members.observe(revenue, during=august, via=buyer, coordinates=(channel,)).root,
            backend,
            tables,
        )
        assert sorted(detailed.primary.to_pylist(), key=lambda row: row["member"]) == [
            {
                "member": "A",
                "value": 450,
                "cell_tag": "defined",
                "cell_reason": None,
                "state_sum": 450,
                "non_null_count": 1,
                "row_count": 1,
            },
            {
                "member": "B",
                "value": 150,
                "cell_tag": "defined",
                "cell_reason": None,
                "state_sum": 150,
                "non_null_count": 1,
                "row_count": 1,
            },
            {
                "member": "C",
                "value": 400,
                "cell_tag": "defined",
                "cell_reason": None,
                "state_sum": 400,
                "non_null_count": 1,
                "row_count": 1,
            },
            {
                "member": "D",
                "value": None,
                "cell_tag": "null",
                "cell_reason": "empty_contribution",
                "state_sum": None,
                "non_null_count": 0,
                "row_count": 0,
            },
        ]
        assert detailed.parts[0][0] == "coordinate"
        assert sorted(detailed.parts[0][1].to_pylist(), key=lambda row: row["member"]) == [
            {"member": "A", "group": "web", "state_sum": 450, "non_null_count": 1, "row_count": 1},
            {
                "member": "B",
                "group": "mobile",
                "state_sum": 150,
                "non_null_count": 1,
                "row_count": 1,
            },
            {"member": "C", "group": "web", "state_sum": 400, "non_null_count": 1, "row_count": 1},
        ]
    finally:
        backend.disconnect()


def test_j1_retained_pandas_matches_source_state_without_duckdb(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    state = case.catalog._state
    context = J1Context(state.registry, state.sidecar, "j1-local", "j1-local")
    domain = case.names.domain
    members = context.members(ms.ref.entity(f"{domain}.customer"))
    revenue = ms.ref.metric(f"{domain}.revenue")
    buyer = ms.ref.relationship(f"{domain}.order_buyer")
    channel = ms.ref.dimension(f"{domain}.order.channel")
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")
    observed = members.observe(revenue, during=august, via=buyer, coordinates=(channel,))
    grouped = observed.group_by(channel)
    backend = ibis.duckdb.connect(str(case.database_path))
    try:
        tables = {
            f"{domain}.customer": backend.table("customer"),
            f"{domain}.order": backend.table("order"),
        }
        source = run_j1_source(context, observed.root, backend, tables)
        expected = run_j1_source(context, grouped.root, backend, tables)
        original_total = run_j1_source(context, observed.rollup().root, backend, tables)
    finally:
        backend.disconnect()

    def forbidden_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("retained J1 continuation reopened DuckDB")

    monkeypatch.setattr(duckdb, "connect", forbidden_connect)
    local_grouped = run_j1_local(grouped.root, source)
    assert sorted(local_grouped.primary.to_pylist(), key=lambda row: row["group"]) == sorted(
        expected.primary.to_pylist(), key=lambda row: row["group"]
    )
    local_total = run_j1_local(observed.rollup().root, source)
    assert local_total.primary.to_pylist() == original_total.primary.to_pylist()
    count = run_j1_local(observed.summarize("count").root, source)
    assert count.primary.column("value").to_pylist() == [4]
    with pytest.raises(MaterializationError, match="non-Defined current row"):
        run_j1_local(observed.summarize("mean").root, source)


def test_j1_receipt_checked_pandas_rollup_is_source_offline(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    state = case.catalog._state
    context = J1Context(state.registry, state.sidecar, "j1-receipt", "j1-receipt")
    domain = case.names.domain
    observed = context.members(ms.ref.entity(f"{domain}.customer")).observe(
        ms.ref.metric(f"{domain}.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{domain}.order_buyer"),
    )
    backend = ibis.duckdb.connect(str(case.database_path))
    try:
        tables = {
            f"{domain}.customer": backend.table("customer"),
            f"{domain}.order": backend.table("order"),
        }
        source = run_j1_source(context, observed.root, backend, tables)
        expected = run_j1_source(context, observed.rollup().root, backend, tables)
    finally:
        backend.disconnect()
    row, rows = j1_row_contracts(context, observed.root)
    specifications = tuple(
        PartWriteSpec(role, contract, 1, ("member", column))
        for role, contract, column in zip(
            J1_SUM_PARTS,
            ("dsl.j1.value_sum", "dsl.j1.non_null_count", "dsl.j1.row_count"),
            ("state_sum", "non_null_count", "row_count"),
            strict=True,
        )
    )
    written = write_local_dataset(
        project_root=case.root,
        staging_path=case.root / ".marivo" / "w2-stage",
        final_path=case.root / ".marivo" / "w2-retained",
        batches=source.primary.to_batches(),
        row_contract=row,
        row_set_contract=rows,
        parts=specifications,
        source_key_validation=True,
        event=lambda _: None,
    )
    provisional = j1_observation_binding(
        observed,
        source,
        input_binding="j1.receipt.fixture",
    )
    assert written.primary_receipt.schema_fingerprint == schema_fingerprint(
        _realized_schema(provisional.row, provisional.schema)
    )
    assert [part.storage_receipt.schema_fingerprint for part in written.retained_parts] == [
        hashlib.sha256(part.schema.serialize().to_pybytes()).hexdigest()
        for part in provisional.parts
    ]
    binding = j1_observation_binding(
        observed,
        source,
        input_binding="j1.receipt.fixture",
        receipt=written.primary_receipt,
        retained_parts=written.retained_parts,
    )
    assert {"complete_coverage", "contribution_partition"} <= set(binding.evidence.completed_checks)

    def forbidden_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("retained J1 receipt reopened DuckDB")

    monkeypatch.setattr(duckdb, "connect", forbidden_connect)
    restored = load_j1_observation(case.root, observed, binding)
    local = run_j1_local(observed.rollup().root, restored)
    assert local.primary.to_pylist() == expected.primary.to_pylist()


def test_j1_receipt_checked_read_selection_and_group_are_source_offline(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    domain = case.names.domain
    state = case.catalog._state
    context = J1Context(state.registry, state.sidecar, "j1-read-receipt", "j1-read-receipt")
    read = context.members(ms.ref.entity(f"{domain}.customer")).read(
        ms.ref.dimension(f"{domain}.customer.region")
    )
    backend = ibis.duckdb.connect(str(case.database_path))
    try:
        tables = {f"{domain}.customer": backend.table("customer")}
        source = run_j1_source(context, read.root, backend, tables)
    finally:
        backend.disconnect()
    row, rows = j1_row_contracts(context, read.root)
    written = write_local_dataset(
        project_root=case.root,
        staging_path=case.root / ".marivo" / "w2-read-stage",
        final_path=case.root / ".marivo" / "w2-read-retained",
        batches=source.primary.to_batches(),
        row_contract=row,
        row_set_contract=rows,
        source_key_validation=True,
        event=lambda _: None,
    )
    binding = j1_read_receipt(read, written.primary_receipt)

    def forbidden_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("fixed J1 read reopened DuckDB")

    monkeypatch.setattr(duckdb, "connect", forbidden_connect)
    restored = load_j1_read(case.root, binding)
    selected = run_j1_local(read.where(read.value.eq("east")).root, restored)
    assert selected.primary.column("member").to_pylist() == ["A", "B"]
    grouped = run_j1_local(read.group_by().root, restored)
    assert set(grouped.primary.column("group").to_pylist()) == {"east", "south", "west"}


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("null_classification", "strict_category_cell"),
        ("missing_key", "missing or duplicate key"),
        ("overflow", "integer range differs"),
        ("nonfinite", "finite_contribution"),
    ],
)
def test_j1_source_rejects_invalid_vectors(
    analysis_dsl_case_factory: DslCaseFactory, scenario: str, expected: str
) -> None:
    case = analysis_dsl_case_factory(scenario)
    domain = case.names.domain
    state = case.catalog._state
    context = J1Context(state.registry, state.sidecar, "j1-invalid", "j1-invalid")
    members = context.members(ms.ref.entity(f"{domain}.customer"))
    backend = ibis.duckdb.connect(str(case.database_path))
    try:
        tables = {
            f"{domain}.customer": backend.table("customer"),
            f"{domain}.order": backend.table("order"),
        }
        if scenario == "null_classification":
            read = members.read(ms.ref.dimension(f"{domain}.customer.region"))
            root = read.where(read.value.eq("east")).root
        elif scenario == "missing_key":
            root = members.root
        else:
            root = members.observe(
                ms.ref.metric(f"{domain}.revenue"),
                during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
                via=ms.ref.relationship(f"{domain}.order_buyer"),
            ).root
        with pytest.raises(MaterializationError, match=expected):
            run_j1_source(context, root, backend, tables)
    finally:
        backend.disconnect()


def test_j1_empty_current_rows_have_distinct_sum_count_mean_policy(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    domain = case.names.domain
    state = case.catalog._state
    context = J1Context(state.registry, state.sidecar, "j1-empty", "j1-empty")
    read = context.members(ms.ref.entity(f"{domain}.customer")).read(
        ms.ref.dimension(f"{domain}.customer.region")
    )
    empty = read.where(read.value.eq("absent")).members()
    observed = empty.observe(
        ms.ref.metric(f"{domain}.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{domain}.order_buyer"),
    )
    backend = ibis.duckdb.connect(str(case.database_path))
    try:
        tables = {
            f"{domain}.customer": backend.table("customer"),
            f"{domain}.order": backend.table("order"),
        }
        retained = run_j1_source(context, observed.root, backend, tables)
        for method, expected in (
            ("count", (0, "defined", None)),
            ("sum", (0, "defined", None)),
            ("mean", (None, "undefined", "empty_mean")),
        ):
            successor = observed.summarize(method)
            source = run_j1_source(context, successor.root, backend, tables)
            local = run_j1_local(successor.root, retained)
            for result in (source, local):
                row = result.primary.to_pylist()[0]
                assert (row["value"], row["cell_tag"], row["cell_reason"]) == expected
    finally:
        backend.disconnect()


def test_j1_local_rejects_duplicate_missing_nonfinite_and_overflow_state(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    domain = case.names.domain
    state = case.catalog._state
    context = J1Context(state.registry, state.sidecar, "j1-state", "j1-state")
    observed = context.members(ms.ref.entity(f"{domain}.customer")).observe(
        ms.ref.metric(f"{domain}.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{domain}.order_buyer"),
    )
    schema = pa.schema(
        [
            pa.field("member", pa.string()),
            pa.field("value", pa.int64()),
            pa.field("cell_tag", pa.string()),
            pa.field("cell_reason", pa.string()),
            pa.field("state_sum", pa.int64()),
            pa.field("non_null_count", pa.int64()),
            pa.field("row_count", pa.int64()),
        ]
    )

    def row(key: str | None, value: int) -> dict[str, object]:
        return {
            "member": key,
            "value": value,
            "cell_tag": "defined",
            "cell_reason": None,
            "state_sum": value,
            "non_null_count": 1,
            "row_count": 1,
        }

    for rows, expected in (
        ([row("A", 1), row("A", 2)], "missing or duplicate key"),
        ([row(None, 1)], "missing or duplicate key"),
    ):
        with pytest.raises(MaterializationError, match=expected):
            J1ExecutionResult(observed.root, pa.Table.from_pylist(rows, schema=schema))
    with pytest.raises(MaterializationError, match="integer overflow"):
        retained = J1ExecutionResult(
            observed.root,
            pa.Table.from_pylist([row("A", 2**63 - 1), row("B", 1)], schema=schema),
            completed_checks=("complete_coverage", "contribution_partition"),
        )
        run_j1_local(observed.rollup().root, retained)
    float_schema = schema.set(1, pa.field("value", pa.float64())).set(
        4, pa.field("state_sum", pa.float64())
    )
    invalid = {**row("A", 1), "value": float("nan"), "state_sum": float("nan")}
    with pytest.raises(MaterializationError, match="finite int64 or float64"):
        J1ExecutionResult(observed.root, pa.Table.from_pylist([invalid], schema=float_schema))
    read = context.members(ms.ref.entity(f"{domain}.customer")).read(
        ms.ref.dimension(f"{domain}.customer.region")
    )
    with pytest.raises(MaterializationError, match="admitted J1 value physical type"):
        J1ExecutionResult(
            read.root,
            pa.Table.from_pylist(
                [{"member": "A", "value": 1, "cell_tag": "defined", "cell_reason": None}],
                schema=pa.schema(
                    [
                        pa.field("member", pa.string()),
                        pa.field("value", pa.int64()),
                        pa.field("cell_tag", pa.string()),
                        pa.field("cell_reason", pa.string()),
                    ]
                ),
            ),
        )
    coordinate_schema = pa.schema(
        [
            pa.field("member", pa.string()),
            pa.field("group", pa.string()),
            pa.field("state_sum", pa.int64()),
            pa.field("non_null_count", pa.int64()),
            pa.field("row_count", pa.int64()),
        ]
    )
    for coordinate_row, expected in (
        (
            {"member": "B", "group": "web", "state_sum": 1, "non_null_count": 1, "row_count": 1},
            "foreign member",
        ),
        (
            {"member": "A", "group": "web", "state_sum": 1, "non_null_count": 1, "row_count": 2},
            "state counts differ",
        ),
    ):
        with pytest.raises(MaterializationError, match=expected):
            J1ExecutionResult(
                observed.root,
                pa.Table.from_pylist([row("A", 1)], schema=schema),
                parts=(
                    (
                        "coordinate",
                        pa.Table.from_pylist([coordinate_row], schema=coordinate_schema),
                    ),
                ),
            )


def test_j1_null_contribution_keeps_cell_and_state_across_routes(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("UPDATE \"order\" SET amount=NULL WHERE order_id='j1_a'")
    domain = case.names.domain
    state = case.catalog._state
    context = J1Context(state.registry, state.sidecar, "j1-null", "j1-null")
    observed = context.members(ms.ref.entity(f"{domain}.customer")).observe(
        ms.ref.metric(f"{domain}.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{domain}.order_buyer"),
    )
    backend = ibis.duckdb.connect(str(case.database_path))
    try:
        tables = {
            f"{domain}.customer": backend.table("customer"),
            f"{domain}.order": backend.table("order"),
        }
        retained = run_j1_source(context, observed.root, backend, tables)
        rows = {row["member"]: row for row in retained.primary.to_pylist()}
        assert (
            rows["A"]["value"],
            rows["A"]["cell_tag"],
            rows["A"]["non_null_count"],
            rows["A"]["row_count"],
        ) == (None, "null", 0, 1)
        assert rows["D"]["row_count"] == 0
        for method in ("sum", "mean"):
            successor = observed.summarize(method)
            with pytest.raises(MaterializationError, match="strict_current_row_cell"):
                run_j1_source(context, successor.root, backend, tables)
            with pytest.raises(MaterializationError, match="non-Defined current row"):
                run_j1_local(successor.root, retained)
        count = observed.summarize("count")
        assert run_j1_source(context, count.root, backend, tables).primary.column(
            "value"
        ).to_pylist() == [4]
        assert run_j1_local(count.root, retained).primary.column("value").to_pylist() == [4]
        rolled = observed.rollup()
        assert run_j1_source(context, rolled.root, backend, tables).primary.column(
            "value"
        ).to_pylist() == [550]
        assert run_j1_local(rolled.root, retained).primary.column("value").to_pylist() == [550]
    finally:
        backend.disconnect()


def test_j1_null_category_rejects_source_and_pandas_grouping(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("null_classification")
    domain = case.names.domain
    state = case.catalog._state
    context = J1Context(state.registry, state.sidecar, "j1-null-category", "j1-null-category")
    members = context.members(ms.ref.entity(f"{domain}.customer"))
    region = ms.ref.dimension(f"{domain}.customer.region")
    read = members.read(region)
    backend = ibis.duckdb.connect(str(case.database_path))
    try:
        tables = {f"{domain}.customer": backend.table("customer")}
        retained = run_j1_source(context, read.root, backend, tables)
        assert any(row["cell_tag"] == "null" for row in retained.primary.to_pylist())
        for grouped in (members.group_by(region), read.group_by()):
            with pytest.raises(MaterializationError, match="strict_group_cell"):
                run_j1_source(context, grouped.root, backend, tables)
        with pytest.raises(MaterializationError, match="non-Defined category"):
            run_j1_local(read.group_by().root, retained)
    finally:
        backend.disconnect()


def test_j1_unqualified_backend_and_physical_type_reject_before_query(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    domain = case.names.domain
    state = case.catalog._state
    context = J1Context(state.registry, state.sidecar, "j1-route", "j1-route")
    observed = context.members(ms.ref.entity(f"{domain}.customer")).observe(
        ms.ref.metric(f"{domain}.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{domain}.order_buyer"),
    )
    backend = ibis.duckdb.connect(str(case.database_path))
    try:
        tables = {
            f"{domain}.customer": backend.table("customer"),
            f"{domain}.order": backend.table("order"),
        }
        with pytest.raises(DatasetCompilationError, match="physical int64 or float64"):
            run_j1_source(
                context,
                observed.root,
                backend,
                {
                    **tables,
                    f"{domain}.order": tables[f"{domain}.order"].mutate(amount=ibis.literal("bad")),
                },
            )

        class UnqualifiedBackend:
            name = "postgres"

        with pytest.raises(DatasetCompilationError, match="qualified DuckDB"):
            run_j1_source(context, observed.root, UnqualifiedBackend(), tables)
    finally:
        backend.disconnect()
