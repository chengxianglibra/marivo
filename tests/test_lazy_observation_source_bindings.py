"""Actual semantic normalization feeds exact, process-local source captures."""

from __future__ import annotations

import asyncio
import pickle
from collections import UserList
from dataclasses import FrozenInstanceError, replace
from typing import TypeAlias

import pytest

from marivo.analysis.observation.errors import ObservationBindingError
from marivo.analysis.observation.source_bindings import (
    SourceBindingScopes,
    SourceBindingValue,
)
from marivo.datasource.ir import (
    AiContextIR,
    CsvSourceIR,
    DatasourceIR,
    DatasourceSourceLocation,
    JsonSourceIR,
    SourceParamIR,
    TableSourceIR,
)
from marivo.refs import ref
from marivo.semantic.ir import EntityIR, SourceLocation, TargetEntityContract
from marivo.semantic.validator import Registry, normalize_target_entity

_Source: TypeAlias = JsonSourceIR | CsvSourceIR


def _entity(
    name: str = "samples",
    *,
    source: _Source | None = None,
    credential_slot: str | None = None,
) -> TargetEntityContract:
    if source is None:
        source = JsonSourceIR(
            path="https://fixture.invalid/samples",
            schema=(("id", "int64"), ("value", "float64")),
            query_params=(("from", SourceParamIR("start")), ("to", SourceParamIR("end"))),
        )
    entity_id = f"monitoring.{name}"
    registry = Registry(
        entities={
            entity_id: EntityIR(
                semantic_id=entity_id,
                domain="monitoring",
                name=name,
                datasource="warehouse",
                source=source,
                primary_key=("id",),
                ai_context=AiContextIR(),
                python_symbol=name,
                location=SourceLocation("fixture.py", 1),
            )
        },
        datasources={
            "warehouse": DatasourceIR(
                semantic_id="warehouse",
                name="warehouse",
                backend_type="duckdb",
                fields={},
                env_refs={credential_slot: "FIXTURE_CREDENTIAL"} if credential_slot else {},
                ai_context=AiContextIR(),
                python_symbol="warehouse",
                location=DatasourceSourceLocation("fixture.py", 1),
            )
        },
    )
    return normalize_target_entity(registry, entity_id)


def test_capture_is_ordered_immutable_and_survives_scope_exit() -> None:
    entity = _entity()
    scopes = SourceBindingScopes((entity,))
    values: dict[str, SourceBindingValue] = {"end": 100, "start": [1, "TOPIC_VALUES_398712"]}
    with scopes.scope({ref.entity(entity.ref.path): values}):
        captured = scopes.capture((entity,))[0]
        assert captured.ordered_parameter_names == ("start", "end")
        assert captured.private_canonical_typed_values == ((1, "TOPIC_VALUES_398712"), 100)
        original_digest = captured.exact_value_digest
        sequence = values["start"]
        assert isinstance(sequence, list)
        sequence.append(999)
        values["end"] = 999
        assert scopes.capture((entity,))[0].exact_value_digest == original_digest
    assert captured.exact_value_digest == original_digest
    assert "TOPIC_VALUES_398712" not in repr(captured)
    assert "TOPIC_VALUES_398712" not in repr(captured.identity_payload())
    assert "TOPIC_VALUES_398712" not in repr(captured.bounded_redacted_projection)
    with pytest.raises(FrozenInstanceError):
        captured.exact_value_digest = "changed"
    with pytest.raises(ObservationBindingError):
        pickle.dumps(captured)
    with pytest.raises(ObservationBindingError):
        scopes.capture((entity,))


def test_scalar_type_and_sequence_order_are_exact_identity() -> None:
    entity = _entity()
    scopes = SourceBindingScopes((entity,))
    digests: list[str] = []
    for value in (True, 1, 1.0, "1", 0.0, -0.0, [1, 2], [2, 1]):
        with scopes.scope({ref.entity(entity.ref.path): {"start": value, "end": 10}}):
            digests.append(scopes.capture((entity,))[0].exact_value_digest)
    assert len(set(digests)) == len(digests)
    with scopes.scope({ref.entity(entity.ref.path): {"end": 10, "start": (1, 2)}}):
        assert scopes.capture((entity,))[0].exact_value_digest == digests[-2]


def test_inner_scope_replaces_complete_map_and_restores_after_failure() -> None:
    entity, other = _entity(), _entity("other")
    scopes = SourceBindingScopes((entity, other))
    outer = {
        ref.entity(entity.ref.path): {"start": 1, "end": 2},
        ref.entity(other.ref.path): {"start": 3, "end": 4},
    }
    with scopes.scope(outer):
        original = scopes.capture((entity, other))
        with scopes.scope({}), pytest.raises(ObservationBindingError):
            scopes.capture((entity,))
        assert scopes.capture((entity, other)) == original
        with (
            pytest.raises(RuntimeError, match="fixture stop"),
            scopes.scope({ref.entity(entity.ref.path): {"start": 5, "end": 6}}),
        ):
            assert scopes.capture((entity,))[0] != original[0]
            with pytest.raises(ObservationBindingError):
                scopes.capture((other,))
            raise RuntimeError("fixture stop")
        assert scopes.capture((entity, other)) == original


def test_unreachable_bindings_and_other_source_factory_do_not_change_capture() -> None:
    entity, other = _entity(), _entity("other")
    scopes = SourceBindingScopes((entity, other))
    foreign_scopes = SourceBindingScopes((entity,))
    with scopes.scope({ref.entity(entity.ref.path): {"start": 1, "end": 2}}):
        first = scopes.capture((entity,))
        with pytest.raises(ObservationBindingError):
            foreign_scopes.capture((entity,))
    with scopes.scope(
        {
            ref.entity(other.ref.path): {"start": 3, "end": 4},
            ref.entity(entity.ref.path): {"end": 2, "start": 1},
        }
    ):
        assert scopes.capture((entity,)) == first


@pytest.mark.parametrize(
    "value",
    [None, float("inf"), float("nan"), [], (), [[1]], {"nested": 1}, {1}, range(2), b"text"],
)
def test_invalid_values_fail_without_rendering_values(value: object) -> None:
    entity = _entity()
    scopes = SourceBindingScopes((entity,))
    with (
        pytest.raises(ObservationBindingError) as failure,
        scopes.scope({ref.entity(entity.ref.path): {"start": value, "end": 2}}),
    ):
        raise AssertionError("Invalid value was admitted")
    error = failure.value
    assert error.expected and error.received and error.repair and error.hint


@pytest.mark.parametrize("values", [{"start": 1}, {"start": 1, "end": 2, "extra": 3}])
def test_missing_or_extra_parameters_fail_locally(values: dict[str, int]) -> None:
    entity = _entity()
    with (
        pytest.raises(ObservationBindingError),
        SourceBindingScopes((entity,)).scope({ref.entity(entity.ref.path): values}),
    ):
        raise AssertionError("Invalid parameter names were admitted")


@pytest.mark.parametrize(
    "key",
    ["monitoring.samples", ref.metric("monitoring.samples"), ref.entity("monitoring.missing")],
)
def test_wrong_or_missing_entity_keys_fail(key: object) -> None:
    with (
        pytest.raises(ObservationBindingError),
        SourceBindingScopes((_entity(),)).scope({key: {"start": 1, "end": 2}}),
    ):
        raise AssertionError("Invalid Entity was admitted")


@pytest.mark.parametrize(
    "name,slot,credential",
    [("token", "value", None), ("value", "Authorization", None), ("start", "from", "start")],
)
def test_sensitive_names_and_aliased_credential_slots_are_rejected(
    name: str, slot: str, credential: str | None
) -> None:
    entity = _entity(
        source=JsonSourceIR(
            path="https://fixture.invalid/data",
            schema=(("id", "int64"),),
            query_params=((slot, SourceParamIR(name)),),
        ),
        credential_slot=credential,
    )
    with (
        pytest.raises(ObservationBindingError) as failure,
        SourceBindingScopes((entity,)).scope(
            {ref.entity(entity.ref.path): {name: "SENTINEL_PRIVATE_VALUE"}}
        ),
    ):
        raise AssertionError("Sensitive slot was admitted")
    assert "SENTINEL_PRIVATE_VALUE" not in str(failure.value)


def test_nonparameterized_and_non_json_entities_reject_scope_bindings() -> None:
    for source in (
        JsonSourceIR(path="https://fixture.invalid/data", schema=(("id", "int64"),)),
        CsvSourceIR(path="fixture.csv", schema=(("id", "int64"),)),
    ):
        entity = _entity(source=source)
        scopes = SourceBindingScopes((entity,))
        assert scopes.capture((entity,)) == ()
        with (
            pytest.raises(ObservationBindingError),
            scopes.scope({ref.entity(entity.ref.path): {"start": 1}}),
        ):
            raise AssertionError("Nonparameterized source accepted bindings")


def test_general_declared_sequence_cannot_hide_a_credential_alias() -> None:
    entity = _entity(
        source=JsonSourceIR(
            path="https://fixture.invalid/data",
            schema=(("id", "int64"),),
            query_params=(("Authorization", UserList([SourceParamIR("value")])),),
        )
    )
    with (
        pytest.raises(ObservationBindingError),
        SourceBindingScopes((entity,)).scope(
            {ref.entity(entity.ref.path): {"value": "SENTINEL_CREDENTIAL"}}
        ),
    ):
        raise AssertionError("Sequence alias admitted a credential slot")


def test_request_body_path_cannot_hide_a_credential_alias() -> None:
    entity = _entity(
        source=JsonSourceIR(
            path="https://fixture.invalid/data",
            schema=(("id", "int64"),),
            method="POST",
            body_json='{"auth": {"token": null}}',
            body_params=((("auth", "token"), SourceParamIR("value")),),
        )
    )
    with (
        pytest.raises(ObservationBindingError),
        SourceBindingScopes((entity,)).scope(
            {ref.entity(entity.ref.path): {"value": "SENTINEL_CREDENTIAL"}}
        ),
    ):
        raise AssertionError("Body alias admitted a credential slot")


def test_registry_scope_assembly_does_not_normalize_unrelated_identity_types() -> None:
    from tests.lazy_observation_fixtures import make_semantic_registry

    registry, _ = make_semantic_registry()
    unrelated = replace(
        registry.entities["sales.orders"],
        semantic_id="sales.untyped",
        name="untyped",
        source=TableSourceIR(table="untyped"),
    )
    extended = replace(registry, entities={**registry.entities, unrelated.semantic_id: unrelated})
    scopes = SourceBindingScopes.from_registry(extended)
    selected = normalize_target_entity(extended, "sales.api")
    with scopes.scope({ref.entity("sales.api"): {"tenant": "customer"}}):
        assert scopes.capture((selected,))[0].private_canonical_typed_values == ("customer",)


def test_async_contexts_do_not_share_active_values() -> None:
    entity = _entity()
    scopes = SourceBindingScopes((entity,))

    async def capture(value: int) -> tuple[object, ...]:
        with scopes.scope({ref.entity(entity.ref.path): {"start": value, "end": 10}}):
            await asyncio.sleep(0)
            return scopes.capture((entity,))[0].private_canonical_typed_values

    async def run() -> None:
        assert await asyncio.gather(capture(1), capture(2)) == [(1, 10), (2, 10)]

    asyncio.run(run())


def test_corrupt_capture_is_detected_before_safe_identity_is_used() -> None:
    entity = _entity()
    scopes = SourceBindingScopes((entity,))
    with scopes.scope({ref.entity(entity.ref.path): {"start": 1, "end": 2}}):
        captured = scopes.capture((entity,))[0]
    object.__setattr__(captured, "private_canonical_typed_values", (3, 4))
    with pytest.raises(ObservationBindingError, match="capture"):
        captured.identity_payload()
