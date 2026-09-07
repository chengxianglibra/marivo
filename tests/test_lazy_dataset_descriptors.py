"""Closed descriptor variants and exact immutable binding identity."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace

import pytest

from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    DatasetFieldId,
    DatasetFieldIdentity,
    _canonical_digest,
    _catalog_identity,
    _complete_from_schema,
    _deferred_type,
    _entity_identity,
    _exact_byte_count,
    _ExactByteCount,
    _field_binding_fingerprint,
    _generated_identity,
    _keyed_cardinality,
    _make_field,
    _make_field_id,
    _make_order_term,
    _make_schema,
    _make_shape_id,
    _ordered_ordering,
    _resolved_type,
    _runtime_metric_identity,
    _runtime_policy_row_bound,
    _singleton_cardinality,
    _StableIdRegistry,
    _static_row_bound,
    _unavailable_byte_count,
    _unknown_row_bound,
    _unordered_ordering,
    _validate_realized_schema,
)
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.state import (
    LogicalDatasetState,
    _logical_state,
    _materialized_state,
    _validate_materialized_state,
)
from marivo.analysis.errors import AnalysisError
from marivo.analysis.refs import ArtifactRef
from marivo.refs import ref
from tests.lazy_dataset_fixtures import TEST_IDS, make_materialized_dataset, make_row_contracts


def test_all_closed_variants_have_exact_non_optional_fields() -> None:
    field_id = _make_field_id("value")
    term = _make_order_term(
        field_id,
        direction="descending",
        nulls="last",
        value_order_contract_id="test.numeric",
        ids=TEST_IDS,
    )
    cases = (
        (_catalog_identity("metric:revenue"), "catalog_ref", {"kind", "identity_id"}),
        (
            _entity_identity(ref.entity("sales.customer"), (("id", "numeric"),), ids=TEST_IDS),
            "entity_identity",
            {"kind", "entity_ref", "identity_signature"},
        ),
        (
            _runtime_metric_identity("runtime:v1"),
            "runtime_metric",
            {"kind", "expression_fingerprint"},
        ),
        (_generated_identity(field_id), "generated", {"kind", "producer_field_id"}),
        (_resolved_type("float64", ids=TEST_IDS), "resolved", {"kind", "physical_type_id"}),
        (_deferred_type("numeric", ids=TEST_IDS), "deferred", {"kind", "admitted_type_class_id"}),
        (_unknown_row_bound(), "unknown", {"kind"}),
        (_static_row_bound(10), "static", {"kind", "max_rows"}),
        (
            _runtime_policy_row_bound("test.collection", ids=TEST_IDS),
            "runtime_policy",
            {"kind", "policy_id"},
        ),
        (_singleton_cardinality(), "singleton", {"kind"}),
        (_keyed_cardinality(_unknown_row_bound()), "keyed", {"kind", "row_bound"}),
        (_unordered_ordering(), "unordered", {"kind"}),
        (_ordered_ordering((term,)), "ordered", {"kind", "terms"}),
        (_complete_from_schema(), "complete_from_schema", {"kind"}),
        (_exact_byte_count(0), "exact", {"kind", "byte_count"}),
        (
            _unavailable_byte_count("test.unavailable", ids=TEST_IDS),
            "unavailable",
            {"kind", "reason_id"},
        ),
    )
    for value, kind, expected_fields in cases:
        assert value.kind == kind
        assert {item.name for item in fields(value)} == expected_fields
        assert not hasattr(value, "__dict__")
        assert all(getattr(value, item.name) is not None for item in fields(value))
        assert "\n" not in repr(value)
        assert "0x" not in repr(value)
        assert len(repr(value)) <= 200
        with pytest.raises((FrozenInstanceError, TypeError)):
            value.kind = "changed"


def test_entity_identity_preserves_complete_ordered_component_signature() -> None:
    base = make_row_contracts("entity")[0].schema.columns[0]
    entity = ref.entity("sales.customer")
    signatures = (
        (("id", "numeric"),),
        (("id", "string"),),
        (("region", "string"), ("id", "numeric")),
        (("id", "numeric"), ("region", "string")),
    )
    identities = tuple(
        _entity_identity(entity, signature, ids=TEST_IDS) for signature in signatures
    )
    assert identities[0].entity_ref == entity
    assert identities[0].identity_signature == (("id", "numeric"),)
    fingerprints = {
        _field_binding_fingerprint(replace(base, _token=_CORE_TOKEN, identity=identity))
        for identity in identities
    }
    other = _entity_identity(ref.entity("sales.account"), signatures[0], ids=TEST_IDS)
    fingerprints.add(_field_binding_fingerprint(replace(base, _token=_CORE_TOKEN, identity=other)))
    assert len(fingerprints) == 5


@pytest.mark.parametrize(
    "signature",
    [(), [], (("id", "unknown"),), (("id", "numeric"), ("id", "string")), (("id",),)],
)
def test_entity_identity_rejects_invalid_component_signatures(signature: tuple) -> None:
    with pytest.raises(DatasetConstructionError):
        _entity_identity(ref.entity("sales.customer"), signature, ids=TEST_IDS)


def test_entity_identity_rejects_a_ref_of_another_kind() -> None:
    with pytest.raises(DatasetConstructionError, match="an exact Entity ref"):
        _entity_identity(ref.metric("sales.revenue"), (("id", "numeric"),), ids=TEST_IDS)


def test_shape_field_id_and_schema_are_exact_bounded_immutable_values() -> None:
    row, _ = make_row_contracts("entity")
    assert str(row.shape_id) == "test/entity@v1"
    assert {item.name for item in fields(row.shape_id)} == {
        "family_id",
        "local_shape_id",
        "semantic_version",
    }
    assert {item.name for item in fields(row.schema)} == {"columns"}
    assert row.schema.columns[0].field_id == _make_field_id("entity_id")
    assert hash(row.schema.columns[0].field_id) == hash(_make_field_id("entity_id"))
    for value in (row.shape_id, row.schema, row.schema.columns[0], row.schema.columns[0].field_id):
        assert not hasattr(value, "__dict__")
        assert len(repr(value)) < 200
        with pytest.raises((FrozenInstanceError, TypeError)):
            attribute = "value"
            setattr(value, attribute, "changed")


def test_constructors_and_untrusted_subclasses_are_rejected() -> None:
    with pytest.raises(TypeError):
        # This malformed public call deliberately omits the private construction token.
        DatasetFieldId(value="value")  # type: ignore[call-arg]
    with pytest.raises(DatasetConstructionError, match="direct construction"):
        DatasetFieldId(value="value", _token=object())
    with pytest.raises(DatasetConstructionError, match="abstract descriptor"):
        DatasetFieldIdentity(_token=_CORE_TOKEN)
    with pytest.raises(DatasetConstructionError, match="unregistered subclass"):
        type("ForgedField", (DatasetFieldId,), {"__module__": __name__})
    with pytest.raises(DatasetConstructionError, match="unregistered subclass"):
        type("ForgedField", (DatasetFieldId,), {"__module__": DatasetFieldId.__module__})
    with pytest.raises(DatasetConstructionError, match="unregistered subclass"):
        type(
            "LogicalDatasetState",
            (LogicalDatasetState,),
            {
                "__module__": LogicalDatasetState.__module__,
                "__qualname__": LogicalDatasetState.__qualname__,
            },
        )


@pytest.mark.parametrize("value", ["", "display name", "field\nname", "/bad", "a" * 161])
def test_field_id_rejects_noncanonical_names(value: str) -> None:
    with pytest.raises(DatasetConstructionError):
        _make_field_id(value)


@pytest.mark.parametrize("version", [0, -1, True])
def test_shape_requires_exact_registered_positive_version(version: int) -> None:
    with pytest.raises(DatasetConstructionError):
        _make_shape_id("test", "scalar", version, ids=TEST_IDS)


def test_stable_vocabulary_is_explicit_and_immutable() -> None:
    with pytest.raises(DatasetConstructionError, match="registered"):
        _make_shape_id("other", "scalar", 1, ids=TEST_IDS)
    with pytest.raises(DatasetConstructionError, match="registered"):
        _resolved_type("backend-guessed", ids=TEST_IDS)
    with pytest.raises(DatasetConstructionError, match="registered"):
        _deferred_type("any-type", ids=TEST_IDS)
    with pytest.raises(DatasetConstructionError, match="registered"):
        _runtime_policy_row_bound("caller-policy", ids=TEST_IDS)
    with pytest.raises(DatasetConstructionError, match="registered"):
        _unavailable_byte_count("unknown", ids=TEST_IDS)
    with pytest.raises(DatasetConstructionError):
        _StableIdRegistry(families=frozenset({"display name"}))
    with pytest.raises(DatasetConstructionError):
        _StableIdRegistry(shapes=frozenset({("missing", "scalar", 1)}))
    with pytest.raises(DatasetConstructionError):
        _StableIdRegistry(physical_type_classes=frozenset({("unknown", "unknown")}))
    with pytest.raises(FrozenInstanceError):
        attribute = "roles"
        setattr(TEST_IDS, attribute, frozenset())


@pytest.mark.parametrize("value", [-1, 0, True])
def test_static_row_bounds_reject_nonpositive_or_boolean_counts(value: int) -> None:
    with pytest.raises(DatasetConstructionError):
        _static_row_bound(value)


@pytest.mark.parametrize("value", [-1, True])
def test_exact_byte_count_rejects_negative_or_boolean_counts(value: int) -> None:
    with pytest.raises(DatasetConstructionError):
        _exact_byte_count(value)


def test_field_binding_fingerprint_covers_every_binding_fact() -> None:
    row, _ = make_row_contracts()
    value = row.schema.columns[0]
    assert {item.name for item in fields(value)} == {
        "field_id",
        "name",
        "role_id",
        "identity",
        "derivation_identity",
        "logical_type_id",
        "physical_type_state",
        "nullable",
    }
    changed = (
        replace(value, _token=_CORE_TOKEN, field_id=_make_field_id("other")),
        replace(value, _token=_CORE_TOKEN, name="other"),
        replace(value, _token=_CORE_TOKEN, role_id="dimension"),
        replace(value, _token=_CORE_TOKEN, identity=_catalog_identity("metric:revenue")),
        replace(value, _token=_CORE_TOKEN, derivation_identity="test.other/v1"),
        replace(value, _token=_CORE_TOKEN, logical_type_id="string"),
        replace(
            value, _token=_CORE_TOKEN, physical_type_state=_deferred_type("numeric", ids=TEST_IDS)
        ),
        replace(value, _token=_CORE_TOKEN, nullable=True),
    )
    original = _field_binding_fingerprint(value)
    assert len(original) == 64
    assert all(_field_binding_fingerprint(item) != original for item in changed)
    assert _field_binding_fingerprint(make_row_contracts()[0].schema.columns[0]) == original


def test_field_factory_rejects_unregistered_roles_and_noncanonical_names() -> None:
    row, _ = make_row_contracts()
    value = row.schema.columns[0]
    for name, role in (
        ("value", "arbitrary"),
        ("", "metric"),
        ("bad\nname", "metric"),
        ("e\u0301", "metric"),
    ):
        with pytest.raises(DatasetConstructionError):
            _make_field(
                field_id=value.field_id,
                name=name,
                role_id=role,
                identity=value.identity,
                derivation_identity=value.derivation_identity,
                logical_type_id=value.logical_type_id,
                physical_type_state=value.physical_type_state,
                nullable=value.nullable,
                ids=TEST_IDS,
            )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_canonical_encoder_rejects_nonfinite_literals(value: float) -> None:
    with pytest.raises(DatasetConstructionError, match="finite"):
        _canonical_digest(("value", value))


def test_logical_state_is_only_the_discriminator() -> None:
    value = _logical_state()
    assert {item.name for item in fields(value)} == {"kind"}
    assert value.kind == "logical"
    assert not hasattr(value, "artifact_ref")


def test_trusted_materialized_state_contains_exact_complete_fields() -> None:
    row, _ = make_row_contracts()
    value = _materialized_state(
        artifact_ref=ArtifactRef(ref="artifact-test"),
        artifact_session_ref="session-original",
        content_authority_digest="content-digest",
        storage_kind_id="test_parquet",
        realized_schema=row.schema,
        realized_row_count=0,
        realized_byte_count=_exact_byte_count(0),
        producing_run_ref="run-test",
        quality_authority_digest="quality-digest",
        evidence_authority_digest="evidence-digest",
        ids=TEST_IDS,
    )
    assert {item.name for item in fields(value)} == {
        "kind",
        "artifact_ref",
        "artifact_session_ref",
        "content_authority_digest",
        "storage_kind_id",
        "realized_schema",
        "realized_row_count",
        "realized_byte_count",
        "producing_run_ref",
        "quality_authority_digest",
        "evidence_authority_digest",
    }
    assert value.realized_schema is row.schema
    assert value.artifact_session_ref == "session-original"
    assert all(getattr(value, item.name) is not None for item in fields(value))
    assert len(repr(value)) <= 200


def test_realized_schema_allows_only_registered_refinements() -> None:
    row, _ = make_row_contracts()
    value = row.schema.columns[0]
    logical = _make_schema(
        (
            replace(
                value,
                _token=_CORE_TOKEN,
                physical_type_state=_deferred_type("numeric", ids=TEST_IDS),
            ),
        )
    )
    _validate_realized_schema(logical, row.schema, ids=TEST_IDS)
    wrong_type = _make_schema(
        (
            replace(
                value,
                _token=_CORE_TOKEN,
                physical_type_state=_resolved_type("string", ids=TEST_IDS),
            ),
        )
    )
    with pytest.raises(DatasetConstructionError, match="admitted"):
        _validate_realized_schema(logical, wrong_type, ids=TEST_IDS)
    with pytest.raises(DatasetConstructionError, match="resolved"):
        _validate_realized_schema(logical, logical, ids=TEST_IDS)
    with pytest.raises(DatasetConstructionError, match="binding differs"):
        _validate_realized_schema(
            logical,
            _make_schema((replace(value, _token=_CORE_TOKEN, name="renamed"),)),
            ids=TEST_IDS,
        )
    with pytest.raises(DatasetConstructionError, match="field count"):
        _validate_realized_schema(logical, _make_schema(()), ids=TEST_IDS)


def test_construction_errors_teach_without_an_eager_constraint_lookup() -> None:
    error = DatasetConstructionError(
        expected="a current field",
        received="stale field",
        repair="Select a field from the current schema.",
    )
    assert isinstance(error, AnalysisError)
    assert error.repair is not None
    assert error.repair.action == "Select a field from the current schema."
    assert error.expected == "a current field"
    assert error.received == "stale field"
    assert "Select a field" in str(error)
    assert "Expected:" in str(error)
    assert "Received:" in str(error)


@pytest.mark.parametrize("realized_row_count", [-1, True])
def test_materialized_state_requires_an_exact_nonnegative_row_count(
    realized_row_count: int,
) -> None:
    row, _ = make_row_contracts()
    with pytest.raises(DatasetConstructionError, match="non-negative"):
        _materialized_state(
            artifact_ref=ArtifactRef(ref="artifact-test"),
            artifact_session_ref="session-original",
            content_authority_digest="content-digest",
            storage_kind_id="test_parquet",
            realized_schema=row.schema,
            realized_row_count=realized_row_count,
            realized_byte_count=_exact_byte_count(0),
            producing_run_ref="run-test",
            quality_authority_digest="quality-digest",
            evidence_authority_digest="evidence-digest",
            ids=TEST_IDS,
        )


def test_materialized_state_rejects_deferred_schema_and_unsafe_refs() -> None:
    row, _ = make_row_contracts()
    value = row.schema.columns[0]
    deferred = _make_schema(
        (
            replace(
                value,
                _token=_CORE_TOKEN,
                physical_type_state=_deferred_type("numeric", ids=TEST_IDS),
            ),
        )
    )
    for schema, artifact_ref in ((deferred, "artifact-test"), (row.schema, "artifact\nunsafe")):
        with pytest.raises(DatasetConstructionError):
            _materialized_state(
                artifact_ref=ArtifactRef(ref=artifact_ref),
                artifact_session_ref="session-original",
                content_authority_digest="content-digest",
                storage_kind_id="test_parquet",
                realized_schema=schema,
                realized_row_count=1,
                realized_byte_count=_unavailable_byte_count("test.unavailable", ids=TEST_IDS),
                producing_run_ref="run-test",
                quality_authority_digest="quality-digest",
                evidence_authority_digest="evidence-digest",
                ids=TEST_IDS,
            )


def test_materialized_state_revalidates_counts_storage_and_byte_reason() -> None:
    state = make_materialized_dataset().state
    _validate_materialized_state(state, ids=TEST_IDS)
    foreign_ids = replace(
        TEST_IDS, byte_unavailable_reasons=TEST_IDS.byte_unavailable_reasons | {"foreign.reason"}
    )
    count = _exact_byte_count(1)
    assert isinstance(count, _ExactByteCount)
    corruptions = (
        replace(state, _token=_CORE_TOKEN, realized_row_count=-1),
        replace(state, _token=_CORE_TOKEN, storage_kind_id="foreign.storage"),
        replace(
            state,
            _token=_CORE_TOKEN,
            realized_byte_count=_unavailable_byte_count("foreign.reason", ids=foreign_ids),
        ),
        replace(
            state,
            _token=_CORE_TOKEN,
            realized_byte_count=replace(count, _token=_CORE_TOKEN, byte_count=-1),
        ),
    )
    for corrupted in corruptions:
        with pytest.raises(DatasetConstructionError):
            _validate_materialized_state(corrupted, ids=TEST_IDS)
