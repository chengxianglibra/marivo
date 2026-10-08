"""Counterexamples for the inactive Analysis DSL exchange and stream boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import ibis
import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis.compiler.graph_lowering import lower as lower_graph
from marivo.analysis.compiler.graph_plan import RouteChoice
from marivo.analysis.compiler.graph_plan import plan as plan_graph
from marivo.analysis.core.graph import Edge, FixedLeaf, method_node
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    DomainSignature,
    ObservedQuantity,
    Signature,
)
from marivo.analysis.core.rules import AssociationScore, RowState
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.materialization.cell_arrow import column as cell_column
from marivo.analysis.materialization.cell_arrow import logical_table
from marivo.analysis.materialization.cell_arrow import rows as cell_rows
from marivo.analysis.materialization.contracts import LocalReceipt
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import (
    CheckedStream,
    FixedInput,
    FixedPartInput,
    PartContract,
    from_arrow,
    from_pandas,
    from_receipts,
)
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract as GraphExchangeContract,
)
from marivo.analysis.materialization.graph_exchange import (
    ExchangePart as GraphExchangePart,
)
from marivo.analysis.materialization.graph_execution import PreparedGraph
from marivo.analysis.materialization.graph_local_execution import (
    execute_fixed_count,
    execute_fixed_row,
    execute_fixed_spearman,
)
from marivo.analysis.materialization.graph_storage import write_table
from marivo.analysis.methods.physical import FixedShape, NoTime, ScalarType
from marivo.analysis.methods.semantics import MethodKey
from marivo.analysis.refs import ArtifactRef
from marivo.refs import ref


@dataclass(frozen=True)
class _CellPolicy:
    cell_reasons: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class _TestBinding:
    row: d.DatasetRowContract
    rows: d.DatasetRowSetContract
    schema: pa.Schema
    method: _CellPolicy
    parts: tuple[PartContract, ...]


@dataclass(frozen=True)
class PartWriteSpec:
    role: str
    contract_id: str
    contract_version: int
    column_names: tuple[str, ...]


@dataclass(frozen=True)
class _PartWrite:
    storage_receipt: LocalReceipt


@dataclass(frozen=True)
class _WrittenExchange:
    primary_receipt: LocalReceipt
    retained_parts: tuple[_PartWrite, ...]


def _write_exchange(
    *,
    project_root: Path,
    staging_path: Path,
    final_path: Path,
    batches: tuple[pa.RecordBatch, ...],
    row_contract: d.DatasetRowContract,
    row_set_contract: d.DatasetRowSetContract,
    parts: tuple[PartWriteSpec, ...],
    event: object,
) -> _WrittenExchange:
    table = pa.Table.from_batches(batches)
    names = tuple(field.name for field in row_contract.schema.columns)
    primary = write_table(
        project_root, staging_path / "primary", final_path / "primary", table.select(names)
    )
    retained = tuple(
        _PartWrite(
            write_table(
                project_root,
                staging_path / part.role,
                final_path / part.role,
                table.select(part.column_names),
            )
        )
        for part in parts
    )
    final_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path.rename(final_path)
    return _WrittenExchange(primary, retained)


def _binding(value_type: pa.DataType | None = None) -> _TestBinding:
    if value_type is None:
        value_type = pa.int64()
    key = d._make_field_id("id")
    domain = d._entity_domain(ref.entity("sales.customers"), key)
    quantity = d._observed_quantity(domain, "metric:sales.revenue", (), ("sum",))
    value_kind = (
        "decimal(18, 2)"
        if pa.types.is_decimal(value_type)
        else "timestamp"
        if pa.types.is_timestamp(value_type)
        else "int64"
    )
    fields = (
        ("id", "int64", False),
        ("value", value_kind, True),
        ("cell_tag", "string", False),
        ("cell_reason", "string", True),
    )
    row = d._make_row_contract(
        schema_version=1,
        shape_id=d.DatasetShapeId(
            _token=d._CORE_TOKEN,
            family_id="test",
            local_shape_id="dsl_exchange",
            semantic_version=1,
        ),
        schema=d._make_schema(
            tuple(
                d.DatasetField(
                    _token=d._CORE_TOKEN,
                    field_id=d._make_field_id(name),
                    name=name,
                    role_id="value",
                    identity=d._generated_identity(d._make_field_id(name)),
                    derivation_identity=f"dsl.{name}",
                    logical_type_id=kind,
                    physical_type_state=d._ResolvedPhysicalType(
                        _token=d._CORE_TOKEN, physical_type_id=kind
                    ),
                    nullable=nullable,
                )
                for name, kind, nullable in fields
            )
        ),
        coordinate_field_ids=(key,),
        key_field_ids=(key,),
        family_semantics=d._complete_from_schema(),
    )
    rows = d._make_row_set_contract(
        schema_version=1,
        cardinality=d._keyed_cardinality(d._unknown_row_bound()),
        ordering=d._unordered_ordering(),
    )
    schema = pa.schema(
        [
            pa.field("id", pa.int64(), nullable=False),
            pa.field("value", value_type, nullable=True),
            pa.field("cell_tag", pa.string(), nullable=False),
            pa.field("cell_reason", pa.string(), nullable=True),
        ]
    )
    method = _CellPolicy(
        (
            ("null", ("source_null",)),
            ("undefined", ("zero_denominator",)),
            ("unknown", ("coverage_unknown",)),
        )
    )
    part_schema = pa.schema(
        [pa.field("id", pa.int64(), nullable=False), pa.field("state_sum", value_type)]
    )
    return _TestBinding(
        row=row,
        rows=rows,
        method=method,
        schema=schema,
        parts=(PartContract("sum", part_schema, ("id",)),),
    )


def _batch(binding: _TestBinding) -> pa.RecordBatch:
    return pa.RecordBatch.from_arrays(
        [
            pa.array([1, 2, 3, 4], type=pa.int64()),
            pa.array([2**53 + 7, None, None, None], type=binding.schema.field("value").type),
            pa.array(["defined", "null", "undefined", "unknown"]),
            pa.array([None, "source_null", "zero_denominator", "coverage_unknown"]),
        ],
        schema=binding.schema,
    )


class _MemoryStream:
    def __init__(
        self,
        schema: pa.Schema,
        batches: tuple[pa.RecordBatch, ...],
        *,
        fail: bool = False,
        close_fail: bool = False,
    ):
        self.schema = schema
        self.batches = batches
        self.fail = fail
        self.close_fail = close_fail
        self.closed = False

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        yield from self.batches
        if self.fail:
            raise ValueError("private-key-canary")

    def close(self) -> None:
        self.closed = True
        if self.close_fail:
            raise ValueError("private-close-canary")


def _exchange_signature() -> Signature:
    domain = DomainSignature(
        Binding("r43", "sales", "customers", "current"),
        "entity",
        (Coordinate(ref.entity("sales.customers"), "id", "identity"),),
        (Coordinate(ref.entity("sales.customers"), "id", "identity"),),
        "customers",
    )
    return Signature(
        domain,
        ObservedQuantity(
            "revenue",
            ref.metric("sales.revenue"),
            "metric-v1",
            "CNY",
            "current",
            "orders",
            "strict",
            "sum@v1",
        ),
    )


def test_source_parquet_and_pandas_share_checked_exchange(tmp_path: Path) -> None:
    binding = _binding()
    signature = _exchange_signature()
    part_contract = PartContract("sum", binding.parts[0].schema, ("id",))
    contract = GraphExchangeContract(
        signature,
        MethodKey("bind_project"),
        "artifact_r43",
        binding.schema,
        ("id",),
        (part_contract,),
        binding.method.cell_reasons,
    )
    primary_batch = _batch(binding)
    part_table = pa.Table.from_arrays(
        [pa.array([1, 2, 3, 4]), pa.array([7, 8, 9, 10])],
        schema=part_contract.schema,
    )
    wide = pa.RecordBatch.from_arrays(
        [*primary_batch.columns, pa.array([7, 8, 9, 10])],
        schema=binding.schema.append(pa.field("state_sum", pa.int64())),
    )
    written = _write_exchange(
        project_root=tmp_path,
        staging_path=tmp_path / "run" / "staging",
        final_path=tmp_path / "artifacts" / "result",
        batches=(wide,),
        row_contract=binding.row,
        row_set_contract=binding.rows,
        parts=(PartWriteSpec("sum", "dsl.sum", 1, ("id", "state_sum")),),
        event=lambda _name: None,
    )
    shuffled = part_table.take(pa.array([3, 1, 0, 2]))
    source = from_arrow(
        pa.Table.from_batches((primary_batch,)),
        contract,
        parts=(GraphExchangePart("sum", shuffled),),
    )
    pandas = from_pandas(
        pa.Table.from_batches((primary_batch,)).to_pandas(types_mapper=pd.ArrowDtype),
        contract,
        parts=(GraphExchangePart("sum", shuffled),),
    )
    fixed = from_receipts(
        FixedInput(
            "artifact_r43",
            tmp_path,
            written.primary_receipt,
            binding.row,
            binding.rows,
            (FixedPartInput("sum", written.retained_parts[0].storage_receipt),),
        ),
        contract,
    )
    assert logical_table(source.primary).equals(logical_table(pandas.primary))
    assert logical_table(source.primary).equals(logical_table(fixed.primary))
    assert source.parts[0].table.num_rows == fixed.parts[0].table.num_rows == 4
    assert source.primary["value"].to_pylist()[0] == 2**53 + 7
    assert cell_column(source.primary, "cell_tag").to_pylist() == [
        "defined",
        "null",
        "undefined",
        "unknown",
    ]
    with pytest.raises(MaterializationError, match="missing, reordered or extra"):
        from_arrow(pa.Table.from_batches((primary_batch,)), contract)
    with pytest.raises(MaterializationError, match="complete keys differ"):
        from_arrow(
            pa.Table.from_batches((primary_batch,)),
            contract,
            parts=(GraphExchangePart("sum", part_table.slice(1)),),
        )


def test_empty_three_producers_keep_schema_and_verified_empty_part(
    tmp_path: Path,
) -> None:
    binding = _binding()
    empty = pa.RecordBatch.from_arrays(
        [pa.array([], type=field.type) for field in binding.schema],
        schema=binding.schema,
    )
    primary = pa.Table.from_batches((empty,), schema=binding.schema)
    part_schema = binding.parts[0].schema
    part = pa.Table.from_arrays(
        [pa.array([], type=field.type) for field in part_schema],
        schema=part_schema,
    )
    contract = GraphExchangeContract(
        _exchange_signature(),
        MethodKey("bind_project"),
        "empty_r43",
        binding.schema,
        ("id",),
        (PartContract("sum", part_schema, ("id",)),),
        binding.method.cell_reasons,
    )
    written = _write_exchange(
        project_root=tmp_path,
        staging_path=tmp_path / "empty-stage",
        final_path=tmp_path / "empty-artifact",
        batches=(empty.append_column("state_sum", pa.array([], type=pa.int64())),),
        row_contract=binding.row,
        row_set_contract=binding.rows,
        parts=(PartWriteSpec("sum", "dsl.sum", 1, ("id", "state_sum")),),
        event=lambda _name: None,
    )
    outputs = (
        from_arrow(primary, contract, parts=(GraphExchangePart("sum", part),)),
        from_pandas(
            primary.to_pandas(types_mapper=pd.ArrowDtype),
            contract,
            parts=(GraphExchangePart("sum", part),),
        ),
        from_receipts(
            FixedInput(
                "empty_r43",
                tmp_path,
                written.primary_receipt,
                binding.row,
                binding.rows,
                (FixedPartInput("sum", written.retained_parts[0].storage_receipt),),
            ),
            contract,
        ),
    )
    assert all(item.primary.num_rows == 0 and item.parts[0].table.num_rows == 0 for item in outputs)
    from marivo.analysis.materialization.cell_arrow import logical_schema

    expected = [(field.name, field.type) for field in binding.schema]
    assert all(
        [(field.name, field.type) for field in logical_schema(item.primary.schema)] == expected
        for item in outputs
    )
    assert all(item.primary.schema.equals(outputs[0].primary.schema) for item in outputs)


def test_checked_stream_early_close_never_completes() -> None:
    binding = _binding()
    raw = _MemoryStream(binding.schema, (_batch(binding),))
    stream = CheckedStream(raw, binding.schema, ("id",), binding.method.cell_reasons)
    next(iter(stream))
    stream.close()
    assert not stream.completed


def test_checked_stream_failure_and_cancel_never_complete() -> None:
    binding = _binding()
    batch = _batch(binding)
    for source in (
        _MemoryStream(binding.schema, (batch,), fail=True),
        _MemoryStream(binding.schema, (batch,), close_fail=True),
    ):
        stream = CheckedStream(source, binding.schema, ("id",), binding.method.cell_reasons)
        with pytest.raises(ValueError):
            list(stream)
        assert source.closed and not stream.completed

    class CancelledStream(_MemoryStream):
        def __iter__(self) -> Iterator[pa.RecordBatch]:
            yield batch
            raise asyncio.CancelledError

    cancelled_source = CancelledStream(binding.schema, ())
    cancelled = CheckedStream(
        cancelled_source, binding.schema, ("id",), binding.method.cell_reasons
    )
    with pytest.raises(asyncio.CancelledError):
        list(cancelled)
    assert cancelled_source.closed and not cancelled.completed


@pytest.mark.parametrize("method_name,expected", [("count", 4), ("count_defined", 1)])
def test_fixed_count_reads_verified_receipts_without_duckdb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method_name: str, expected: int
) -> None:
    binding = _binding()
    signature = _exchange_signature()
    batch = _batch(binding)
    wide = pa.RecordBatch.from_arrays(
        [*batch.columns, pa.array([7, 8, 9, 10])],
        schema=binding.schema.append(pa.field("state_sum", pa.int64())),
    )
    written = _write_exchange(
        project_root=tmp_path,
        staging_path=tmp_path / "run" / "staging",
        final_path=tmp_path / "artifacts" / "result",
        batches=(wide,),
        row_contract=binding.row,
        row_set_contract=binding.rows,
        parts=(PartWriteSpec("sum", "dsl.sum", 1, ("id", "state_sum")),),
        event=lambda _name: None,
    )
    fixed = FixedLeaf(
        ArtifactRef(ref="artifact_r43"),
        "definition_r43",
        signature,
        ScalarType("int64"),
        FixedShape(NoTime()),
    )
    target = DomainSignature(signature.domain.binding, "singleton", (), (), "all")
    method = method_node(
        (Edge("quantity", fixed),),
        RowState(
            method_name,
            target,
            method_name,
            "count_all" if method_name == "count" else "defined_only",
            numeric_check_id="source.cell_policy@v1" if method_name == "count_defined" else None,
        ),
        value_type=ScalarType("int64"),
    )
    admitted = plan_graph(method, routes=(RouteChoice(method.identity, "artifact_python"),))
    lowered = lower_graph(admitted, bindings=())
    contract = GraphExchangeContract(
        signature,
        MethodKey("bind_project"),
        "artifact_r43",
        binding.schema,
        ("id",),
        (PartContract("sum", binding.parts[0].schema, ("id",)),),
        binding.method.cell_reasons,
    )
    selected = FixedInput(
        "artifact_r43",
        tmp_path,
        written.primary_receipt,
        binding.row,
        binding.rows,
        (FixedPartInput("sum", written.retained_parts[0].storage_receipt),),
    )
    monkeypatch.setattr(
        ibis.duckdb,
        "connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fixed execution opened DuckDB")
        ),
    )
    from marivo.datasource.adapters import SourceSession

    monkeypatch.setattr(
        SourceSession,
        "__init__",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fixed execution opened a source session")
        ),
    )
    execute = execute_fixed_count if method_name == "count" else execute_fixed_row
    result = execute(PreparedGraph(admitted), lowered, selected, contract)
    assert cell_rows(result.primary) == [
        {"value": expected, "cell_tag": "defined", "cell_reason": None}
    ]
    assert result.parts[0].table[f"row_state__{method_name}"].to_pylist() == [expected]
    assert len(result.completed_checks) == (0 if method_name == "count" else 1)
    part = result.parts[0]
    for invalid_count in (-1, expected + 1):
        corrupted = part.table.set_column(0, part.table.schema.field(0), pa.array([invalid_count]))
        with pytest.raises(MaterializationError, match="numerical state disagree"):
            from_arrow(
                result.primary,
                result.contract,
                parts=(GraphExchangePart(part.role, corrupted),),
                completed_checks=result.completed_checks,
                method_state=result.method_state,
            )
    from marivo.analysis.materialization import graph_local_execution

    def unexpected_read(*args: object, **kwargs: object) -> None:
        raise AssertionError("unqualified type opened a receipt")

    monkeypatch.setattr(graph_local_execution, "from_receipts", unexpected_read)
    for value_type in (pa.decimal128(18, 2), pa.timestamp("us"), pa.float64()):
        invalid_schema = contract.schema.set(1, pa.field("value", value_type))
        with pytest.raises(MaterializationError, match="value type differs"):
            execute(
                PreparedGraph(admitted), lowered, selected, replace(contract, schema=invalid_schema)
            )


@pytest.mark.parametrize("method,expected", [("sum", 14), ("mean", 3.5)])
def test_fixed_arithmetic_uses_verified_cells_and_state(
    tmp_path: Path, method: str, expected: int | float
) -> None:
    binding = _binding()
    signature = _exchange_signature()
    batch = pa.RecordBatch.from_arrays(
        [
            pa.array([1, 2, 3, 4]),
            pa.array([5, 2, 3, 4]),
            pa.array(["defined"] * 4),
            pa.array([None] * 4, type=pa.string()),
        ],
        schema=binding.schema,
    )
    wide = pa.RecordBatch.from_arrays(
        [*batch.columns, pa.array([5, 2, 3, 4])],
        schema=binding.schema.append(pa.field("state_sum", pa.int64())),
    )
    written = _write_exchange(
        project_root=tmp_path,
        staging_path=tmp_path / "run" / "staging",
        final_path=tmp_path / "artifacts" / "result",
        batches=(wide,),
        row_contract=binding.row,
        row_set_contract=binding.rows,
        parts=(PartWriteSpec("sum", "dsl.sum", 1, ("id", "state_sum")),),
        event=lambda _name: None,
    )
    fixed = FixedLeaf(
        ArtifactRef(ref="artifact_r43"),
        "definition_r43",
        signature,
        ScalarType("int64"),
        FixedShape(NoTime()),
    )
    target = DomainSignature(signature.domain.binding, "singleton", (), (), "all")
    node = method_node(
        (Edge("quantity", fixed),),
        RowState(method, target, method, "strict", numeric_check_id="source.finite_numeric@v1"),
        value_type=ScalarType("float64" if method == "mean" else "int64"),
    )
    admitted = plan_graph(node, routes=(RouteChoice(node.identity, "artifact_python"),))
    lowered = lower_graph(admitted, bindings=())
    contract = GraphExchangeContract(
        signature,
        MethodKey("bind_project"),
        "artifact_r43",
        binding.schema,
        ("id",),
        (PartContract("sum", binding.parts[0].schema, ("id",)),),
        binding.method.cell_reasons,
    )
    selected = FixedInput(
        "artifact_r43",
        tmp_path,
        written.primary_receipt,
        binding.row,
        binding.rows,
        (FixedPartInput("sum", written.retained_parts[0].storage_receipt),),
    )
    result = execute_fixed_row(PreparedGraph(admitted), lowered, selected, contract)
    assert result.primary["value"].to_pylist() == [expected]
    assert result.parts[0].table["row_state__sum"].to_pylist() == [14]
    assert len(result.completed_checks) == 1
    assert result.completed_checks[0].requirement.obligation.check_id == (
        "source.finite_numeric@v1"
    )
    if method == "mean":
        assert result.parts[0].table["row_state__count"].to_pylist() == [4]
    part = result.parts[0]
    corrupted = part.table.set_column(0, part.table.schema.field(0), pa.array([999]))
    for producer in (from_arrow, from_pandas):
        with pytest.raises(MaterializationError, match="numerical state disagree"):
            producer(
                result.primary if producer is from_arrow else result.primary.to_pandas(),
                result.contract,
                parts=(GraphExchangePart(part.role, corrupted),),
                completed_checks=result.completed_checks,
                method_state=result.method_state,
            )


def test_fixed_spearman_pairs_complete_keys_without_source_or_duckdb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = _binding()
    first_signature = _exchange_signature()
    second_quantity = replace(
        first_signature.quantity,
        definition_id="other",
        metric_ref=ref.metric("sales.other"),
    )
    second_signature = replace(first_signature, quantity=second_quantity)
    inputs = []
    contracts = []
    leaves = []
    for index, (signature, keys, values) in enumerate(
        (
            (first_signature, [1, 2, 3, 4], [1, 2, 3, 4]),
            (second_signature, [1, 2, 3, 4], [4, 3, 2, 1]),
        )
    ):
        batch = pa.RecordBatch.from_arrays(
            [
                pa.array(keys, type=pa.int64()),
                pa.array(values, type=pa.int64()),
                pa.array(["defined"] * 4),
                pa.array([None] * 4, type=pa.string()),
            ],
            schema=binding.schema,
        )
        written = _write_exchange(
            project_root=tmp_path,
            staging_path=tmp_path / f"stage-{index}",
            final_path=tmp_path / f"artifact-{index}",
            batches=(batch.append_column("state_sum", pa.array(values, type=pa.int64())),),
            row_contract=binding.row,
            row_set_contract=binding.rows,
            parts=(PartWriteSpec("sum", "dsl.sum", 1, ("id", "state_sum")),),
            event=lambda _name: None,
        )
        artifact = f"artifact_r43_{index}"
        inputs.append(
            FixedInput(
                artifact,
                tmp_path,
                written.primary_receipt,
                binding.row,
                binding.rows,
                (FixedPartInput("sum", written.retained_parts[0].storage_receipt),),
            )
        )
        contracts.append(
            GraphExchangeContract(
                signature,
                MethodKey("bind_project"),
                artifact,
                binding.schema,
                ("id",),
                (PartContract("sum", binding.parts[0].schema, ("id",)),),
                binding.method.cell_reasons,
            )
        )
        leaves.append(
            FixedLeaf(
                ArtifactRef(ref=artifact),
                f"definition_{index}",
                signature,
                ScalarType("int64"),
                FixedShape(NoTime()),
            )
        )
    target = DomainSignature(first_signature.domain.binding, "singleton", (), (), "all")
    node = method_node(
        (Edge("quantity", leaves[0]), Edge("quantity", leaves[1])),
        AssociationScore(
            target, "rank-association", "source.exact_pairing@v1", "source.finite_numeric@v1"
        ),
        value_type=ScalarType("float64"),
    )
    admitted = plan_graph(node, routes=(RouteChoice(node.identity, "artifact_python"),))
    lowered = lower_graph(admitted, bindings=())
    monkeypatch.setattr(
        ibis.duckdb,
        "connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fixed execution opened DuckDB")
        ),
    )
    from marivo.datasource.adapters import SourceSession

    monkeypatch.setattr(
        SourceSession,
        "__init__",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fixed execution opened a source session")
        ),
    )
    result = execute_fixed_spearman(
        PreparedGraph(admitted), lowered, (inputs[0], inputs[1]), (contracts[0], contracts[1])
    )
    assert cell_rows(result.primary) == [
        {"status": "valid", "value": -1.0, "cell_tag": "defined", "cell_reason": None}
    ]
    assert result.parts[0].table["pair_counts__complete_pair_count"].to_pylist() == [4]
    assert len(result.completed_checks) == 2
    assert result.method_state is not None
    assert result.method_state["status"].to_pylist() == ["valid"]
    part = result.parts[0]
    for invalid_count in (-1, 0, 5):
        index = part.table.schema.get_field_index("pair_counts__complete_pair_count")
        corrupted = part.table.set_column(
            index, part.table.schema.field(index), pa.array([invalid_count])
        )
        with pytest.raises(MaterializationError, match="numerical state disagree"):
            from_arrow(
                result.primary,
                result.contract,
                parts=(GraphExchangePart(part.role, corrupted),),
                completed_checks=result.completed_checks,
                method_state=result.method_state,
            )
    with pytest.raises(MaterializationError, match="method state vector"):
        from_arrow(
            result.primary,
            result.contract,
            parts=result.parts,
            completed_checks=result.completed_checks,
        )
    with pytest.raises(MaterializationError, match="state and primary Cell disagree"):
        from_arrow(
            result.primary,
            result.contract,
            parts=result.parts,
            completed_checks=result.completed_checks,
            method_state=pa.table({"status": ["constant_a"]}),
        )
    shared = method_node(
        (Edge("quantity", leaves[0]), Edge("quantity", leaves[0])),
        AssociationScore(
            target,
            "self-rank-association",
            "source.exact_pairing@v1",
            "source.finite_numeric@v1",
        ),
        value_type=ScalarType("float64"),
    )
    shared_plan = plan_graph(shared, routes=(RouteChoice(shared.identity, "artifact_python"),))
    shared_lowered = lower_graph(shared_plan, bindings=())
    import marivo.analysis.materialization.graph_local_execution as local_execution

    original_read = local_execution.from_receipts
    reads = []

    def counted_read(item, contract):
        reads.append(item.artifact_ref)
        return original_read(item, contract)

    monkeypatch.setattr(local_execution, "from_receipts", counted_read)
    self_result = execute_fixed_spearman(
        PreparedGraph(shared_plan),
        shared_lowered,
        (inputs[0], inputs[0]),
        (contracts[0], contracts[0]),
    )
    assert self_result.primary["value"].to_pylist() == [1.0]
    assert reads == [inputs[0].artifact_ref]
    with pytest.raises(MaterializationError, match="missing, reordered or extra"):
        execute_fixed_spearman(
            PreparedGraph(admitted),
            lowered,
            (inputs[0], replace(inputs[1], parts=())),
            (contracts[0], contracts[1]),
        )
