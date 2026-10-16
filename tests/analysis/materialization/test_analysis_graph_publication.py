"""Low-level exchange, storage and old-generation rejection boundaries.

Current atomic publication and committed-output reading are covered by
`test_terminal_publication.py`; the old graph publisher rejects before admission.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pyarrow as pa
import pytest

from marivo.analysis.core.graph import Edge, SourceDefinition, SourceLeaf, method_node
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    DomainSignature,
    ObservedQuantity,
    Signature,
)
from marivo.analysis.core.rules import RowState
from marivo.analysis.materialization.errors import (
    IntegrityError,
    MaterializationError,
)
from marivo.analysis.materialization.graph_exchange import ExchangeContract, from_arrow
from marivo.analysis.materialization.graph_protocol import (
    SourceRunInput,
    decode,
    digest,
    encode,
)
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.methods.physical import NoTime, ScalarType, SourceShape
from marivo.analysis.methods.semantics import MethodKey
from marivo.refs import ref


def _root(session):
    binding = Binding(session, "inventory", "products", "all")
    entity = ref.entity("inventory.product")
    coordinate = Coordinate(entity, "id", "identity")
    domain = DomainSignature(binding, "entity", (coordinate,), (coordinate,), "products")
    quantity = ObservedQuantity(
        "stock",
        ref.metric("inventory.stock"),
        "stock-v1",
        "units",
        "all",
        "products",
        "strict",
        "sum@v1",
    )
    leaf = SourceLeaf(
        SourceDefinition(
            quantity.metric_ref,
            "stock-v1",
            ref.datasource("db"),
            SourceShape("duckdb", "table", "native", NoTime()),
        ),
        Signature(domain, quantity),
        ScalarType("int64"),
    )
    return leaf, _count(leaf)


def _count(leaf):
    domain = DomainSignature(leaf.signature.domain.binding, "singleton", (), (), "all-products")
    return method_node(
        (Edge("quantity", leaf),),
        RowState("count", domain, "current-count", "count_all"),
        value_type=ScalarType("int64"),
    )


def test_optional_singleton_exchange_rejects_multiple_rows() -> None:
    leaf, _ = _root("test-session")
    quantity = leaf.signature.quantity
    assert quantity is not None
    singleton = DomainSignature(leaf.signature.domain.binding, "singleton", (), (), "selected")
    table = pa.table(
        {
            "value": pa.array([1, 2], type=pa.int64()),
            "cell_tag": ["defined", "defined"],
            "cell_reason": pa.array([None, None], type=pa.string()),
        }
    )
    contract = ExchangeContract(
        Signature(singleton, quantity),
        MethodKey("parts_transport"),
        "exact-binding",
        table.schema,
        (),
        allow_empty_singleton=True,
    )
    with pytest.raises(MaterializationError, match="singleton result has an invalid row count"):
        from_arrow(table, contract)


@pytest.mark.parametrize("generation", (8,))
def test_existing_generation_rejection_preserves_project_bytes(
    tmp_path: Path, generation: int
) -> None:
    old_path = tmp_path / f".marivo/analysis/generations/v{generation}/store.sqlite3"
    old_path.parent.mkdir(parents=True)
    with sqlite3.connect(old_path) as connection:
        connection.execute(f"PRAGMA user_version={generation}")
    before = old_path.read_bytes()
    files = tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")))
    with pytest.raises(IntegrityError):
        SessionStore.open_existing(tmp_path)
    assert tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))) == files
    with pytest.raises(IntegrityError):
        SessionStore(tmp_path)
    assert old_path.read_bytes() == before


def test_run_input_canonical_golden_vector():
    from marivo.analysis.materialization.graph_protocol import RUN_INPUT

    value = SourceRunInput(
        "marivo.analysis.run_input/v1",
        "source",
        "a" * 64,
        "b" * 64,
        (("metric-v1", "semantic-v1", "binding-v1"),),
    )
    expected = (
        '{"definition_fingerprint":"'
        + "a" * 64
        + '","kind":"source","ordered_source_bindings":[["metric-v1","semantic-v1","binding-v1"]],"plan_digest":"'
        + "b" * 64
        + '","schema":"marivo.analysis.run_input/v1"}'
    )
    assert encode(value, RUN_INPUT) == expected
    assert digest(expected) == "d7825bef0f2575368195e757487ae31ee11e98c29fbef14ba85c690b4c57aa20"
    assert decode(expected, RUN_INPUT) == value


def test_storage_rejects_symlink_before_writing(tmp_path):
    from marivo.analysis.materialization.graph_storage import write_table

    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (tmp_path / "link").symlink_to(foreign, target_is_directory=True)
    with pytest.raises(IntegrityError, match="symlink"):
        write_table(
            tmp_path, tmp_path / "link" / "staging", tmp_path / "output", pa.table({"id": [1]})
        )
    assert not list(foreign.iterdir())


def test_storage_write_failure_removes_run_owned_staging(tmp_path, monkeypatch):
    import marivo.analysis.materialization.graph_storage as storage

    staging = tmp_path / "run" / "output" / "primary"

    def fail(*args, **kwargs):
        raise OSError("write denied")

    monkeypatch.setattr(storage.pq, "write_table", fail)
    with pytest.raises(MaterializationError, match="local Parquet write failed"):
        storage.write_table(
            tmp_path,
            staging,
            tmp_path / "artifact" / "primary",
            pa.table({"id": [1]}),
        )
    assert not staging.exists()
