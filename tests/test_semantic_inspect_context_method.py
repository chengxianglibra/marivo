from __future__ import annotations

import ibis

from marivo.analysis.datasources.metadata import ColumnMetadata, TableMetadata
from marivo.semantic.dtos import (
    BoundedProfilePolicy,
    MetadataOnlyPolicy,
    SelectedColumnsPolicy,
    TableSource,
)
from marivo.semantic.reader import SemanticProject


def _fake_inspect_source(datasource, *, source, include_partitions=True):
    return TableMetadata(
        datasource=datasource,
        table=source.table,
        database=source.database,
        backend_type="duckdb",
        comment="orders fact",
        columns=(
            ColumnMetadata("order_id", "INTEGER", False, "Primary id", 1),
            ColumnMetadata("amount", "DOUBLE", True, "Gross amount", 2),
        ),
        partitions=(),
        warnings=(),
    )


def _fake_inspect_source_compound(datasource, *, source, include_partitions=True):
    return TableMetadata(
        datasource=datasource,
        table=source.table,
        database=source.database,
        backend_type="duckdb",
        comment="order lines fact",
        columns=(
            ColumnMetadata("order_id", "INTEGER", False, "Order id", 1),
            ColumnMetadata("line_num", "INTEGER", False, "Line number", 2),
            ColumnMetadata("amount", "DOUBLE", True, "Line amount", 3),
        ),
        partitions=(),
        warnings=(),
    )


def _backend_factory(_name):
    con = ibis.duckdb.connect(":memory:")
    con.con.execute("CREATE TABLE orders (order_id INT, amount DOUBLE)")
    con.con.execute("INSERT INTO orders VALUES (1, 10.0), (2, 20.0)")
    return con


def _backend_factory_compound(_name):
    con = ibis.duckdb.connect(":memory:")
    con.con.execute("CREATE TABLE order_lines (order_id INT, line_num INT, amount DOUBLE)")
    con.con.execute("INSERT INTO order_lines VALUES (1, 1, 10.0), (1, 2, 20.0), (2, 1, 30.0)")
    return con


_ORDERS_DOMAIN_PY = """\
import marivo.semantic as ms
ms.domain(name="sales", default=True)

orders = ms.entity(
    name="orders",
    datasource="warehouse",
    source=ms.table("orders"),
    primary_key=["order_id"],
)

@ms.dimension(entity=orders)
def amount(table):
    return table.amount
"""

_ORDER_LINES_DOMAIN_PY = """\
import marivo.semantic as ms
ms.domain(name="sales", default=True)

lines = ms.entity(
    name="order_lines",
    datasource="warehouse",
    source=ms.table("order_lines"),
    primary_key=["order_id", "line_num"],
)

@ms.dimension(entity=lines)
def amount(table):
    return table.amount
"""


def _make_project(factory, model_py):
    return factory({"sales/_domain.py": model_py})


def test_inspect_source_context_returns_pack_and_persists(tmp_path):
    root = tmp_path / ".marivo" / "semantic"
    root.mkdir(parents=True)
    project = SemanticProject(workspace_dir=tmp_path)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=_backend_factory
    )
    pack = project.inspect_source_context(
        datasource="warehouse",
        source=TableSource(table="orders"),
        sample_policy=BoundedProfilePolicy(limit=50),
    )
    assert pack.datasource == "warehouse"
    assert {c.column for c in pack.column_profiles} == {"order_id", "amount"}


def test_inspect_source_context_records_raw_preview_for_readiness(tmp_path):
    root = tmp_path / ".marivo" / "semantic"
    root.mkdir(parents=True)
    project = SemanticProject(workspace_dir=tmp_path)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=_backend_factory
    )
    project.inspect_source_context(
        datasource="warehouse",
        source=TableSource(table="orders"),
        sample_policy=BoundedProfilePolicy(limit=50),
    )
    # the dataset-level raw preview ref is now visible to readiness plumbing
    assert any("orders" in ref for ref in project.raw_preview_evidence())


def test_metadata_only_does_not_record_raw_preview(tmp_path):
    root = tmp_path / ".marivo" / "semantic"
    root.mkdir(parents=True)
    project = SemanticProject(workspace_dir=tmp_path)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=_backend_factory
    )
    project.inspect_source_context(
        datasource="warehouse",
        source=TableSource(table="orders"),
        sample_policy=MetadataOnlyPolicy(),
    )
    assert project.raw_preview_evidence() == ()


# -- auto-bridge: inspect → record_primary_key_sample -------------------------


def test_inspect_source_context_auto_records_primary_key_sample(
    semantic_project_factory,
):
    project = _make_project(semantic_project_factory, _ORDERS_DOMAIN_PY)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=_backend_factory
    )
    project.collect_source_preview(
        datasource="warehouse", table="orders", backend_factory=_backend_factory
    )
    report = project.readiness()
    assert any(w.kind == "primary_key_unsampled" for w in report.warnings)

    project.inspect_source_context(
        datasource="warehouse",
        source=TableSource(table="orders"),
        sample_policy=BoundedProfilePolicy(limit=50),
    )
    report = project.readiness()
    assert not any(w.kind == "primary_key_unsampled" for w in report.warnings)


def test_inspect_source_context_metadata_only_skips_auto_record(
    semantic_project_factory,
):
    project = _make_project(semantic_project_factory, _ORDERS_DOMAIN_PY)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=_backend_factory
    )
    project.collect_source_preview(
        datasource="warehouse", table="orders", backend_factory=_backend_factory
    )
    project.inspect_source_context(
        datasource="warehouse",
        source=TableSource(table="orders"),
        sample_policy=MetadataOnlyPolicy(),
    )
    report = project.readiness()
    assert any(w.kind == "primary_key_unsampled" for w in report.warnings)


def test_inspect_column_context_covers_primary_key(
    semantic_project_factory,
):
    project = _make_project(semantic_project_factory, _ORDERS_DOMAIN_PY)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=_backend_factory
    )
    project.collect_source_preview(
        datasource="warehouse", table="orders", backend_factory=_backend_factory
    )
    report = project.readiness()
    assert any(w.kind == "primary_key_unsampled" for w in report.warnings)

    project.inspect_column_context(
        datasource="warehouse",
        source=TableSource(table="orders"),
        columns=("order_id", "amount"),
        sample_policy=SelectedColumnsPolicy(limit=100, columns=("order_id", "amount")),
    )
    report = project.readiness()
    assert not any(w.kind == "primary_key_unsampled" for w in report.warnings)


def test_inspect_column_context_does_not_cover_primary_key(
    semantic_project_factory,
):
    project = _make_project(semantic_project_factory, _ORDERS_DOMAIN_PY)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=_backend_factory
    )
    project.collect_source_preview(
        datasource="warehouse", table="orders", backend_factory=_backend_factory
    )
    project.inspect_column_context(
        datasource="warehouse",
        source=TableSource(table="orders"),
        columns=("amount",),
        sample_policy=SelectedColumnsPolicy(limit=100, columns=("amount",)),
    )
    report = project.readiness()
    assert any(w.kind == "primary_key_unsampled" for w in report.warnings)


def test_inspect_column_context_compound_key_partial(
    semantic_project_factory,
):
    project = _make_project(semantic_project_factory, _ORDER_LINES_DOMAIN_PY)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source_compound,
        backend_factory=_backend_factory_compound,
    )
    project.collect_source_preview(
        datasource="warehouse",
        table="order_lines",
        backend_factory=_backend_factory_compound,
    )
    project.inspect_column_context(
        datasource="warehouse",
        source=TableSource(table="order_lines"),
        columns=("order_id",),
        sample_policy=SelectedColumnsPolicy(limit=100, columns=("order_id",)),
    )
    report = project.readiness()
    assert any(w.kind == "primary_key_unsampled" for w in report.warnings)


def test_inspect_column_context_compound_key_full(
    semantic_project_factory,
):
    project = _make_project(semantic_project_factory, _ORDER_LINES_DOMAIN_PY)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source_compound,
        backend_factory=_backend_factory_compound,
    )
    project.collect_source_preview(
        datasource="warehouse",
        table="order_lines",
        backend_factory=_backend_factory_compound,
    )
    report = project.readiness()
    assert any(w.kind == "primary_key_unsampled" for w in report.warnings)

    project.inspect_column_context(
        datasource="warehouse",
        source=TableSource(table="order_lines"),
        columns=("order_id", "line_num"),
        sample_policy=SelectedColumnsPolicy(limit=100, columns=("order_id", "line_num")),
    )
    report = project.readiness()
    assert not any(w.kind == "primary_key_unsampled" for w in report.warnings)
