"""Public result projections owned by an exact checked Store 8 Artifact."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

import pandas as pd
import pyarrow as pa

from marivo._data_render import _DataCard, _validate_display
from marivo.analysis.core.graph import MethodNode
from marivo.analysis.core.model import (
    AttributionPart,
    CoveragePart,
    DerivedQuantity,
    DisplayPart,
    FitInputsPart,
    FunnelAllocationPart,
    FunnelComparisonPart,
    FunnelPart,
    ObservedQuantity,
    PairInputsPart,
    RolledQuantity,
    Signature,
    TrainingInputsPart,
)
from marivo.analysis.core.rules import (
    BindProject,
    CellDerive,
    DisplayTable,
    ObserveCount,
    ObserveMetric,
    ObserveWeightedMean,
    PartsTransport,
    PreparedObservation,
    ReferenceDerive,
)
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.state import MaterializedDatasetState, _materialized_state
from marivo.analysis.errors import AnalysisRepair
from marivo.analysis.evidence import _dataset_types as t
from marivo.analysis.materialization import graph_store
from marivo.analysis.materialization.graph_exchange import ExchangeResult
from marivo.analysis.materialization.graph_protocol import DESCRIPTOR, digest, encode, invalid
from marivo.analysis.materialization.graph_snapshot import GraphDocument, MethodRecord, Record
from marivo.analysis.materialization.graph_storage import read_result
from marivo.analysis.materialization.graph_store import GraphArtifact
from marivo.analysis.methods.semantics import observation_disclosure
from marivo.analysis.refs import ArtifactRef
from marivo.introspection.live.model import LiveHelpTarget
from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES
from marivo.semantic.runtime_metric import RuntimeMetricExpr

if TYPE_CHECKING:
    from marivo.analysis.materialization.admission import DatasetRuntime

_NUMERIC_TYPES = frozenset(
    {
        *(
            f"decimal:{precision}:{scale}"
            for precision in range(1, 39)
            for scale in range(precision + 1)
        ),
        *(f"duration:{unit}" for unit in ("s", "ms", "us", "ns")),
    }
)
_IDS = d._StableIdRegistry(
    roles=frozenset({"member", "group", "value", "cell", "status", "metric_identity", "count"}),
    logical_types=frozenset({"string", "int64", "float64", "boolean", "date", "timestamp"})
    | _NUMERIC_TYPES,
    physical_types=frozenset({"string", "int64", "float64", "boolean", "date", "timestamp"})
    | _NUMERIC_TYPES,
    storage_kinds=frozenset({"local_parquet"}),
)


def _public_table(result: ExchangeResult, *, include_cells: bool = False) -> pa.Table:
    table = result.primary
    signature = result.contract.signature
    terminal = any(isinstance(p, DisplayPart) and p.role == "columns" for p in signature.parts)
    if terminal:
        # Authored labels live in the checked definition, never become physical lookup keys.
        columns = next(
            p for p in signature.parts if isinstance(p, DisplayPart) and p.role == "columns"
        )
        count = len(columns.components) // 3
        table = table.select(
            (
                *result.contract.key_fields,
                *(
                    f"column_{i}__{field}"
                    for i in range(count)
                    for field in (
                        ("value", "cell_tag", "cell_reason") if include_cells else ("value",)
                    )
                ),
            )
        )
    names = list(table.column_names)
    for index, key in enumerate(result.contract.key_fields):
        names[names.index(key)] = (
            ("member" if signature.domain.kind == "entity" else "group")
            if index == 0
            else f"coord_{index - 1}"
        )
    funnel = next(
        (
            p
            for p in signature.parts
            if isinstance(p, (FunnelPart, FunnelComparisonPart, FunnelAllocationPart))
        ),
        None,
    )
    if funnel is not None:
        from marivo.analysis.materialization.funnel_execution import AXIS

        axes = (
            funnel.axes
            if isinstance(funnel, FunnelPart)
            else funnel.current.axes
            if isinstance(funnel, FunnelComparisonPart)
            else funnel.comparison.current.axes
        )
        names[0] = "resolution_key" if isinstance(funnel, FunnelAllocationPart) else "step"
        for i, axis in enumerate(axes):
            key = result.contract.key_fields[i + 1]
            dtype = pa.int64() if axis.dimension.logical_type == "int64" else pa.string()
            decoded = pa.array(
                [AXIS.validate_json(value, strict=True) for value in table[key].to_pylist()],
                type=dtype,
            )
            table = table.set_column(table.schema.get_field_index(key), key, decoded)
            names[i + 1] = axis.dimension.ref.path
        if isinstance(funnel, FunnelAllocationPart):
            names[len(result.contract.key_fields) - 2] = "other_mask_key"
            names[len(result.contract.key_fields) - 1] = "contribution_kind"
    quantity = signature.quantity
    if (
        isinstance(quantity, DerivedQuantity)
        and quantity.method_version == "association.spearman@v1"
    ):
        names[names.index("value")] = "coefficient"
        counts = next((part.table for part in result.parts if part.role == "pair_counts"), None)
        if counts is not None:
            for name in counts.column_names:
                if name not in result.contract.key_fields:
                    table = table.append_column(name, counts[name])
                    names.append(name.removeprefix("pair_counts__"))
    ordering = next((p.table for p in result.parts if p.role == "ordering"), None)
    if ordering is not None:
        keys = result.contract.key_fields
        positions = {
            tuple(row[k] for k in keys): row["ordering__position"] for row in ordering.to_pylist()
        }
        indices = sorted(
            range(table.num_rows), key=lambda i: positions[tuple(table[k][i].as_py() for k in keys)]
        )
        return table.rename_columns(names).take(pa.array(indices, type=pa.int64()))
    table = table.rename_columns(names)
    return (
        table.sort_by(
            [
                (names[result.primary.column_names.index(key)], "ascending")
                for key in result.contract.key_fields
            ]
        )
        if result.contract.key_fields
        else table
    )


def _meaning(
    node: Record,
    document: GraphDocument,
    *,
    signature: Signature | None = None,
    expand_inputs: bool = True,
) -> tuple[tuple[str, str], ...]:
    """Read display meaning from frozen definitions, never the current catalog."""
    signature = node.signature if signature is None else signature
    domain = signature.domain
    facts: list[tuple[str, str]] = [("grain", domain.kind)]
    if domain.instance_key:
        facts.append(
            (
                "coordinates",
                ", ".join(
                    dict.fromkeys(
                        f"{c.entity_ref.path}.{c.field} ({c.role})" for c in domain.instance_key
                    )
                ),
            )
        )
    quantity = signature.quantity
    if quantity is not None:
        facts.append(("unit", quantity.unit or "not declared"))
        facts.append(("method", quantity.method_version.split("@")[0]))
    if isinstance(node, MethodRecord) and isinstance(node.parameters, BindProject):
        facts.append(("field", node.parameters.ref.path))
    grid = domain.time_grid
    if grid is not None:
        facts.extend(
            (
                ("time_grain", grid.grain_token),
                (
                    "window",
                    f"[{grid.cells[0].start.isoformat()}, {grid.cells[-1].end.isoformat()})",
                ),
                ("timezone", grid.report_timezone),
            )
        )
    if isinstance(quantity, (ObservedQuantity, RolledQuantity)):
        original_id = (
            quantity.definition_id
            if isinstance(quantity, ObservedQuantity)
            else quantity.original_id
        )
        # Business coverage gives the quantity a new identity while retaining
        # its observation as an explicit frozen input.
        records = {record.identity: record for record in document.nodes}
        for parent in document.nodes:
            if (
                isinstance(parent, MethodRecord)
                and isinstance(parent.parameters, PartsTransport)
                and parent.parameters.mode == "business_coverage"
                and parent.signature.quantity is not None
                and parent.signature.quantity.definition_id == original_id
            ):
                origin = records[parent.inputs[0].node].signature.quantity
                if origin is not None:
                    original_id = origin.definition_id
                break
        for parent in document.nodes:
            if not isinstance(parent, MethodRecord):
                continue
            params = parent.parameters
            if isinstance(params, PreparedObservation):
                params = params.observation
            if (
                isinstance(params, (ObserveMetric, ObserveCount, ObserveWeightedMean))
                and params.quantity.definition_id == original_id
            ):
                ref = params.quantity.metric_ref
                facts.extend(
                    (
                        ("metric", ref.label if isinstance(ref, RuntimeMetricExpr) else ref.path),
                        (
                            "observation_window",
                            f"[{params.start}, {params.end})"
                            if params.start is not None
                            else "each retained grid cell"
                            if params.grid_window
                            else "unbounded",
                        ),
                        ("timezone", params.report_timezone),
                    )
                )
                if isinstance(params, ObserveWeightedMean):
                    facts.append(("weighting", f"contribution field {params.weight_column}"))
                break
    if (
        expand_inputs
        and isinstance(node, MethodRecord)
        and isinstance(node.parameters, (CellDerive, DisplayTable))
    ):
        labels = (
            node.parameters.labels
            if isinstance(node.parameters, DisplayTable)
            else ("current", "baseline")
        )
        inputs = node.retained_endpoints or tuple(edge.node for edge in node.inputs)
        records = {record.identity: record for record in document.nodes}
        for label, child in zip(labels, inputs, strict=False):
            facts.extend(
                (f"{label}.{name}", value)
                for name, value in _meaning(records[child], document, expand_inputs=False)
                if name not in ("grain", "coordinates")
            )
    return tuple(dict.fromkeys(facts))


def _column_boundaries(node: Record, document: GraphDocument) -> tuple[tuple[str, str], ...]:
    """Retain each terminal column's interpretation after its analysis API ends."""
    facts: list[tuple[str, str]] = []
    signature = node.signature
    assumptions = {item.fact for item in signature.evidence if item.basis == "assumption"}
    if assumptions:
        facts.append(
            (
                "premise_assumptions",
                ", ".join(sorted({f.kind for f in assumptions})) + "; not checked",
            )
        )
    if isinstance(signature.quantity, ObservedQuantity):
        facts.extend(observation_disclosure(signature.quantity.method_version, node.value_type))
    for part in signature.parts:
        if isinstance(part, FitInputsPart):
            facts.append(
                ("fit", f"deviation.{part.method}; original fit retained; selection does not refit")
            )
        elif isinstance(part, CoveragePart) and part.business_windows is not None:
            facts.append(("business_coverage", "uncovered buckets are Unknown"))
        elif isinstance(part, DisplayPart) and part.role == "ranking_domain":
            facts.append(
                (
                    "ranking",
                    f"{part.order}, ties={part.ties}; original ranks retained after selection",
                )
            )
        elif isinstance(part, TrainingInputsPart):
            facts.append(
                (
                    "forecast",
                    f"{part.model}; zero_mean_uncorrelated_homoskedastic_normal_innovations@v1",
                )
            )
        elif isinstance(part, PairInputsPart):
            facts.append(("association", f"{part.method}; original search retained; not causal"))
        elif isinstance(part, AttributionPart):
            facts.append(
                (
                    "allocation",
                    f"{part.method}; original components; complete_partition={part.complete}",
                )
            )
    reference = next(
        (
            parent.parameters
            for parent in document.nodes
            if isinstance(parent, MethodRecord)
            and isinstance(parent.parameters, ReferenceDerive)
            and parent.signature.quantity == signature.quantity
        ),
        None,
    )
    if reference is not None:
        facts.append(
            ("reference_policy", "retained original inputs; selection preserves reference")
        )
        if reference.kind == "standardize":
            facts.append(
                ("weights", "represented sum without normalization; no actual population claim")
            )
        elif reference.kind == "share":
            facts.append(
                ("range", "signed unless nonnegative support and positive denominator proved")
            )
    return tuple(dict.fromkeys(facts))


def _schema(table: pa.Table) -> d.DatasetSchema:
    fields: list[d.DatasetField] = []
    for column in table.schema:
        kind = str(column.type)
        if kind == "double":
            kind = "float64"
        if kind == "bool":
            kind = "boolean"
        if pa.types.is_date(column.type):
            kind = "date"
        if pa.types.is_timestamp(column.type):
            kind = "timestamp"
        if pa.types.is_decimal(column.type):
            kind = f"decimal:{column.type.precision}:{column.type.scale}"
        if pa.types.is_duration(column.type):
            kind = f"duration:{column.type.unit}"
        role = (
            "member"
            if column.name.startswith("member")
            else "group"
            if column.name.startswith(("group", "coord_"))
            else "cell"
            if column.name.startswith("cell_")
            else "metric_identity"
            if column.name.startswith("metric_key_")
            else "count"
            if column.name.endswith("_count")
            else "status"
            if column.name == "status"
            else "value"
        )
        identifier = d._make_field_id("graph." + column.name)
        fields.append(
            d._make_field(
                field_id=identifier,
                name=column.name,
                role_id=role,
                identity=d._generated_identity(identifier),
                derivation_identity="graph." + column.name,
                logical_type_id=kind,
                physical_type_state=d._resolved_type(kind, ids=_IDS),
                nullable=column.nullable,
                ids=_IDS,
            )
        )
    return d._make_schema(tuple(fields))


@dataclass(frozen=True, slots=True)
class GraphDataset:
    runtime: DatasetRuntime
    artifact: GraphArtifact
    projection: (
        Literal[
            "values",
            "ranks",
            "contribution",
            "current",
            "baseline",
            "observed",
            "reference",
            "deviation",
            "score",
            "start",
            "end",
            "count",
            "duration",
            "coefficient",
            "selected",
            "prediction",
            "lower",
            "upper",
        ]
        | None
    ) = None

    def evidence_digest(self) -> t.ArtifactDigest:
        with self.runtime.store._read() as connection:
            current = graph_store.artifact(
                self.runtime.store, connection, self.artifact.artifact_ref
            )
            if current is None:
                raise invalid("selected Artifact disappeared")
            from marivo.analysis.materialization.graph_findings import collection

            return collection(
                self.runtime.store,
                connection,
                current.descriptor,
                current.artifact_ref,
                _validated=current.validated,
            )[1]

    def findings(self, limit: int = 20, cursor: str | None = None) -> t.FindingPage:
        from marivo.analysis.materialization.graph_findings import collection, page

        with self.runtime.store._read() as connection:
            current = graph_store.artifact(
                self.runtime.store, connection, self.artifact.artifact_ref
            )
            if current is None:
                raise invalid("selected Artifact disappeared")
            findings, _ = collection(
                self.runtime.store,
                connection,
                current.descriptor,
                current.artifact_ref,
                _validated=current.validated,
            )
            return page(findings, current.artifact_ref, limit, cursor)

    def finding(self, finding_id: str) -> t.Finding:
        from marivo.analysis.materialization.graph_findings import collection

        with self.runtime.store._read() as connection:
            current = graph_store.artifact(
                self.runtime.store, connection, self.artifact.artifact_ref
            )
            if current is None:
                raise invalid("selected Artifact disappeared")
            findings, _ = collection(
                self.runtime.store,
                connection,
                current.descriptor,
                current.artifact_ref,
                _validated=current.validated,
            )
            for finding in findings:
                if finding.finding_id == finding_id:
                    return finding
        from marivo.analysis.errors import FindingNotFoundError

        raise FindingNotFoundError(
            message="The selected Finding does not belong to this Artifact.",
            expected="an exact Finding identity from this Artifact",
            received=str(finding_id),
            location="analysis.finding",
            repair=AnalysisRepair(
                kind="inspect",
                action="Read result.findings() and select an owned Finding identity.",
                help_target=LiveHelpTarget(surface="analysis"),
            ),
        )

    def verified(self) -> ExchangeResult:
        store = self.runtime.store
        if self.artifact.session_ref != self.runtime.session_ref:
            raise invalid("Artifact belongs to another Session")
        with store._read() as connection:
            current = graph_store.artifact(store, connection, self.artifact.artifact_ref)
        if current is None:
            raise invalid("selected Artifact disappeared")
        result = read_result(store.project_root, current.descriptor, _validated=current.validated)
        if self.projection is not None:
            from marivo.analysis.materialization.graph_display import project

            if self.projection in ("coefficient", "selected", "prediction", "lower", "upper"):
                from marivo.analysis.materialization.graph_relation import Relation
                from marivo.analysis.materialization.statistical_execution import (
                    execute as execute_statistical,
                )

                relation = Relation.restore(replace(self, projection=None))
                node = (
                    relation.association_field(self.projection)
                    if self.projection in ("coefficient", "selected")
                    else relation.forecast_field(
                        "prediction"
                        if self.projection == "prediction"
                        else "lower"
                        if self.projection == "lower"
                        else "upper"
                    )
                ).root
                assert isinstance(node, MethodNode)
                result = execute_statistical(node, (result,), current.artifact_ref)
            elif self.projection in ("start", "end", "count", "duration"):
                from marivo.analysis.materialization.graph_relation import Relation
                from marivo.analysis.materialization.runs_execution import execute

                node = (
                    Relation.restore(replace(self, projection=None)).run_field(self.projection).root
                )
                assert isinstance(node, MethodNode)
                result = execute(node, (result,), current.artifact_ref)
            elif self.projection in ("observed", "reference", "deviation", "score"):
                from marivo.analysis.materialization.deviation_execution import (
                    project as deviation_project,
                )
                from marivo.analysis.materialization.graph_relation import Relation

                relation = Relation.restore(replace(self, projection=None))
                node = relation.deviation_field(self.projection).root

                assert isinstance(node, MethodNode)
                result = deviation_project(
                    result, self.projection, node=node, binding=current.artifact_ref
                )
            elif self.projection in ("contribution", "current", "baseline"):
                from marivo.analysis.materialization.graph_attribution import (
                    project as attribution_project,
                )

                result = attribution_project(result, self.projection)
            else:
                assert self.projection in ("values", "ranks")
                result = project(result, self.projection)
        return result

    @property
    def state(self) -> MaterializedDatasetState:
        table = _public_table(self.verified())
        descriptor = self.artifact.descriptor
        authority = digest(encode(descriptor, DESCRIPTOR))
        return _materialized_state(
            artifact_ref=ArtifactRef(ref=self.artifact.artifact_ref),
            artifact_session_ref=self.artifact.session_ref,
            content_authority_digest=authority,
            storage_kind_id="local_parquet",
            realized_schema=_schema(table),
            realized_row_count=table.num_rows,
            realized_byte_count=d._exact_byte_count(
                descriptor.primary_receipt.local.realized_byte_count
            ),
            producing_run_ref=self.artifact.producing_run_ref,
            quality_authority_digest=authority,
            evidence_authority_digest=authority,
            ids=_IDS,
        )

    @property
    def schema(self) -> d.DatasetSchema:
        return self.state.realized_schema

    def to_pandas(self) -> pd.DataFrame:
        checked = self.verified()
        table = _public_table(checked)
        definition = self.artifact.validated.root
        params = definition.parameters
        if isinstance(params, DisplayTable):
            table = table.rename_columns(
                [*table.column_names[: len(checked.contract.key_fields)], *params.labels]
            )
            frame: pd.DataFrame = table.to_pandas(types_mapper=pd.ArrowDtype)
        elif (
            any(
                isinstance(p, (DisplayPart, AttributionPart))
                for p in checked.contract.signature.parts
            )
            or (
                any(isinstance(p, FitInputsPart) for p in checked.contract.signature.parts)
                and "value" in table.column_names
                and table.schema.field("value").type == pa.int64()
                and table["value"].null_count > 0
            )
            or (
                any(
                    isinstance(p, (FunnelPart, FunnelComparisonPart, FunnelAllocationPart))
                    for p in checked.contract.signature.parts
                )
                and any(
                    field.type == pa.int64() and table[field.name].null_count > 0
                    for field in table.schema
                )
            )
        ):
            frame = table.to_pandas(types_mapper=pd.ArrowDtype)
        else:
            frame = table.to_pandas()
        return frame.copy(deep=True)

    def show(
        self,
        *,
        n: int | None = None,
        max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES,
        checked: ExchangeResult | None = None,
        facts: tuple[tuple[str, str], ...] = (),
        boundaries: tuple[tuple[str, str], ...] = (),
        findings: bool = False,
    ) -> None:
        _validate_display(n, max_output_bytes)
        checked = self.verified() if checked is None else checked
        public = _public_table(checked, include_cells=True)
        params = self.artifact.validated.root.parameters
        columns = list(public.column_names)
        labels = dict(zip(columns, columns, strict=True))
        cell_columns: dict[str, tuple[str, str]] = {}
        for column in columns:
            if column in ("value", "coefficient") or column.endswith("__value"):
                prefix = "" if column == "coefficient" else column.removesuffix("value")
                tag, reason = prefix + "cell_tag", prefix + "cell_reason"
                if tag in columns and reason in columns:
                    cell_columns[column] = (tag, reason)
        columns = [c for c in columns if not any(c in pair for pair in cell_columns.values())]
        if isinstance(params, DisplayTable):
            labels.update({f"column_{i}__value": label for i, label in enumerate(params.labels)})
            root = self.artifact.validated.root
            document = self.artifact.validated.document
            records = {record.identity: record for record in document.nodes}
            inputs = root.retained_endpoints or tuple(edge.node for edge in root.inputs)
            boundaries += tuple(
                (f"{label}.{name}", value)
                for label, child in zip(params.labels, inputs, strict=True)
                for name, value in _column_boundaries(records[child], document)
            )
        hidden = (
            public.column_names[0]
            if checked.contract.signature.domain.kind == "entity" and checked.contract.key_fields
            else None
        )
        from marivo.analysis.materialization.graph_reference import disclosure

        boundaries += tuple(
            (name, value) for name, value in disclosure(checked) if name != "retained_reference"
        )
        precision = (checked.primary.schema.metadata or {}).get(b"r7.precision")
        if precision is not None and not any(
            name == "captured_precision" for name, _ in boundaries
        ):
            boundaries += (("captured_precision", precision.decode()),)
        for column, (tag_column, reason_column) in cell_columns.items():
            counts: Counter[str] = Counter()
            reasons: Counter[tuple[str, str]] = Counter()
            for tag, reason in zip(public[tag_column], public[reason_column], strict=True):
                cell_tag: str = tag.as_py()
                counts[cell_tag] += 1
                if cell_tag != "defined":
                    reasons[cell_tag, str(reason.as_py())] += 1
            if any(tag != "defined" and count for tag, count in counts.items()):
                boundaries += (
                    (
                        f"Cell states ({labels[column]}; all result rows)",
                        ", ".join(
                            f"{tag}={counts[tag]}"
                            for tag in ("defined", "null", "undefined", "unknown")
                        )
                        + "; reasons: "
                        + ", ".join(
                            f"{tag}({reason})={count}"
                            for (tag, reason), count in sorted(reasons.items())
                        ),
                    ),
                )
        ranking = next(
            (
                p
                for p in checked.contract.signature.parts
                if isinstance(p, DisplayPart) and p.role == "ranking_domain"
            ),
            None,
        )
        ranks: dict[tuple[object, ...], object] = {}
        keys = tuple(public.column_names[: len(checked.contract.key_fields)])
        if ranking is not None:
            original = next(p.table for p in checked.parts if p.role == "ranking_domain")
            boundaries += (
                (
                    "ranking",
                    f"{ranking.order}, ties={ranking.ties}; original_rows={original.num_rows}, selected_rows={public.num_rows}",
                ),
            )
            if self.projection is None and not (
                isinstance(params, PartsTransport) and params.display_view is not None
            ):
                rank_table = next(p.table for p in checked.parts if p.role == "ranks")
                for batch in rank_table.to_batches(max_chunksize=128):
                    rank_rows: list[dict[str, object]] = batch.to_pylist()
                    for row in rank_rows:
                        ranks[tuple(row[k] for k in checked.contract.key_fields)] = row[
                            "ranks__value"
                        ]
                columns.append("rank")
                labels["rank"] = "rank"

        duration_units: dict[str, str] = {}
        for column in columns:
            if column in public.column_names and pa.types.is_duration(
                public.schema.field(column).type
            ):
                duration_units[column] = public.schema.field(column).type.unit
                public = public.set_column(
                    public.schema.get_field_index(column), column, public[column].cast(pa.int64())
                )

        def rows() -> Iterator[Sequence[object]]:
            for batch in public.to_batches(max_chunksize=128):
                batch_rows: list[dict[str, object]] = batch.to_pylist()
                for row in batch_rows:
                    values: list[object] = []
                    for column in columns:
                        if column == hidden:
                            value: object = "<identity>"
                        elif column == "rank" and column not in row:
                            value = ranks[tuple(row[k] for k in keys)]
                        elif column in cell_columns and row[cell_columns[column][0]] != "defined":
                            tag_column, reason_column = cell_columns[column]
                            value = f"{str(row[tag_column]).title()}({row[reason_column]})"
                        elif column in duration_units and row[column] is not None:
                            value = f"{row[column]} {duration_units[column]}"
                        else:
                            value = row[column]
                        values.append(value)
                    yield values

        continuations: tuple[str, ...] = ()
        if findings:
            count = self.evidence_digest().finding_count
            if count:
                continuations = (f".findings(limit=20).show() — {count} saved Findings",)
        card = _DataCard(
            identity=f"Artifact {self.artifact.artifact_ref}",
            columns=tuple(labels[column] for column in columns),
            rows=rows,
            row_count=public.num_rows,
            facts=tuple(
                dict.fromkeys(
                    (
                        *facts,
                        *_meaning(
                            self.artifact.validated.root,
                            self.artifact.validated.document,
                            signature=checked.contract.signature,
                        ),
                    )
                )
            ),
            boundaries=tuple(dict.fromkeys(boundaries)),
            continuations=continuations,
        )
        print(card.render(n=n, max_output_bytes=max_output_bytes))
