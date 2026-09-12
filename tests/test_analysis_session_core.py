"""Public Session ownership and external semantic layer wiring."""

import textwrap

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.semantic.catalog import SemanticCatalog


def test_session_public_fields_are_read_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("immutable")
    for name in ("id", "name", "created_at"):
        with pytest.raises(AttributeError):
            setattr(session, name, "other")


def test_session_exposes_catalog_property(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("catalog")
    assert isinstance(session.catalog, SemanticCatalog)
    assert session.catalog.workspace_dir == tmp_path


@pytest.mark.parametrize(
    "kwargs", [{"backends": {}}, {"use_datasources": False}, {"backend_factory": None}]
)
def test_session_constructor_rejects_old_runtime_keywords(tmp_path, monkeypatch, kwargs):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(TypeError):
        mv.Session(**kwargs)
    assert not (tmp_path / ".marivo").exists()


def test_session_catalog_loads_external_semantic_layer(tmp_path, monkeypatch):
    import textwrap

    import marivo.analysis as mv

    project_root = tmp_path / "project"
    external_models = tmp_path / "external" / "models"
    project_root.mkdir()
    (project_root / "marivo.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "demo"

            [semantic]
            layer_paths = ["../external/models"]
            """
        ),
        encoding="utf-8",
    )
    for models_root, datasource, domain, entity, metric in (
        (project_root / "models", "local_warehouse", "sales", "orders", "revenue"),
        (external_models, "external_warehouse", "finance", "refunds", "refunds_total"),
    ):
        datasource_dir = models_root / "datasources"
        semantic_dir = models_root / "semantic" / domain
        datasource_dir.mkdir(parents=True, exist_ok=True)
        semantic_dir.mkdir(parents=True, exist_ok=True)
        (datasource_dir / f"{datasource}.py").write_text(
            f"import marivo.datasource as md\nmd.duckdb(name={datasource!r}, path=':memory:')\n",
            encoding="utf-8",
        )
        (semantic_dir / "_domain.py").write_text(
            f"import marivo.semantic as ms\nms.domain(name={domain!r}, owner='Mina Zhang')\n",
            encoding="utf-8",
        )
        (semantic_dir / "objects.py").write_text(
            textwrap.dedent(
                f"""
                import marivo.datasource as md
                import marivo.semantic as ms

                source = ms.ref.datasource("{datasource}")
                rows = ms.entity(name={entity!r}, datasource=source, source=md.table({entity!r}, columns={{"id": md.source_column("id", data_type="int64"), "amount": md.source_column("amount", data_type="float64")}}), primary_key=["id"])

                @ms.metric(entities=[rows], additivity="additive")
                def {metric}(table):
                    return table.amount.sum()
                """
            ),
            encoding="utf-8",
        )

    monkeypatch.chdir(project_root)

    session = mv.session.get_or_create(name="external_layer_session")

    assert (
        session.catalog.require(ms.ref.metric("finance.refunds_total")).ref.path
        == "finance.refunds_total"
    )


@pytest.mark.runtime
def test_session_observe_uses_external_layer_datasource(tmp_path, monkeypatch):
    import marivo.analysis as mv

    project_root = tmp_path / "project"
    external_models = tmp_path / "external" / "models"
    db_path = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE refunds (id BIGINT, amount DOUBLE)")
    con.execute("INSERT INTO refunds VALUES (1, 100.0), (2, 50.0)")
    con.close()
    project_root.mkdir()
    (project_root / "marivo.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "demo"

            [semantic]
            layer_paths = ["../external/models"]
            """
        ),
        encoding="utf-8",
    )
    datasource_dir = external_models / "datasources"
    semantic_dir = external_models / "semantic" / "finance"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    semantic_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(db_path)!r})\n",
        encoding="utf-8",
    )
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='finance', owner='Mina Zhang')\n",
        encoding="utf-8",
    )
    (semantic_dir / "objects.py").write_text(
        textwrap.dedent(
            """
            import marivo.datasource as md
            import marivo.semantic as ms

            source = ms.ref.datasource("warehouse")
            rows = ms.entity(name="refunds", datasource=source, source=md.table("refunds", columns={"id": md.source_column("id", data_type="int64"), "amount": md.source_column("amount", data_type="float64")}), primary_key=["id"])

            amount = ms.measure_column(name="amount", entity=rows, column="amount", additivity="additive")
            refunds_total = ms.aggregate(name="refunds_total", measure=amount, agg="sum")
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project_root)

    session = mv.session.get_or_create(name="external_layer_observe")
    metric = session.catalog.require(ms.ref.metric("finance.refunds_total")).ref
    frame = session.observe(metric).aggregate().execute()

    assert frame.to_pandas()["refunds_total"].tolist() == [150.0]
