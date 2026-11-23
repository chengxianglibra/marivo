"""Public catalog loading, low-level pipeline loading and reader bridge contracts."""

from __future__ import annotations

import os
import py_compile
from pathlib import Path

import duckdb
import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo._kernel.refs import Ref, SemanticKindTag
from marivo.semantic import _catalog_definition
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.catalog_entries import EntryDefinition
from marivo.semantic.errors import ErrorKind, SemanticLoadFailed
from marivo.semantic.ir import SourceLocation
from marivo.semantic.loader import LoadResult, load_project
from marivo.semantic.reader import SemanticProject


def _write_minimal_project(tmp_path: Path) -> None:
    semantic = tmp_path / "models" / "semantic" / "sales"
    ds = tmp_path / "models" / "datasources"
    semantic.mkdir(parents=True)
    ds.mkdir(parents=True)
    (ds / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "ms.domain(name='sales', owner='Mina Zhang', default=True)\n"
    )
    (semantic / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "orders_ref = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
        "source=md.table('orders'))\n"
        "amount_ref = ms.measure_column(name='amount', entity=orders_ref, column='amount', "
        "additivity=ms.additive_all())\n"
        "\n"
        ""
        "amount = amount_ref\n"
        "\n"
        "@ms.metric()\n"
        "def revenue():\n"
        "    return amount.sum()\n"
    )


def test_ms_load_returns_semantic_catalog(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    assert isinstance(catalog, SemanticCatalog)


def test_exact_lookup_projects_only_requested_definition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_minimal_project(tmp_path)
    projected: list[str] = []
    definition_for = _catalog_definition.definition_for

    def record_projection(catalog: SemanticCatalog, ref: Ref[SemanticKindTag]) -> EntryDefinition:
        projected.append(ref.key)
        return definition_for(catalog, ref)

    monkeypatch.setattr(_catalog_definition, "definition_for", record_projection)
    catalog = ms.load(workspace_dir=tmp_path)
    entry = catalog.get("metric:sales.revenue")
    assert entry.kind == "metric"
    assert not projected
    assert entry.definition.formula == "amount.sum(time=None)"
    assert projected == ["metric:sales.revenue"]


def test_load_selects_function_at_its_declaration_and_refreshes_edited_source(
    tmp_path: Path,
) -> None:
    _write_minimal_project(tmp_path)
    model = tmp_path / "models/semantic/sales/datasets.py"
    source = "# coding: latin-1\n# Café model source\n" + model.read_text(encoding="utf-8")
    source = source.replace(
        "@ms.metric()\ndef revenue():",
        "def revenue():\n    return missing_helper()\n\nFACTOR = 2\n\n@ms.metric()\ndef revenue():",
    ).replace("return amount.sum()", "return amount.sum() * FACTOR * 3")
    model.write_text(source, encoding="latin-1")
    cache_path = py_compile.compile(str(model), doraise=True)
    assert cache_path is not None
    cached = Path(cache_path).read_bytes()
    stamp = model.stat()
    old = ms.load(workspace_dir=tmp_path)

    model.write_text(source.replace("FACTOR = 2", "FACTOR = 4").replace("* 3", "* 5"), "latin-1")
    os.utime(model, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert model.stat().st_size == stamp.st_size
    new = ms.load(workspace_dir=tmp_path)
    before = old.get("metric:sales.revenue")
    after = new.get("metric:sales.revenue")
    assert before.kind == "metric"
    assert after.kind == "metric"
    assert before.definition.formula == "((amount.sum(time=None) * 2) * 3)"
    assert after.definition.formula == "((amount.sum(time=None) * 4) * 5)"
    assert old.definition_fingerprint != new.definition_fingerprint
    assert Path(cache_path).read_bytes() == cached


def test_decorated_multiline_literal_preserves_dedented_value(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    database = tmp_path / "source.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE orders (note VARCHAR)")
        connection.executemany(
            "INSERT INTO orders VALUES (?)",
            [("alpha\n\nomega",), ("alpha\n   \nomega",), ("alpha\n   \nomega",)],
        )
    (tmp_path / "models/datasources/warehouse.py").write_text(
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database)!r})\n"
    )
    model = tmp_path / "models/semantic/sales/datasets.py"
    literal = '"""alpha\n   \nomega"""'
    source = (
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        f"assert {literal} == 'alpha\\n   \\nomega'\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
        "source=md.table('orders'))\n"
        "note = ms.dimension_column(name='note', entity=orders, column='note')\n"
        "@ms.metric()\ndef matching_orders():\n"
        f"    return orders.count().where(note == {literal})\n"
    )
    model.write_text(source)
    catalog = ms.load(workspace_dir=tmp_path)
    entry = catalog.get("metric:sales.matching_orders")
    assert entry.kind == "metric"
    result = catalog.preview(entry, scope=md.unpruned(max_rows=20, timeout_seconds=15))
    assert result.rows == ({"value": 1},)

    model.write_text(source.replace(f"note == {literal}", r"note == 'alpha\n\nomega'"))
    assert ms.load(workspace_dir=tmp_path).definition_fingerprint == catalog.definition_fingerprint


@pytest.mark.parametrize(
    ("constructor", "kind"),
    [("dimension_column", "dimension"), ("time_dimension_column", "time_dimension")],
)
def test_filtered_dimension_owner_is_a_load_error(
    tmp_path: Path, constructor: str, kind: str
) -> None:
    _write_minimal_project(tmp_path)
    ops = tmp_path / "models/semantic/ops"
    ops.mkdir()
    (ops / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='ops', owner='Tester')\n"
    )
    (ops / "orders.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
        "source=md.table('orders'))\n"
    )
    field = tmp_path / "models/semantic/sales/notes.py"
    field.write_text(
        "import marivo.semantic as ms\n"
        f"ms.{constructor}(name='note', entity=ms.ref.entity('ops.orders'), "
        "column='note', domain=ms.ref.domain('ops'))\n"
    )
    with pytest.raises(SemanticLoadFailed) as caught:
        ms.load(workspace_dir=tmp_path, domains=["sales"])
    (error,) = caught.value.errors
    assert error.kind == ErrorKind.MISSING_ENTITY_REF
    assert error.location == SourceLocation(str(field), 2)
    assert error.semantic_refs == ("ops.orders.note", "ops.orders")
    assert error.received == "ops.orders"
    assert error.repair is not None
    assert "Include 'ops' in domains" in error.repair.action

    complete = ms.load(workspace_dir=tmp_path, domains=["sales", "ops"])
    identity = f"{kind}:ops.orders.note"
    assert any(item.id == identity for item in complete.search(kind=kind).items)


def test_load_project_returns_load_result(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    semantic_root = tmp_path / "models" / "semantic"
    result = load_project(semantic_root)
    assert isinstance(result, LoadResult)
    assert result.status == "ready"
    assert result.compiled_state is not None
    assert result.registry is not None


def test_load_project_rejects_workspace_root_with_ms_load_hint(tmp_path: Path) -> None:
    """Passing a workspace root (not models/semantic/) errors with the correct
    usage in the hint, so the public entry is discoverable from the failure."""
    _write_minimal_project(tmp_path)
    result = load_project(tmp_path)
    assert result.status == "errored"
    assert result.errors
    hints = " ".join((getattr(e, "hint", "") or "") for e in result.errors)
    assert "ms.load(workspace_dir=" in hints


def test_project_catalog_returns_semantic_catalog(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)

    project = SemanticProject(workspace_dir=tmp_path)
    result = project.load()
    assert isinstance(result, LoadResult)
    catalog = project.catalog()
    assert isinstance(catalog, SemanticCatalog)


def test_ms_load_and_project_catalog_agree(tmp_path: Path) -> None:
    """Both the public entry and the reader bridge yield the same catalog."""
    _write_minimal_project(tmp_path)

    via_public = ms.load(workspace_dir=tmp_path)
    project = SemanticProject(workspace_dir=tmp_path)
    project.load()
    via_bridge = project.catalog()
    assert via_public.definition_fingerprint == via_bridge.definition_fingerprint
