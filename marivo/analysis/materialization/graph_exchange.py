"""Transient schema-first exchange for private graph method execution."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import pandas as pd
import pyarrow as pa

from marivo.analysis.compiler.graph_plan import CheckRequirement
from marivo.analysis.core.model import (
    AttributionPart,
    ConditionCellsPart,
    CoordinateStatePart,
    CorrespondencePart,
    DerivedQuantity,
    DisplayPart,
    GridCellsPart,
    OriginalStatePart,
    RowStatePart,
    Signature,
    part_role,
)
from marivo.analysis.core.rules import ReferenceDerive
from marivo.analysis.datasets.descriptors import DatasetRowContract, DatasetRowSetContract
from marivo.analysis.materialization.contracts import LocalReceipt
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.execution import BatchStream
from marivo.analysis.materialization.reads import open_receipt_batch_stream
from marivo.analysis.materialization.storage import _hash_file, _open_payload
from marivo.analysis.methods.registry import REGISTRY
from marivo.analysis.methods.semantics import MethodKey
from marivo.analysis.methods.state_validation import (
    coordinate_state_matches,
    denominator_interval_spans_zero,
    difference_matches,
    state_matches,
)


def numeric_primary(table: pa.Table) -> pa.Table:
    """Expose exact Duration ticks to numerical validators without changing the receipt."""
    if "value" in table.column_names and pa.types.is_duration(table.schema.field("value").type):
        return table.set_column(
            table.schema.get_field_index("value"), "value", table["value"].cast(pa.int64())
        ).append_column(
            "__duration_unit",
            pa.array([table.schema.field("value").type.unit] * table.num_rows, type=pa.string()),
        )
    return table


def _invalid(received: str) -> MaterializationError:
    return MaterializationError(
        expected="a complete schema-bound private graph exchange",
        received=received,
        repair="Use the exact registered method, input binding and verified producer.",
        stage="graph_exchange",
    )


@dataclass(frozen=True, slots=True)
class PartContract:
    role: str
    schema: pa.Schema
    key_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not self.role
            or not isinstance(self.schema, pa.Schema)
            or len(set(self.key_fields)) != len(self.key_fields)
            or not set(self.key_fields) <= set(self.schema.names)
        ):
            raise _invalid("invalid part role, schema or complete key")


if TYPE_CHECKING:
    from marivo.analysis.materialization.graph_protocol import ValidatedDescriptor


@dataclass(frozen=True, slots=True)
class ExchangeContract:
    signature: Signature
    method: MethodKey
    input_binding: str
    schema: pa.Schema
    key_fields: tuple[str, ...]
    parts: tuple[PartContract, ...] = ()
    cell_reasons: tuple[tuple[str, tuple[str, ...]], ...] = ()
    state_kind: str = "none"
    state_schema: pa.Schema | None = None
    pending_checks: tuple[CheckRequirement, ...] = ()
    allow_empty_singleton: bool = False
    column_reasons: tuple[tuple[tuple[str, tuple[str, ...]], ...], ...] = ()

    _frozen: ValidatedDescriptor | None = None

    def __post_init__(self) -> None:
        expected_state: str | None
        if self._frozen is not None:
            from marivo.analysis.materialization.graph_protocol import fixed_signature

            value = self._frozen.descriptor
            if (
                self.signature
                not in (value.signature, fixed_signature(value, _validated=self._frozen))
                or value.method_state.input_binding != self.input_binding
                or value.method_state.method_name != self.method.name
                or value.method_state.method_version != self.method.version
            ):
                raise _invalid("frozen exchange differs from validated descriptor")
            expected_state = value.method_state.kind
        else:
            expected_state = REGISTRY.lookup(self.method).semantics.persistent_state_kind
        if (
            not isinstance(self.signature, Signature)
            or not isinstance(self.method, MethodKey)
            or not self.input_binding
            or not isinstance(self.schema, pa.Schema)
            or len(set(self.key_fields)) != len(self.key_fields)
            or not set(self.key_fields) <= set(self.schema.names)
            or len({part.role for part in self.parts}) != len(self.parts)
            or not {part_role(part) for part in self.signature.parts}
            <= {part.role for part in self.parts}
            or any(
                part.key_fields != self.key_fields
                for part in self.parts
                if part.role
                not in (
                    "grid_cells",
                    "subject_map",
                    "pair_inputs",
                    "association_state",
                    "training_inputs",
                    "forecast_state",
                    "future_cells",
                    "condition_cells",
                    "run_cells",
                    "fit_inputs",
                    "fit_state",
                    "table_fits",
                    "fixed_reference",
                    "reference_proof",
                    "strata",
                    "stratum_values",
                    "ranking_domain",
                    "partitions",
                    "ordering",
                    "retention",
                    "history_view",
                    "entry_axes",
                    "funnel_state",
                    "finding_policy",
                )
                and not any(
                    isinstance(p, AttributionPart) and p.role == part.role
                    for p in self.signature.parts
                )
            )
            or len({tag for tag, _ in self.cell_reasons}) != len(self.cell_reasons)
            or any(tag not in ("null", "undefined", "unknown") for tag, _ in self.cell_reasons)
            or self.state_kind
            not in (
                "none",
                "anchor_retention",
                "subject_retention",
                "occurrence_inputs",
                "canonical_history",
                "history_view",
                "journey_assignment",
                "entry_axes",
                "history_view",
                "funnel_components",
                "funnel_comparison",
                "funnel_allocation",
                "attribution_additive",
                "attribution_component_mix",
                "ranking",
                "table",
                "cohort",
                "share",
                "penetration",
                "standardized",
                "original_min",
                "original_max",
                "original_mean",
                "original_fold",
                "original_sum",
                "original_sum_zero",
                "original_count",
                "original_ratio",
                "original_weighted_mean",
                "original_linear",
                "row_sum",
                "row_count",
                "row_count_defined",
                "row_mean",
                "row_min",
                "row_max",
                "ratio",
                "difference",
                "relative_change",
                "relation_ratio",
                "spearman",
            )
            or (self.state_kind == "none") != (self.state_schema is None)
            or self.state_kind != expected_state
            or any(not isinstance(item, CheckRequirement) for item in self.pending_checks)
            or type(self.allow_empty_singleton) is not bool
            or (
                self.allow_empty_singleton
                and (
                    self.key_fields
                    or self.method.name
                    not in (
                        "deviation.zscore",
                        "deviation.mad",
                        "deviation.read",
                        "display.rank",
                        "display.table",
                        "parts_transport",
                        "cell.difference",
                        "cell.relative_change",
                        "cell.ratio",
                    )
                )
            )
        ):
            raise _invalid("invalid method, binding, schema or ordered keys")
        if len(self.key_fields) != len(self.signature.domain.instance_key):
            raise _invalid("physical keys differ from the complete domain identity")
        names = self.schema.names
        if self.signature.quantity is not None and names[-3:] != [
            "value",
            "cell_tag",
            "cell_reason",
        ]:
            raise _invalid("quantity lacks the complete Cell vector")
        if self.state_schema is not None and (
            not isinstance(self.state_schema, pa.Schema)
            or self.state_schema.names
            != [
                *self.key_fields,
                "status",
                *(
                    ("error_bound",)
                    if self.state_kind in ("share", "penetration", "standardized")
                    else ()
                ),
            ]
            or self.state_schema.field("status").type != pa.string()
            or any(
                self.state_schema.field(key).type != self.schema.field(key).type
                for key in self.key_fields
            )
        ):
            raise _invalid("method state lacks exact keys and status")


class _TableStream:
    def __init__(self, table: pa.Table) -> None:
        self._reader = table.to_reader()
        self.schema = table.schema

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        return iter(self._reader)

    def close(self) -> None:
        self._reader.close()


class _PartStream:
    def __init__(self, batches: Iterator[pa.RecordBatch], schema: pa.Schema) -> None:
        self._batches = batches
        self.schema = schema

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        return self._batches

    def close(self) -> None:
        close = getattr(self._batches, "close", None)
        if close is not None:
            close()


def _key_rows(batch: pa.RecordBatch, fields: tuple[str, ...]) -> Iterator[tuple[object, ...]]:
    from marivo.analysis.materialization.execute_deadline import check

    for start in range(0, batch.num_rows, 1024):
        check()
        count = min(1024, batch.num_rows - start)
        if fields:
            columns: tuple[list[object], ...] = tuple(
                batch.column(name).slice(start, count).to_pylist() for name in fields
            )
            yield from zip(*columns, strict=True)
        else:
            for _ in range(count):
                yield ()


class CheckedStream:
    """Validate one producer; completion requires exhaustion and successful close."""

    def __init__(
        self,
        source: BatchStream,
        schema: pa.Schema,
        keys: tuple[str, ...],
        cell_reasons: tuple[tuple[str, tuple[str, ...]], ...] = (),
        validate_cells: bool = True,
        nullable_keys: frozenset[str] = frozenset(),
    ) -> None:
        if not source.schema.equals(schema, check_metadata=False):
            source.close()
            raise _invalid("producer schema differs from selected exchange")
        self._source = source
        self.schema = schema
        self._keys = keys
        self._nullable_keys = nullable_keys
        self._cell_reasons = dict(cell_reasons)
        self._validate_cell_values = validate_cells
        self._started = False
        self._closed = False
        self.completed = False
        self._key_index: set[tuple[object, ...]] | None = None

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        if self._started or self._closed:
            raise _invalid("producer was consumed twice or already closed")
        self._started = True
        return self._batches()

    def _batches(self) -> Iterator[pa.RecordBatch]:
        from marivo.analysis.materialization.execute_deadline import check

        seen_keys: set[tuple[object, ...]] = set()
        exhausted = False
        try:
            for batch in self._source:
                check()
                if not batch.schema.equals(self.schema, check_metadata=False):
                    raise _invalid("batch schema changed")
                if self._keys:
                    for key in _key_rows(batch, self._keys):
                        check()
                        if (
                            any(
                                value is None and name not in self._nullable_keys
                                for name, value in zip(self._keys, key, strict=True)
                            )
                            or key in seen_keys
                        ):
                            raise _invalid("null or duplicate complete key")
                        seen_keys.add(key)
                if self._validate_cell_values and {"value", "cell_tag", "cell_reason"} <= set(
                    self.schema.names
                ):
                    self._validate_cells(batch)
                yield batch
            check()
            exhausted = True
        finally:
            self._closed = True
            self._source.close()
            self.completed = exhausted
            if exhausted:
                self._key_index = seen_keys

    def _validate_cells(self, batch: pa.RecordBatch) -> None:
        from marivo.analysis.materialization.execute_deadline import check

        for start in range(0, batch.num_rows, 1024):
            check()
            values: list[bool] = batch.column("value").slice(start, 1024).is_valid().to_pylist()
            tags: list[object] = batch.column("cell_tag").slice(start, 1024).to_pylist()
            reasons: list[object] = batch.column("cell_reason").slice(start, 1024).to_pylist()
            for valid, tag, reason in zip(values, tags, reasons, strict=True):
                if tag == "defined":
                    if not valid or reason is not None:
                        raise _invalid("invalid Defined Cell")
                elif (
                    tag not in ("null", "undefined", "unknown")
                    or valid
                    or reason
                    not in (self._cell_reasons.get(tag, ()) if isinstance(tag, str) else ())
                ):
                    raise _invalid("invalid non-Defined Cell")

    def _complete_key_index(self) -> set[tuple[object, ...]]:
        if not self.completed or self._key_index is None:
            raise _invalid("producer key validation did not complete")
        return self._key_index

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._source.close()


@dataclass(frozen=True, slots=True)
class ExchangePart:
    role: str
    table: pa.Table


@dataclass(frozen=True, slots=True)
class ExchangeResult:
    contract: ExchangeContract
    primary: pa.Table
    parts: tuple[ExchangePart, ...]
    completed_checks: tuple[CompletedCheck, ...]
    method_state: pa.Table | None = None


@dataclass(frozen=True, slots=True)
class CompletedCheck:
    """Invocation-local proof, without durable Run or publication authority."""

    requirement: CheckRequirement
    result_digest: str


def collect(
    source: BatchStream,
    contract: ExchangeContract,
    *,
    parts: tuple[ExchangePart, ...] = (),
    completed_checks: tuple[CompletedCheck, ...] = (),
    method_state: pa.Table | None = None,
) -> ExchangeResult:
    """Exhaust one producer and validate every independently keyed state part."""
    nullable = frozenset(
        f"key_{i}"
        for i, c in enumerate(contract.signature.domain.instance_key)
        if c.field.startswith("attribution:axis:")
    )
    stream = CheckedStream(
        source, contract.schema, contract.key_fields, contract.cell_reasons, nullable_keys=nullable
    )
    try:
        primary = pa.Table.from_batches(tuple(stream), schema=contract.schema)
    finally:
        stream.close()
    if not stream.completed:
        raise _invalid("producer did not complete")
    if not contract.key_fields and primary.num_rows not in (
        (0, 1) if contract.allow_empty_singleton else (1,)
    ):
        raise _invalid("singleton result has an invalid row count")
    if tuple(part.role for part in parts) != tuple(part.role for part in contract.parts):
        raise _invalid("missing, reordered or extra method state part")
    if any(
        isinstance(p, (OriginalStatePart, RowStatePart, CoordinateStatePart)) and p.version != "v1"
        for p in contract.signature.parts
    ):
        raise _invalid("unsupported required numerical state version")
    primary_keys = (
        stream._complete_key_index()
        if contract.key_fields
        else _table_keys(primary, contract.key_fields, nullable)
    )
    for declared, part in zip(contract.parts, parts, strict=True):
        if not part.table.schema.equals(declared.schema, check_metadata=False):
            raise _invalid(f"{declared.role} schema differs")
        from marivo.analysis.core.model import ReferenceStatePart

        attribution_part = next(
            (
                p
                for p in contract.signature.parts
                if isinstance(p, AttributionPart) and p.role == part.role
            ),
            None,
        )
        if declared.role in (
            "condition_cells",
            "run_cells",
            "pair_inputs",
            "association_state",
            "training_inputs",
            "forecast_state",
            "future_cells",
            "fit_inputs",
            "fit_state",
            "table_fits",
            "retention",
            "history_view",
            "entry_axes",
            "funnel_state",
            "finding_policy",
        ):
            if (
                declared.key_fields
                or part.table.num_rows != 1
                or part.table.schema != pa.schema([(declared.role + "__retained", pa.string())])
            ):
                raise _invalid("closed retained state must be one exact string payload")
            continue
        if declared.role in ("grid_cells", "subject_map"):
            run_input = next(
                (p for p in contract.signature.parts if isinstance(p, ConditionCellsPart)), None
            )
            original_grid = (
                next((p for p in contract.signature.parts if isinstance(p, GridCellsPart)), None)
                if declared.role == "grid_cells"
                else None
            )
            expected_keys = (
                tuple(f"key_{i}" for i in range(len(original_grid.input_domain.instance_key)))
                if original_grid is not None
                else contract.key_fields
                if run_input is None
                else tuple(f"key_{i}" for i in range(len(run_input.input_domain.instance_key)))
            )
            if declared.key_fields != expected_keys:
                raise _invalid(
                    "original mapping keys differ from their declared complete input domain"
                )
            _table_keys(part.table, declared.key_fields, nullable)
            continue
        if attribution_part is not None:
            from marivo.analysis.materialization.graph_attribution import (
                part_keys as attribution_keys,
            )

            if declared.key_fields != attribution_keys(attribution_part):
                raise _invalid("attribution part keys differ from its frozen scope")
            if any(
                part.table.schema.field(k).type != primary.schema.field(k).type
                for k in declared.key_fields
            ):
                raise _invalid("attribution scope key types differ")
            _table_keys(part.table, declared.key_fields, nullable)
            continue
        reference_part = next(
            (
                item
                for item in contract.signature.parts
                if isinstance(item, ReferenceStatePart) and item.role == part.role
            ),
            None,
        )
        if reference_part is not None:
            from marivo.analysis.materialization.graph_reference import (
                part_keys as reference_key_fields,
            )

            if declared.key_fields != reference_key_fields(contract.signature, part.role):
                raise _invalid("reference part keys differ from its frozen input domain")
            if reference_part.version != "v1":
                raise _invalid("unsupported reference part version")
            if "value" in part.table.column_names and (
                "error_bound" not in part.table.column_names
                or part.table.schema.field("error_bound").type != pa.float64()
                or any(
                    type(value) is not float or not math.isfinite(value) or value < 0
                    for value in part.table["error_bound"].to_pylist()
                )
            ):
                raise _invalid("reference operands lack finite nonnegative error bounds")
            if reference_part.original_state is not None:
                support_state = reference_part.original_state
                if any(
                    not state_matches(
                        "original_" + support_state.method_version.removesuffix("@v1"),
                        row,
                        row,
                        empty_rules=support_state.empty_rules,
                    )
                    for row in numeric_primary(part.table).to_pylist()
                ):
                    raise _invalid("reference support Cell disagrees with original additive state")
            checked = CheckedStream(
                _TableStream(part.table),
                part.table.schema,
                declared.key_fields,
                reference_part.cell_reasons,
            )
            tuple(checked)
            _table_keys(part.table, declared.key_fields)
            continue
        if any(
            part.table.schema.field(key).type != primary.schema.field(key).type
            for key in declared.key_fields
        ):
            raise _invalid(f"{declared.role} key types differ")
        part_keys = _table_keys(part.table, declared.key_fields, nullable)
        if declared.role == "cohort_decision" and contract.state_kind == "cohort":
            if not primary_keys <= part_keys:
                raise _invalid("cohort decision lacks selected target keys")
        elif declared.role in (
            "ranking_domain",
            "partitions",
            "ordering",
        ):
            if not primary_keys <= part_keys:
                raise _invalid("ranking scope lacks selected keys")
        elif part_keys != primary_keys:
            raise _invalid(f"{declared.role} complete keys differ")
    coordinate = next(
        (part for part in contract.signature.parts if isinstance(part, CoordinateStatePart)), None
    )
    if coordinate is not None:
        by_role = {part.role: part.table for part in parts}
        if "original_state" not in by_role or part_role(coordinate) not in by_role:
            raise _invalid("coordinate partition lacks original or coordinate components")
        original = {
            tuple(row[name] for name in contract.key_fields): row
            for row in by_role["original_state"].to_pylist()
        }
        for row in by_role[part_role(coordinate)].to_pylist():
            key = tuple(row[name] for name in contract.key_fields)
            if not coordinate_state_matches(
                coordinate.components,
                coordinate.value_type,
                row.get(part_role(coordinate) + "__groups"),
                original[key],
                coordinate.columns,
            ):
                raise _invalid("coordinate partition differs from its complete original state")
    by_role = {part.role: part.table for part in parts}
    for declaration in contract.signature.parts:
        if (
            isinstance(declaration, OriginalStatePart)
            and declaration.method_version == "fold@v1"
            and (
                declaration.fold_kind is None
                or any(
                    value != declaration.fold_kind
                    for value in by_role["original_state"]["original_state__fold_kind"].to_pylist()
                )
            )
        ):
            raise _invalid("retained fold kind differs from the declared original quantity")
    if any(part.role in ("pair_inputs", "training_inputs") for part in parts):
        from marivo.analysis.materialization.statistical_execution import (
            validate as validate_statistics,
        )

        validate_statistics(contract, primary, parts)
    if any(part.role == "condition_cells" for part in parts):
        from marivo.analysis.materialization.runs_execution import validate

        validate(contract, primary, parts)
    if any(part.role == "fit_inputs" for part in parts):
        from marivo.analysis.materialization.deviation_execution import (
            validate as validate_deviation,
        )

        validate_deviation(contract, primary, parts)
    if any(part.role == "table_fits" for part in parts):
        from marivo.analysis.materialization.deviation_execution import validate_table_fits

        validate_table_fits(contract, primary, parts)
    if any(part.role == "retention" for part in parts):
        from marivo.analysis.materialization.retention_execution import (
            validate as validate_retention,
        )

        validate_retention(contract, primary, parts)
    if any(part.role == "anchor" for part in parts):
        from marivo.analysis.materialization.anchor_execution import validate as validate_anchor

        validate_anchor(contract, primary, parts)
    if any(part.role == "history_view" for part in parts):
        from marivo.analysis.materialization.history_views import validate as validate_view

        validate_view(contract, primary, parts)
    if any(part.role == "history" for part in parts):
        from marivo.analysis.materialization.history_execution import validate as validate_history

        validate_history(contract, primary, parts)
    if any(part.role == "journey" for part in parts):
        from marivo.analysis.materialization.journey_execution import validate as validate_journey

        validate_journey(contract, primary, parts)
    if any(part.role in ("entry_axes", "funnel_state") for part in parts):
        from marivo.analysis.materialization.funnel_execution import validate as validate_funnel

        validate_funnel(contract, primary, parts)
    if contract.state_kind == "none":
        from marivo.analysis.materialization.business_coverage import partial_primary

        partial = partial_primary(contract, parts, primary)
        # Transport preserves the owning state invariant even without a new method vector.
        for declaration in contract.signature.parts:
            if not isinstance(declaration, (OriginalStatePart, RowStatePart)):
                continue
            role = "original_state" if isinstance(declaration, OriginalStatePart) else "row_state"
            prefix = "original_" if isinstance(declaration, OriginalStatePart) else "row_"
            method = declaration.method_version.removesuffix("@v1").removeprefix("row.")
            if declaration.version != "v1" or not declaration.method_version.endswith("@v1"):
                raise _invalid("unsupported transported numerical state version")
            table = next(part.table for part in parts if part.role == role)
            keyed = {tuple(row[k] for k in contract.key_fields): row for row in table.to_pylist()}
            for row in numeric_primary(partial).to_pylist():
                if not state_matches(
                    prefix + method,
                    row,
                    keyed[tuple(row[k] for k in contract.key_fields)],
                    empty_rules=declaration.empty_rules
                    if isinstance(declaration, OriginalStatePart)
                    else (),
                ):
                    raise _invalid("transported numerical state and primary Cell disagree")
    if isinstance(
        contract.signature.quantity, DerivedQuantity
    ) and contract.signature.quantity.method_version in (
        "cell.difference@v1",
        "cell.relative_change@v1",
        "cell.ratio@v1",
    ):
        _verify_difference_parts(contract, parts, primary)
    if isinstance(
        contract.signature.quantity, DerivedQuantity
    ) and contract.signature.quantity.method_version.startswith("reference."):
        _verify_reference_parts(contract, parts, primary)
    if contract.state_schema is None:
        if method_state is not None:
            raise _invalid("unexpected method state vector")
    else:
        if (
            method_state is None
            or not method_state.schema.equals(contract.state_schema, check_metadata=False)
            or _table_keys(method_state, contract.key_fields, nullable) != primary_keys
        ):
            raise _invalid("missing or mismatched method state vector")
        states = {
            tuple(row[name] for name in contract.key_fields): row["status"]
            for row in method_state.to_pylist()
        }
        if contract.state_kind in (
            "difference",
            "relative_change",
            "relation_ratio",
            "share",
            "penetration",
            "standardized",
        ):
            if any(
                states[tuple(row[name] for name in contract.key_fields)] != row["cell_tag"]
                for row in primary.to_pylist()
            ):
                raise _invalid("Difference state status differs from its primary Cell")
            if contract.state_kind in ("share", "penetration", "standardized"):
                from marivo.analysis.materialization.graph_reference import error_bounds

                if (
                    "error_bound" not in method_state.column_names
                    or method_state.schema.field("error_bound").type != pa.float64()
                    or method_state["error_bound"].to_pylist()
                    != error_bounds(reference_parameters(contract.signature), parts, primary)
                ):
                    raise _invalid("reference result error envelope differs from retained operands")
        elif contract.state_kind in (
            "ranking",
            "table",
            "attribution_additive",
            "attribution_component_mix",
        ):
            expected_status = (
                primary["cell_tag"].to_pylist()
                if contract.state_kind != "table"
                else ["accepted"] * primary.num_rows
            )
            if [
                states[tuple(row[k] for k in contract.key_fields)] for row in primary.to_pylist()
            ] != expected_status:
                raise _invalid("display method status differs")
        elif contract.state_kind in (
            "entry_axes",
            "history_view",
            "funnel_components",
            "funnel_comparison",
            "funnel_allocation",
        ):
            if any(value != "accepted" for value in states.values()):
                raise _invalid("funnel state contains unaccepted components")
        elif contract.state_kind == "journey_assignment":
            from marivo.analysis.materialization.journey_execution import validate

            validate(contract, primary, parts)
            if any(value != "accepted" for value in states.values()):
                raise _invalid("Journey state contains unaccepted assignments")
        elif contract.state_kind == "occurrence_inputs":
            from marivo.analysis.materialization.domain_preparation import validate_exchange

            validate_exchange(contract, primary, parts)
            if any(value != "accepted" for value in states.values()):
                raise _invalid("occurrence state contains unaccepted inputs")
        elif contract.state_kind == "cohort":
            if any(value != "accepted" for value in states.values()):
                raise _invalid("cohort state contains an unaccepted target")
        elif contract.state_kind in ("anchor_retention", "subject_retention"):
            if any(
                states[tuple(row[k] for k in contract.key_fields)] != row["cell_tag"]
                for row in primary.to_pylist()
            ):
                raise _invalid("retention state vector differs from its Boolean Cells")
        elif contract.state_kind == "canonical_history":
            if any(
                states[tuple(row[key] for key in contract.key_fields)] != row["classification"]
                for row in primary.to_pylist()
            ):
                raise _invalid("canonical History state vector differs from its Subject ledger")
        elif contract.state_kind == "spearman" and any(p.role == "pair_inputs" for p in parts):
            if any(
                states[tuple(row[k] for k in contract.key_fields)] != row["status"]
                for row in primary.to_pylist()
            ):
                raise _invalid("statistical status differs from retained candidate state")
        else:
            _verify_single_state_part(contract, parts, primary, states)
    if any(
        not any(check.requirement == pending for pending in contract.pending_checks)
        for check in completed_checks
    ):
        raise _invalid("completed check is not pending in this exchange")
    if any(
        not any(check.requirement == pending for check in completed_checks)
        for pending in contract.pending_checks
    ):
        raise _invalid("method result retains an uncompleted check")
    from marivo.analysis._cohort import decide
    from marivo.analysis.core.model import CohortDecisionPart

    for signature_part in contract.signature.parts:
        if isinstance(signature_part, CohortDecisionPart):
            table = next(part.table for part in parts if part.role == "cohort_decision")
            columns = (
                "cohort_decision__true_count",
                "cohort_decision__unknown_count",
                "cohort_decision__false_count",
            )
            if any(
                name not in table.column_names or table.schema.field(name).type != pa.int64()
                for name in columns
            ):
                raise _invalid("cohort decision count schema differs")
            expected = (
                len(signature_part.opportunity_domain.time_grid.cells)
                if signature_part.opportunity_domain.time_grid is not None
                else 1
            )
            accepted_column = "cohort_decision__accepted"
            if (
                accepted_column not in table.column_names
                or table.schema.field(accepted_column).type != pa.bool_()
            ):
                raise _invalid("cohort decision lacks explicit target qualification")
            selected_keys = set()
            for row in table.to_pylist():
                t, u, f = (row[name] for name in columns)
                if signature_part.opportunity_domain.kind == "journey":
                    expected = row.get("cohort_decision__opportunity_count")
                    if type(expected) is not int or expected < 0:
                        raise _invalid("Journey cohort lacks the complete opportunity count")
                if (
                    any(type(value) is not int or value < 0 for value in (t, u, f))
                    or t + u + f != expected
                    or type(row[accepted_column]) is not bool
                    or decide(
                        signature_part.rule, signature_part.count, signature_part.empty, t, u, f
                    )
                    is not row[accepted_column]
                ):
                    raise _invalid("cohort decision lacks complete, decidable opportunity counts")
                if row[accepted_column]:
                    selected_keys.add(tuple(row[key] for key in contract.key_fields))
            if selected_keys != primary_keys:
                raise _invalid("cohort selected image differs from complete target decisions")
    if any(isinstance(p, AttributionPart) for p in contract.signature.parts):
        from marivo.analysis.materialization.graph_attribution import validate

        validate(contract, primary, parts)
    else:
        from marivo.analysis.materialization.graph_attribution import validate_endpoints

        validate_endpoints(contract.signature, parts, contract.key_fields)
    if any(isinstance(p, DisplayPart) for p in contract.signature.parts):
        from marivo.analysis.materialization.graph_display import validate

        validate(contract, primary, parts)
    return ExchangeResult(contract, primary, parts, completed_checks, method_state)


def _verify_difference_parts(
    contract: ExchangeContract, parts: tuple[ExchangePart, ...], primary: pa.Table
) -> None:
    keys = contract.key_fields
    primary_type = numeric_primary(primary).schema.field("value").type
    quantity = contract.signature.quantity
    assert quantity is not None
    division = quantity.method_version != "cell.difference@v1"
    for part in parts:
        if part.role not in ("current_endpoint", "baseline_endpoint"):
            continue
        expected_names = (
            *keys,
            *(f"{part.role}__{name}" for name in ("value", "cell_tag", "cell_reason")),
        )
        if tuple(part.table.column_names[: len(expected_names)]) != expected_names:
            raise _invalid("incomplete ordered Difference endpoint schema")
        endpoint_type = part.table.schema.field(f"{part.role}__value").type
        if not (
            (
                division
                and (
                    pa.types.is_integer(endpoint_type)
                    or pa.types.is_floating(endpoint_type)
                    or pa.types.is_decimal(endpoint_type)
                )
            )
            or endpoint_type == primary_type
            or (
                pa.types.is_decimal(endpoint_type)
                and pa.types.is_decimal(primary_type)
                and endpoint_type.scale == primary_type.scale
            )
        ) or any(
            part.table.schema.field(f"{part.role}__{name}").type != pa.string()
            for name in ("cell_tag", "cell_reason")
        ):
            raise _invalid("Difference endpoint types differ from the numeric contract")
    endpoints = {
        part.role: {
            tuple(row[name] for name in contract.key_fields): row for row in part.table.to_pylist()
        }
        for part in parts
        if part.role in ("current_endpoint", "baseline_endpoint")
    }
    if set(endpoints) != {"current_endpoint", "baseline_endpoint"}:
        raise _invalid("missing ordered Difference endpoints")
    correspondence = next((part.table for part in parts if part.role == "correspondence"), None)
    if correspondence is None:
        raise _invalid("missing complete endpoint correspondence")
    mapping_fields = (
        *((key, primary.schema.field(key).type) for key in keys),
        ("correspondence__current_present", pa.bool_()),
        ("correspondence__baseline_present", pa.bool_()),
        *(
            (f"correspondence__{side}_error_bound", pa.float64())
            for side in ("current", "baseline", "result")
        ),
        *(
            (f"correspondence__{side}_key_{i}", primary.schema.field(key).type)
            for side in ("current", "baseline")
            for i, key in enumerate(keys)
        ),
    )
    if tuple(correspondence.column_names) != tuple(name for name, _ in mapping_fields) or any(
        correspondence.schema.field(name).type != dtype for name, dtype in mapping_fields
    ):
        raise _invalid("incomplete or mistyped endpoint correspondence schema")
    mapped = {
        tuple(row[name] for name in contract.key_fields): row for row in correspondence.to_pylist()
    }
    for row in numeric_primary(primary).to_pylist():
        key = tuple(row[name] for name in contract.key_fields)
        mapping = mapped[key]
        definition = next(
            part for part in contract.signature.parts if isinstance(part, CorrespondencePart)
        )
        presence = tuple(
            mapping[f"correspondence__{side}_present"] for side in ("current", "baseline")
        )
        if any(type(present) is not bool for present in presence):
            raise _invalid("correspondence presence must be non-null Boolean")
        for side, present in zip(("current", "baseline"), presence, strict=True):
            if not present and mapping[f"correspondence__{side}_error_bound"] != 0.0:
                raise _invalid("missing coordinates cannot carry an operand error bound")
        for side in ("current", "baseline", "result"):
            bound = mapping[f"correspondence__{side}_error_bound"]
            if type(bound) is not float or not math.isfinite(bound) or bound < 0:
                raise _invalid("comparison error bounds must be finite and nonnegative")
        for side, present in zip(("current", "baseline"), presence, strict=True):
            endpoint = endpoints[f"{side}_endpoint"][key]
            value = endpoint[f"{side}_endpoint__value"]
            tag = endpoint[f"{side}_endpoint__cell_tag"]
            reason = endpoint[f"{side}_endpoint__cell_reason"]
            if present:
                valid = (
                    tag == "defined"
                    and reason is None
                    and value is not None
                    and (not isinstance(value, float) or math.isfinite(value))
                    and (not isinstance(value, Decimal) or value.is_finite())
                ) or (
                    tag in ("null", "undefined", "unknown")
                    and value is None
                    and isinstance(reason, str)
                    and bool(reason)
                )
                if not valid:
                    raise _invalid("present comparison endpoint must retain a valid Cell")
        if not any(presence) or (definition.policy == "exact" and not all(presence)):
            raise _invalid(
                "exact Difference correspondence differs from its complete endpoint keys"
            )
        for side, present in zip(("current", "baseline"), presence, strict=True):
            expected_key = key if present else (None,) * len(key)
            if present and side == "baseline" and definition.time_index is not None:
                index = definition.time_index
                forward = dict(definition.bucket_mapping)
                token = key[index]
                if not isinstance(token, str) or token not in forward:
                    raise _invalid("output bucket absent from its frozen original grid")
                expected_key = (*key[:index], forward[token], *key[index + 1 :])
            if (
                tuple(mapping[f"correspondence__{side}_key_{i}"] for i in range(len(key)))
                != expected_key
            ):
                raise _invalid("correspondence presence and complete endpoint keys disagree")
        if not all(presence):
            for index, side in enumerate(("current", "baseline")):
                endpoint = endpoints[f"{side}_endpoint"][key]
                if presence[index]:
                    if (
                        definition.policy == "metric_empty"
                        and endpoint[f"{side}_endpoint__cell_tag"] != "defined"
                    ):
                        raise _invalid("metric_empty existing operand is not Defined")
                    continue
                expected_tag = (
                    None
                    if definition.policy == "keep"
                    else "defined"
                    if definition.empty_rules[index] == "zero"
                    else "null"
                    if definition.empty_rules[index] == "null"
                    else "undefined"
                )
                expected_reason = (
                    "empty_contribution"
                    if expected_tag == "null"
                    else "zero_denominator"
                    if expected_tag == "undefined"
                    else None
                )
                expected_value = 0 if expected_tag == "defined" else None
                if (
                    endpoint[f"{side}_endpoint__cell_tag"] != expected_tag
                    or endpoint[f"{side}_endpoint__cell_reason"] != expected_reason
                    or endpoint[f"{side}_endpoint__value"] != expected_value
                ):
                    raise _invalid("missing coordinate and retained empty finish disagree")
            expected_reason = (
                "missing_side"
                if definition.policy == "keep"
                else next(
                    (
                        "empty_contribution"
                        if definition.empty_rules[i] == "null"
                        else "zero_denominator"
                        for i in (0, 1)
                        if not presence[i] and definition.empty_rules[i] != "zero"
                    ),
                    None,
                )
            )
            if expected_reason is not None:
                if mapping["correspondence__result_error_bound"] != 0.0:
                    raise _invalid("non-numeric missing-side result has a nonzero error bound")
                if (
                    row["value"] is not None
                    or row["cell_tag"]
                    != ("null" if expected_reason == "empty_contribution" else "undefined")
                    or row["cell_reason"] != expected_reason
                ):
                    raise _invalid("missing-side result and presence disagree")
                continue
        from marivo.analysis.methods.comparison import ComparisonMethod, propagated_error

        method: ComparisonMethod = (
            "relative_change"
            if quantity.method_version == "cell.relative_change@v1"
            else "ratio"
            if quantity.method_version == "cell.ratio@v1"
            else "difference"
        )
        try:
            expected_error = propagated_error(
                method,
                endpoints["current_endpoint"][key]["current_endpoint__value"],
                endpoints["baseline_endpoint"][key]["baseline_endpoint__value"],
                row["value"],
                mapping["correspondence__current_error_bound"],
                mapping["correspondence__baseline_error_bound"],
            )
        except (ValueError, OverflowError, TypeError) as error:
            raise _invalid("invalid retained comparison error envelope") from error
        if mapping["correspondence__result_error_bound"] != expected_error:
            raise _invalid("comparison error envelope and retained operands disagree")
        if not difference_matches(
            row,
            endpoints["current_endpoint"][key],
            endpoints["baseline_endpoint"][key],
            method=quantity.method_version,
            duration_ratio=quantity.value_policy == "duration_ratio_unknown",
        ):
            raise _invalid("Difference endpoints and primary Cell disagree")


def _verify_single_state_part(
    contract: ExchangeContract,
    parts: tuple[ExchangePart, ...],
    primary: pa.Table,
    states: dict[tuple[object, ...], object],
) -> None:
    role = (
        "pair_counts"
        if contract.state_kind == "spearman"
        else "original_state"
        if contract.state_kind
        in (
            "original_min",
            "original_max",
            "original_mean",
            "original_fold",
            "original_sum",
            "original_sum_zero",
            "original_count",
            "original_ratio",
            "original_weighted_mean",
            "original_linear",
        )
        else "row_state"
    )
    state_part = next((part.table for part in parts if part.role == role), None)
    if state_part is None:
        raise _invalid("missing required numerical state part")
    keyed_parts = {
        tuple(row[name] for name in contract.key_fields): row for row in state_part.to_pylist()
    }
    for row in numeric_primary(primary).to_pylist():
        key = tuple(row[name] for name in contract.key_fields)
        original = next(
            (p for p in contract.signature.parts if isinstance(p, OriginalStatePart)), None
        )
        if denominator_interval_spans_zero(contract.state_kind, keyed_parts[key]):
            raise _invalid(
                "float64 denominator error interval spans zero; use a stable denominator or precise input types"
            )
        if not state_matches(
            contract.state_kind,
            row,
            keyed_parts[key],
            empty_rules=original.empty_rules if original else (),
        ):
            raise _invalid("method part and primary numerical state disagree")
        status = states[key]
        if not isinstance(status, str) or not status:
            raise _invalid("missing method state status")
        if contract.state_kind == "spearman":
            if (
                row.get("status") != status
                or (status == "valid") != (row["cell_tag"] == "defined")
                or (status != "valid" and row["cell_reason"] != status)
            ):
                raise _invalid("Spearman state and primary Cell disagree")
        elif status != row.get("cell_tag"):
            raise _invalid("method state and primary Cell disagree")


def _table_keys(
    table: pa.Table, fields: tuple[str, ...], nullable: frozenset[str] = frozenset()
) -> set[tuple[object, ...]]:
    from marivo.analysis.materialization.execute_deadline import check

    for name in fields:
        table.column(name)
    result: set[tuple[object, ...]] = set()
    for batch in table.to_batches(max_chunksize=1024):
        for key in _key_rows(batch, fields):
            check()
            if (
                any(
                    value is None and name not in nullable
                    for name, value in zip(fields, key, strict=True)
                )
                or key in result
            ):
                raise _invalid("null or duplicate complete part key")
            result.add(key)
    return result


def from_pandas(
    frame: pd.DataFrame,
    contract: ExchangeContract,
    *,
    parts: tuple[ExchangePart, ...] = (),
    completed_checks: tuple[CompletedCheck, ...] = (),
    method_state: pa.Table | None = None,
) -> ExchangeResult:
    """Round-trip a selected local method through the common Arrow contract."""
    try:
        table = pa.Table.from_pandas(frame, schema=contract.schema, preserve_index=False, safe=True)
    except (pa.ArrowException, ValueError, TypeError) as error:
        raise _invalid(f"lossy pandas-to-Arrow conversion: {type(error).__name__}") from error
    return collect(
        _TableStream(table),
        contract,
        parts=parts,
        completed_checks=completed_checks,
        method_state=method_state,
    )


def from_arrow(
    table: pa.Table,
    contract: ExchangeContract,
    *,
    parts: tuple[ExchangePart, ...] = (),
    completed_checks: tuple[CompletedCheck, ...] = (),
    method_state: pa.Table | None = None,
) -> ExchangeResult:
    """Validate an owned Arrow result through the same producer contract."""
    return collect(
        _TableStream(table),
        contract,
        parts=parts,
        completed_checks=completed_checks,
        method_state=method_state,
    )


@dataclass(frozen=True, slots=True)
class VerifiedFixedInput:
    """Invocation-owned v7 input, already exhausted by the receipt owner."""

    artifact_ref: str
    receipt: LocalReceipt
    result: ExchangeResult


@dataclass(frozen=True, slots=True)
class FixedPartInput:
    role: str
    receipt: LocalReceipt


@dataclass(frozen=True, slots=True)
class FixedInput:
    artifact_ref: str
    root: Path
    receipt: LocalReceipt
    row: DatasetRowContract
    rows: DatasetRowSetContract
    parts: tuple[FixedPartInput, ...]


def from_receipts(selected: FixedInput, contract: ExchangeContract) -> ExchangeResult:
    """Exhaust exact local receipts without opening a source or native scan."""
    if selected.artifact_ref != contract.input_binding:
        raise _invalid("fixed receipt does not match the exact input binding")
    if tuple(item.role for item in selected.parts) != tuple(part.role for part in contract.parts):
        raise _invalid("missing, reordered or extra fixed part receipt")
    checked_parts: list[ExchangePart] = []
    for item, declared in zip(selected.parts, contract.parts, strict=True):
        reader = _PartStream(
            _verified_part_batches(selected.root, item.receipt, declared.schema),
            declared.schema,
        )
        stream = CheckedStream(reader, declared.schema, declared.key_fields)
        try:
            table = pa.Table.from_batches(tuple(stream), schema=declared.schema)
        finally:
            stream.close()
        if not stream.completed:
            raise _invalid(f"{declared.role} receipt did not complete")
        checked_parts.append(ExchangePart(item.role, table))
    primary_reader = open_receipt_batch_stream(
        selected.root, selected.receipt, selected.row, selected.rows
    )
    return collect(primary_reader, contract, parts=tuple(checked_parts))


def _verified_part_batches(
    root: Path, receipt: LocalReceipt, schema: pa.Schema
) -> Iterator[pa.RecordBatch]:
    """Verify a physical part without borrowing a legacy method codec."""
    parquet, path = _open_payload(root, receipt)
    rows = 0
    try:
        if (
            not parquet.schema_arrow.equals(schema, check_metadata=False)
            or receipt.schema_fingerprint
            != hashlib.sha256(schema.serialize().to_pybytes()).hexdigest()
        ):
            raise _invalid("fixed part schema or fingerprint differs")
        for batch in parquet.iter_batches(batch_size=1024):
            rows += batch.num_rows
            yield batch
        if (
            rows != receipt.realized_row_count
            or _hash_file(path) != receipt.bytes_hash
            or receipt.file_manifest[0].sha256 != receipt.bytes_hash
        ):
            raise _invalid("fixed part content or cardinality differs")
    finally:
        parquet.close()


def reference_parameters(signature: Signature) -> ReferenceDerive:
    from marivo.analysis.core.model import ReferenceStatePart
    from marivo.analysis.core.rules import ReferenceDerive

    quantity = signature.quantity
    assert isinstance(quantity, DerivedQuantity)
    kind = quantity.method_version.removeprefix("reference.").removesuffix("@v1")
    if kind not in ("share", "penetration", "standardize"):
        raise _invalid("unsupported reference method")
    proof = next(
        (
            part
            for part in signature.parts
            if isinstance(part, ReferenceStatePart) and part.role == "reference_proof"
        ),
        None,
    )
    strata = next(
        (
            part
            for part in signature.parts
            if isinstance(part, ReferenceStatePart) and part.role == "stratum_values"
        ),
        None,
    )
    if proof is None or strata is None:
        raise _invalid("missing reference state declarations")
    reference_kind: Literal["share", "penetration", "standardize"] = (
        "share" if kind == "share" else "penetration" if kind == "penetration" else "standardize"
    )
    return ReferenceDerive(
        reference_kind,
        signature.domain,
        proof.reference_id,
        quantity.unit,
        quantity.time_scope,
        strata.domain.instance_key if kind == "standardize" else (),
        share_state=proof.original_state,
    )


def _verify_reference_parts(
    contract: ExchangeContract, parts: tuple[ExchangePart, ...], primary: pa.Table
) -> None:
    from marivo.analysis.materialization.graph_reference import error_bounds, finish, physical_type

    params = reference_parameters(contract.signature)
    try:
        expected = finish(params, physical_type(primary.schema.field("value").type), parts)
        error_bounds(params, parts, primary)
    except (ValueError, OverflowError) as error:
        raise _invalid(str(error)) from error
    names = contract.key_fields
    rows = {tuple(row[key] for key in names): row for row in expected.to_pylist()}
    if any(rows.get(tuple(row[key] for key in names)) != row for row in primary.to_pylist()):
        raise _invalid("reference primary differs from its immutable inputs")
