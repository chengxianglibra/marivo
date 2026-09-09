"""Runtime-selected component dependencies and independent physical schema checks."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

import ibis.expr.datatypes as dt
import ibis.expr.types as ir
import pyarrow as pa
from sqlglot import expressions as sge

from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.descriptors import DatasetRowContract, _EntityFieldIdentity
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    EngineReceipt,
    LocalReceipt,
    RetainedPart,
    StorageReceipt,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.storage import _integrity, _matches_type
from marivo.analysis.observation.contracts import (
    EntityPresentMetricSemantics,
    EntityReducedMetricSemantics,
)
from marivo.analysis.observation.distinct_contracts import (
    DISTINCT_KEY_COLUMN,
    DISTINCT_MEMBERSHIP_CONTRACT_IDS,
    membership_endpoint_name,
)
from marivo.analysis.observation.distribution_contracts import DISTRIBUTION_CONTRACT_IDS
from marivo.analysis.observation.fold_contracts import (
    MetricFoldAuthorityV1,
    RetainedFoldPayload,
    fold_part_role,
    fold_state_columns,
)
from marivo.analysis.observation.private_parts import source_private_part_authorities

if TYPE_CHECKING:
    from ibis.backends.duckdb import Backend

_STATE_TYPE_CHECKS: dict[str, tuple[Callable[[pa.DataType], bool], ...]] = {
    "integer": (pa.types.is_integer,),
    "numeric": (pa.types.is_integer, pa.types.is_floating, pa.types.is_decimal),
    "timestamp": (pa.types.is_timestamp,),
    "floating": (pa.types.is_floating,),
    "boolean": (pa.types.is_boolean,),
}


def source_private_role(role: str) -> bool:
    """Recognize the closed source-private membership and distribution role families."""
    return role.startswith(
        ("metric_membership.", "delta_membership.", "metric_distribution.", "delta_distribution.")
    )


def source_private_part(part: RetainedPart) -> bool:
    return source_private_role(part.role) or part.contract_id in (
        *DISTINCT_MEMBERSHIP_CONTRACT_IDS,
        *DISTRIBUTION_CONTRACT_IDS,
    )


def reject_source_private_transfer() -> None:
    raise MaterializationError(
        expected="source-native use of exact retained membership or distribution",
        received="a local or generic retained-part transfer",
        repair="Use a compatible engine target and source-native attribution execution.",
        stage="execution_boundary",
    )


def guard_part_transfer(part: RetainedPart) -> None:
    if source_private_part(part):
        reject_source_private_transfer()
    guard_receipt_transfer(part.storage_receipt)


def guard_receipt_transfer(receipt: StorageReceipt) -> None:
    """Recognize only the payload position owned by each retained-part adapter."""
    if isinstance(receipt, EngineReceipt):
        parts = receipt.qualified_relation_ref.split("/")
        part_path = parts[:-1] if parts[-1] == "payload.duckdb" else ()
    elif isinstance(receipt, LocalReceipt):
        part_path = receipt.project_relative_path.split("/")
    else:
        parts = receipt.immutable_prefix_or_manifest_ref.split("/")
        part_path = parts[:-1] if parts[-1] == "manifest.json" else ()
    if len(part_path) >= 2 and part_path[-2] == "parts" and source_private_role(part_path[-1]):
        reject_source_private_transfer()


def metric_parts(row: DatasetRowContract) -> tuple[MetricFoldAuthorityV1, ...]:
    semantics = row.family_semantics
    if not isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        return ()
    retained = {binding[0].value for binding in semantics.metric_bindings if binding[3]}
    return tuple(item for item in semantics.metric_folds if item.field_id in retained)


def required_part_roles(dataset: Dataset, *, input_dataset: Dataset | None = None) -> set[str]:
    """Propagate consumed state to the exact input, respecting producer boundaries."""
    from marivo.analysis.operators.attribution_contracts import AttributePayload
    from marivo.analysis.operators.contracts import ComparePayload

    required: set[str] = set()

    def visit(value: Dataset, demanded: set[str]) -> None:
        if value is input_dataset or (
            isinstance(value, MaterializedDataset)
            and isinstance(input_dataset, MaterializedDataset)
            and value.state.artifact_ref == input_dataset.state.artifact_ref
        ):
            required.update(demanded)
            return
        if input_dataset is None:
            required.update(demanded)
        if not isinstance(value, LogicalDataset) or not isinstance(value._root, LogicalRootHandle):
            return
        payload = value._root.payload
        for child in value._inputs:
            if value._root.operator_id == "session.observe":
                child_demand: set[str] = set()
            elif isinstance(payload, (ComparePayload, AttributePayload, RetainedFoldPayload)):
                child_demand = _row_part_roles(child.row_contract)
            else:
                child_demand = demanded
            visit(child, child_demand)

    visit(dataset, _row_part_roles(dataset.row_contract))
    return required


def _row_part_roles(row: DatasetRowContract) -> set[str]:

    membership = {role for role, _ in source_private_part_authorities(row)}
    if row.shape_id.family_id == "delta":
        from marivo.analysis.operators.attribution_contracts import delta_part_authorities

        return {role for role, _ in delta_part_authorities(row)} | membership
    return {fold_part_role(item) for item in metric_parts(row)} | membership


def membership_schema(row: DatasetRowContract, role: str, schema: pa.Schema) -> tuple[str, ...]:
    """Check private membership types using only the independently owned schema."""
    from marivo.analysis.observation.distinct_contracts import membership_part_authorities

    authority = next(
        (item for name, item in membership_part_authorities(row) if name == role), None
    )
    if authority is None or authority.membership is None:
        _integrity("one registered membership role", "unknown private membership role")
    keys = tuple(field for field in row.schema.columns if field.field_id in row.key_field_ids)
    expected = (*(field.name for field in keys), DISTINCT_KEY_COLUMN)
    if tuple(schema.names) != expected:
        _integrity(
            "complete contribution coordinates and one private member key", "part fields differ"
        )
    for field in keys:
        physical = schema.field(field.name).type
        if isinstance(field.identity, _EntityFieldIdentity):
            signature = field.identity.identity_signature
            if (
                not pa.types.is_struct(physical)
                or tuple(physical.names) != tuple(name for name, _ in signature)
                or any(
                    not _matches_type(kind, physical.field(name).type) for name, kind in signature
                )
            ):
                _integrity(
                    "complete declared Entity coordinate identity", "part coordinate type differs"
                )
        elif not _matches_type(field.logical_type_id, physical):
            _integrity("declared contribution coordinate types", "part coordinate type differs")
    if (
        schema.field(DISTINCT_KEY_COLUMN).type
        != dt.dtype(authority.membership.key_logical_type).to_pyarrow()
    ):
        _integrity("the exact registered private member key type", "part member type differs")
    return tuple(field.name for field in keys)


def validate_source_private_relation(
    backend: Backend,
    table: ir.Table,
    primary: ir.Table,
    row: DatasetRowContract,
    role: str,
    record: Callable[[str, str], None],
) -> None:
    """Inspect source-private state natively; return only scalar violations."""
    from marivo.analysis.observation.distinct_contracts import membership_part_authorities

    if role.startswith(("metric_distribution.", "delta_distribution.")):
        from marivo.analysis.materialization.distribution import validate_distribution_relation

        validate_distribution_relation(backend, table, primary, row, role, record)
        return
    record("engine_check.membership_schema", backend.compile(table.limit(0)))
    keys = membership_schema(row, role, backend.to_pyarrow(table.limit(0)).schema)
    authority = next(item for name, item in membership_part_authorities(row) if name == role)
    endpoint = membership_endpoint_name(row, role)

    def quoted(name: str) -> str:
        return sge.to_identifier(name, quoted=True).sql(dialect="duckdb")

    member = quoted(DISTINCT_KEY_COLUMN)
    assert authority.membership is not None
    null_member = " OR ".join(
        [f"{member} IS NULL"]
        + [
            f"struct_extract({member}, {sge.Literal.string(name).sql(dialect='duckdb')}) IS NULL"
            for name, _ in authority.membership.identity_signature
        ]
    )
    coordinates = ", ".join(quoted(name) for name in keys)
    equality = (
        " AND ".join(f"m.{quoted(name)} IS NOT DISTINCT FROM p.{quoted(name)}" for name in keys)
        or "TRUE"
    )
    grouped = f"{coordinates}, " if coordinates else ""
    grouping = f" GROUP BY {coordinates}" if coordinates else ""
    sql = (
        f"WITH membership AS ({backend.compile(table)}), primary_rows AS ({backend.compile(primary)}), "
        f"counts AS (SELECT {grouped}count(*) AS __mv_members FROM membership{grouping}) "
        "SELECT "
        f"(SELECT count(*) FROM membership WHERE {null_member}) + "
        f"(SELECT count(*) FROM (SELECT {grouped}{member} FROM membership "
        f"GROUP BY {grouped}{member} HAVING count(*) <> 1)) + "
        f"(SELECT count(*) FROM membership m WHERE NOT EXISTS (SELECT 1 FROM primary_rows p WHERE {equality})) + "
        f"(SELECT count(*) FROM primary_rows p LEFT JOIN counts m ON {equality} "
        f"WHERE p.{quoted(endpoint)} IS NULL OR p.{quoted(endpoint)} <> coalesce(m.__mv_members, 0))"
    )
    record("engine_check.membership_integrity", sql)
    violations: object = backend.raw_sql(sql).fetchone()[0]
    if violations != 0:
        _integrity(
            "unique complete membership with exact primary endpoints",
            "private membership support or endpoint mismatch",
        )


def selected_parts(
    descriptor: ArtifactDescriptor, dataset: Dataset, *, input_dataset: Dataset | None = None
) -> tuple[RetainedPart, ...]:
    """Select final state and consumed folds on the exact input's dependency path."""
    required = required_part_roles(dataset, input_dataset=input_dataset)
    available = {part.role: part for part in descriptor.retained_parts}
    if not required.issubset(available):
        _integrity("every consumed registered Metric state role", "missing required Metric part")
    return tuple(part for part in descriptor.retained_parts if part.role in required)


def component_schema(row: DatasetRowContract, role: str, schema: pa.Schema) -> tuple[str, ...]:
    """Validate meaning from the owner; the receipt separately pins physical schema."""
    states = _part_state_columns(row, role)
    keys = tuple(field for field in row.schema.columns if field.field_id in row.key_field_ids)
    expected = (*(field.name for field in keys), *(name for name, _, _ in states))
    if tuple(schema.names) != expected:
        _integrity(
            "exact complete contribution keys and component state fields", "part fields differ"
        )
    for field in keys:
        physical = schema.field(field.name).type
        if isinstance(field.identity, _EntityFieldIdentity):
            signature = field.identity.identity_signature
            if not pa.types.is_struct(physical) or tuple(physical.names) != tuple(
                name for name, _ in signature
            ):
                _integrity(
                    "the complete retained Entity identity signature",
                    "part identity schema differs",
                )
            if any(not _matches_type(kind, physical.field(name).type) for name, kind in signature):
                _integrity("the declared Entity identity types", "part identity type differs")
        elif not _matches_type(field.logical_type_id, physical):
            _integrity("the declared contribution coordinate types", "part coordinate type differs")
    for name, kind, _nullable in states:
        physical = schema.field(name).type
        admitted = any(check(physical) for check in _STATE_TYPE_CHECKS.get(kind, ()))
        if not admitted:
            _integrity("the registered component state type class", "part state type differs")
    return tuple(field.name for field in keys)


def checked_component_batches(
    batches: Iterable[pa.RecordBatch], row: DatasetRowContract, role: str
) -> Iterable[pa.RecordBatch]:
    """Check required component support fields as actual data, independently of headers."""
    nonnull = tuple(name for name, _, nullable in _part_state_columns(row, role) if not nullable)
    for batch in batches:
        component_schema(row, role, batch.schema)
        if any(batch.column(name).null_count for name in nonnull):
            _integrity("non-null retained support and coverage", "null required component state")
        if row.shape_id.family_id == "delta":
            from marivo.analysis.operators.attribution_contracts import (
                delta_part_authorities,
                delta_presence_name,
                delta_state_name,
            )

            side = role.removeprefix("delta_components.")
            authority = next(item for name, item in delta_part_authorities(row) if name == role)
            present = batch.column(delta_presence_name(side)).to_pylist()
            for name, _, nullable in fold_state_columns(authority):
                values = batch.column(delta_state_name(side, name)).to_pylist()
                if any(
                    (selected and not nullable and value is None)
                    or (not selected and value is not None)
                    for selected, value in zip(present, values, strict=True)
                ):
                    _integrity(
                        "side presence consistent with complete component state",
                        "invalid Delta side component support",
                    )
        yield batch


def _part_state_columns(row: DatasetRowContract, role: str) -> tuple[tuple[str, str, bool], ...]:
    if row.shape_id.family_id == "delta":
        from marivo.analysis.operators.attribution_contracts import (
            delta_part_authorities,
            delta_presence_name,
            delta_state_name,
        )

        authority = next((item for name, item in delta_part_authorities(row) if name == role), None)
        if authority is None:
            _integrity("an exact registered Delta side role", "unknown Delta part role")
        side = role.removeprefix("delta_components.")
        return (
            *(
                (delta_state_name(side, name), kind, True)
                for name, kind, _ in fold_state_columns(authority)
            ),
            (delta_presence_name(side), "boolean", False),
        )
    authority = next((item for item in metric_parts(row) if fold_part_role(item) == role), None)
    if authority is None:
        _integrity("a required role owned by the Metric contract", "unknown Metric part role")
    return fold_state_columns(authority)
