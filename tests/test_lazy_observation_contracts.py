"""Actual source normalizers establish complete immutable Dataset contracts."""

from dataclasses import FrozenInstanceError, replace

import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.datasets.base import _make_materialized_dataset
from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    _EntityFieldIdentity,
    _exact_byte_count,
    _make_schema,
    _resolved_type,
    _row_contract_fingerprint,
)
from marivo.analysis.datasets.errors import DatasetConstructionError, DatasetOwnershipError
from marivo.analysis.datasets.handles import LogicalRootHandle, MaterializedScanLeafHandle
from marivo.analysis.datasets.state import _materialized_state
from marivo.analysis.observation.contracts import (
    EntityPresentMetricSemantics,
    EntityReducedMetricSemantics,
    MetricPayload,
    PopulationPayload,
)
from marivo.analysis.observation.metric import LogicalMetricDataset, MaterializedMetricDataset
from marivo.analysis.observation.population import (
    LogicalPopulationDataset,
    MaterializedPopulationDataset,
)
from marivo.analysis.observation.predicates import eq, gt
from marivo.analysis.refs import ArtifactRef
from marivo.analysis.session._lazy_sources import LazySources
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.errors import SemanticLoadError
from marivo.semantic.validator import Registry
from tests.lazy_observation_fixtures import make_sources

REVENUE = ref.metric("sales.revenue")
REGION = ref.dimension("sales.customers.region")
DAY = ref.time_dimension("sales.orders.order_time")
WINDOW = time_scope(start="2026-02-01", end="2026-03-01")


def test_population_identity_is_a_non_null_ordered_tuple() -> None:
    sources = make_sources()
    for name, signature in (
        ("customers", (("id", "int64"),)),
        ("composite", (("tenant", "string"), ("id", "int64"))),
    ):
        population = sources.population(ref.entity(f"sales.{name}"))
        assert isinstance(population, LogicalPopulationDataset)
        assert str(population.row_contract.shape_id) == "population/entity-membership@v1"
        assert population.schema is population.row_contract.schema
        field = population.schema.columns[0]
        assert field.name == "entity_identity" and field.logical_type_id == "identity_tuple"
        assert field.nullable is False
        assert isinstance(field.identity, _EntityFieldIdentity)
        assert field.identity.identity_signature == signature
        assert population.row_contract.key_field_ids == (field.field_id,)
        assert population.row_set_contract.cardinality.kind == "keyed"
        assert population.row_set_contract.ordering.kind == "unordered"
    with pytest.raises(DatasetConstructionError, match="source-only"):
        sources.population(ref.entity("sales.unkeyed"))


@pytest.mark.parametrize("name,kind", [("snapshots", "snapshot"), ("validity", "validity")])
def test_versioned_population_owns_exact_excluded_endpoint(name: str, kind: str) -> None:
    sources = make_sources()
    entity = ref.entity(f"sales.{name}")
    with pytest.raises(DatasetConstructionError, match="unscoped"):
        sources.population(entity)
    population = sources.population(entity, time_scope=WINDOW)
    assert isinstance(population._root, LogicalRootHandle)
    payload = population._root.payload
    assert isinstance(payload, PopulationPayload)
    selection = payload.version_selection
    assert selection is not None and selection.kind == kind
    assert selection.interpretation == "before_endpoint"
    if kind == "snapshot":
        assert selection.period == "2026-02-28"
    else:
        assert selection.start_operator == "lt" and selection.end_operator == "ge"
    assert population.schema.columns[0].identity.identity_signature == (("id", "int64"),)
    assert "require execution" in population.contract().render()


def test_all_eight_shapes_have_complete_canonical_keys_and_order() -> None:
    sources = make_sources()
    base = sources.observe([REVENUE, ref.metric("sales.order_count")], time_scope=WINDOW)
    variants = (
        base,
        base.with_dimensions(REGION),
        base.with_time_axis(DAY, grain=grain("day")),
        base.with_time_axis(DAY, grain=grain("day")).with_dimensions(REGION),
    )
    for dataset, shape in zip(
        variants,
        ("entity", "entity-dimension", "entity-time", "entity-dimension-time"),
        strict=True,
    ):
        assert dataset.row_contract.shape_id.local_shape_id == shape
        assert isinstance(dataset.row_contract.family_semantics, EntityPresentMetricSemantics)
        columns = dataset.schema.columns
        assert columns[0].name == "entity_identity"
        assert tuple(column.name for column in columns[-2:]) == ("revenue", "order_count")
        assert all(column.nullable for column in columns[-2:])
        assert dataset.row_contract.key_field_ids == dataset.row_contract.coordinate_field_ids
        reduced = dataset.aggregate()
        expected = shape.removeprefix("entity-") if shape != "entity" else "scalar"
        assert reduced.row_contract.shape_id.local_shape_id == expected
        assert isinstance(reduced.row_contract.family_semantics, EntityReducedMetricSemantics)
        assert reduced.row_contract.family_semantics.reduced_identity_signature == (
            ("id", "int64"),
        )
        assert reduced.row_set_contract.cardinality.kind == (
            "singleton" if expected == "scalar" else "keyed"
        )
    dimensions_first = base.with_dimensions(REGION).with_time_axis(DAY, grain=grain("day"))
    time_first = variants[-1]
    assert _row_contract_fingerprint(dimensions_first.row_contract) == _row_contract_fingerprint(
        time_first.row_contract
    )
    assert tuple(column.name for column in time_first.schema.columns) == (
        "entity_identity",
        "region",
        "order_time",
        "revenue",
        "order_count",
    )
    assert time_first.definition_fingerprint != dimensions_first.definition_fingerprint


@pytest.mark.parametrize(
    "name", ["revenue", "order_count", "mean_amount", "weighted_amount", "conversion_rate"]
)
def test_initial_metric_variants_bind_real_component_contracts(name: str) -> None:
    dataset = make_sources().observe(ref.metric(f"sales.{name}"), time_scope=WINDOW).aggregate()
    assert isinstance(dataset._root, LogicalRootHandle) and isinstance(
        dataset._root.payload, MetricPayload
    )
    metric = dataset._root.payload.definition.metrics[0]
    assert metric.graph.nodes and metric.required_state
    assert metric.evaluation_order == ("space", "time", "compose")
    assert metric.supports_coordinate_aggregation
    assert dataset.row_contract.family_semantics.metric_bindings[0][3] == metric.required_state


def test_default_population_and_independent_observation_scope() -> None:
    sources = make_sources()
    january = time_scope(start="2026-01-01", end="2026-02-01")
    inferred = sources.observe(REVENUE, time_scope=WINDOW)
    root = inferred._root
    assert isinstance(root, LogicalRootHandle)
    population_root = root.inputs[0].root
    assert isinstance(population_root, LogicalRootHandle)
    assert isinstance(population_root.payload, PopulationPayload)
    assert population_root.payload.time_scope is None
    explicit = sources.population(ref.entity("sales.orders"), time_scope=january)
    observed = sources.observe(REVENUE, population=explicit, time_scope=WINDOW)
    assert isinstance(observed._root, LogicalRootHandle)
    assert isinstance(observed._root.payload, MetricPayload)
    assert observed._root.payload.definition.time_scope == WINDOW
    assert (
        observed._root.payload.definition.population_definition == explicit.definition_fingerprint
    )
    assert observed.definition_fingerprint != inferred.definition_fingerprint


def test_safe_different_root_ratio_requires_explicit_population() -> None:
    sources = make_sources()
    metric = ref.metric("sales.cross_root_ratio")
    with pytest.raises(DatasetConstructionError, match="different component roots"):
        sources.observe(metric)
    customers = sources.population(ref.entity("sales.customers"))
    observed = sources.observe(metric, population=customers).aggregate()
    assert isinstance(observed._root, LogicalRootHandle) and isinstance(
        observed._root.payload, MetricPayload
    )
    definition = observed._root.payload.definition
    assert definition.entity.ref.path == "sales.customers"
    assert len(definition.metrics[0].computation_roots) == 2
    assert definition.contribution_paths == (
        ("sales.line_order", "sales.order_customer"),
        ("sales.order_customer",),
    )
    with pytest.raises(DatasetConstructionError, match="fanout"):
        sources.observe(REVENUE, population=sources.population(ref.entity("sales.lines")))


def test_local_adjacent_negative_admission() -> None:
    sources = make_sources()
    dataset = sources.observe(REVENUE, time_scope=WINDOW)
    with pytest.raises(DatasetConstructionError):
        sources.observe([])
    with pytest.raises(DatasetConstructionError):
        sources.observe([REVENUE, REVENUE])
    with pytest.raises(DatasetConstructionError):
        sources.observe("sales.revenue")
    with pytest.raises(DatasetConstructionError):
        sources.observe(ref.entity("sales.orders"))
    with pytest.raises(SemanticLoadError):
        sources.observe(ref.metric("sales.absent"))
    with pytest.raises(DatasetConstructionError):
        sources.observe(REVENUE, time_dimension=DAY)
    with pytest.raises(DatasetConstructionError):
        dataset.with_dimensions(REGION, REGION)
    with pytest.raises(DatasetConstructionError):
        dataset.with_dimensions(DAY)
    timed = dataset.with_time_axis(DAY, grain=grain("day"))
    with pytest.raises(DatasetConstructionError):
        timed.with_time_axis(DAY, grain=grain("day"))
    assert "metric.with_time_axis:" not in timed.contract().render()
    with pytest.raises(DatasetConstructionError):
        dataset.with_time_axis(DAY, grain=grain("hour"))
    with pytest.raises(DatasetConstructionError):
        dataset.aggregate().aggregate()
    with pytest.raises(DatasetConstructionError):
        dataset.aggregate().where(gt(REVENUE, 0))
    with pytest.raises(DatasetConstructionError):
        dataset.aggregate().with_dimensions(REGION)


def test_selection_and_projection_preserve_exact_contribution_boundaries() -> None:
    source = make_sources().observe(
        [REVENUE, ref.metric("sales.conversion_rate")], time_scope=WINDOW
    )
    selected = source.with_time_axis(DAY, grain=grain("day")).where(gt(REVENUE, 60))
    reduced = selected.aggregate().metric(ref.metric("sales.conversion_rate"))
    assert isinstance(reduced._root, LogicalRootHandle) and isinstance(
        reduced._root.payload, MetricPayload
    )
    assert reduced._root.payload.definition.selection_boundaries
    assert len(reduced._root.payload.definition.metrics) == 1
    assert tuple(column.name for column in reduced.schema.columns) == (
        "order_time",
        "conversion_rate",
    )
    assert len(source.schema.columns) == 3
    with pytest.raises(DatasetConstructionError):
        reduced.metric(REVENUE)
    with pytest.raises((FrozenInstanceError, DatasetConstructionError, AttributeError)):
        source._definition_fingerprint = "changed"


def test_parameterized_source_capture_survives_scope_exit_and_chaining() -> None:
    sources = make_sources()
    api = ref.entity("sales.api")
    metric = ref.metric("sales.api_value")
    with sources.source_bindings({api: {"tenant": "PRIVATE_CAPTURE_2910"}}):
        observed = sources.observe(metric, time_scope=WINDOW)
        membership = sources.population(api)
    filtered = (
        observed.where(gt(metric, 0))
        .with_dimensions(ref.dimension("sales.api.region"))
        .with_time_axis(ref.time_dimension("sales.api.observed_at"), grain=grain("day"))
        .aggregate()
    )
    filtered_membership = membership.where(eq(ref.dimension("sales.api.region"), "EU"))
    assert filtered.kind == "metric" and filtered_membership.kind == "population"
    assert "PRIVATE_CAPTURE_2910" not in repr(filtered)
    assert "PRIVATE_CAPTURE_2910" not in filtered.contract().render()
    assert isinstance(observed._root, LogicalRootHandle)
    assert "PRIVATE_CAPTURE_2910" not in repr(observed._root.parameters)


def _retained(
    logical: LogicalMetricDataset | LogicalPopulationDataset,
) -> MaterializedMetricDataset | MaterializedPopulationDataset:
    """Trusted test authority only; this helper makes no persistence/recovery claim."""
    ids = logical._registration.ids
    schema = _make_schema(
        tuple(
            replace(
                column,
                _token=_CORE_TOKEN,
                physical_type_state=_resolved_type(column.logical_type_id, ids=ids),
            )
            for column in logical.schema.columns
        )
    )
    state = _materialized_state(
        artifact_ref=ArtifactRef(ref="art_observation_fixture"),
        artifact_session_ref=logical._owner.session_id,
        content_authority_digest="fixture-content",
        storage_kind_id="parquet",
        realized_schema=schema,
        realized_row_count=1,
        realized_byte_count=_exact_byte_count(64),
        producing_run_ref="run_fixture",
        quality_authority_digest="fixture-quality",
        evidence_authority_digest="fixture-evidence",
        ids=ids,
    )
    result = _make_materialized_dataset(
        owner=logical._owner,
        registry=logical._registry,
        family_id=logical.kind,
        row_contract=logical.row_contract,
        row_set_contract=logical.row_set_contract,
        state=state,
        definition_fingerprint=logical.definition_fingerprint,
    )
    assert isinstance(result, (MaterializedMetricDataset, MaterializedPopulationDataset))
    return result


def test_materialized_rows_only_admit_exact_scans_and_new_sources() -> None:
    sources = make_sources()
    retained = _retained(sources.observe(REVENUE, time_scope=WINDOW))
    assert isinstance(retained, MaterializedMetricDataset)
    filtered = retained.where(gt(REVENUE, 0)).metric(REVENUE)
    assert isinstance(filtered._root, LogicalRootHandle)
    parent = filtered._root.inputs[0].root
    assert isinstance(parent, LogicalRootHandle)
    assert isinstance(parent.inputs[0].root, MaterializedScanLeafHandle)
    assert not hasattr(parent.inputs[0].root, "payload")
    for action in (
        retained.aggregate,
        lambda: retained.with_dimensions(REGION),
        lambda: filtered.aggregate(),
    ):
        with pytest.raises(DatasetConstructionError, match="retained rows"):
            action()
    followup = make_sources(session_id="next-session").observe(
        REVENUE, population=retained, time_scope=WINDOW
    )
    assert isinstance(followup._root, LogicalRootHandle)
    assert isinstance(followup._root.inputs[0].root, MaterializedScanLeafHandle)
    with pytest.raises(DatasetOwnershipError):
        make_sources(store_id="other-store").observe(REVENUE, population=retained)
    with pytest.raises(DatasetOwnershipError):
        make_sources(session_id="other-session").observe(
            REVENUE, population=sources.observe(REVENUE)
        )


def test_family_registration_rejects_corrupt_entity_shape() -> None:
    from marivo.analysis.datasets.base import _make_logical_dataset
    from marivo.analysis.datasets.descriptors import _make_row_contract

    logical = make_sources().observe(REVENUE)
    metric_value = logical.schema.columns[-1]
    corrupt = _make_row_contract(
        schema_version=1,
        shape_id=logical.row_contract.shape_id,
        schema=_make_schema((metric_value,)),
        coordinate_field_ids=(metric_value.field_id,),
        key_field_ids=(metric_value.field_id,),
        family_semantics=logical.row_contract.family_semantics,
    )
    assert isinstance(logical._root, LogicalRootHandle)
    with pytest.raises(DatasetConstructionError, match="coordinate"):
        _make_logical_dataset(
            owner=logical._owner,
            registry=logical._registry,
            family_id="metric",
            row_contract=corrupt,
            row_set_contract=logical.row_set_contract,
            operator_id="session.observe",
            payload=logical._root.payload,
        )


def test_private_facade_requires_an_explicit_complete_action_port() -> None:
    from marivo.analysis.session._lazy_sources import make_lazy_sources
    from tests.lazy_observation_fixtures import make_semantic_registry

    registry, sidecar = make_semantic_registry()
    with pytest.raises(DatasetConstructionError, match="runtime owner"):
        make_lazy_sources(
            semantic_registry=registry,
            sidecar=sidecar,
            action_port=None,
            session_id="s",
            store_id="store",
        )


def test_metric_value_name_collision_uses_stable_identity() -> None:
    from marivo.analysis.session._lazy_sources import make_lazy_sources
    from marivo.semantic.validator import Registry
    from tests.lazy_observation_fixtures import NoIoActionPort, make_semantic_registry

    original, sidecar = make_semantic_registry()
    registry = Registry(
        domains=dict(original.domains),
        datasources=dict(original.datasources),
        entities=dict(original.entities),
        dimensions=dict(original.dimensions),
        measures=dict(original.measures),
        metrics=dict(original.metrics),
        relationships=dict(original.relationships),
    )
    registry.metrics["sales.revenue"] = replace(registry.metrics["sales.revenue"], name="region")
    registry.freeze()
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="s",
        store_id="store",
    )
    result = sources.observe(REVENUE).with_dimensions(REGION)
    assert tuple(column.name for column in result.schema.columns) == (
        "entity_identity",
        "dimension__sales__customers__region",
        "metric__sales__revenue",
    )


def _editable_authority() -> tuple[Registry, CompiledExpressionSidecar]:
    from marivo.semantic.validator import Registry
    from tests.lazy_observation_fixtures import make_semantic_registry

    original, sidecar = make_semantic_registry()
    return Registry(
        domains=dict(original.domains),
        datasources=dict(original.datasources),
        entities=dict(original.entities),
        dimensions=dict(original.dimensions),
        measures=dict(original.measures),
        metrics=dict(original.metrics),
        relationships=dict(original.relationships),
    ), sidecar


def _sources_from(registry: Registry, sidecar: CompiledExpressionSidecar) -> LazySources:
    from marivo.analysis.session._lazy_sources import make_lazy_sources
    from tests.lazy_observation_fixtures import NoIoActionPort

    registry.freeze()
    return make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="session-observation",
        store_id="store-observation",
    )


def test_population_prefers_its_own_default_before_remote_defaults() -> None:
    registry, sidecar = _editable_authority()
    registry.dimensions["sales.customers.signup_time"] = replace(
        registry.dimensions["sales.orders.order_time"],
        semantic_id="sales.customers.signup_time",
        entity="sales.customers",
        name="signup_time",
    )
    population = _sources_from(registry, sidecar).population(
        ref.entity("sales.orders"), time_scope=WINDOW
    )
    assert isinstance(population._root, LogicalRootHandle) and isinstance(
        population._root.payload, PopulationPayload
    )
    assert population._root.payload.reference_axis.ref.path == "sales.orders.order_time"


def test_versioned_intermediate_and_incompatible_join_keys_fail_locally() -> None:
    from marivo.semantic.ir import JoinKey

    registry, sidecar = _editable_authority()
    relationship = registry.relationships["sales.order_customer"]
    registry.relationships["sales.order_customer"] = replace(
        relationship, to_entity="sales.snapshots"
    )
    registry.relationships["sales.snapshot_customer"] = replace(
        relationship,
        semantic_id="sales.snapshot_customer",
        from_entity="sales.snapshots",
    )
    sources = _sources_from(registry, sidecar)
    with pytest.raises(DatasetConstructionError, match="versioned"):
        sources.observe(REVENUE, population=sources.population(ref.entity("sales.customers")))
    with pytest.raises(DatasetConstructionError, match="versioned"):
        sources.observe(REVENUE).with_time_axis(
            ref.time_dimension("sales.snapshots.snapshot_at"), grain=grain("day")
        )
    registry, sidecar = _editable_authority()
    registry.relationships["sales.order_customer"] = replace(
        registry.relationships["sales.order_customer"], keys=(JoinKey("region", "id"),)
    )
    sources = _sources_from(registry, sidecar)
    with pytest.raises(DatasetConstructionError, match="join keys"):
        sources.observe(REVENUE, population=sources.population(ref.entity("sales.customers")))


def test_retained_population_never_recaptures_its_parameterized_origin() -> None:
    from marivo.semantic.ir import JoinKey

    registry, sidecar = _editable_authority()
    registry.relationships["sales.order_api"] = replace(
        registry.relationships["sales.order_customer"],
        semantic_id="sales.order_api",
        to_entity="sales.api",
        keys=(JoinKey("customer_id", "id"),),
    )
    sources = _sources_from(registry, sidecar)
    with sources.source_bindings({ref.entity("sales.api"): {"tenant": "PRIVATE_OLD_ORIGIN"}}):
        retained = _retained(sources.population(ref.entity("sales.api")))
    result = sources.observe(REVENUE, population=retained)
    assert isinstance(result._root, LogicalRootHandle) and isinstance(
        result._root.payload, MetricPayload
    )
    assert not result._root.payload.captures
    assert isinstance(result._root.inputs[0].root, MaterializedScanLeafHandle)


def test_retained_membership_rejects_changed_current_identity() -> None:
    retained = _retained(make_sources().population(ref.entity("sales.customers")))
    registry, sidecar = _editable_authority()
    registry.entities["sales.customers"] = replace(
        registry.entities["sales.customers"], primary_key=("tenant", "id")
    )
    sources = _sources_from(registry, sidecar)
    recovered = _make_materialized_dataset(
        owner=sources._owner,
        registry=sources._registry,
        family_id="population",
        row_contract=retained.row_contract,
        row_set_contract=retained.row_set_contract,
        state=retained.state,
        definition_fingerprint=retained.definition_fingerprint,
    )
    assert isinstance(recovered, MaterializedPopulationDataset)
    with pytest.raises(DatasetConstructionError, match="changed identity"):
        recovered.where(eq(REGION, "EU"))


def test_coordinate_and_membership_fingerprints_bind_sources_and_join_keys() -> None:
    from marivo.datasource.ir import CsvSourceIR
    from marivo.semantic.ir import JoinKey

    baseline = make_sources()
    metric_fingerprint = baseline.observe(REVENUE).with_dimensions(REGION).definition_fingerprint
    population_fingerprint = (
        baseline.population(ref.entity("sales.orders"))
        .where(eq(REGION, "EU"))
        .definition_fingerprint
    )
    for change in ("source", "join"):
        registry, sidecar = _editable_authority()
        if change == "source":
            entity = registry.entities["sales.customers"]
            assert isinstance(entity.source, CsvSourceIR)
            registry.entities[entity.semantic_id] = replace(
                entity, source=replace(entity.source, path="changed.csv")
            )
        else:
            registry.relationships["sales.order_customer"] = replace(
                registry.relationships["sales.order_customer"], keys=(JoinKey("id", "id"),)
            )
        changed = _sources_from(registry, sidecar)
        assert (
            changed.observe(REVENUE).with_dimensions(REGION).definition_fingerprint
            != metric_fingerprint
        )
        assert (
            changed.population(ref.entity("sales.orders"))
            .where(eq(REGION, "EU"))
            .definition_fingerprint
            != population_fingerprint
        )


def test_semi_additive_contract_is_explicitly_deferred_and_not_advertised() -> None:
    from marivo.semantic.ir import SemiAdditive, TimeFoldIR

    registry, sidecar = _editable_authority()
    registry.measures["sales.orders.amount"] = replace(
        registry.measures["sales.orders.amount"],
        additivity=SemiAdditive("sales.orders.order_time", TimeFoldIR("last")),
    )
    with pytest.raises(DatasetConstructionError, match="semi-additive"):
        _sources_from(registry, sidecar).observe(REVENUE)
    versioned = make_sources().population(ref.entity("sales.snapshots"), time_scope=WINDOW)
    assert "population.where:" not in versioned.contract().render()
