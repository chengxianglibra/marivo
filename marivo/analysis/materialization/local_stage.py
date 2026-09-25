"""Private first-route execution from pure definition to committed Dataset authority."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa

from marivo.analysis.compiler.nodes import (
    CompiledDataset,
    RetainedPartSpec,
)
from marivo.analysis.compiler.placement import (
    ArtifactReadStep,
    PhysicalStageGraph,
    SourceStep,
)
from marivo.analysis.datasets.base import LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.event_attribution import FunnelAttributePayload, FunnelAttributeSpec
from marivo.analysis.domains.event_comparison import FunnelComparePayload, FunnelCompareSpec
from marivo.analysis.materialization import dataset_publication, source_stage
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    ArtifactRecord,
    StorageReceipt,
)
from marivo.analysis.materialization.errors import (
    MaterializationError,
)
from marivo.analysis.materialization.errors import _execution_error as _error
from marivo.analysis.materialization.execution import ExecutionAdapter
from marivo.analysis.materialization.execution_state import ExecutionProgress
from marivo.analysis.materialization.local_execution import (
    ArtifactInput,
    LocalBoundary,
    LocalGraphRequest,
    LocalInputStreams,
    LocalPartInput,
    LocalResult,
    LocalStage,
    StreamInput,
    execute_local,
)
from marivo.analysis.materialization.storage import (
    DatasetWriteResult,
    PartWriteSpec,
)
from marivo.analysis.observation.contracts import (
    MetricPayload,
    RetainedRowsPayload,
)
from marivo.analysis.observation.fold_contracts import RetainedFoldPayload
from marivo.analysis.operators.association_contracts import (
    CorrelatePayload,
    CorrelateSpecV1,
)
from marivo.analysis.operators.candidate_contracts import (
    CandidatePayload,
    CandidateSpecV1,
)
from marivo.analysis.operators.driver_contracts import (
    DriverCandidatePayload,
    DriverCandidateSpecV1,
)
from marivo.analysis.operators.forecast_contracts import (
    ForecastPayload,
    ForecastSpecV1,
)
from marivo.analysis.operators.row import RowCall

if TYPE_CHECKING:
    from marivo.analysis.operators.dsl_j1_values import J1ExecutionResult


def _j1_local_table(rows: list[dict[str, object]], schema: pa.Schema) -> pa.Table:
    for row in rows:
        for field in schema:
            value = row.get(field.name)
            if pa.types.is_float64(field.type) and type(value) is int:
                converted = float(value)
                if not math.isfinite(converted) or int(converted) != value:
                    raise MaterializationError(
                        expected="lossless int64 to float64 J1 output",
                        received=f"{field.name} loses integer precision",
                        repair="Use an admitted numeric output type that retains the exact sum.",
                        stage="local_execution",
                    )
    return pa.Table.from_pylist(rows, schema=schema)


def run_j1_compare_local(
    root: LogicalRootHandle,
    current: J1ExecutionResult,
    baseline: J1ExecutionResult,
) -> J1ExecutionResult:
    """Compute one exact-key private difference over two fixed pandas inputs."""
    from marivo.analysis.compiler.placement import place_j1_local
    from marivo.analysis.operators.dsl_j1_contracts import J1_COMPARE_DIFFERENCE
    from marivo.analysis.operators.dsl_j1_values import J1ExecutionResult, _number

    place_j1_local(root, current.root, baseline_root=baseline.root)
    names = ("member", "value", "cell_tag", "cell_reason")
    if (
        tuple(current.primary.column_names[:4]) != names
        or tuple(baseline.primary.column_names[:4]) != names
        or current.primary.schema.field("member").type
        != baseline.primary.schema.field("member").type
        or current.primary.schema.field("value").type != baseline.primary.schema.field("value").type
    ):
        raise MaterializationError(
            expected="matching keyed numeric comparison endpoints",
            received="endpoint schema differs",
            repair="Select two exact observed Artifacts with matching keys and numeric type.",
            stage="local_admission",
        )
    value_type = str(current.primary.schema.field("value").type)
    J1_COMPARE_DIFFERENCE.require_route("local", "pandas", "entity", value_type)
    left = current.primary.to_pandas(types_mapper=pd.ArrowDtype).copy(deep=True)
    right = baseline.primary.to_pandas(types_mapper=pd.ArrowDtype).copy(deep=True)
    if left["member"].duplicated().any() or right["member"].duplicated().any():
        raise MaterializationError(
            expected="unique endpoint keys",
            received="duplicate comparison key",
            repair="Use exact keyed observations.",
            stage="local_execution",
        )
    if set(left["member"].tolist()) != set(right["member"].tolist()):
        raise MaterializationError(
            expected="complete exact-key endpoint pairing",
            received="missing comparison side",
            repair="Compare observations over the same complete member domain.",
            stage="local_execution",
        )
    indexed_right = right.set_index("member", verify_integrity=True)
    rows: list[dict[str, object]] = []
    for item in left.itertuples(index=False, name=None):
        member, current_value, current_tag, _current_reason = item[:4]
        other = indexed_right.loc[member]
        baseline_value = other["value"]
        if current_tag != "defined" or other["cell_tag"] != "defined":
            raise MaterializationError(
                expected="Defined comparison endpoint Cells",
                received="non-Defined endpoint",
                repair="Choose a method that admits these Cell states.",
                stage="local_execution",
            )
        lhs, rhs = current_value, baseline_value
        _number(lhs)
        _number(rhs)
        difference = lhs - rhs
        _number(difference)
        rows.append(
            {"member": member, "value": difference, "cell_tag": "defined", "cell_reason": None}
        )
    schema = pa.schema(
        [
            current.primary.schema.field("member"),
            pa.field("value", current.primary.schema.field("value").type),
            pa.field("cell_tag", pa.string()),
            pa.field("cell_reason", pa.string()),
        ]
    )
    return J1ExecutionResult(
        root,
        _j1_local_table(rows, schema),
        parts=tuple(
            zip(
                J1_COMPARE_DIFFERENCE.contract.required_parts,
                (current.primary.select(names), baseline.primary.select(names)),
                strict=True,
            )
        ),
        completed_checks=J1_COMPARE_DIFFERENCE.contract.required_checks,
    )


def run_j1_local(root: LogicalRootHandle, retained: J1ExecutionResult) -> J1ExecutionResult:
    """Evaluate one J1 successor over fixed, fully validated Arrow input in pandas."""
    from marivo.analysis.compiler.placement import place_j1_local
    from marivo.analysis.operators.dsl_j1_contracts import j1_numeric_method
    from marivo.analysis.operators.dsl_j1_values import (
        J1ExecutionResult,
        admit_numeric_threshold,
        merge_counts,
        merge_numbers,
    )
    from marivo.analysis.operators.registry import MethodDomain

    place_j1_local(root, retained.root)
    operation = root.operator_id
    method_contract = j1_numeric_method(root)
    if method_contract is not None:
        input_domain: MethodDomain = (
            "group" if "group" in retained.primary.column_names else "entity"
        )
        value_field = retained.primary.schema.field("value").type
        value_type = "float64" if pa.types.is_float64(value_field) else str(value_field)
        method_contract.require_route("local", "pandas", input_domain, value_type)
        inherited_checks = set(method_contract.contract.required_checks) - {
            "strict_current_row_cell"
        }
        missing = inherited_checks - set(retained.completed_checks)
        if missing:
            raise MaterializationError(
                expected="completed input-bound J1 method checks",
                received=f"missing {tuple(sorted(missing))!r}",
                repair="Use a fully validated retained input with exact method evidence.",
                stage="local_admission",
            )
    parameters = root.parameters
    if type(parameters) is not tuple:
        raise _error("implementation_registration")
    if operation == "dsl.j1.members" and root.inputs:
        if "member" not in retained.primary.column_names:
            raise _error("implementation_registration")
        return J1ExecutionResult(
            root,
            retained.primary.select(("member",)),
            completed_checks=retained.completed_checks,
        )
    if operation == "dsl.j1.where" and len(parameters) == 3 and parameters[0] == "numeric":
        if tuple(retained.primary.column_names) != ("member", "value", "cell_tag", "cell_reason"):
            raise _error("implementation_registration")
        comparison = parameters[1]
        if comparison not in ("lt", "lte", "gt", "gte", "eq"):
            raise _error("implementation_registration")
        threshold = admit_numeric_threshold(
            str(retained.primary.schema.field("value").type), parameters[2], "local_admission"
        )
        numeric_rows = retained.primary.to_pylist()
        if any(item["cell_tag"] != "defined" for item in numeric_rows):
            raise MaterializationError(
                expected="finite Defined numeric Cells on the full input domain",
                received="non-Defined selection input",
                repair="Select an admitted complete numeric relation.",
                stage="local_execution",
            )
        selected_keys: set[object] = set()
        selected_rows: list[dict[str, object]] = []
        for item in numeric_rows:
            value = item["value"]
            if type(value) is not int and type(value) is not float:
                raise _error("output_validation")
            if (
                (comparison == "lt" and value < threshold)
                or (comparison == "lte" and value <= threshold)
                or (comparison == "gt" and value > threshold)
                or (comparison == "gte" and value >= threshold)
                or (comparison == "eq" and value == threshold)
            ):
                selected_keys.add(item["member"])
                selected_rows.append(item)
        parts = tuple(
            (
                role,
                part.filter(
                    pa.array(
                        [item["member"] in selected_keys for item in part.to_pylist()],
                        type=pa.bool_(),
                    )
                ),
            )
            for role, part in retained.parts
        )
        return J1ExecutionResult(
            root,
            pa.Table.from_pylist(selected_rows, schema=retained.primary.schema),
            parts=parts,
            completed_checks=tuple(
                dict.fromkeys((*retained.completed_checks, "strict_numeric_cell"))
            ),
        )
    frame = retained.primary.to_pandas(types_mapper=pd.ArrowDtype).copy(deep=True)
    if operation == "dsl.j1.where":
        if tuple(frame.columns) != ("member", "value", "cell_tag", "cell_reason"):
            raise _error("implementation_registration")
        if (frame["cell_tag"] != "defined").any():
            raise MaterializationError(
                expected="Defined categorical input Cells",
                received="non-Defined category",
                repair="Select only an admitted complete category relation.",
                stage="local_execution",
            )
        if len(parameters) != 2 or not isinstance(parameters[1], str):
            raise _error("implementation_registration")
        value = parameters[1]
        selected = frame.loc[frame["value"] == value, ["member"]].copy()
        table = pa.Table.from_pandas(
            selected,
            schema=pa.schema([retained.primary.schema.field("member")]),
            preserve_index=False,
        )
        return J1ExecutionResult(root, table, completed_checks=("strict_category_cell",))
    if operation == "dsl.j1.group":
        if len(parameters) == 2 and parameters[1] == "contribution":
            coordinate = next((part for role, part in retained.parts if role == "coordinate"), None)
            if coordinate is None:
                raise MaterializationError(
                    expected="retained keyed contribution coordinate",
                    received="missing coordinate state",
                    repair="Retain the original coordinate part before grouping.",
                    stage="local_admission",
                )
            part_frame = coordinate.to_pandas(types_mapper=pd.ArrowDtype).copy(deep=True)
            if part_frame["group"].isna().any():
                raise _error("output_validation")
            rows: list[dict[str, object]] = []
            for key, values in part_frame.groupby("group", dropna=False, sort=True):
                support = merge_counts(values["non_null_count"])
                row_count = merge_counts(values["row_count"])
                amount = merge_numbers(values["state_sum"].dropna())
                rows.append(
                    {
                        "group": key,
                        "value": amount if support else None,
                        "cell_tag": "defined" if support else "null",
                        "cell_reason": None if support else "empty_contribution",
                        "state_sum": amount if support else None,
                        "non_null_count": support,
                        "row_count": row_count,
                    }
                )
            schema = pa.schema(
                [
                    coordinate.schema.field("group"),
                    retained.primary.schema.field("value"),
                    retained.primary.schema.field("cell_tag"),
                    retained.primary.schema.field("cell_reason"),
                    retained.primary.schema.field("state_sum"),
                    retained.primary.schema.field("non_null_count"),
                    retained.primary.schema.field("row_count"),
                ]
            )
            return J1ExecutionResult(
                root,
                _j1_local_table(rows, schema),
                completed_checks=retained.completed_checks,
            )
        if tuple(frame.columns) == ("member", "value", "cell_tag", "cell_reason"):
            if (frame["cell_tag"] != "defined").any():
                raise MaterializationError(
                    expected="Defined group categories",
                    received="non-Defined category",
                    repair="Group a complete single-valued category relation.",
                    stage="local_execution",
                )
            selected = frame[["value"]].drop_duplicates().rename(columns={"value": "group"})
            schema = pa.schema([retained.primary.schema.field("value").with_name("group")])
            return J1ExecutionResult(
                root,
                pa.Table.from_pandas(selected, schema=schema, preserve_index=False),
                completed_checks=("strict_category_cell",),
            )
        raise _error("implementation_registration")
    if operation == "dsl.j1.rollup":
        required = {"state_sum", "non_null_count", "row_count"}
        if not required <= set(frame.columns):
            raise MaterializationError(
                expected="complete original Metric state",
                received="missing sum/count support",
                repair="Retain every registered original component before rollup.",
                stage="local_admission",
            )
        support = merge_counts(frame["non_null_count"])
        row_count = merge_counts(frame["row_count"])
        amount = merge_numbers(frame["state_sum"].dropna())
        row: dict[str, object] = {
            "value": amount if support else None,
            "cell_tag": "defined" if support else "null",
            "cell_reason": None if support else "empty_contribution",
            "state_sum": amount if support else None,
            "non_null_count": support,
            "row_count": row_count,
        }
        schema = pa.schema([retained.primary.schema.field(name) for name in row])
        return J1ExecutionResult(
            root,
            _j1_local_table([row], schema),
            completed_checks=retained.completed_checks,
        )
    if operation == "dsl.j1.summarize":
        if not parameters or not isinstance(parameters[0], str):
            raise _error("implementation_registration")
        method = parameters[0]
        count = len(frame)
        if method in ("sum", "mean") and (frame["cell_tag"] != "defined").any():
            raise MaterializationError(
                expected="finite Defined current-row values",
                received="non-Defined current row",
                repair="Select complete Defined rows or use summarize('count').",
                stage="local_execution",
            )
        if method == "count":
            row = {
                "value": count,
                "cell_tag": "defined",
                "cell_reason": None,
                "current_count": count,
            }
            schema = pa.schema(
                [
                    pa.field("value", pa.int64()),
                    pa.field("cell_tag", pa.string()),
                    pa.field("cell_reason", pa.string()),
                    pa.field("current_count", pa.int64()),
                ]
            )
        elif method in ("sum", "mean"):
            values = frame["value"].tolist()
            amount = merge_numbers(values)
            if method == "sum":
                row = {
                    "value": amount,
                    "cell_tag": "defined",
                    "cell_reason": None,
                    "current_sum": amount,
                }
                schema = pa.schema(
                    [
                        pa.field("value", retained.primary.schema.field("value").type),
                        pa.field("cell_tag", pa.string()),
                        pa.field("cell_reason", pa.string()),
                        pa.field("current_sum", retained.primary.schema.field("value").type),
                    ]
                )
            else:
                row = {
                    "value": amount / count if count else None,
                    "cell_tag": "defined" if count else "undefined",
                    "cell_reason": None if count else "empty_mean",
                    "current_sum": amount,
                    "current_count": count,
                }
                schema = pa.schema(
                    [
                        pa.field("value", pa.float64()),
                        pa.field("cell_tag", pa.string()),
                        pa.field("cell_reason", pa.string()),
                        pa.field("current_sum", retained.primary.schema.field("value").type),
                        pa.field("current_count", pa.int64()),
                    ]
                )
        else:
            raise _error("implementation_registration")
        return J1ExecutionResult(
            root,
            _j1_local_table([row], schema),
            completed_checks=tuple(
                dict.fromkeys(
                    (
                        *retained.completed_checks,
                        *(("strict_current_row_cell",) if method in ("sum", "mean") else ()),
                    )
                )
            ),
        )
    raise MaterializationError(
        expected="an admitted J1 retained-input method",
        received=operation,
        repair="Use a registered local continuation with complete fixed input state.",
        stage="local_admission",
    )


if TYPE_CHECKING:
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.dataset_execution import ExecutionEvidence


def _local_output_batches(table: pa.Table) -> list[pa.RecordBatch]:
    """Keep the exact schema when a valid local selection produces zero rows."""
    return table.to_batches(max_chunksize=1024) or [
        pa.RecordBatch.from_arrays(
            [pa.array([], type=field.type) for field in table.schema], schema=table.schema
        )
    ]


def local_input_parts(
    self: DatasetRuntime,
    descriptor: ArtifactDescriptor,
    dataset: LogicalDataset,
    *,
    input_dataset: MaterializedDataset | None = None,
) -> tuple[tuple[LocalPartInput, ...], tuple[Iterable[pa.RecordBatch], ...]]:
    from marivo.analysis.materialization.reads import part_schema
    from marivo.analysis.materialization.retained import component_schema, selected_parts

    selected = selected_parts(descriptor, dataset, input_dataset=input_dataset)
    inputs: list[LocalPartInput] = []
    for part in selected:
        schema = part_schema(self.store.project_root, part)
        keys = component_schema(descriptor.row_contract, part.role, schema)
        inputs.append(
            LocalPartInput(
                part.role,
                part.contract_id,
                part.contract_version,
                schema,
                keys,
                part.storage_receipt,
            )
        )
    return tuple(inputs), ()


def local_output_parts(result: LocalResult) -> tuple[PartWriteSpec, ...]:
    return tuple(
        PartWriteSpec(
            part.role, part.contract_id, part.contract_version, tuple(part.table.column_names)
        )
        for part in result.parts
        if part.contract_id
        in (
            "metric.sufficient_components",
            "delta.sufficient_components",
            "event_funnel.additive_components",
        )
    )


def require_projected_parts(recipe: CompiledDataset) -> None:
    from marivo.analysis.materialization.retained import reject_source_private_transfer

    if any(not isinstance(part, RetainedPartSpec) for part in recipe.retained_parts):
        reject_source_private_transfer()


def source_local_parts(
    dataset: LogicalDataset, recipe: CompiledDataset
) -> tuple[LocalPartInput, ...]:
    from marivo.analysis.materialization.retained import component_schema

    require_projected_parts(recipe)
    schema = recipe.expression.schema().to_pyarrow()
    return tuple(
        LocalPartInput(
            part.role,
            part.contract_id,
            part.contract_version,
            pa.schema([schema.field(name) for name in part.column_names]),
            component_schema(
                dataset.row_contract,
                part.role,
                pa.schema([schema.field(name) for name in part.column_names]),
            ),
        )
        for part in recipe.retained_parts
        if isinstance(part, RetainedPartSpec)
    )


def run_local_graph(
    self: DatasetRuntime,
    physical: PhysicalStageGraph,
    boundaries: tuple[LocalBoundary, ...],
    streams: tuple[LocalInputStreams, ...],
    run_ref: str,
    *,
    cancel_source: Callable[[], None],
) -> LocalResult:
    from marivo.analysis.operators.attribution_contracts import (
        AttributePayload,
        AttributeSpecV1,
    )
    from marivo.analysis.operators.contracts import ComparePayload, CompareSpecV1

    stages: list[LocalStage] = []
    for step in physical.local_steps:
        root = step.dataset._root
        if not isinstance(root, LogicalRootHandle):
            raise _error("implementation_registration", run_ref)
        payload = root.payload
        call: (
            RowCall
            | FunnelCompareSpec
            | FunnelAttributeSpec
            | CompareSpecV1
            | AttributeSpecV1
            | CorrelateSpecV1
            | ForecastSpecV1
            | CandidateSpecV1
            | DriverCandidateSpecV1
        )
        if isinstance(
            payload,
            (
                ComparePayload,
                FunnelComparePayload,
                FunnelAttributePayload,
                AttributePayload,
                CorrelatePayload,
                ForecastPayload,
                CandidatePayload,
                DriverCandidatePayload,
            ),
        ):
            call = payload.spec
        elif isinstance(payload, (MetricPayload, RetainedRowsPayload, RetainedFoldPayload)):
            if len(step.inputs) != 1:
                raise _error("implementation_registration", run_ref)
            source = step.dataset._inputs[0]
            call = RowCall(
                step.implementation.local_method or "",
                source.row_contract,
                source.row_set_contract,
                step.dataset.row_contract,
                step.dataset.row_set_contract,
                payload.predicate if not isinstance(payload, RetainedFoldPayload) else None,
                payload.rank if not isinstance(payload, RetainedFoldPayload) else None,
                payload.limit_count if not isinstance(payload, RetainedFoldPayload) else None,
                payload.spec if isinstance(payload, RetainedFoldPayload) else None,
            )
        else:
            raise _error("implementation_registration", run_ref)
        stages.append(LocalStage(step.output, step.inputs, call))
    request = LocalGraphRequest(boundaries, tuple(stages), physical.primary_output)
    self._event("local_execution_started")
    result = execute_local(request, streams)
    self.statistics.local_handoffs = result.handoffs
    self._event("local_execution_completed")
    return result


def _source_count(backend: ExecutionAdapter, recipe: CompiledDataset, *, role: str) -> object:
    count_sql = backend.compile(recipe.expression.aggregate(__mv_rows=recipe.expression.count()))
    return backend.read_scalar(
        backend.statement(
            count_sql,
            role=role,
            inputs=(
                backend.prepare(recipe.expression.aggregate(__mv_rows=recipe.expression.count())),
            ),
        )
    )


def _correlation_input(
    self: DatasetRuntime,
    step: SourceStep,
    backend: ExecutionAdapter,
    recipe: CompiledDataset,
    run_ref: str,
) -> tuple[LocalBoundary, LocalInputStreams]:
    from marivo.analysis.materialization.local_execution import PairInput

    root = step.dataset._root
    if not isinstance(root, LogicalRootHandle) or not isinstance(root.payload, CorrelatePayload):
        raise _error("implementation_registration", run_ref)
    pair_count = _source_count(backend, recipe, role="correlation_cardinality")
    if type(pair_count) is not int or pair_count < 0:
        raise _error("output_validation", run_ref)
    return (
        LocalBoundary(step.output, PairInput(root.payload.spec, pair_count)),
        LocalInputStreams(source_stage.batches(self, backend, recipe.expression, 1024)),
    )


def _distribution_input(
    self: DatasetRuntime,
    step: SourceStep,
    backend: ExecutionAdapter,
    recipe: CompiledDataset,
    run_ref: str,
) -> tuple[LocalBoundary, LocalInputStreams]:
    from marivo.analysis.materialization.local_execution import CoalitionInput
    from marivo.analysis.operators.attribution_contracts import AttributePayload

    root = step.dataset._root
    if (
        not isinstance(root, LogicalRootHandle)
        or not isinstance(root.payload, AttributePayload)
        or recipe.numerical_input != "distribution_coalitions"
    ):
        raise _error("implementation_registration", run_ref)
    expected_count = _source_count(backend, recipe, role="distribution_cardinality")
    if (
        not isinstance(expected_count, int)
        or isinstance(expected_count, bool)
        or expected_count < 0
    ):
        raise MaterializationError(
            expected="a non-negative source-certified coalition count",
            received="invalid coalition count",
            repair="Narrow comparison scopes or lower top_k before retrying.",
            stage="transfer_guard",
            run_ref=run_ref,
        )
    return (
        LocalBoundary(step.output, CoalitionInput(root.payload.spec, expected_count)),
        LocalInputStreams(source_stage.batches(self, backend, recipe.expression, 1024)),
    )


def _projected_source_input(
    self: DatasetRuntime,
    dataset: LogicalDataset,
    step: SourceStep,
    backend: ExecutionAdapter,
    recipe: CompiledDataset,
) -> tuple[LocalBoundary, LocalInputStreams]:
    from marivo.analysis.materialization.retained import required_part_roles

    needed_roles = required_part_roles(dataset, input_dataset=step.dataset)
    selected_specs = tuple(part for part in recipe.retained_parts if part.role in needed_roles)
    require_projected_parts(replace(recipe, retained_parts=selected_specs))
    names = tuple(
        dict.fromkeys(
            (
                *recipe.primary_columns,
                *(
                    name
                    for part in selected_specs
                    if isinstance(part, RetainedPartSpec)
                    for name in part.column_names
                ),
            )
        )
    )
    projected = replace(
        recipe,
        expression=recipe.expression.select(*names),
        retained_parts=selected_specs,
    )
    local_parts = source_local_parts(step.dataset, projected)
    return (
        LocalBoundary(
            step.output,
            StreamInput(
                step.dataset.row_contract,
                step.dataset.row_set_contract,
                wide_parts=bool(local_parts),
            ),
            local_parts,
        ),
        LocalInputStreams(source_stage.batches(self, backend, projected.expression, 1024)),
    )


def _retained_input(
    self: DatasetRuntime,
    dataset: LogicalDataset,
    step: ArtifactReadStep,
    records: Mapping[str, ArtifactRecord],
) -> tuple[LocalBoundary, LocalInputStreams]:
    descriptor = records[step.dataset.state.artifact_ref.ref].descriptor
    local_parts, part_batches = local_input_parts(
        self, descriptor, dataset, input_dataset=step.dataset
    )
    selected = ArtifactInput(
        self.store.project_root,
        descriptor.storage_receipt,
        step.dataset.row_contract,
        step.dataset.row_set_contract,
    )
    return LocalBoundary(step.output, selected, local_parts), LocalInputStreams((), part_batches)


def _local_inputs(
    self: DatasetRuntime,
    dataset: LogicalDataset,
    physical: PhysicalStageGraph,
    prepared: Mapping[int, tuple[ExecutionAdapter, CompiledDataset, dict[str, ir.Table]]],
    records: Mapping[str, ArtifactRecord],
    run_ref: str,
) -> tuple[tuple[LocalBoundary, ...], tuple[LocalInputStreams, ...]]:
    boundaries: list[LocalBoundary] = []
    streams: list[LocalInputStreams] = []
    for step in physical.steps:
        if isinstance(step, SourceStep):
            backend, recipe, _ = prepared[step.output]
            if step.operation == "correlation":
                boundary, stream = _correlation_input(self, step, backend, recipe, run_ref)
            elif step.operation == "distribution":
                boundary, stream = _distribution_input(self, step, backend, recipe, run_ref)
            else:
                boundary, stream = _projected_source_input(self, dataset, step, backend, recipe)
        elif isinstance(step, ArtifactReadStep):
            boundary, stream = _retained_input(self, dataset, step, records)
        else:
            continue
        boundaries.append(boundary)
        streams.append(stream)
    return tuple(boundaries), tuple(streams)


def execute_local_stages(
    self: DatasetRuntime,
    dataset: LogicalDataset,
    physical: PhysicalStageGraph,
    prepared: Mapping[int, tuple[ExecutionAdapter, CompiledDataset, dict[str, ir.Table]]],
    records: Mapping[str, ArtifactRecord],
    run_ref: str,
    evidence: ExecutionEvidence,
    progress: ExecutionProgress,
) -> tuple[str, DatasetWriteResult[StorageReceipt]]:
    boundaries, streams = _local_inputs(self, dataset, physical, prepared, records, run_ref)
    progress.phase = "stage_execution"

    def cancel_sources() -> None:
        for backend, _, _ in prepared.values():
            backend.interrupt()

    local_result = run_local_graph(
        self, physical, boundaries, streams, run_ref, cancel_source=cancel_sources
    )
    evidence.attribution_summary = (
        local_result.summaries.attribution or evidence.attribution_summary
    )
    evidence.association_summary = (
        local_result.summaries.association or evidence.association_summary
    )
    evidence.forecast_summary = local_result.summaries.forecast or evidence.forecast_summary
    evidence.candidate_summary = local_result.summaries.candidate or evidence.candidate_summary
    evidence.validations = [
        (
            "source_prefix.final_row_key_unique"
            if name == "dataset.final_row_key_unique"
            else name,
            value,
        )
        for name, value in evidence.validations
    ]
    evidence.validations.append(("dataset.final_row_key_unique", 0))
    progress.phase = "storage_staging"
    return dataset_publication.write_output(
        self,
        dataset,
        _local_output_batches(local_result.table),
        run_ref,
        parts=local_output_parts(local_result),
        source_key_validation=True,
    )
