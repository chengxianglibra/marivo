"""R4.3 private source execution through issued R1 reads and Arrow exchange."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from typing import NoReturn

import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa

from marivo.analysis.compiler.cell_lowering import lower_cells
from marivo.analysis.compiler.graph_lowering import (
    IntegrityCheck,
    LoweredLocal,
    LoweredPlan,
    LoweredQuery,
    LoweredRead,
    LoweredRelation,
    SemanticCheck,
    TemporalCheck,
)
from marivo.analysis.compiler.graph_plan import (
    CheckRequirement,
    LocalCompute,
    PlannedCheck,
    SourceField,
    SourceRead,
    SourceSchema,
)
from marivo.analysis.core.cell_encoding import (
    CellBook,
    CellEncoding,
    CellFields,
    EncodedCell,
    KnownCell,
    ValidityCell,
)
from marivo.analysis.core.data_types import (
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.graph import BoundGraph, MethodNode
from marivo.analysis.core.model import Defined, DerivedQuantity, ReferenceStatePart
from marivo.analysis.core.rules import (
    AssociationFit,
    AssociationRead,
    AttributionDerive,
    BindProject,
    CellCompare,
    CellDerive,
    ComparisonRead,
    DeviationFit,
    DeviationRead,
    DisplayRank,
    DisplayTable,
    ForecastFit,
    ForecastRead,
    PartsTransport,
    PreparedObservation,
    ReferenceDerive,
    RowState,
    TimeRunRead,
    TimeRuns,
)
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.cell_arrow import (
    binding,
    from_rows,
    project,
    required,
    rows,
    schema_binding,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import (
    CompletedCheck,
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    PartContract,
    StageOutput,
    from_pandas,
)
from marivo.analysis.materialization.graph_execution import PreparedGraph
from marivo.analysis.materialization.graph_preflight import bind_entity_output, relation_operations
from marivo.analysis.materialization.graph_spearman_execution import finish_spearman
from marivo.analysis.methods.comparison import evaluate as evaluate_comparison
from marivo.analysis.methods.comparison import propagated_cell, propagated_error
from marivo.analysis.methods.registry import REGISTRY
from marivo.datasource.adapters import (
    DURATION_UNIT_METADATA_KEY,
    TIMESTAMP_UNIT_METADATA_KEY,
    BoundSource,
    CompiledRead,
    PhysicalRequirement,
    SourceSession,
    provider_for,
)
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic.validator import normalize_target_entity


def _as_arrow(frame: pd.DataFrame, schema: pa.Schema) -> pa.Table:
    try:
        return pa.Table.from_pandas(frame, schema=schema, preserve_index=False, safe=False)
    except (pa.ArrowException, TypeError, ValueError) as error:
        raise _invalid(
            f"pandas result cannot enter the frozen Arrow schema: {type(error).__name__}"
        ) from error


def _validate_pandas_cells(frame: pd.DataFrame, schema: pa.Schema) -> None:
    for cell in binding(schema, exact=True).cells:
        values = frame[cell.fields.value]
        missing = values.isna()
        if isinstance(cell, KnownCell):
            if len(frame) and (missing.any() if cell.code == 0 else not missing.all()):
                raise _invalid("source result disagrees with its known Cell")
        elif isinstance(cell, ValidityCell):
            continue
        else:
            codes = frame[cell.state]
            if codes.isna().any():
                raise _invalid("source result contains an absent Cell state code")
            admitted = {0, *cell.book.codes()}
            if cell.optional:
                admitted.add(-1)
            for raw, value_is_missing in zip(codes, missing, strict=True):
                if isinstance(raw, bool):
                    raise _invalid("source result contains a non-integer Cell state")
                if not isinstance(raw, int | float):
                    try:
                        code = int(raw)
                    except (OverflowError, TypeError, ValueError) as error:
                        raise _invalid("source result contains a non-integer Cell state") from error
                    if code != raw:
                        raise _invalid("source result contains a non-integer Cell state")
                else:
                    code = int(raw)
                if code not in admitted:
                    raise _invalid("source result contains an undeclared Cell state")
                if (code == 0) == value_is_missing:
                    raise _invalid("source result disagrees with its Cell state")
                if code > 0:
                    cell.book.decode(code)


def _project_schema(schema: pa.Schema, columns: tuple[str, ...]) -> pa.Schema:
    empty = pa.Table.from_batches((), schema=schema)
    return project(empty, columns).schema


def _split_transport(
    stage: LoweredRelation, table: pd.DataFrame, schema: pa.Schema, transport_schema: pa.Schema
) -> tuple[pd.DataFrame, tuple[ExchangePart, ...], dict[str, pa.Schema]]:
    from marivo.analysis.compiler.coordinate_state import physical

    if "__marivo_coordinate_row" in table.columns:
        from marivo.analysis.core.model import CoordinateStatePart, part_role
        from marivo.analysis.methods.coordinate_state import retain

        keys = tuple(key.column for key in stage.layout.keys)
        positions: dict[tuple[object, ...], int] = {}
        for index, key in enumerate(
            table.loc[:, keys].itertuples(index=False, name=None)
            if keys
            else (() for _ in range(len(table)))
        ):
            positions.setdefault(tuple(key), index)
        primary = table.iloc[list(positions.values())].loc[:, schema.names].copy()
        part = next(p for p in stage.node.signature.parts if isinstance(p, CoordinateStatePart))
        actual = table.loc[table["__marivo_coordinate_row"].fillna(False)]
        columns = [*keys, *("__coordinate__" + name for name in (*part.columns, *part.components))]
        full = _as_arrow(
            actual.loc[:, columns],
            pa.schema([transport_schema.field(name) for name in columns]),
        ).rename_columns([*keys, *part.columns, *part.components])
        retained = retain(
            stage.node.signature,
            part_role(part),
            full,
            _as_arrow(primary, _project_schema(schema, stage.layout.columns)),
        )
        return (
            primary,
            (ExchangePart(part_role(part), retained.to_pandas()),),
            {part_role(part): retained.schema},
        )

    children = physical(stage)
    kinds = table["__marivo_row_kind"]
    if kinds.isna().any() or any(value not in range(len(children) + 1) for value in kinds):
        raise _invalid("terminal transport contains an absent or unknown row kind")
    result: list[pd.DataFrame] = []
    for index, expression in enumerate((stage.expression, *(value for _, value in children))):
        selected = table.loc[table["__marivo_row_kind"] == index]
        names = tuple(expression.columns)
        result.append(
            selected.loc[:, [f"__part_{index}__{name}" for name in names]]
            .rename(
                columns=dict(zip((f"__part_{index}__{name}" for name in names), names, strict=True))
            )
            .reset_index(drop=True)
        )
    return (
        result[0],
        tuple(
            ExchangePart(role, value) for (role, _), value in zip(children, result[1:], strict=True)
        ),
        {role: expression.schema().to_pyarrow() for role, expression in children},
    )


def _invalid(received: str) -> MaterializationError:
    return MaterializationError(
        expected="one admitted and qualified source graph with completed checks",
        received=received,
        repair="Use the exact lowered plan and selected R1 source session.",
        stage="graph_source",
    )


def _issue(
    source: SourceSession,
    lowered: LoweredPlan,
    expression: ir.Table,
    *,
    purpose: str,
) -> CompiledRead:
    bindings = lowered.sources_for(expression)
    if not bindings:
        raise _invalid("source expression has no exact R1 binding")
    qualified = tuple(source.qualify(item.source, lowered.source_requirement) for item in bindings)
    stage_policies = {
        item.expression.op(): item.cell_reasons
        for item in lowered.stages
        if isinstance(item, LoweredRelation)
        and any(
            getattr(stage, "operation", None) == "prepare" and stage.output == item.output
            for stage in lowered.admitted.stages
        )
    }
    raw_cells: dict[ops.Relation, tuple[CellEncoding, ...]] = {}
    for item in lowered.bindings:
        cell = item.layout.cell
        if cell is not None:
            fields = CellFields(cell.value, cell.tag, cell.reason)
            encoded = EncodedCell(
                fields, CellBook.from_reasons(item.cell_reasons), cell.tag + "__state"
            )
            relation = item.source.relation.op()
            previous = raw_cells.get(relation, ())
            if not any(old.fields == fields for old in previous):
                raw_cells[relation] = (*previous, encoded)
    compact = lower_cells(expression, raw_cells=raw_cells, stage_policies=stage_policies)
    rewritten = compact.expression
    if not isinstance(rewritten, ir.Table):
        raise _invalid("rewritten stage is not an Ibis table")
    # Ibis outer joins can widen state nullability; the Cell carrier is still
    # mandatory (including the non-null code for an absent optional operand).
    rewritten = rewritten.mutate(
        **{
            cell.state: rewritten[cell.state].cast("!int16")
            for cell in compact.cells
            if isinstance(cell, EncodedCell) and rewritten[cell.state].type().nullable
        }
    )
    schema = schema_binding(
        compact.expression.schema().to_pyarrow(), compact.cells, compact.logical_columns
    )
    if not rewritten.schema().to_pyarrow().equals(schema, check_metadata=False):
        differences = tuple(
            (name, str(dtype), str(rewritten.schema().get(name)))
            for name, dtype in compact.expression.schema().items()
            if rewritten.schema().get(name) != dtype
        )
        raise _invalid(f"staged relation changes emitted field types: {differences}")
    return source.compile(qualified, rewritten, purpose=purpose, expected_schema=schema)


def _read(
    source: SourceSession,
    lowered: LoweredPlan,
    expression: ir.Table,
    *,
    purpose: str,
    keys: tuple[str, ...] = (),
    validate_cells: bool = False,
    cell_reasons: tuple[tuple[str, tuple[str, ...]], ...] = (),
) -> tuple[pd.DataFrame, pa.Schema]:
    issued = _issue(source, lowered, expression, purpose=purpose)
    frame = source.execute(issued)
    if tuple(frame.columns) != tuple(issued.schema.names):
        raise _invalid("source DataFrame columns differ from the issued schema")
    if binding(issued.schema).cells:
        _validate_pandas_cells(frame, issued.schema)
    if keys and (
        frame.loc[:, list(keys)].isna().to_numpy().any() or frame.duplicated(list(keys)).any()
    ):
        raise _invalid("source result contains a null or duplicate complete key")
    if validate_cells and {"value", "cell_tag", "cell_reason"} <= set(frame.columns):
        reasons = dict(cell_reasons)
        for row in frame.loc[:, ["value", "cell_tag", "cell_reason"]].to_dict("records"):
            value, tag, reason = row["value"], row["cell_tag"], row["cell_reason"]
            if tag == "defined":
                if pd.isna(value) or not pd.isna(reason):
                    raise _invalid("source result contains an invalid Defined Cell")
            elif (
                tag not in ("null", "undefined", "unknown")
                or not pd.isna(value)
                or reason not in reasons.get(tag, ())
            ):
                raise _invalid("source result contains an invalid non-Defined Cell")
    return frame, issued.schema


def _consume_field(stage: LoweredRelation, table: pd.DataFrame, schema: pa.Schema) -> None:
    """Require the operand returned by a field lookup, without proving its mapping."""
    params = stage.node.parameters if isinstance(stage.node, MethodNode) else None
    if not isinstance(params, BindProject) or params.field_contract is None:
        return
    cell = stage.layout.cell
    assert cell is not None
    encoding = next(
        (item for item in binding(schema, exact=True).cells if item.fields.tag == cell.tag),
        None,
    )
    if encoding is None:
        raise _invalid("required field Cell tag has no exact schema binding")
    missing = (
        int((table[encoding.state] == -1).sum())
        if isinstance(encoding, EncodedCell) and encoding.optional
        else 0
    )
    if missing:
        raise MaterializationError(
            expected=f"an owner operand for {params.ref.path} on every consumed complete key",
            received=f"required field owner operand is missing for {missing} consumed complete keys",
            repair=f"Restore the required {params.field_owner.path} records for this field lookup and retry.",
            stage="source_execution",
        )


def _field_lookup(stage: LoweredRelation) -> bool:
    """Unknown field matching must be consumed before a later SQL operation drops rows."""
    node = stage.node
    if not isinstance(node, MethodNode) or not isinstance(node.parameters, BindProject):
        return False
    params = node.parameters
    return params.field_contract is not None and (
        bool(params.path)
        or params.owner_selection != node.inputs[0].node.signature.domain.version_selection
    )


def _check(
    source: SourceSession,
    lowered: LoweredPlan,
    check: IntegrityCheck | SemanticCheck | TemporalCheck,
) -> CompletedCheck | None:
    table, _schema = _read(
        source,
        lowered,
        check.violations,
        purpose="analysis.graph.check",
        validate_cells=False,
    )
    if isinstance(check, TemporalCheck):
        from datetime import date, datetime, timezone

        from marivo.analysis.core.time_grid import instant

        for row in table.to_dict("records"):
            raw, actual = row["raw_time"], row["normalized_time"]
            if raw is None and actual is None:
                continue
            from marivo.semantic.ir import StrptimeParse

            if isinstance(raw, str) and isinstance(check.axis.parse, StrptimeParse):
                try:
                    raw = datetime.strptime(raw, check.axis.parse.format)
                except ValueError as error:
                    raise _invalid(
                        "source value does not match the declared temporal format"
                    ) from error
            if isinstance(raw, datetime):
                read_zone = check.axis.timezone or "UTC"
                try:
                    expected_time: date = instant(raw, read_zone).replace(tzinfo=None)
                except DatasetConstructionError as error:
                    raise MaterializationError(
                        expected=f"one unique native instant for {check.axis.ref.path} in {read_zone}",
                        received=f"invalid or ambiguous wall timestamp {raw.isoformat()}",
                        repair=f"Correct the stored {check.axis.ref.path} wall timestamps using {read_zone}, or store timestamp values with explicit UTC offsets.",
                        stage="source_time_validation",
                    ) from error
                if isinstance(actual, datetime) and actual.tzinfo is not None:
                    actual = actual.astimezone(timezone.utc).replace(tzinfo=None)
            elif isinstance(raw, date):
                expected_time = raw
            else:
                raise _invalid("unqualified temporal source representation")
            if actual != expected_time:
                raise _invalid("source engine timezone rules differ from frozen temporal authority")
        return None
    if not table.empty:
        expected = (
            check.expected
            if isinstance(check, IntegrityCheck)
            else str(check.requirement.obligation.fact.kind)
        )
        repair = "Correct the exact source rows or choose a qualified input."
        if isinstance(check, SemanticCheck):
            fact = check.requirement.obligation.fact
            owner = next(
                (
                    item.node if isinstance(item, LoweredRelation) else item.stage.node
                    for item in lowered.stages
                    if isinstance(item, (LoweredRelation, LoweredLocal))
                    and (
                        item.node if isinstance(item, LoweredRelation) else item.stage.node
                    ).identity
                    == check.requirement.node_id
                ),
                None,
            )
            if fact.subject_id.startswith("buckets:"):
                expected += ": complete original period buckets without renumbering"
                repair = (
                    "Use the complete captured period grid; retain its original bucket coordinates."
                )
            elif fact.subject_id.startswith("coverage:"):
                expected += ": complete original observation state coverage"
                repair = "Use original observations with complete retained state before applying Metric empty finish."
            elif fact.subject_id.startswith("fold_alignment:"):
                expected += ": aligned pre-fold sampling coordinates"
                repair = (
                    "Keep separate spatial groups unless their captured sampling coordinates align."
                )
            elif (
                fact.kind == "key_set_equal"
                and isinstance(owner, MethodNode)
                and isinstance(owner.parameters, CellDerive)
            ):
                repair = "Use inputs with equal complete key sets, or compare with align=mv.outer() to preserve missing sides explicitly."
            elif (
                fact.kind == "mapping_total"
                and isinstance(owner, MethodNode)
                and isinstance(owner.parameters, BindProject)
            ):
                repair = "Provide the required operand for every consumed complete key."
            elif (
                fact.kind == "cell_policy"
                and isinstance(owner, MethodNode)
                and isinstance(owner.parameters, BindProject)
            ):
                expected += f": a valid scalar Cell for {owner.parameters.ref.path} on each consumed complete key"
                repair = f"Correct missing or invalid source values for {owner.parameters.ref.path}; classification coordinates require Defined scalar values for every consumed complete key."
        if expected.startswith("r7."):
            from marivo.analysis.core.domain_captures import DomainPreparationError

            raise DomainPreparationError(
                expected.split(":", 1)[0],
                "prepare",
                expected,
                f"{len(table)} violating captured rows",
                "Correct the named occurrence keys, participant or version before retrying.",
            )
        raise MaterializationError(
            expected=expected,
            received=f"{len(table)} violating rows",
            repair=repair,
            stage="graph_check",
        )
    if isinstance(check, SemanticCheck):
        digest = hashlib.sha256(
            repr((tuple(table.columns), tuple(map(str, table.dtypes)))).encode()
        ).hexdigest()
        return CompletedCheck(check.requirement, digest, check.consumers)
    return None


def _ordered_checks(
    completed: list[CompletedCheck], pending: tuple[CheckRequirement, ...]
) -> tuple[CompletedCheck, ...]:
    """Freeze all origin-group checks in the plan's order, independent of deadline order."""
    if any(proof.requirement not in pending for proof in completed):
        raise _invalid("completed source check has no admitted origin")
    ordered: list[CompletedCheck] = []
    for requirement in pending:
        proofs = tuple(
            proof
            for proof in completed
            if proof.requirement == requirement or requirement in proof.consumers
        )
        if not proofs:
            raise _invalid("source check has no completed evidence")
        combined = hashlib.sha256(
            "".join(proof.result_digest for proof in proofs).encode()
        ).hexdigest()
        ordered.append(CompletedCheck(requirement, combined))
    return tuple(ordered)


def _result(
    stage: LoweredRelation,
    table: pd.DataFrame,
    completed: tuple[CompletedCheck, ...],
    pending: tuple[CheckRequirement, ...],
    retained_parts: tuple[ExchangePart, ...] = (),
    schema: pa.Schema | None = None,
    retained_schemas: dict[str, pa.Schema] | None = None,
) -> ExchangeResult:
    if not isinstance(stage.node, MethodNode):
        raise _invalid("root must be a registered method node")
    if schema is None:
        raise _invalid("pandas source result lacks its issued Arrow schema")
    from marivo.analysis.core.model import AttributionPart, DisplayPart
    from marivo.analysis.methods.physical import DurationType

    def restore_duration(name: str, dtype: pa.DataType) -> None:
        nonlocal schema
        assert schema is not None
        table[name] = (
            pa.array(table[name], type=pa.int64(), from_pandas=True)
            .cast(dtype)
            .to_pandas(types_mapper=pd.ArrowDtype)
        )
        field = schema.field(name)
        schema = schema.set(schema.get_field_index(name), field.with_type(dtype))

    if isinstance(stage.node.value_type, DurationType) and "value" in table.columns:
        restore_duration("value", pa.duration(stage.node.value_type.unit))
    for declaration in stage.node.signature.parts:
        if isinstance(declaration, DisplayPart):
            import ibis.expr.datatypes as dt

            for component, dtype in zip(declaration.components, declaration.types, strict=True):
                if dtype.startswith("interval("):
                    name = declaration.role + "__" + component
                    if name in table.columns:
                        restore_duration(name, dt.dtype(dtype).to_pyarrow())
                        if declaration.role == "columns":
                            restore_duration(component, dt.dtype(dtype).to_pyarrow())
    if "original_state__samples" in table.columns:
        from marivo.analysis.methods.temporal_fold import decode_samples, encode_samples

        try:
            table["original_state__samples"] = table["original_state__samples"].map(
                lambda value: encode_samples(decode_samples(value))
            )
        except (ValueError, TypeError, OverflowError) as error:
            raise _invalid("invalid source pre-fold samples") from error
    key_names = tuple(item.column for item in stage.layout.keys)
    cell_names = (
        ()
        if stage.layout.cell is None
        else (
            stage.layout.cell.value,
            stage.layout.cell.tag,
            stage.layout.cell.reason,
        )
    )
    primary_names = (*key_names, *stage.layout.extras, *cell_names)
    primary_schema = _project_schema(schema, primary_names)
    primary = table.loc[:, list(primary_schema.names)].copy()
    saved_schemas = retained_schemas or {}
    part_contracts: list[PartContract] = []
    parts: list[ExchangePart] = []
    from marivo.analysis.core.model import part_role

    for part in stage.layout.parts:
        role = part_role(part.part)
        names = (*key_names, *(component.column for component in part.columns))
        selected_schema = saved_schemas.get(role, _project_schema(schema, names))
        retained = next((item for item in retained_parts if item.role == role), None)
        selected = (
            retained.table.loc[:, list(selected_schema.names)].copy()
            if retained is not None
            else table.loc[:, list(selected_schema.names)].copy()
        )
        if isinstance(stage.node.value_type, DurationType) and isinstance(
            part.part, AttributionPart
        ):
            for name in (
                ("contribution", "current", "baseline")
                if role == "allocation"
                else ("target", "total")
                if role == "reconciliation"
                else ()
            ):
                column = role + "__" + name
                dtype = pa.duration(stage.node.value_type.unit)
                selected[column] = (
                    pa.array(selected[column], type=pa.int64(), from_pandas=True)
                    .cast(dtype)
                    .to_pandas(types_mapper=pd.ArrowDtype)
                )
                selected_schema = selected_schema.set(
                    selected_schema.get_field_index(column),
                    selected_schema.field(column).with_type(dtype),
                )
        from marivo.analysis.materialization.graph_reference import part_keys
        from marivo.analysis.methods.coordinate_state import (
            PartitionCoordinateLayout,
        )
        from marivo.analysis.methods.coordinate_state import (
            layout as coordinate_layout,
        )

        spec = coordinate_layout(stage.node.signature, role)
        retained_keys = (
            tuple(dict.fromkeys((*spec.keys, *spec.coordinates)))
            if isinstance(spec, PartitionCoordinateLayout)
            else part_keys(stage.node.signature, role)
        )
        part_contracts.append(PartContract(role, selected_schema, retained_keys))
        parts.append(ExchangePart(role, selected))
    from marivo.analysis.methods.coordinate_state import (
        KeyedCoordinateLayout,
        PartitionCoordinateLayout,
    )
    from marivo.analysis.methods.coordinate_state import (
        layout as coordinate_layout,
    )

    for retained in retained_parts:
        if any(part.role == retained.role for part in parts):
            continue
        spec = coordinate_layout(stage.node.signature, retained.role)
        if not isinstance(spec, (KeyedCoordinateLayout, PartitionCoordinateLayout)):
            raise _invalid("undeclared contribution receipt")
        keys = (
            tuple(dict.fromkeys((*spec.keys, *spec.coordinates)))
            if isinstance(spec, PartitionCoordinateLayout)
            else spec.keys
        )
        selected_schema = saved_schemas.get(retained.role)
        if selected_schema is None:
            raise _invalid("retained source part lacks its frozen schema")
        parts.append(retained)
        part_contracts.append(PartContract(retained.role, selected_schema, keys))
    source_ids = ",".join(stage.source_ids)
    state_kind = REGISTRY.lookup(stage.node.method).semantics.persistent_state_kind
    if state_kind is None:
        raise _invalid(f"method {stage.node.method} has no durable state qualification")
    if state_kind == "spearman":
        for status, value in zip(primary["status"], primary["value"], strict=True):
            if status == "valid" and (
                type(value) is not float or not math.isfinite(value) or abs(value) > 1 + 1e-12
            ):
                raise _invalid(
                    "finite valid Spearman coefficient in [-1, 1] required at conversion"
                )
    state: pd.DataFrame | None = None
    state_schema: pa.Schema | None = None
    if state_kind != "none":
        statuses = (
            ["accepted"] * len(primary)
            if state_kind
            in (
                "cohort",
                "table",
            )
            else primary["status"].tolist()
            if state_kind == "spearman"
            else _pandas_cell_values(primary, primary_schema, "cell_tag")
        )
        state_values: dict[str, object] = {key: primary[key].tolist() for key in key_names}
        state_values["status"] = statuses
        state_fields = [
            *(primary_schema.field(name) for name in key_names),
            pa.field("status", pa.string()),
        ]
        if (
            state_kind in ("share", "penetration", "standardized")
            and isinstance(stage.node.signature.quantity, DerivedQuantity)
            and stage.node.signature.quantity.method_version.startswith("reference.")
        ):
            from marivo.analysis.materialization.graph_exchange import reference_parameters
            from marivo.analysis.materialization.graph_reference import _pandas_error_bounds

            state_values["error_bound"] = _pandas_error_bounds(
                reference_parameters(stage.node.signature),
                tuple(parts),
                primary,
                {part.role: part.schema for part in part_contracts},
                primary_schema,
            )
            state_fields.append(pa.field("error_bound", pa.float64()))
        state_schema = pa.schema(state_fields)
        state = pd.DataFrame(state_values)
    contract = ExchangeContract(
        stage.node.signature,
        stage.node.method,
        source_ids,
        primary_schema,
        key_names,
        tuple(part_contracts),
        stage.cell_reasons,
        state_kind,
        state_schema,
        pending,
        (
            isinstance(stage.node.parameters, (CellDerive, DisplayRank, DisplayTable))
            or (
                isinstance(stage.node.parameters, PartsTransport)
                and (
                    stage.node.parameters.mode in ("where", "limit")
                    or stage.node.parameters.display_view is not None
                )
            )
        )
        and not key_names,
        stage.column_reasons,
    )
    return from_pandas(
        primary,
        contract,
        parts=tuple(parts),
        completed_checks=completed,
        method_state=state,
        validate=False,
    )


def _pandas_cell_values(frame: pd.DataFrame, schema: pa.Schema, name: str) -> list[str | None]:
    if name in frame.columns:
        return [None if pd.isna(value) else str(value) for value in frame[name].tolist()]
    cell = next(
        (item for item in binding(schema, exact=True).cells if item.fields.tag == name), None
    )
    if cell is None:
        raise _invalid(f"pandas exchange lacks the logical Cell field {name}")
    if isinstance(cell, KnownCell):
        tag, _reason = cell.book.decode(cell.code)
        return [tag] * len(frame)
    if isinstance(cell, ValidityCell):
        defined = cell.book.decode(0)[0]
        missing = cell.book.decode(cell.missing_code)[0]
        return [missing if absent else defined for absent in frame[cell.fields.value].isna()]
    result: list[str | None] = []
    for raw in frame[cell.state].tolist():
        code = int(raw)
        result.append(None if code == -1 else cell.book.decode(code)[0])
    return result


def _local_checks(
    source: SourceSession, lowered: LoweredPlan, output: str
) -> tuple[CompletedCheck, ...]:
    """Run the semantic checks owned by one source-local successor output."""
    proofs = (
        _check(source, lowered, check)
        for check in lowered.checks
        if isinstance(check, SemanticCheck) and check.requirement.stage_output == output
    )
    return tuple(proof for proof in proofs if proof is not None)


def execute_source_graph(
    prepared: PreparedGraph, lowered: LoweredPlan, source: SourceSession
) -> ExchangeResult:
    """Evaluate a private source graph without Run, Store or Artifact publication."""
    if (
        prepared.admitted is not lowered.admitted
        or lowered.admitted.classification.kind != "source"
    ):
        raise _invalid("prepared and lowered graph identity or input class differs")
    if any(
        isinstance(stage.node, MethodNode)
        and isinstance(stage.node.parameters, (PreparedObservation,))
        for stage in lowered.stages
        if isinstance(stage, LoweredRelation)
    ) or any(
        isinstance(stage, LoweredLocal)
        and (
            (
                isinstance(stage.stage.node.parameters, PartsTransport)
                and stage.stage.node.parameters.mode == "business_coverage"
            )
            or isinstance(
                stage.stage.node.parameters,
                (
                    CellCompare,
                    ComparisonRead,
                    PartsTransport,
                    RowState,
                    DisplayRank,
                    DisplayTable,
                    CellDerive,
                    ReferenceDerive,
                    AssociationFit,
                    AssociationRead,
                    ForecastFit,
                    ForecastRead,
                    TimeRuns,
                    TimeRunRead,
                    DeviationFit,
                    DeviationRead,
                ),
            )
        )
        for stage in lowered.stages
    ):
        from marivo.analysis.materialization.graph_preparation import execute as execute_preparation

        return execute_preparation(prepared, lowered, source)
    local = tuple(stage for stage in lowered.stages if isinstance(stage, LoweredLocal))
    if local and (
        any(
            len(item.stage.inputs) != 1
            or item.stage.node.method.name
            not in (
                "association.spearman",
                "cell.difference",
                "cell.relative_change",
                "cell.ratio",
                "reference.share",
                "reference.penetration",
                "reference.standardize",
                "attribution.additive_difference",
                "attribution.component_mix",
                "display.rank",
                "display.table",
            )
            or (
                item.stage.node.method.name == "association.spearman"
                and item.stage.output != lowered.primary_output
            )
            for item in local
        )
    ):
        raise _invalid("unqualified source-local successor")
    primary = next(
        (
            stage
            for stage in lowered.stages
            if isinstance(stage, LoweredRelation) and stage.output == lowered.primary_output
        ),
        None,
    )
    if primary is None and not local:
        raise _invalid("missing lowered primary relation")
    # Native checks and terminal output are independent live reads.
    direct_native = not local and all(
        isinstance(stage, LoweredRelation) for stage in lowered.stages
    )
    completed: list[CompletedCheck] = []
    tables: dict[str, pd.DataFrame] = {}
    issued_reads: dict[str, CompiledRead] = {}
    read_schemas: dict[str, pa.Schema] = {}
    final_local: LoweredRelation | None = None
    coordinate_parts: dict[str, tuple[ExchangePart, ...]] = {}
    coordinate_schemas: dict[str, dict[str, pa.Schema]] = {}
    reference_results: dict[str, ExchangeResult] = {}
    try:
        for stage in lowered.stages:
            if isinstance(stage, LoweredLocal):
                predecessor = next(
                    (
                        item
                        for item in lowered.stages
                        if isinstance(item, LoweredRelation)
                        and item.output == stage.stage.inputs[0]
                    ),
                    None,
                )
                if predecessor is not None and isinstance(
                    stage.stage.node.parameters, AttributionDerive
                ):
                    from marivo.analysis.materialization.graph_attribution import pack, result

                    schemas = dict(coordinate_schemas.get(predecessor.output, {}))
                    endpoint_parts: list[ExchangePart] = []
                    for role, expression in predecessor.part_expressions:
                        if role not in ("current_endpoint", "baseline_endpoint", "basis"):
                            continue
                        endpoint_parts.append(
                            ExchangePart(
                                role,
                                tables[predecessor.output].loc[:, list(expression.columns)].copy(),
                            )
                        )
                        schemas[role] = expression.schema().to_pyarrow()
                    retained = (
                        *coordinate_parts.get(predecessor.output, ()),
                        *endpoint_parts,
                    )
                    finished = result(
                        stage.stage.node.signature,
                        stage.stage.node.value_type,
                        retained,
                        ",".join(predecessor.source_ids),
                        schemas=schemas,
                    )
                    table = pack(finished, read_schemas[predecessor.output])
                    coordinate_parts[stage.stage.output] = finished.parts
                    coordinate_schemas[stage.stage.output] = {
                        part.role: contract_part.schema
                        for part, contract_part in zip(
                            finished.parts, finished.contract.parts, strict=True
                        )
                    }
                    tables[stage.stage.output] = table
                    read_schemas[stage.stage.output] = read_schemas[predecessor.output]
                    final_local = replace(predecessor, output=stage.stage.output)
                    completed.extend(_local_checks(source, lowered, stage.stage.output))
                    continue
                if predecessor is not None and isinstance(
                    stage.stage.node.parameters, (DisplayRank, DisplayTable)
                ):
                    from marivo.analysis.materialization.graph_display import finish

                    table = finish(
                        stage.stage.node,
                        tables[predecessor.output],
                        read_schemas[predecessor.output],
                    )
                    final_local = replace(predecessor, output=stage.stage.output)
                    coordinate_parts[stage.stage.output] = coordinate_parts.get(
                        predecessor.output, ()
                    )
                    coordinate_schemas[stage.stage.output] = coordinate_schemas.get(
                        predecessor.output, {}
                    )
                    tables[stage.stage.output] = table
                    read_schemas[stage.stage.output] = read_schemas[predecessor.output]
                    completed.extend(_local_checks(source, lowered, stage.stage.output))
                    continue
                if predecessor is not None and isinstance(
                    stage.stage.node.parameters, ReferenceDerive
                ):
                    from marivo.analysis.materialization.graph_reference import (
                        _ReferenceParts,
                    )
                    from marivo.analysis.materialization.graph_reference import (
                        result as reference_result,
                    )

                    part_frames: list[ExchangePart] = []
                    part_schemas: dict[str, pa.Schema] = {}
                    for role, expression in predecessor.part_expressions:
                        part_index = (
                            1
                            if role in ("fixed_reference", "strata")
                            else 2
                            if role == "reference_proof"
                            and stage.stage.node.parameters.kind == "share"
                            else 0
                        )
                        input_identity = stage.stage.node.inputs[part_index].node.identity
                        prior = reference_results.get(input_identity)
                        if prior is not None:
                            from marivo.analysis.materialization.graph_exchange import (
                                _pandas_records,
                            )

                            schema = expression.schema().to_pyarrow()
                            prior_rows = _pandas_records(prior.primary, prior.contract.schema)
                            if "error_bound" in schema.names:
                                assert prior.method_state is not None
                                bounds = {
                                    tuple(item[name] for name in prior.contract.key_fields): item[
                                        "error_bound"
                                    ]
                                    for item in prior.method_state.to_dict("records")
                                }
                                for prior_row in prior_rows:
                                    key = tuple(
                                        prior_row[name] for name in prior.contract.key_fields
                                    )
                                    prior_row["error_bound"] = bounds[key]
                            frame = pd.DataFrame.from_records(
                                (
                                    {name: prior_row[name] for name in schema.names}
                                    for prior_row in prior_rows
                                ),
                                columns=schema.names,
                            )
                        else:
                            frame, schema = _read(
                                source,
                                lowered,
                                expression,
                                purpose="analysis.graph.reference",
                                cell_reasons=next(
                                    part.cell_reasons
                                    for part in predecessor.node.signature.parts
                                    if isinstance(part, ReferenceStatePart) and part.role == role
                                ),
                            )
                        part_frames.append(ExchangePart(role, frame))
                        part_schemas[role] = schema
                    parts = _ReferenceParts(tuple(part_frames), part_schemas)
                    finished = reference_result(
                        stage.stage.node, parts, ",".join(predecessor.source_ids)
                    )
                    reference_results[stage.stage.node.identity] = finished
                    coordinate_parts[stage.stage.output] = finished.parts
                    coordinate_schemas[stage.stage.output] = {
                        contract_part.role: contract_part.schema
                        for contract_part in finished.contract.parts
                    }
                    table = tables[predecessor.output]
                    keys = tuple(key.column for key in predecessor.layout.keys)
                    from marivo.analysis.materialization.graph_exchange import _pandas_records

                    finished_rows = {
                        tuple(row[name] for name in keys): row
                        for row in _pandas_records(finished.primary, finished.contract.schema)
                    }
                    assert finished.method_state is not None
                    bounds = {
                        tuple(row[name] for name in keys): row["error_bound"]
                        for row in finished.method_state.to_dict("records")
                    }
                    reference_rows: list[dict[str, object]] = []
                    for raw in table.to_dict("records"):
                        row: dict[str, object] = {
                            name: value for name, value in raw.items() if isinstance(name, str)
                        }
                        key = tuple(row[name] for name in keys)
                        row.update(
                            {
                                name: finished_rows[key][name]
                                for name in ("value", "cell_tag", "cell_reason")
                            }
                        )
                        row["reference_proof__retained"] = bounds[key]
                        reference_rows.append(row)
                    table = from_rows(
                        reference_rows, issued_reads[predecessor.output].schema
                    ).to_pandas()
                    tables[stage.stage.output] = table
                    read_schemas[stage.stage.output] = issued_reads[predecessor.output].schema
                    final_local = replace(predecessor, output=stage.stage.output)
                    completed.extend(_local_checks(source, lowered, stage.stage.output))
                    continue
                if predecessor is not None and isinstance(stage.stage.node.parameters, CellDerive):
                    node = stage.stage.node
                    params = node.parameters
                    assert isinstance(params, CellDerive)
                    table = tables[predecessor.output]
                    from marivo.analysis.materialization.graph_exchange import _pandas_records

                    for side, input_index in (("current", 0), ("baseline", 1)):
                        prior = reference_results.get(node.inputs[input_index].node.identity)
                        if prior is None:
                            continue
                        if prior.method_state is None:
                            raise _invalid("source reference result lacks retained error bounds")
                        prior_rows = _pandas_records(prior.primary, prior.contract.schema)
                        key_fields = prior.contract.key_fields
                        values_by_key = {
                            tuple(row[name] for name in key_fields): row for row in prior_rows
                        }
                        bounds_by_key = {
                            tuple(row[name] for name in key_fields): row["error_bound"]
                            for row in prior.method_state.to_dict("records")
                        }
                        if values_by_key.keys() != bounds_by_key.keys():
                            raise _invalid("source reference primary and error-bound keys differ")
                        updated_rows: list[dict[str, object]] = [
                            {str(name): value for name, value in record.items()}
                            for record in table.to_dict("records")
                        ]
                        for row in updated_rows:
                            key = tuple(
                                row[f"correspondence__{side}_key_{index}"]
                                for index in range(len(key_fields))
                            )
                            value = values_by_key.get(key)
                            if value is None or key not in bounds_by_key:
                                raise _invalid(
                                    f"source reference result lacks a matched {side} endpoint"
                                )
                            row.update(
                                {
                                    f"{side}_endpoint__value": value["value"],
                                    f"{side}_endpoint__cell_tag": value["cell_tag"],
                                    f"{side}_endpoint__cell_reason": value["cell_reason"],
                                    f"correspondence__{side}_error_bound": bounds_by_key[key],
                                }
                            )
                        table = pd.DataFrame.from_records(updated_rows, columns=table.columns)
                    comparison_rows: list[dict[str, object]] = []
                    schema = read_schemas[predecessor.output]
                    logical_rows = rows(_as_arrow(table, schema))
                    for row in logical_rows:
                        left_tag = required(row["current_endpoint__cell_tag"], str)
                        right_tag = required(row["baseline_endpoint__cell_tag"], str)
                        left_reason = (
                            ""
                            if left_tag == "defined"
                            else required(row["current_endpoint__cell_reason"], str)
                        )
                        right_reason = (
                            ""
                            if right_tag == "defined"
                            else required(row["baseline_endpoint__cell_reason"], str)
                        )
                        propagated = propagated_cell(left_tag, left_reason, right_tag, right_reason)
                        if propagated is not None:
                            row["value"] = None
                            row["cell_tag"], row["cell_reason"] = propagated
                            row["correspondence__result_error_bound"] = 0.0
                            comparison_rows.append(row)
                            continue
                        try:
                            cell = evaluate_comparison(
                                params.method,
                                row["current_endpoint__value"],
                                row["baseline_endpoint__value"],
                                node.inputs[0].node.value_type,
                                node.inputs[1].node.value_type,
                            )
                        except (ValueError, OverflowError) as error:
                            raise _invalid(f"comparison finish failed: {error}") from error
                        try:
                            row["correspondence__result_error_bound"] = propagated_error(
                                params.method,
                                row["current_endpoint__value"],
                                row["baseline_endpoint__value"],
                                cell.value if isinstance(cell, Defined) else None,
                                required(row["correspondence__current_error_bound"], float),
                                required(row["correspondence__baseline_error_bound"], float),
                            )
                        except (ValueError, OverflowError) as error:
                            raise _invalid(str(error)) from error
                        row["value"] = cell.value if isinstance(cell, Defined) else None
                        row["cell_tag"] = "defined" if isinstance(cell, Defined) else "undefined"
                        row["cell_reason"] = None if isinstance(cell, Defined) else cell.reason
                        comparison_rows.append(row)
                    table = from_rows(comparison_rows, schema).to_pandas()
                    final_local = replace(predecessor, output=stage.stage.output)
                    # Validate the finished Cell, endpoint and correspondence exchange before use.
                    coordinate_parts[stage.stage.output] = coordinate_parts.get(
                        predecessor.output, ()
                    )
                    coordinate_schemas[stage.stage.output] = coordinate_schemas.get(
                        predecessor.output, {}
                    )
                    _result(
                        final_local,
                        table,
                        (),
                        (),
                        coordinate_parts[stage.stage.output],
                        issued_reads[predecessor.output].schema,
                    )
                    tables[stage.stage.output] = table
                    read_schemas[stage.stage.output] = schema
                    completed.extend(_local_checks(source, lowered, stage.stage.output))
                    continue
                if predecessor is None or predecessor.layout.extras != (
                    "va",
                    "taga",
                    "reasona",
                    "vb",
                    "tagb",
                    "reasonb",
                ):
                    raise _invalid("missing exact paired source preparation")
                paired = tables[predecessor.output]
                from marivo.analysis.materialization.cell_arrow import logical_schema
                from marivo.analysis.materialization.graph_exchange import _pandas_records

                paired_schema = logical_schema(read_schemas[predecessor.output])
                paired = pd.DataFrame.from_records(
                    _pandas_records(paired, read_schemas[predecessor.output]),
                    columns=paired_schema.names,
                )
                keys = tuple(item.column for item in predecessor.layout.keys)
                left_schema = pa.schema(
                    [
                        *(paired_schema.field(key) for key in keys),
                        paired_schema.field("va").with_name("value"),
                        paired_schema.field("taga").with_name("cell_tag"),
                        paired_schema.field("reasona").with_name("cell_reason"),
                    ]
                )
                right_schema = pa.schema(
                    [
                        *(paired_schema.field(key) for key in keys),
                        paired_schema.field("vb").with_name("value"),
                        paired_schema.field("tagb").with_name("cell_tag"),
                        paired_schema.field("reasonb").with_name("cell_reason"),
                    ]
                )
                left = paired.loc[:, [*keys, "va", "taga", "reasona"]].rename(
                    columns={"va": "value", "taga": "cell_tag", "reasona": "cell_reason"}
                )
                right = paired.loc[:, [*keys, "vb", "tagb", "reasonb"]].rename(
                    columns={"vb": "value", "tagb": "cell_tag", "reasonb": "cell_reason"}
                )
                return finish_spearman(
                    stage,
                    left,
                    right,
                    keys,
                    (left_schema, right_schema),
                    ",".join(predecessor.source_ids),
                    _ordered_checks(completed, lowered.admitted.checks),
                    lowered.admitted.checks,
                )
            if not isinstance(stage, LoweredRelation):
                raise _invalid("unqualified source stage")
            for check in lowered.checks:
                if (
                    isinstance(check, SemanticCheck)
                    and check.requirement.stage_output == stage.output
                    and check.requirement.obligation.before == "consume"
                ):
                    proof = _check(source, lowered, check)
                    if proof is not None:
                        completed.append(proof)
            if (
                direct_native
                and stage.output != lowered.primary_output
                and not _field_lookup(stage)
            ):
                continue
            issued = _issue(
                source,
                lowered,
                stage.expression,
                purpose="analysis.graph.stage",
            )
            issued_reads[stage.output] = issued
            read_schemas[stage.output] = issued.schema
            expression = stage.transport if stage.transport is not None else stage.expression
            if stage.transport is None:
                table = source.execute(issued)
            else:
                received, transport_schema = _read(
                    source,
                    lowered,
                    expression,
                    purpose="analysis.graph.stage",
                    cell_reasons=stage.cell_reasons,
                )
                table, retained, retained_schemas = _split_transport(
                    stage, received, issued.schema, transport_schema
                )
                coordinate_parts[stage.output] = retained
                coordinate_schemas[stage.output] = retained_schemas
                if tuple(table.columns) != tuple(issued.schema.names):
                    raise _invalid("split transport columns differ from the issued graph stage")
            _consume_field(stage, table, issued.schema)
            tables[stage.output] = table
            for check in lowered.checks:
                if (
                    isinstance(check, (IntegrityCheck, TemporalCheck))
                    and check.stage_output == stage.output
                ) or (
                    isinstance(check, SemanticCheck)
                    and check.requirement.stage_output == stage.output
                    and check.requirement.obligation.before == "publish"
                ):
                    proof = _check(source, lowered, check)
                    if proof is not None:
                        completed.append(proof)
        if final_local is not None and final_local.output == lowered.primary_output:
            primary = final_local
        assert primary is not None
        retained_parts = list(coordinate_parts.get(primary.output, ()))
        retained_schemas = dict(coordinate_schemas.get(primary.output, {}))
        retained_roles = {part.role for part in retained_parts}
        for role, expression in primary.part_expressions:
            if role in retained_roles:
                continue
            frame, part_schema = _read(
                source,
                lowered,
                expression,
                purpose="analysis.graph.part",
                cell_reasons=next(
                    (
                        part.cell_reasons
                        for part in primary.node.signature.parts
                        if isinstance(part, ReferenceStatePart) and part.role == role
                    ),
                    (),
                ),
            )
            retained_parts.append(ExchangePart(role, frame))
            retained_schemas[role] = part_schema
        return _result(
            primary,
            tables[primary.output],
            _ordered_checks(completed, lowered.admitted.checks),
            lowered.admitted.checks,
            tuple(retained_parts),
            read_schemas[primary.output],
            retained_schemas,
        )
    finally:
        pass


# R2.3 Query execute preparation and SourceRead execution. Preparation binds each
# contribution root once and admits column types from the schema alone; it never
# submits a business read. The legacy graph execution above is unchanged.


@dataclass(frozen=True, slots=True, eq=False)
class PreparedSources:
    """Schema-only bindings for one Query invocation and its open source session."""

    source: SourceSession
    schemas: tuple[SourceSchema, ...]
    relations: dict[str, ir.Table]
    bound: dict[str, BoundSource]
    sources: dict[str, SourceSession] = field(default_factory=dict)


def _preparation_error(expected: str, received: str) -> MaterializationError:
    return MaterializationError(
        expected=expected,
        received=received,
        repair="Use a source column whose declared type converts losslessly; no business row was read.",
        stage="execute_preparation",
        help_target="Query.execute",
    )


def _route_gap(expected: str, received: str, repair: str) -> NoReturn:
    from marivo.analysis.errors import AnalysisStageUnavailableError

    raise AnalysisStageUnavailableError(
        stage="query_execution",
        expected=expected,
        received=received,
        repair=f"{repair} No business row was read and no Run was admitted.",
        target="Query.execute",
    )


def admit_source_type(field: pa.Field, path: str) -> DataType:
    """Map one bound source column to its lossless stage type, before any read."""
    physical = field.type
    unit = (field.metadata or {}).get(DURATION_UNIT_METADATA_KEY)
    if unit is not None:
        if physical != pa.int64() or unit not in (b"s", b"ms", b"us", b"ns"):
            raise _preparation_error("int64 ticks with a fixed Duration unit", repr(field))
        return {
            b"s": DurationDataType("s"),
            b"ms": DurationDataType("ms"),
            b"us": DurationDataType("us"),
            b"ns": DurationDataType("ns"),
        }[unit]
    if pa.types.is_signed_integer(physical) or physical in (pa.uint8(), pa.uint16(), pa.uint32()):
        return ScalarDataType("int64")
    if pa.types.is_floating(physical):
        return ScalarDataType("float64")
    if pa.types.is_boolean(physical):
        return ScalarDataType("boolean")
    if pa.types.is_string(physical) or pa.types.is_large_string(physical):
        return ScalarDataType("string")
    if physical == pa.date32():
        return ScalarDataType("date")
    if pa.types.is_decimal128(physical):
        return DecimalDataType(physical.precision, physical.scale)
    if pa.types.is_timestamp(physical) and physical.tz is not None:
        original_unit = (field.metadata or {}).get(TIMESTAMP_UNIT_METADATA_KEY)
        units = (b"s", b"ms", b"us", b"ns")
        if original_unit in units and units.index(original_unit) > units.index(
            physical.unit.encode("ascii")
        ):
            raise _preparation_error(
                f"{path}: a lossless timestamp[{original_unit.decode('ascii')}] source carrier",
                f"{path}: {physical} loses source precision",
            )
        return TimestampDataType(physical.unit, physical.tz)
    if pa.types.is_duration(physical):
        return DurationDataType(physical.unit)
    raise _preparation_error(
        f"{path}: a column type with a lossless stage carrier",
        f"{path}: {physical}",
    )


def _admitted(schema: pa.Schema, path: str, column: str) -> SourceField:
    index = schema.get_field_index(column)
    if index < 0:
        raise _preparation_error(
            f"{path}: column {column!r} on the bound source", repr(schema.names)
        )
    item = schema.field(index)
    return SourceField(path, column, admit_source_type(item, path), item.nullable)


def _query_entities(graph: BoundGraph, state: CompiledSemanticState) -> tuple[str, ...]:
    """Resolve every required Entity before opening connections."""
    from marivo._definition.expressions import MemberCount, PerReduce, PerWeightedMean
    from marivo._definition.graph import walk

    closure = walk((graph.root,), resolve=dict(graph.signature.declared_metrics).get)
    if any(isinstance(node, (MemberCount, PerReduce, PerWeightedMean)) for node in closure.order):
        _route_gap(
            "ordinary source reductions and Metric formulas",
            "Population/sample preparation",
            "Population and internal sample execution are delivered in R3.",
        )
    observation = graph.signature.observation
    entities: dict[str, None] = {}
    for contribution in () if observation is None else observation.contributions:
        root = contribution.root.ref.path
        entities[root] = None
        for read in (*contribution.reads, *contribution.coordinates):
            entities[read.entity.ref.path] = None
            for step in read.path:
                relation = state.registry.relationships[step.ref.path]
                entities[relation.from_entity] = None
                entities[relation.to_entity] = None
            if (
                state.registry.entities[root].datasource
                != state.registry.entities[read.entity.ref.path].datasource
            ):
                _route_gap(
                    "scalar relationship endpoints on one datasource",
                    read.field.ref.path,
                    "Reduce independent datasource roots separately before combining Metrics.",
                )
    return tuple(entities)


def prepare_query_sources(
    graph: BoundGraph,
    state: CompiledSemanticState,
    source: SourceSession,
    *,
    sources: dict[str, SourceSession] | None = None,
) -> PreparedSources:
    """Prepare all physical fields and checked relationships without business reads."""
    from marivo.analysis.materialization.query_preparation import physical_fields, prepare_fields
    from marivo.datasource.timezone import probe_engine_timezone

    available = sources or {source.datasource.semantic_id: source}
    registry, sidecar = state.registry, state.sidecar
    entities: dict[str, ir.Table] = {}
    bounds: dict[str, BoundSource] = {}
    owners: dict[str, SourceSession] = {}
    for path in _query_entities(graph, state):
        contract = normalize_target_entity(registry, path)
        owner = available[contract.datasource_ref.path]
        if owner.provider.name != "duckdb":
            _route_gap(
                "a qualified numeric datasource route",
                owner.provider.name,
                "Use a qualified DuckDB datasource for this numeric route.",
            )
        bound = owner.bind(contract.source, source_identity=path)
        entities[path] = bind_entity_output(
            registry, sidecar, path, owner, bound, contract.dependency_fingerprint
        )
        bounds[path] = bound
        owners[path] = owner
    schemas: list[SourceSchema] = []
    relations: dict[str, ir.Table] = {}
    observation = graph.signature.observation
    for contribution in () if observation is None else observation.contributions:
        root = contribution.root.ref.path
        prepared = prepare_fields(
            contribution, entities, state, probe_engine_timezone(owners[root]._backend)
        )
        for dependency in prepared.dependencies:
            for physical in physical_fields(dependency):
                for bound in bounds.values():
                    if physical.rel == bound.relation.op():
                        raw = bound.facts.schema.field(physical.name)
                        metadata = raw.metadata or {}
                        if (
                            DURATION_UNIT_METADATA_KEY in metadata
                            or TIMESTAMP_UNIT_METADATA_KEY in metadata
                        ):
                            admit_source_type(raw, f"{root}: {physical.name}")
        table = prepared.table
        schema = table.schema().to_pyarrow()
        keys = tuple(_admitted(schema, name, name) for name in registry.entities[root].primary_key)
        fields = []
        originals = dict(prepared.originals)
        for path, column in prepared.columns:
            admitted = _admitted(schema, path, column)
            expression = originals[column]
            duration = None
            for physical in physical_fields(expression):
                for bound in bounds.values():
                    if physical.rel == bound.relation.op():
                        raw = bound.facts.schema.field(physical.name)
                        metadata = raw.metadata or {}
                        if not (
                            DURATION_UNIT_METADATA_KEY in metadata
                            or TIMESTAMP_UNIT_METADATA_KEY in metadata
                        ):
                            continue
                        dtype = admit_source_type(raw, path)
                        if isinstance(dtype, DurationDataType):
                            if duration is not None and duration != dtype:
                                raise _preparation_error("one exact Duration unit", path)
                            duration = dtype
            if duration is not None and (
                isinstance(admitted.dtype, DecimalDataType)
                or admitted.dtype in (ScalarDataType("int64"), ScalarDataType("float64"))
            ):
                admitted = replace(admitted, dtype=duration)
            fields.append(admitted)
        schemas.append(
            SourceSchema(root, keys, tuple(fields), contribution.occurrence, prepared.checks)
        )
        stage_id = "read:" + ".".join(str(i) for i in contribution.occurrence)
        relations[stage_id] = table
    return PreparedSources(source, tuple(schemas), relations, bounds, owners)


@contextmanager
def open_query_sources(
    service: DatasourceConnectionService, graph: BoundGraph, state: CompiledSemanticState
) -> Iterator[PreparedSources]:
    """Open the declared datasource sessions used by this invocation."""
    entities = _query_entities(graph, state)
    datasources = tuple(
        dict.fromkeys(state.registry.entities[path].datasource for path in entities)
    )
    with ExitStack() as stack:
        sources: dict[str, SourceSession] = {}
        for name in datasources:
            datasource = state.registry.datasources[name]
            backend = stack.enter_context(service.use_backend(datasource.name, read_only=True))
            sources[name] = stack.enter_context(
                SourceSession(provider_for(datasource.backend_type), datasource, backend)
            )
        if not sources:
            _route_gap(
                "a source contribution",
                "source-free query",
                "Execute the local-only plan directly.",
            )
        yield prepare_query_sources(graph, state, next(iter(sources.values())), sources=sources)


def read_stage(prepared: PreparedSources, lowered: LoweredRead) -> StageOutput:
    """Submit one lowered SourceRead and normalize its pandas result."""
    stage = lowered.stage
    expression = lowered.expression
    source = prepared.sources.get(stage.source_id, prepared.source)
    relations = set(expression.op().find(ops.Relation))
    qualified = tuple(
        source.qualify(
            bound, PhysicalRequirement("query.source_read", 1, relation_operations(expression))
        )
        for path, bound in prepared.bound.items()
        if prepared.sources.get(path, prepared.source) is source
        and bound.relation.op() in relations
    )
    issued = source.compile(
        qualified,
        expression,
        purpose=f"query.{stage.stage_id}",
        expected_schema=expression.schema().to_pyarrow(),
    )
    frame = source.execute(issued)
    from marivo.semantic.metric_graph_lowering import numeric_failure

    for name, check in lowered.numeric_checks:
        if frame[name].fillna(False).any():
            coordinates = frame.loc[frame[name].fillna(False), list(stage.keys)].head(6)
            received = f"{check.received}; source={stage.source_id}; coordinates={coordinates.to_dict(orient='records')}"
            if any(name == column for column, _, _ in stage.preparation_checks):
                raise MaterializationError(
                    expected=check.expected,
                    received=received,
                    repair="Inspect the relationship keys and exact snapshot/validity selection. Correct missing matches or overlapping rows so each selected contribution has exactly one matching field row, then retry.",
                    stage="scalar_field_lookup",
                    help_target="Query.execute",
                )
            numeric_failure(check.expected, received)
    if "__numeric_present" in frame.columns:
        frame = frame.loc[frame["__numeric_present"].fillna(False)]
    from fractions import Fraction

    from marivo._definition.expressions import SourceAggregate, SourceWeightedMean

    reduction = stage.reduction
    if (
        reduction is not None
        and reduction.duration is not None
        and isinstance(reduction.node, SourceAggregate)
    ):
        node = reduction.node
        if node.reducer in ("mean", "median", "quantile"):
            finished: list[int | None] = []
            for row in frame.to_dict("records"):
                count = int(row["__duration_count"])
                if count == 0:
                    finished.append(None)
                    continue
                lower = int(row[reduction.value_slot])
                if node.reducer == "mean":
                    exact = Fraction(lower, count)
                else:
                    q = 0.5 if node.reducer == "median" else node.q
                    assert q is not None
                    position = Fraction(q * (count - 1))
                    fraction = position - (position.numerator // position.denominator)
                    exact = lower + fraction * (int(row["__duration_upper"]) - lower)
                finished.append(round(exact))
            frame[reduction.value_slot] = pd.Series(
                finished, dtype="int64[pyarrow]", index=frame.index
            )
    if (
        reduction is not None
        and reduction.duration is not None
        and isinstance(reduction.node, SourceWeightedMean)
    ):
        values: list[int | None] = []
        for numerator, denominator in zip(
            frame[reduction.value_slot], frame["__duration_weight_sum"], strict=True
        ):
            ticks = None if denominator == 0 else round(Fraction(numerator) / Fraction(denominator))
            if ticks is not None and not -(2**63) <= ticks < 2**63:
                numeric_failure("signed int64 Duration ticks", "weighted_mean output overflow")
            values.append(ticks)
        frame[reduction.value_slot] = pd.Series(values, dtype="int64[pyarrow]", index=frame.index)
    for column in stage.columns:
        if isinstance(column.dtype, DurationDataType):
            try:
                tick_array = pa.array(frame[column.slot_id], type=pa.int64(), from_pandas=True)
                frame[column.slot_id] = pd.Series(
                    pd.arrays.ArrowExtensionArray(tick_array.cast(pa.duration(column.dtype.unit))),
                    index=frame.index,
                )
            except (pa.ArrowException, OverflowError, ValueError, TypeError) as error:
                numeric_failure("lossless signed int64 Duration ticks", str(error))
    return StageOutput.from_frame(
        frame.loc[:, [c.slot_id for c in stage.columns]], stage.columns, stage.keys
    )


def execute_numeric_query(
    prepared: PreparedSources | None,
    lowered: LoweredQuery,
    *,
    observe_read: Callable[[LoweredRead, StageOutput], None] | None = None,
    observe_local: Callable[[LocalCompute, StageOutput], None] | None = None,
) -> StageOutput:
    """Execute the fixed numeric schedule and return invocation-local pandas data."""
    from marivo.analysis.core.model import reject
    from marivo.analysis.materialization.graph_execution import run_query_plan
    from marivo.analysis.materialization.graph_local_execution import run_local

    def check(requirement: PlannedCheck, output: StageOutput) -> None:
        reject(
            "an implemented Query check algorithm",
            requirement.check_id,
            "Bind a qualified check route before executing this plan.",
            "analysis.query_check",
        )

    if lowered.plan.checks:
        reject(
            "implemented Query check algorithms",
            repr(tuple(item.check_id for item in lowered.plan.checks)),
            "Use the qualified numeric checks emitted by lowering; no business row was read.",
            "analysis.query_check",
        )

    def read(stage: SourceRead) -> StageOutput:
        if prepared is None:
            reject(
                "prepared source connections",
                stage.stage_id,
                "Prepare the source before execution.",
                "analysis.query_source",
            )
        request = lowered.read(stage.stage_id)
        output = read_stage(prepared, request)
        if observe_read is not None:
            observe_read(request, output)
        return output

    def local(stage: LocalCompute, inputs: tuple[StageOutput, ...]) -> StageOutput:
        output = run_local(stage, inputs)
        if observe_local is not None:
            observe_local(stage, output)
        return output

    return run_query_plan(lowered.plan, read, local, check)
