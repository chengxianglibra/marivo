"""Contract tests for the final sealed semantic Ref value."""

from __future__ import annotations

import copy
import dataclasses
import json
import pickle
from collections.abc import Callable

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from marivo.refs import (
    DimensionKind,
    MetricKind,
    Ref,
    RefPayloadV1,
    SemanticKind,
    SemanticKindTag,
    TimeDimensionKind,
    _decode_ref_key,
    _decode_ref_payload,
)
from marivo.refs import (
    ref as ref_factory,
)


@pytest.mark.parametrize(
    ("factory", "path", "kind"),
    [
        (ref_factory.domain, "sales", SemanticKind.DOMAIN),
        (ref_factory.datasource, "warehouse", SemanticKind.DATASOURCE),
        (ref_factory.entity, "sales.orders", SemanticKind.ENTITY),
        (ref_factory.dimension, "sales.orders.country", SemanticKind.DIMENSION),
        (
            ref_factory.time_dimension,
            "sales.orders.created_at",
            SemanticKind.TIME_DIMENSION,
        ),
        (ref_factory.measure, "sales.orders.amount", SemanticKind.MEASURE),
        (ref_factory.metric, "sales.revenue", SemanticKind.METRIC),
        (ref_factory.relationship, "sales.orders_to_users", SemanticKind.RELATIONSHIP),
    ],
)
def test_exact_factories_cover_all_kinds(
    factory: Callable[[str], Ref[SemanticKindTag]], path: str, kind: SemanticKind
) -> None:
    ref = factory(path)
    assert type(ref) is Ref
    assert ref.kind is kind
    assert ref.path == path
    assert ref.key == f"{kind.value}:{path}"
    assert ref.name == path.rsplit(".", 1)[-1]


def test_value_identity_is_kind_and_path_only() -> None:
    left = ref_factory.metric("sales.revenue")
    equal = ref_factory.metric("sales.revenue")
    other_path = ref_factory.metric("other.revenue")
    other_kind = ref_factory.entity("sales.revenue")
    assert left == equal
    assert hash(left) == hash(equal)
    assert left != other_path
    assert left != other_kind
    assert {left: "value"}[equal] == "value"
    assert str(left) == "metric:sales.revenue"
    with pytest.raises(AttributeError):
        left.path = "sales.other"
    with pytest.raises(AttributeError):
        left.kind = SemanticKind.ENTITY
    assert not hasattr(left, "__dict__")
    assert not hasattr(left, "_resolver")


def test_copy_deepcopy_pickle_and_replace_contract() -> None:
    ref = ref_factory.dimension("sales.orders.country")
    assert copy.copy(ref) is ref
    assert copy.deepcopy(ref) is ref
    for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
        restored = pickle.loads(pickle.dumps(ref, protocol=protocol))
        assert restored == ref
        assert type(restored) is Ref
    with pytest.raises(TypeError):
        dataclasses.replace(ref, path="sales.orders.region")  # type: ignore[type-var]  # Ref is not a dataclass.


def test_current_ref_payload_and_canonical_key_decode() -> None:
    ref = ref_factory.metric("sales.revenue")
    payload = RefPayloadV1.from_ref(ref)
    assert _decode_ref_payload(payload) == ref
    assert (
        _decode_ref_payload(
            {
                "schema": "marivo.semantic_ref/v1",
                "kind": "metric",
                "path": "sales.revenue",
            }
        )
        == ref
    )
    assert _decode_ref_key("metric:sales.revenue") == ref


class _MetricEnvelope(BaseModel):
    metric: Ref[MetricKind]


def test_pydantic_python_mode_accepts_only_exact_ref_and_preserves_value() -> None:
    ref = ref_factory.metric("sales.revenue")
    envelope = _MetricEnvelope.model_validate({"metric": ref})
    assert envelope.metric is ref
    dumped = envelope.model_dump(mode="python")
    assert dumped["metric"] is ref
    for invalid in (
        {
            "schema": "marivo.semantic_ref/v1",
            "kind": "metric",
            "path": "sales.revenue",
        },
        ref_factory.entity("sales.orders"),
    ):
        with pytest.raises(ValidationError):
            _MetricEnvelope.model_validate({"metric": invalid})


def test_pydantic_json_mode_is_structured_exact_and_kind_checked() -> None:
    ref = ref_factory.metric("sales.revenue")
    envelope = _MetricEnvelope(metric=ref)
    raw = envelope.model_dump_json()
    assert json.loads(raw) == {
        "metric": {
            "schema": "marivo.semantic_ref/v1",
            "kind": "metric",
            "path": "sales.revenue",
        }
    }
    assert _MetricEnvelope.model_validate_json(raw).metric == ref
    for payload in (
        {"metric": "metric:sales.revenue"},
        {
            "metric": {
                "schema": "marivo.semantic_ref/v1",
                "kind": "entity",
                "path": "sales.orders",
            }
        },
        {
            "metric": {
                "schema": "marivo.semantic_ref/v1",
                "kind": "metric",
                "path": "sales.revenue",
                "extra": True,
            }
        },
    ):
        with pytest.raises(ValidationError):
            _MetricEnvelope.model_validate_json(json.dumps(payload))


def test_pydantic_json_schema_uses_const_and_closed_union() -> None:
    exact = TypeAdapter(Ref[MetricKind]).json_schema()
    assert exact["properties"]["kind"]["const"] == "metric"
    field = TypeAdapter(Ref[DimensionKind | TimeDimensionKind]).json_schema()
    assert field["properties"]["kind"]["enum"] == ["dimension", "time_dimension"]
    with pytest.raises(TypeError):
        TypeAdapter(Ref)


@pytest.mark.parametrize(
    ("args", "kwargs"),
    [
        ((), {}),
        (("sales.revenue",), {}),
        ((SemanticKind.METRIC, "sales.revenue"), {}),
        ((), {"kind": SemanticKind.METRIC, "path": "sales.revenue"}),
        ((SemanticKind.METRIC,), {"path": "sales.revenue"}),
        ((SemanticKind.METRIC, "sales.revenue", "extra"), {}),
    ],
)
def test_every_raw_construction_form_is_sealed(
    args: tuple[object, ...], kwargs: dict[str, object]
) -> None:
    with pytest.raises(TypeError):
        Ref(*args, **kwargs)  # type: ignore[arg-type]  # Reject raw construction.


def test_subclassing_is_sealed() -> None:
    with pytest.raises(TypeError):

        class InvalidRef(Ref[MetricKind]):  # type: ignore[misc]  # Verify the runtime subclass seal.
            pass


@pytest.mark.parametrize(
    ("factory", "path"),
    [
        (ref_factory.datasource, "prod-mysql"),
        (ref_factory.datasource, "Warehouse"),
        (ref_factory.datasource, "1warehouse"),
        (ref_factory.datasource, " warehouse"),
        (ref_factory.entity, "  sales.x  "),
        (ref_factory.entity, ""),
        (ref_factory.metric, "revenue"),
        (ref_factory.metric, "sales.revenue.extra"),
        (ref_factory.entity, "sales."),
        (ref_factory.dimension, "sales.orders.OrderID"),
    ],
)
def test_path_grammar_is_exact_and_never_normalizes(
    factory: Callable[[str], Ref[SemanticKindTag]], path: str
) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory(path)


def test_ref_is_data_only_and_factories_are_separate() -> None:
    value = ref_factory.dimension("sales.orders.country")
    assert not callable(value)
    assert not hasattr(value, "metric")
    assert not hasattr(Ref, "metric")
