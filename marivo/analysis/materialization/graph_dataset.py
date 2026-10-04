"""Public result projections owned by an exact checked Store 7 Artifact."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

import pandas as pd
import pyarrow as pa

from marivo.analysis.core.graph import MethodNode
from marivo.analysis.core.model import (
    AttributionPart,
    DerivedQuantity,
    DisplayPart,
    FitInputsPart,
    FunnelAllocationPart,
    FunnelComparisonPart,
    FunnelPart,
)
from marivo.analysis.core.rules import DisplayTable, PartsTransport
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.state import MaterializedDatasetState, _materialized_state
from marivo.analysis.errors import AnalysisRepair
from marivo.analysis.evidence import _dataset_types as t
from marivo.analysis.materialization import graph_store
from marivo.analysis.materialization.graph_exchange import ExchangeResult
from marivo.analysis.materialization.graph_protocol import DESCRIPTOR, digest, encode, invalid
from marivo.analysis.materialization.graph_storage import read_result
from marivo.analysis.materialization.graph_store import GraphArtifact
from marivo.analysis.refs import ArtifactRef
from marivo.introspection.live.model import LiveHelpTarget

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


def _public_table(result: ExchangeResult) -> pa.Table:
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
            (*result.contract.key_fields, *(f"column_{i}__value" for i in range(count)))
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
            if current != self.artifact:
                raise invalid("selected Artifact changed or disappeared")
            from marivo.analysis.materialization.graph_findings import collection

            return collection(
                self.runtime.store, connection, current.descriptor, current.artifact_ref
            )[1]

    def findings(self, limit: int = 20, cursor: str | None = None) -> t.FindingPage:
        from marivo.analysis.materialization.graph_findings import collection, page

        with self.runtime.store._read() as connection:
            current = graph_store.artifact(
                self.runtime.store, connection, self.artifact.artifact_ref
            )
            if current != self.artifact:
                raise invalid("selected Artifact changed or disappeared")
            findings, _ = collection(
                self.runtime.store, connection, current.descriptor, current.artifact_ref
            )
            return page(findings, current.artifact_ref, limit, cursor)

    def finding(self, finding_id: str) -> t.Finding:
        from marivo.analysis.materialization.graph_findings import collection

        with self.runtime.store._read() as connection:
            current = graph_store.artifact(
                self.runtime.store, connection, self.artifact.artifact_ref
            )
            if current != self.artifact:
                raise invalid("selected Artifact changed or disappeared")
            findings, _ = collection(
                self.runtime.store, connection, current.descriptor, current.artifact_ref
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
        if current != self.artifact:
            raise invalid("selected Artifact changed or disappeared")
        result = read_result(store.project_root, current.descriptor)
        if self.projection is not None:
            from marivo.analysis.materialization.graph_display import project

            if self.projection in ("coefficient", "selected", "prediction", "lower", "upper"):
                from marivo.analysis.core.graph import MethodNode
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
                from marivo.analysis.core.graph import MethodNode
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
                from marivo.analysis.core.graph import MethodNode

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
        from marivo.analysis.materialization.graph_protocol import validate_descriptor

        definition = validate_descriptor(self.artifact.descriptor)
        params = definition.parameters if isinstance(definition, MethodNode) else None
        if isinstance(params, DisplayTable):
            table = table.rename_columns(
                [*table.column_names[: len(checked.contract.key_fields)], *params.labels]
            )
            frame: pd.DataFrame = table.to_pandas(types_mapper=pd.ArrowDtype)
        elif any(
            isinstance(p, (DisplayPart, AttributionPart)) for p in checked.contract.signature.parts
        ) or (
            any(isinstance(p, FitInputsPart) for p in checked.contract.signature.parts)
            and "value" in table.column_names
            and table.schema.field("value").type == pa.int64()
            and table["value"].null_count > 0
        ):
            frame = table.to_pandas(types_mapper=pd.ArrowDtype)
        else:
            frame = table.to_pandas()
        return frame.copy(deep=True)

    def show(self, *, max_output_bytes: int | None = None) -> None:
        checked = self.verified()
        public = _public_table(checked)
        table = public.to_pandas(types_mapper=pd.ArrowDtype)
        frame = table.head(5).copy()
        from marivo.analysis.materialization.graph_protocol import validate_descriptor

        definition = validate_descriptor(self.artifact.descriptor)
        params = definition.parameters if isinstance(definition, MethodNode) else None
        if isinstance(params, DisplayTable):
            frame = frame.rename(
                columns=dict(
                    zip(
                        public.column_names[len(checked.contract.key_fields) :],
                        params.labels,
                        strict=True,
                    )
                )
            ).astype(object)
            keys = checked.contract.key_fields
            cells = {tuple(row[k] for k in keys): row for row in checked.primary.to_pylist()}
            for i, label in enumerate(params.labels):
                for index, public_row in enumerate(public.to_pylist()[:5]):
                    row = cells[tuple(public_row[k] for k in public.column_names[: len(keys)])]
                    tag, reason = row[f"column_{i}__cell_tag"], row[f"column_{i}__cell_reason"]
                    if tag != "defined":
                        frame.at[frame.index[index], label] = f"{str(tag).title()}({reason})"
        hidden = (
            [public.column_names[0]]
            if checked.contract.signature.domain.kind == "entity" and checked.contract.key_fields
            else []
        )
        for name in hidden:
            frame[name] = "<identity>"
        from marivo.analysis.materialization.graph_reference import disclosure

        facts = "".join(f"\n{name}: {value}" for name, value in disclosure(checked))
        precision = (checked.primary.schema.metadata or {}).get(b"r7.precision")
        if precision is not None:
            facts += "\nCaptured precision: " + precision.decode()[:2048]
        ranking = next(
            (
                p
                for p in checked.contract.signature.parts
                if isinstance(p, DisplayPart) and p.role == "ranking_domain"
            ),
            None,
        )
        if ranking is not None:
            original = next(p.table for p in checked.parts if p.role == "ranking_domain")
            facts += f"\nranking: {ranking.order}, ties={ranking.ties}; original_rows={original.num_rows}, selected_rows={len(table)}"
            if self.projection is None and not (
                isinstance(params, PartsTransport) and params.display_view is not None
            ):
                rank_table = next(p.table for p in checked.parts if p.role == "ranks")
                keys = checked.contract.key_fields
                ranks = {
                    tuple(row[k] for k in keys): row["ranks__value"]
                    for row in rank_table.to_pylist()
                }
                frame["rank"] = [
                    ranks[tuple(row[k] for k in public.column_names[: len(keys)])]
                    for row in public.to_pylist()[:5]
                ]
        text = (
            f"Artifact {self.artifact.artifact_ref} rows={len(table)}{facts}"
            f"\n{frame.to_string(index=False)}"
        )
        limit = 8192 if max_output_bytes is None else max(0, min(8192, max_output_bytes))
        print(text.encode()[: max(0, limit - 1)].decode(errors="ignore"))
