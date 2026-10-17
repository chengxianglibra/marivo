"""Transient schema-first exchange for private graph method execution."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from marivo.analysis.binding.correspondence import ComparisonEndpoint
from marivo.analysis.compiler.graph_plan import CheckRequirement
from marivo.analysis.core.cell_encoding import KnownCell, ValidityCell
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    ScalarDataType,
    TimestampDataType,
)
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
from marivo.analysis.materialization.cell_arrow import (
    binding,
    compact_schema,
    from_rows,
    logical_schema,
    pack,
    required,
    schema_binding,
)
from marivo.analysis.materialization.cell_arrow import rows as cell_rows
from marivo.analysis.materialization.cell_arrow import validate as validate_cells
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.methods.physical import stage_arrow_type
from marivo.analysis.methods.registry import REGISTRY
from marivo.analysis.methods.semantics import MethodKey
from marivo.analysis.methods.state_validation import (
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


def to_arrow(frame: pd.DataFrame, schema: pa.Schema) -> pa.Table:
    """Encode one pandas result at an explicit Arrow schema boundary."""
    if tuple(frame.columns) != tuple(schema.names):
        raise _invalid(
            f"pandas columns {tuple(frame.columns)!r} differ from frozen schema {tuple(schema.names)!r}"
        )
    try:
        if len(frame) == 0:
            return pa.Table.from_batches((), schema=schema)
        table = pa.Table.from_pandas(frame, schema=schema, preserve_index=False, safe=False)
        return table.replace_schema_metadata(schema.metadata)
    except (pa.ArrowException, TypeError, ValueError) as error:
        raise _invalid(
            f"pandas result cannot enter the frozen Arrow schema: {type(error).__name__}"
        ) from error


def _invalid(received: str) -> MaterializationError:
    return MaterializationError(
        expected="a complete schema-bound private graph exchange",
        received=received,
        repair="Use the exact registered method, input binding and verified producer.",
        stage="graph_exchange",
    )


# Roles whose table is exactly one ``<role>__retained`` JSON string with no key fields.
RETAINED_ROLES: frozenset[str] = frozenset(
    (
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
        "finding_policy",
    )
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
            raise _invalid(
                f"invalid part role or complete key: role={self.role!r}, keys={self.key_fields!r}, columns={tuple(self.schema.names)!r}"
            )


if TYPE_CHECKING:
    from marivo.analysis.materialization.graph_protocol import ValidatedDescriptor
    from marivo.analysis.materialization.query_components import ComponentFrame


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
            value = self._frozen.descriptor
            if (
                self.signature != value.signature
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
                if part.role not in RETAINED_ROLES
                and part.role
                not in (
                    "grid_cells",
                    "subject_map",
                    "fixed_reference",
                    "reference_proof",
                    "strata",
                    "stratum_values",
                    "ranking_domain",
                    "partitions",
                    "ordering",
                    "coordinate_state",
                    "allocation_state",
                    "current_endpoint_coordinates",
                    "baseline_endpoint_coordinates",
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
                        "cell.compare",
                        "comparison.read",
                        "cell.relative_change",
                        "cell.ratio",
                    )
                )
            )
        ):
            raise _invalid("invalid method, binding, schema or ordered keys")
        if len(self.key_fields) != len(self.signature.domain.instance_key):
            raise _invalid("physical keys differ from the complete domain identity")
        names = logical_schema(self.schema).names
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


@dataclass(frozen=True, slots=True)
class ExchangePart:
    role: str
    table: pd.DataFrame


@dataclass(frozen=True, slots=True)
class ArrowPart:
    """Arrow table carried only at an explicit typed codec boundary."""

    role: str
    table: pa.Table


@dataclass(frozen=True, slots=True)
class ExchangeResult:
    contract: ExchangeContract
    primary: pd.DataFrame
    parts: tuple[ExchangePart, ...]
    completed_checks: tuple[CompletedCheck, ...]
    method_state: pd.DataFrame | None = None


@dataclass(frozen=True, slots=True)
class CompletedCheck:
    """Invocation-local proof, without durable Run or publication authority."""

    requirement: CheckRequirement
    result_digest: str
    consumers: tuple[CheckRequirement, ...] = ()


def _compact_result(
    contract: ExchangeContract,
    primary: pa.Table,
    parts: tuple[ArrowPart, ...],
    completed_checks: tuple[CompletedCheck, ...],
    method_state: pa.Table | None,
) -> ExchangeResult:
    """Freeze Cell carriers only after producer-owned schema validation."""
    from marivo.analysis.core.model import ReferenceStatePart

    primary = pack(primary, contract.cell_reasons, contract.column_reasons)
    parts = tuple(
        ArrowPart(
            part.role,
            pack(
                part.table,
                next(
                    (
                        declared.cell_reasons
                        for declared in contract.signature.parts
                        if isinstance(declared, ReferenceStatePart) and declared.role == part.role
                    ),
                    contract.cell_reasons,
                ),
            ),
        )
        for part in parts
    )
    contract = replace(
        contract,
        schema=primary.schema,
        parts=tuple(
            replace(declared, schema=part.table.schema)
            for declared, part in zip(contract.parts, parts, strict=True)
        ),
    )
    return ExchangeResult(
        contract,
        primary.to_pandas(types_mapper=pd.ArrowDtype),
        tuple(
            ExchangePart(part.role, part.table.to_pandas(types_mapper=pd.ArrowDtype))
            for part in parts
        ),
        completed_checks,
        None if method_state is None else method_state.to_pandas(types_mapper=pd.ArrowDtype),
    )


def _validate_exchange(
    primary: pa.Table,
    contract: ExchangeContract,
    *,
    parts: tuple[ArrowPart, ...] = (),
    completed_checks: tuple[CompletedCheck, ...] = (),
    method_state: pa.Table | None = None,
) -> ExchangeResult:
    """Validate one materialized typed codec result and its independently keyed state parts."""
    nullable = frozenset(
        f"key_{i}"
        for i, c in enumerate(contract.signature.domain.instance_key)
        if c.field.startswith("attribution:axis:")
    )
    if not primary.schema.equals(contract.schema, check_metadata=False):
        raise _invalid("producer schema differs from selected exchange")
    if binding(contract.schema).cells:
        validate_cells(primary)
    elif {"value", "cell_tag", "cell_reason"} <= set(contract.schema.names):
        for row in primary.to_pylist():
            value, tag, reason = row["value"], row["cell_tag"], row["cell_reason"]
            if tag == "defined":
                if value is None or reason is not None:
                    raise _invalid("invalid Defined Cell")
            elif (
                tag not in ("null", "undefined", "unknown")
                or value is not None
                or reason not in dict(contract.cell_reasons).get(tag, ())
            ):
                raise _invalid("invalid non-Defined Cell")
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
    primary_keys = _table_keys(primary, contract.key_fields, nullable)
    for declared, part in zip(contract.parts, parts, strict=True):
        if not part.table.schema.equals(declared.schema, check_metadata=False):
            raise _invalid(f"{declared.role} schema differs")
        from marivo.analysis.core.model import ReferenceStatePart
        from marivo.analysis.methods.coordinate_state import key_fields as coordinate_keys

        retained_keys = coordinate_keys(contract.signature, declared.role)
        if retained_keys is not None:
            if declared.key_fields != retained_keys:
                raise _invalid("contribution part keys differ from its frozen layout")
            _table_keys(part.table, declared.key_fields)
            continue
        attribution_part = next(
            (
                p
                for p in contract.signature.parts
                if isinstance(p, AttributionPart) and p.role == part.role
            ),
            None,
        )
        if declared.role in RETAINED_ROLES:
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
                    for row in cell_rows(numeric_primary(part.table))
                ):
                    raise _invalid("reference support Cell disagrees with original additive state")
            if binding(part.table.schema).cells:
                validate_cells(part.table)
            elif {"value", "cell_tag", "cell_reason"} <= set(part.table.column_names):
                _validate_arrow_tag_cells(part.table, reference_part.cell_reasons)
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
    from marivo.analysis.methods.coordinate_state import validate as validate_coordinates

    try:
        validate_coordinates(contract.signature, {part.role: part.table for part in parts})
    except (ValueError, KeyError, pa.ArrowException) as error:
        raise _invalid(str(error)) from error
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
    pandas_primary = primary.to_pandas()
    pandas_parts = tuple(ExchangePart(part.role, part.table.to_pandas()) for part in parts)
    if any(part.role in ("pair_inputs", "training_inputs") for part in parts):
        from marivo.analysis.materialization.statistical_execution import (
            validate as validate_statistics,
        )

        validate_statistics(contract, pandas_primary, pandas_parts)
    if any(part.role == "condition_cells" for part in parts):
        from marivo.analysis.materialization.runs_execution import validate as validate_runs

        validate_runs(contract, pandas_primary, pandas_parts)
    if any(part.role == "fit_inputs" for part in parts):
        from marivo.analysis.materialization.deviation_execution import (
            validate as validate_deviation,
        )

        validate_deviation(contract, pandas_primary, pandas_parts)
    if any(part.role == "table_fits" for part in parts):
        from marivo.analysis.materialization.deviation_execution import validate_table_fits

        validate_table_fits(contract, pandas_primary, pandas_parts)
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
            keyed = {tuple(row[k] for k in contract.key_fields): row for row in cell_rows(table)}
            for row in cell_rows(numeric_primary(partial)):
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
    from marivo.analysis.core.model import ComparisonPart

    comparison = next((p for p in contract.signature.parts if isinstance(p, ComparisonPart)), None)
    if comparison is not None:
        from marivo.analysis.materialization.cell_arrow import logical_table
        from marivo.analysis.materialization.graph_reference import physical_type

        table = logical_table(primary)
        error_table = next(part.table for part in parts if part.role == "comparison")
        for field, quantity, dtype in (
            ("absolute", comparison.absolute, comparison.absolute_type),
            ("relative", comparison.relative, comparison.relative_type),
        ):
            fields = (
                *contract.key_fields,
                *(f"{field}__{name}" for name in ("value", "cell_tag", "cell_reason")),
            )
            if (
                not set(fields) <= set(table.column_names)
                or physical_type(table.schema.field(f"{field}__value").type).name != dtype
            ):
                raise _invalid("comparison is missing a mandatory precisely typed Cell")
            projected = table.select(fields).rename_columns(
                (*contract.key_fields, "value", "cell_tag", "cell_reason")
            )
            adjusted = tuple(
                ArrowPart(
                    part.role,
                    part.table.set_column(
                        part.table.schema.get_field_index("correspondence__result_error_bound"),
                        "correspondence__result_error_bound",
                        error_table[f"comparison__{field}_error_bound"],
                    ),
                )
                if part.role == "correspondence"
                else part
                for part in parts
                if part.role != "comparison"
            )
            view_contract = replace(
                contract,
                signature=replace(
                    contract.signature,
                    quantity=quantity,
                    parts=tuple(
                        p for p in contract.signature.parts if not isinstance(p, ComparisonPart)
                    ),
                ),
                schema=projected.schema,
                parts=tuple(part for part in contract.parts if part.role != "comparison"),
                _frozen=None,
            )
            _verify_difference_parts(view_contract, adjusted, projected)
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
            for row in cell_rows(method_state)
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
                for row in cell_rows(primary)
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
                [row["cell_tag"] for row in cell_rows(primary)]
                if contract.state_kind != "table"
                else ["accepted"] * primary.num_rows
            )
            if [
                states[tuple(row[k] for k in contract.key_fields)] for row in cell_rows(primary)
            ] != expected_status:
                raise _invalid("display method status differs")
        elif contract.state_kind == "cohort":
            if any(value != "accepted" for value in states.values()):
                raise _invalid("cohort state contains an unaccepted target")
        elif contract.state_kind == "spearman" and any(p.role == "pair_inputs" for p in parts):
            if any(
                states[tuple(row[k] for k in contract.key_fields)] != row["status"]
                for row in cell_rows(primary)
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
    return _compact_result(contract, primary, parts, completed_checks, method_state)


def _verify_difference_parts(
    contract: ExchangeContract, parts: tuple[ArrowPart, ...], primary: pa.Table
) -> None:
    keys = contract.key_fields
    primary_type = primary.schema.field("value").type
    quantity = contract.signature.quantity
    assert quantity is not None
    division = quantity.method_version != "cell.difference@v1"
    endpoint_types = tuple(
        part.table.schema.field(f"{part.role}__value").type
        for part in parts
        if part.role in ("current_endpoint", "baseline_endpoint")
    )
    if (
        any(pa.types.is_duration(dtype) for dtype in endpoint_types)
        and len(set(endpoint_types)) != 1
    ):
        raise _invalid("Difference endpoint Duration units differ")
    for part in parts:
        if part.role not in ("current_endpoint", "baseline_endpoint"):
            continue
        expected_names = (
            *keys,
            *(f"{part.role}__{name}" for name in ("value", "cell_tag", "cell_reason")),
        )
        if tuple(logical_schema(part.table.schema).names[: len(expected_names)]) != expected_names:
            raise _invalid("incomplete ordered Difference endpoint schema")
        endpoint_type = part.table.schema.field(f"{part.role}__value").type
        if not (
            (
                division
                and (
                    pa.types.is_integer(endpoint_type)
                    or pa.types.is_floating(endpoint_type)
                    or pa.types.is_decimal(endpoint_type)
                    or pa.types.is_duration(endpoint_type)
                )
            )
            or endpoint_type == primary_type
            or (
                pa.types.is_decimal(endpoint_type)
                and pa.types.is_decimal(primary_type)
                and endpoint_type.scale == primary_type.scale
            )
        ) or any(
            logical_schema(part.table.schema).field(f"{part.role}__{name}").type != pa.string()
            for name in ("cell_tag", "cell_reason")
        ):
            raise _invalid("Difference endpoint types differ from the numeric contract")

    def endpoint_rows(part: ArrowPart) -> list[dict[str, object]]:
        name = f"{part.role}__value"
        table = part.table
        if pa.types.is_duration(table.schema.field(name).type):
            table = table.set_column(
                table.schema.get_field_index(name), name, table[name].cast(pa.int64())
            )
        return cell_rows(table)

    endpoints = {
        part.role: {
            tuple(row[name] for name in contract.key_fields): row for row in endpoint_rows(part)
        }
        for part in parts
        if part.role in ("current_endpoint", "baseline_endpoint")
    }
    if set(endpoints) != {"current_endpoint", "baseline_endpoint"}:
        raise _invalid("missing ordered Difference endpoints")
    correspondence = next((part.table for part in parts if part.role == "correspondence"), None)
    if correspondence is None:
        raise _invalid("missing complete endpoint correspondence")
    definition = next(
        part for part in contract.signature.parts if isinstance(part, CorrespondencePart)
    )
    mapping_fields = (
        *((key, primary.schema.field(key).type) for key in keys),
        ("correspondence__current_present", pa.bool_()),
        ("correspondence__baseline_present", pa.bool_()),
        *(
            (f"correspondence__{side}_error_bound", pa.float64())
            for side in ("current", "baseline", "result")
        ),
        *(
            (
                f"correspondence__{side}_key_{i}",
                pa.string() if i == definition.time_index else primary.schema.field(key).type,
            )
            for side in ("current", "baseline")
            for i, key in enumerate(keys)
        ),
    )
    if tuple(correspondence.column_names) != tuple(name for name, _ in mapping_fields) or any(
        correspondence.schema.field(name).type != dtype for name, dtype in mapping_fields
    ):
        raise _invalid("incomplete or mistyped endpoint correspondence schema")
    mapped = {
        tuple(row[name] for name in contract.key_fields): row for row in cell_rows(correspondence)
    }
    for row in cell_rows(numeric_primary(primary)):
        key = tuple(row[name] for name in contract.key_fields)
        mapping = mapped[key]
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
            complete = f"{side}_endpoint__complete"
            if complete in endpoint and endpoint[complete] is not present:
                raise _invalid("comparison endpoint coverage differs from correspondence presence")
            if not present and any(
                value is not None
                for name, value in endpoint.items()
                if name.startswith(f"{side}_endpoint__state__")
            ):
                raise _invalid("absent comparison endpoint cannot retain original components")
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
            if present and definition.time_index is not None:
                index = definition.time_index
                assert definition.time_keys is not None
                table = (
                    definition.time_keys.current
                    if side == "current"
                    else definition.time_keys.baseline
                )
                forward = {aligned: bucket for bucket, aligned in table}
                token = key[index]
                if type(token) is not str or token not in forward:
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
            if definition.policy == "keep":
                if mapping["correspondence__result_error_bound"] != 0.0:
                    raise _invalid("non-numeric missing-side result has a nonzero error bound")
                if (
                    row["value"] is not None
                    or row["cell_tag"] != "undefined"
                    or row["cell_reason"] != "missing_side"
                ):
                    raise _invalid("missing-side result and presence disagree")
                continue
        from marivo.analysis.methods.comparison import propagated_cell

        current_endpoint = endpoints["current_endpoint"][key]
        baseline_endpoint = endpoints["baseline_endpoint"][key]
        passed = propagated_cell(
            str(current_endpoint["current_endpoint__cell_tag"]),
            str(current_endpoint["current_endpoint__cell_reason"]),
            str(baseline_endpoint["baseline_endpoint__cell_tag"]),
            str(baseline_endpoint["baseline_endpoint__cell_reason"]),
        )
        if passed is not None:
            # A non-Defined operand passes through per Cell without arithmetic.
            if mapping["correspondence__result_error_bound"] != 0.0:
                raise _invalid("non-numeric comparison result has a nonzero error bound")
            if row["value"] is not None or (row["cell_tag"], row["cell_reason"]) != passed:
                raise _invalid("propagated comparison Cell and retained operands disagree")
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
                required(mapping["correspondence__current_error_bound"], float),
                required(mapping["correspondence__baseline_error_bound"], float),
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
    parts: tuple[ArrowPart, ...],
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
        tuple(row[name] for name in contract.key_fields): row for row in cell_rows(state_part)
    }
    for row in cell_rows(numeric_primary(primary)):
        key = tuple(row[name] for name in contract.key_fields)
        original = next(
            (p for p in contract.signature.parts if isinstance(p, OriginalStatePart)), None
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
    result: set[tuple[object, ...]] = set()
    columns = tuple(table[name].to_pylist() for name in fields)
    for key in zip(*columns, strict=True) if fields else (() for _ in range(table.num_rows)):
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


def _validate_arrow_tag_cells(
    table: pa.Table, cell_reasons: tuple[tuple[str, tuple[str, ...]], ...]
) -> None:
    reasons = dict(cell_reasons)
    for row in table.to_pylist():
        value, tag, reason = row["value"], row["cell_tag"], row["cell_reason"]
        if tag == "defined":
            if value is None or reason is not None:
                raise _invalid("invalid Defined Cell")
        elif (
            tag not in ("null", "undefined", "unknown")
            or value is not None
            or reason not in reasons.get(tag, ())
        ):
            raise _invalid("invalid non-Defined Cell")


def _compact_pandas(
    frame: pd.DataFrame,
    schema: pa.Schema,
    reasons: tuple[tuple[str, tuple[str, ...]], ...],
    column_reasons: tuple[tuple[tuple[str, tuple[str, ...]], ...], ...] = (),
) -> tuple[pd.DataFrame, pa.Schema]:
    declared = binding(schema)
    schema = schema_binding(schema, declared.cells, declared.logical_columns)
    if declared.cells and tuple(frame.columns) == tuple(schema.names):
        return frame, schema
    if declared.cells and tuple(frame.columns) != declared.logical_columns:
        raise _invalid("pandas columns differ from the frozen Cell schema")
    schema = compact_schema(schema, reasons, column_reasons)
    if binding(schema).cells:
        _validate_pandas_tag_cells(frame, reasons)
        table = from_rows(
            (
                {str(key): None if pd.isna(value) else value for key, value in row.items()}
                for row in frame.to_dict("records")
            ),
            schema,
        )
        return table.to_pandas(types_mapper=pd.ArrowDtype), schema
    if tuple(frame.columns) != tuple(schema.names):
        raise _invalid("pandas columns differ from the selected physical schema")
    return frame, schema


def from_pandas(
    frame: pd.DataFrame,
    contract: ExchangeContract,
    *,
    parts: tuple[ExchangePart, ...] = (),
    completed_checks: tuple[CompletedCheck, ...] = (),
    method_state: pd.DataFrame | None = None,
    validate: bool = True,
) -> ExchangeResult:
    """Validate and retain an owned local method as pandas frames."""
    try:
        output, schema = _compact_pandas(
            frame, contract.schema, contract.cell_reasons, contract.column_reasons
        )
        if tuple(part.role for part in parts) != tuple(part.role for part in contract.parts):
            raise _invalid("missing, reordered or extra method state part")
        packed_parts: list[ExchangePart] = []
        part_contracts: list[PartContract] = []
        for declared, part in zip(contract.parts, parts, strict=True):
            packed, part_schema = _compact_pandas(
                part.table, declared.schema, _pandas_part_reasons(contract, declared.role)
            )
            packed_parts.append(ExchangePart(part.role, packed))
            part_contracts.append(replace(declared, schema=part_schema))
        parts = tuple(packed_parts)
        contract = replace(contract, schema=schema, parts=tuple(part_contracts))
    except (pa.ArrowException, ValueError, TypeError) as error:
        raise _invalid(f"pandas Cell codec failed: {type(error).__name__}") from error
    if tuple(part.role for part in parts) != tuple(part.role for part in contract.parts):
        raise _invalid("missing, reordered or extra method state part")
    for declared, part in zip(contract.parts, parts, strict=True):
        if tuple(part.table.columns) != tuple(declared.schema.names):
            raise _invalid(f"{declared.role} columns differ from its frozen schema")
    if (method_state is None) != (contract.state_schema is None) or (
        method_state is not None
        and contract.state_schema is not None
        and tuple(method_state.columns) != tuple(contract.state_schema.names)
    ):
        raise _invalid("missing or mismatched method state vector")
    if {item.requirement for item in completed_checks} != set(contract.pending_checks):
        raise _invalid("method result lacks its exact completed check records")
    _validate_pandas_cells(output, schema)
    _validate_pandas_tag_cells(output, contract.cell_reasons)
    _validate_pandas_finite(output, schema)
    for declared, part in zip(contract.parts, parts, strict=True):
        _validate_pandas_cells(part.table, declared.schema)
        part_reasons = _pandas_part_reasons(contract, declared.role)
        _validate_pandas_tag_cells(part.table, part_reasons)
        _validate_pandas_finite(part.table, declared.schema)
    if method_state is not None and contract.state_schema is not None:
        _validate_pandas_finite(method_state, contract.state_schema)
    if validate:
        return _validate_exchange(
            to_arrow(output, contract.schema),
            contract,
            parts=tuple(
                ArrowPart(part.role, to_arrow(part.table, declared.schema))
                for part, declared in zip(parts, contract.parts, strict=True)
            ),
            completed_checks=completed_checks,
            method_state=None
            if method_state is None
            else to_arrow(method_state, contract.state_schema or pa.schema([])),
        )
    return ExchangeResult(contract, output, parts, completed_checks, method_state)


def _pandas_part_reasons(
    contract: ExchangeContract, role: str
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    from marivo.analysis.core.model import ReferenceStatePart

    return next(
        (
            item.cell_reasons
            for item in contract.signature.parts
            if isinstance(item, ReferenceStatePart) and item.role == role
        ),
        contract.cell_reasons,
    )


def _pandas_records(frame: pd.DataFrame, schema: pa.Schema) -> list[dict[str, object]]:
    records = [
        {
            name: None if pd.isna(value) else value
            for name, value in record.items()
            if isinstance(name, str)
        }
        for record in frame.to_dict("records")
    ]
    for cell in binding(schema).cells:
        if isinstance(cell, KnownCell):
            tag, reason = cell.book.decode(cell.code)
            for row in records:
                row[cell.fields.tag], row[cell.fields.reason] = tag, reason
        elif isinstance(cell, ValidityCell):
            missing = cell.book.decode(cell.missing_code)
            for row in records:
                row[cell.fields.tag], row[cell.fields.reason] = (
                    ("defined", None) if not pd.isna(row[cell.fields.value]) else missing
                )
        else:
            for row in records:
                raw = row.pop(cell.state)
                if pd.isna(raw):
                    raise _invalid("physical Cell state cannot be null")
                code = int(raw)
                if code == -1 and cell.optional:
                    row[cell.fields.tag], row[cell.fields.reason] = None, None
                else:
                    row[cell.fields.tag], row[cell.fields.reason] = cell.book.decode(code)
    return records


def _validate_pandas_tag_cells(
    frame: pd.DataFrame, reasons: tuple[tuple[str, tuple[str, ...]], ...]
) -> None:
    if not {"value", "cell_tag", "cell_reason"} <= set(frame.columns):
        return
    allowed = dict(reasons)
    for row in frame.loc[:, ["value", "cell_tag", "cell_reason"]].to_dict("records"):
        value, tag, reason = row["value"], row["cell_tag"], row["cell_reason"]
        if tag == "defined":
            if pd.isna(value) or not pd.isna(reason):
                raise _invalid("invalid Defined Cell")
        elif (
            tag not in ("null", "undefined", "unknown")
            or not pd.isna(value)
            or not isinstance(reason, str)
            or reason not in allowed.get(tag, ())
        ):
            raise _invalid("invalid non-Defined Cell")


def _validate_pandas_finite(frame: pd.DataFrame, schema: pa.Schema) -> None:
    for field in schema:
        if not pa.types.is_floating(field.type):
            continue
        for value in frame[field.name].dropna().tolist():
            if not isinstance(value, int | float) or not math.isfinite(value):
                raise _invalid(f"non-finite numeric output in {field.name}")


def _validate_pandas_cells(frame: pd.DataFrame, schema: pa.Schema) -> None:
    for cell in binding(schema, exact=True).cells:
        if cell.fields.value not in frame.columns:
            raise _invalid("Cell binding refers to an absent value field")
        missing = frame[cell.fields.value].isna()
        if isinstance(cell, KnownCell):
            if len(frame) and (missing.any() if cell.code == 0 else not missing.all()):
                raise _invalid("Cell state disagrees with value validity")
            continue
        if isinstance(cell, ValidityCell):
            continue
        states = frame[cell.state]
        if states.isna().any():
            raise _invalid("physical Cell state cannot be null")
        admitted = (
            *((-1,) if cell.optional else ()),
            0,
            *cell.book.codes(),
        )
        for state, absent in zip(states, missing, strict=True):
            if isinstance(state, bool):
                raise _invalid("physical Cell state is not an integer code")
            try:
                code = int(state)
            except (OverflowError, TypeError, ValueError) as error:
                raise _invalid("physical Cell state is not an integer code") from error
            if code != state or code not in admitted:
                raise _invalid("undeclared physical Cell code")
            if (code == 0) == bool(absent):
                raise _invalid("Cell state disagrees with value validity")


def from_arrow(
    table: pa.Table,
    contract: ExchangeContract,
    *,
    parts: tuple[ArrowPart, ...] = (),
    completed_checks: tuple[CompletedCheck, ...] = (),
    method_state: pa.Table | None = None,
    validate: bool = True,
) -> ExchangeResult:
    """Validate an owned Arrow result through the same producer contract."""
    if not validate:
        if not table.schema.equals(contract.schema, check_metadata=False):
            raise _invalid("producer schema differs from selected exchange")
        if tuple(part.role for part in parts) != tuple(part.role for part in contract.parts):
            raise _invalid("missing, reordered or extra method state part")
        for declared, part in zip(contract.parts, parts, strict=True):
            if not part.table.schema.equals(declared.schema, check_metadata=False):
                raise _invalid(f"{declared.role} schema differs")
        if (method_state is None) != (contract.state_schema is None) or (
            method_state is not None
            and contract.state_schema is not None
            and not method_state.schema.equals(contract.state_schema, check_metadata=False)
        ):
            raise _invalid("missing or mismatched method state vector")
        if {item.requirement for item in completed_checks} != set(contract.pending_checks):
            raise _invalid("method result lacks its exact completed check records")
        for numeric in (
            table,
            *(part.table for part in parts),
            *((method_state,) if method_state is not None else ()),
        ):
            for field in numeric.schema:
                if (
                    pa.types.is_floating(field.type)
                    and pc.any(pc.invert(pc.is_finite(numeric[field.name]))).as_py()
                ):
                    raise _invalid(f"non-finite numeric output in {field.name}")
        return _compact_result(contract, table, parts, completed_checks, method_state)
    return _validate_exchange(
        table,
        contract,
        parts=parts,
        completed_checks=completed_checks,
        method_state=method_state,
    )


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
    contract: ExchangeContract, parts: tuple[ArrowPart, ...], primary: pa.Table
) -> None:
    from marivo.analysis.materialization.graph_reference import error_bounds, finish, physical_type

    params = reference_parameters(contract.signature)
    schemas = {part.role: part.schema for part in contract.parts}
    pandas_parts = tuple(ExchangePart(part.role, part.table.to_pandas()) for part in parts)
    try:
        expected = finish(
            params,
            physical_type(primary.schema.field("value").type),
            pandas_parts,
            schemas=schemas,
        )
        error_bounds(params, parts, primary)
    except (ValueError, OverflowError) as error:
        raise _invalid(str(error)) from error
    names = contract.key_fields
    expected_rows = [
        {str(name): value for name, value in row.items()} for row in expected.to_dict("records")
    ]
    actual_rows = _pandas_records(primary.to_pandas(), contract.schema)
    expected_by_key = {tuple(row[key] for key in names): row for row in expected_rows}
    actual_by_key = {tuple(row[key] for key in names): row for row in actual_rows}
    if expected_by_key != actual_by_key:
        raise _invalid("reference primary differs from its immutable inputs")


# R2.3 Query stage handoff. StageOutput is the only invocation-local value passed
# between SourceRead and LocalCompute stages; it is never serialized or reused as
# a computation input. The legacy ExchangeResult above keeps serving the graph DSL.

_NORMALIZED = object()


def _stage_error(expected: str, received: str) -> MaterializationError:
    return MaterializationError(
        expected=expected,
        received=received,
        repair="Correct the source field type or stage layout; Marivo does not cast lossy values back.",
        stage="stage_output",
        help_target="Query.execute",
    )


def _type_name(dtype: DataType) -> str:
    if isinstance(dtype, ScalarDataType):
        return dtype.name
    if isinstance(dtype, DecimalDataType):
        return f"decimal({dtype.precision},{dtype.scale})"
    if isinstance(dtype, TimestampDataType):
        return f"timestamp({dtype.unit},{dtype.timezone})"
    return f"duration({dtype.unit})"


def _admits(source: pa.DataType, target: DataType) -> bool:
    if pa.types.is_null(source):
        return True
    if isinstance(target, ScalarDataType):
        if target.name == "boolean":
            return bool(pa.types.is_boolean(source))
        if target.name == "string":
            return bool(pa.types.is_string(source) or pa.types.is_large_string(source))
        if target.name == "int64":
            return bool(pa.types.is_integer(source))
        if target.name == "float64":
            return bool(pa.types.is_floating(source))
        return bool(pa.types.is_date(source))
    if isinstance(target, DecimalDataType):
        return bool(pa.types.is_decimal(source))
    if isinstance(target, TimestampDataType):
        return bool(pa.types.is_timestamp(source))
    return bool(pa.types.is_duration(source))


def _stage_column(series: pd.Series, column: DataColumn) -> pd.Series:
    try:
        values = pa.array(series, from_pandas=True)
    except (pa.ArrowInvalid, pa.ArrowTypeError) as error:
        raise _stage_error(
            f"{column.slot_id}: one typed {_type_name(column.dtype)} column",
            f"{series.dtype}: {error}",
        ) from None
    if not _admits(values.type, column.dtype):
        raise _stage_error(
            f"{column.slot_id}: a lossless {_type_name(column.dtype)} carrier",
            f"{column.slot_id}: {series.dtype} delivered for {_type_name(column.dtype)}",
        )
    if (
        isinstance(column.dtype, TimestampDataType)
        and pa.types.is_timestamp(values.type)
        and values.type.tz != column.dtype.timezone
    ):
        raise _stage_error(
            f"{column.slot_id}: timezone {column.dtype.timezone}",
            f"{column.slot_id}: timezone {values.type.tz} delivered",
        )
    if not column.nullable and values.null_count:
        raise _stage_error(
            f"{column.slot_id}: a nonnullable column without NA",
            f"{values.null_count} NA values",
        )
    try:
        exact = values.cast(stage_arrow_type(column.dtype), safe=True)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as error:
        raise _stage_error(
            f"{column.slot_id}: a lossless {_type_name(column.dtype)} conversion", str(error)
        ) from None
    return pd.Series(pd.arrays.ArrowExtensionArray(exact), name=column.slot_id)


def normalize_stage_frame(
    frame: pd.DataFrame, columns: tuple[DataColumn, ...], keys: tuple[str, ...]
) -> pd.DataFrame:
    """Return a new exact-typed stage frame; source and local outputs share this entry.

    Column order, complete keys, dtype, timezone and NA are checked against the
    planned slots. Lossy conversions fail instead of being cast back afterward.
    """
    expected = tuple(column.slot_id for column in columns)
    if tuple(frame.columns) != expected:
        raise _stage_error(
            f"stage column order {expected!r}", repr(tuple(str(c) for c in frame.columns))
        )
    nonnullable_keys = [
        column.slot_id for column in columns if column.slot_id in keys and not column.nullable
    ]
    if keys and (
        frame.duplicated(list(keys)).any() or frame.loc[:, nonnullable_keys].isna().to_numpy().any()
    ):
        raise _stage_error(f"a complete unique stage key {keys!r}", "null or duplicate key values")
    if not columns:
        return pd.DataFrame(index=pd.RangeIndex(len(frame)))
    return pd.DataFrame(
        {column.slot_id: _stage_column(frame[column.slot_id], column) for column in columns},
        columns=list(expected),
    )


@dataclass(frozen=True, slots=True)
class CheckReceipt:
    """One completed planned check for this exact stage output instance."""

    check_id: str
    stage_id: str


@dataclass(frozen=True, slots=True, eq=False)
class StageOutput:
    """Invocation-local typed pandas stage data; build only through ``from_frame``."""

    columns: tuple[DataColumn, ...]
    keys: tuple[str, ...]
    table: pd.DataFrame
    receipts: tuple[CheckReceipt, ...] = ()
    _token: object = None
    key_groups: tuple[tuple[str, ...], ...] = ()
    layout: ComparisonEndpoint | None = None
    components: tuple[ComponentFrame, ...] = ()
    population_frames: tuple[pd.DataFrame, ...] = ()

    def __post_init__(self) -> None:
        if self._token is not _NORMALIZED:
            raise _stage_error(
                "a normalized stage frame from StageOutput.from_frame",
                "an unnormalized stage table",
            )

    @classmethod
    def from_frame(
        cls,
        frame: pd.DataFrame,
        columns: tuple[DataColumn, ...],
        keys: tuple[str, ...],
        receipts: tuple[CheckReceipt, ...] = (),
        key_groups: tuple[tuple[str, ...], ...] = (),
        layout: ComparisonEndpoint | None = None,
        components: tuple[ComponentFrame, ...] = (),
        population_frames: tuple[pd.DataFrame, ...] = (),
    ) -> StageOutput:
        return cls(
            columns,
            keys,
            normalize_stage_frame(frame, columns, keys),
            receipts,
            _NORMALIZED,
            key_groups,
            layout,
            components,
            population_frames,
        )
