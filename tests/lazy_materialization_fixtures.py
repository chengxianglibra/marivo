"""Pure committed-value builders for v3 codec and Store boundary tests."""

from dataclasses import replace

from marivo.analysis import time_scope
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    FileEntry,
    LocalReceipt,
    MaterializationContract,
    PopulationAuthority,
    RetainedPart,
    manifest_digest,
    required_retained_contracts,
    schema_fingerprint,
)
from marivo.analysis.materialization.quality import QualitySummary
from marivo.analysis.observation.contracts import (
    PopulationPayload,
    _version_selection_payload,
    producer_contract,
    scope_payload,
)
from marivo.refs import ref
from tests.lazy_observation_fixtures import make_sources


def descriptor(*, metric: bool = False, population: str = "customers") -> ArtifactDescriptor:
    """Build metadata only; the caller tests Store authority, not backend production."""
    sources = make_sources(session_id="session")
    logical = (
        sources.observe([ref.metric("sales.revenue"), ref.metric("sales.order_count")])
        if metric
        else sources.population(
            ref.entity(f"sales.{population}"),
            time_scope=(
                time_scope(start="2026-02-01", end="2026-03-01")
                if population in ("snapshots", "validity")
                else None
            ),
        )
    )
    realized = d._make_schema(
        tuple(
            replace(
                column,
                _token=d._CORE_TOKEN,
                physical_type_state=d._resolved_type(
                    column.logical_type_id, ids=logical._registration.ids
                ),
            )
            for column in logical.schema.columns
        )
    )
    root = logical._root
    assert isinstance(root, LogicalRootHandle)
    registration = producer_contract(root.operator_id)
    contract = MaterializationContract(
        registration.producer_id,
        1,
        logical.row_contract.shape_id,
        registration.quality_id,
        1,
        registration.evidence_id,
        1,
        "none",
        1,
        (registration.validation_id,),
        required_retained_contracts(
            logical.row_contract, registration.retained_contract_ids, sampled=False
        ),
        "zero_findings@v1",
    )
    file = FileEntry("data.parquet", 8, "a" * 64)
    primary = LocalReceipt(
        ".marivo/analysis/generations/v4/sessions/session/artifacts/artifact/primary",
        (file,),
        manifest_digest((file,)),
        "a" * 64,
        schema_fingerprint(realized),
        2,
        256,
    )
    parts = []
    for column in logical.schema.columns:
        if column.role_id == "metric" and isinstance(column.identity, d._CatalogFieldIdentity):
            role = "metric_components." + d._canonical_digest(column.identity.identity_id[7:])[:20]
            parts.append(
                RetainedPart(
                    role,
                    "metric.sufficient_components",
                    1,
                    replace(
                        primary,
                        project_relative_path=primary.project_relative_path.replace(
                            "/primary", "/parts/" + role
                        ),
                        schema_fingerprint="c" * 64,
                    ),
                )
            )
    entity = "sales.orders" if metric else f"sales.{population}"
    root_payload = root.payload
    return ArtifactDescriptor(
        logical.definition_fingerprint,
        logical.row_contract,
        logical.row_set_contract,
        realized,
        logical._lineage,
        "d" * 64,
        PopulationAuthority(
            logical.definition_fingerprint,
            entity,
            (("id", "int64"),),
            membership_scope=scope_payload(root_payload.time_scope)
            if isinstance(root_payload, PopulationPayload)
            else None,
            version_selection=_version_selection_payload(root_payload.version_selection)
            if isinstance(root_payload, PopulationPayload)
            else None,
            validation_results=(("identity_uniqueness", 0),),
        ),
        None,
        ((root.operator_id, 1),),
        contract,
        primary,
        tuple(parts),
        QualitySummary(sample_size=2, evaluated_check_count=1, failed_check_count=0),
    )
