"""Closed IR and authoring contract tests for typed table column bindings."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource._capabilities.contracts import source_subject_ref
from marivo.datasource.authoring_store import snapshot_identity
from marivo.datasource.ir import (
    TableColumnBindingIR,
    TableSourceIR,
    source_to_dict,
)
from marivo.semantic._definition_identity import definition_fingerprint
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.ir import source_from_dict


def _projected_source(*, reversed_order: bool = False) -> TableSourceIR:
    columns = (
        {
            "score": md.source_column("_generated_score", data_type="double"),
            "event_time": md.source_column("event.timestamp", data_type="timestamp"),
        }
        if reversed_order
        else {
            "event_time": md.source_column("event.timestamp", data_type="timestamp"),
            "score": md.source_column("_generated_score", data_type="float64"),
        }
    )
    return md.table("events", database="warehouse", columns=columns)


def _semantic_definition_fingerprint(source: TableSourceIR) -> str:
    entity_ref = ms.ref.entity("sales.events")
    return definition_fingerprint(
        selected_root_roles=(),
        filtered_domains=(),
        definitions={entity_ref: source},
        dependencies={},
        sidecar=CompiledExpressionSidecar(
            bodies={},
            field_owners={},
            catalog_refs=frozenset({entity_ref}),
        ),
    )


def _snapshot_identity(source: TableSourceIR) -> str:
    return snapshot_identity(
        datasource_fingerprint="sha256:datasource",
        source=source,
        scope=md.unpruned(max_rows=100, timeout_seconds=30),
        columns=("event_time", "score"),
        schema_fingerprint="sha256:schema",
        persist_values=False,
    )


def test_unprojected_table_keeps_existing_value_and_dictionary_shape() -> None:
    source = md.table("orders", database=("warehouse", "sales"))

    assert source.columns == ()
    assert source.to_dict() == {
        "kind": "table",
        "table": "orders",
        "database": ["warehouse", "sales"],
    }
    assert source_to_dict(source) == source.to_dict()
    assert source_from_dict(source.to_dict()) == source


def test_unprojected_table_keeps_existing_semantic_definition_identity() -> None:
    source = md.table("events", database="warehouse")

    assert _semantic_definition_fingerprint(source) == (
        "sha256:bdc8690f60d432375c111f5c0755ebc55be77ce415462f8fd8c5939993ac6a4e"
    )


def test_source_column_is_frozen_and_canonicalizes_ibis_type() -> None:
    binding = md.source_column("event.timestamp", data_type="varchar")

    assert isinstance(binding, TableColumnBindingIR)
    assert binding.source == "event.timestamp"
    assert binding.data_type == "string"
    assert binding.to_dict() == {
        "source": "event.timestamp",
        "data_type": "string",
    }
    with pytest.raises(FrozenInstanceError):
        binding.data_type = "float64"  # type: ignore[misc]


def test_projected_table_is_canonical_and_round_trips() -> None:
    source = _projected_source(reversed_order=True)

    assert source.columns == (
        (
            "event_time",
            TableColumnBindingIR(source="event.timestamp", data_type="timestamp"),
        ),
        (
            "score",
            TableColumnBindingIR(source="_generated_score", data_type="float64"),
        ),
    )
    assert source.to_dict() == {
        "kind": "table",
        "table": "events",
        "database": "warehouse",
        "columns": {
            "event_time": {
                "source": "event.timestamp",
                "data_type": "timestamp",
            },
            "score": {
                "source": "_generated_score",
                "data_type": "float64",
            },
        },
    }
    assert source_to_dict(source) == source.to_dict()
    assert source_from_dict(source.to_dict()) == source


def test_direct_ir_construction_applies_builder_validation_and_ordering() -> None:
    direct = TableSourceIR(
        table="events",
        columns=(
            ("score", TableColumnBindingIR("_generated_score", "double")),
            ("event_time", TableColumnBindingIR("event.timestamp", "timestamp")),
        ),
    )
    built = md.table(
        "events",
        columns={
            "event_time": md.source_column("event.timestamp", data_type="timestamp"),
            "score": md.source_column("_generated_score", data_type="float64"),
        },
    )

    assert direct == built


@pytest.mark.parametrize("source", [42, "", "event\x00time"])
def test_source_column_rejects_invalid_physical_identifier(source: object) -> None:
    with pytest.raises((TypeError, ValueError), match=r"TableColumnBindingIR\.source"):
        md.source_column(source, data_type="string")  # type: ignore[arg-type]


@pytest.mark.parametrize("data_type", [42, "", "not_a_type", "string\x00"])
def test_source_column_rejects_invalid_data_type(data_type: object) -> None:
    with pytest.raises((TypeError, ValueError), match=r"TableColumnBindingIR\.data_type"):
        md.source_column("event_time", data_type=data_type)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "columns",
    [
        [],
        {"event_time": "timestamp"},
        {"event_time": ("event.timestamp", "timestamp")},
        {"": TableColumnBindingIR("event_time", "timestamp")},
        {"event\x00time": TableColumnBindingIR("event_time", "timestamp")},
    ],
)
def test_table_builder_rejects_non_closed_binding_shapes(columns: object) -> None:
    with pytest.raises((TypeError, ValueError), match=r"columns"):
        md.table("events", columns=columns)  # type: ignore[arg-type]


def test_table_builder_rejects_empty_mapping_and_duplicate_physical_source() -> None:
    with pytest.raises(ValueError, match="at least one binding"):
        md.table("events", columns={})

    with pytest.raises(ValueError, match="duplicate physical source"):
        md.table(
            "events",
            columns={
                "event_time": md.source_column("event.timestamp", data_type="timestamp"),
                "copied_time": md.source_column("event.timestamp", data_type="timestamp"),
            },
        )


@pytest.mark.parametrize(
    "columns",
    [
        [],
        (("event_time",),),
        (("event_time", "timestamp"),),
        (
            ("event_time", TableColumnBindingIR("event.timestamp", "timestamp")),
            ("event_time", TableColumnBindingIR("event.timestamp.copy", "timestamp")),
        ),
    ],
)
def test_direct_table_ir_rejects_invalid_binding_tuples(columns: object) -> None:
    with pytest.raises((TypeError, ValueError), match=r"TableSourceIR\.columns"):
        TableSourceIR(table="events", columns=columns)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "columns",
    [
        None,
        {},
        {"event_time": "timestamp"},
        {"event_time": {"source": "event.timestamp"}},
        {
            "event_time": {
                "source": "event.timestamp",
                "data_type": "timestamp",
                "expression": "now()",
            }
        },
        {"event_time": {"source": 1, "data_type": "timestamp"}},
        {1: {"source": "event.timestamp", "data_type": "timestamp"}},
    ],
)
def test_source_from_dict_rejects_malformed_projected_bindings(columns: object) -> None:
    with pytest.raises((TypeError, ValueError), match=r"TableSourceIR\.columns"):
        source_from_dict(
            {
                "kind": "table",
                "table": "events",
                "database": None,
                "columns": columns,
            }
        )


def test_binding_order_and_equivalent_types_preserve_all_slice_one_identities() -> None:
    first = _projected_source()
    reordered = _projected_source(reversed_order=True)

    assert first == reordered
    assert first.to_dict() == reordered.to_dict()
    assert source_subject_ref(first) == source_subject_ref(reordered)
    assert _snapshot_identity(first) == _snapshot_identity(reordered)
    assert _semantic_definition_fingerprint(first) == _semantic_definition_fingerprint(reordered)


@pytest.mark.parametrize(
    "changed",
    [
        md.table(
            "events",
            database="warehouse",
            columns={
                "occurred_at": md.source_column("event.timestamp", data_type="timestamp"),
                "score": md.source_column("_generated_score", data_type="float64"),
            },
        ),
        md.table(
            "events",
            database="warehouse",
            columns={
                "event_time": md.source_column("event.occurred_at", data_type="timestamp"),
                "score": md.source_column("_generated_score", data_type="float64"),
            },
        ),
        md.table(
            "events",
            database="warehouse",
            columns={
                "event_time": md.source_column("event.timestamp", data_type="timestamp(3)"),
                "score": md.source_column("_generated_score", data_type="float64"),
            },
        ),
        md.table(
            "events",
            database="warehouse",
            columns={
                "event_time": md.source_column("event.timestamp", data_type="timestamp"),
            },
        ),
    ],
)
def test_binding_changes_invalidate_all_slice_one_identities(changed: TableSourceIR) -> None:
    original = _projected_source()

    assert original != changed
    assert source_subject_ref(original) != source_subject_ref(changed)
    assert _snapshot_identity(original) != _snapshot_identity(changed)
    assert _semantic_definition_fingerprint(original) != _semantic_definition_fingerprint(changed)
