"""Public result projections owned by an exact checked Store 7 Artifact."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd
import pyarrow as pa

from marivo.analysis.core.model import DerivedQuantity
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.state import MaterializedDatasetState, _materialized_state
from marivo.analysis.materialization import graph_store
from marivo.analysis.materialization.graph_exchange import ExchangeResult
from marivo.analysis.materialization.graph_protocol import DESCRIPTOR, digest, encode, invalid
from marivo.analysis.materialization.graph_storage import read_result
from marivo.analysis.materialization.graph_store import GraphArtifact
from marivo.analysis.refs import ArtifactRef

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
    names = list(table.column_names)
    for index, key in enumerate(result.contract.key_fields):
        names[names.index(key)] = (
            ("member" if signature.domain.kind == "entity" else "group")
            if index == 0
            else f"coord_{index - 1}"
        )
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

    def verified(self) -> ExchangeResult:
        store = self.runtime.store
        if self.artifact.session_ref != self.runtime.session_ref:
            raise invalid("Artifact belongs to another Session")
        with store._read() as connection:
            current = graph_store.artifact(store, connection, self.artifact.artifact_ref)
        if current != self.artifact:
            raise invalid("selected Artifact changed or disappeared")
        return read_result(store.project_root, current.descriptor)

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
        frame: pd.DataFrame = _public_table(self.verified()).to_pandas()
        return frame.copy(deep=True)

    def show(self, *, max_output_bytes: int | None = None) -> None:
        checked = self.verified()
        table = _public_table(checked).to_pandas()
        frame = table.head(5).copy()
        hidden = [name for name in frame.columns if str(name).startswith("member")]
        for name in hidden:
            frame[name] = "<identity>"
        from marivo.analysis.materialization.graph_reference import disclosure

        facts = "".join(f"\n{name}: {value}" for name, value in disclosure(checked))
        text = (
            f"Artifact {self.artifact.artifact_ref} rows={len(table)}{facts}"
            f"\n{frame.to_string(index=False)}"
        )
        limit = 8192 if max_output_bytes is None else max(0, min(8192, max_output_bytes))
        print(text.encode()[: max(0, limit - 1)].decode(errors="ignore"))
