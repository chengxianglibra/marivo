"""Tests for configured semantic layer model roots."""

from __future__ import annotations

import textwrap
from pathlib import Path

import duckdb
import pytest

import marivo.datasource as md
from marivo.config import load_semantic_layer_paths
from marivo.semantic.reader import SemanticProject


def _write_manifest(root: Path, body: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "marivo.toml").write_text(textwrap.dedent(body), encoding="utf-8")


def test_semantic_layer_paths_absent_returns_empty_tuple(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        """
        [project]
        name = "demo"
        """,
    )

    assert load_semantic_layer_paths(tmp_path) == ()


def test_semantic_layer_paths_missing_manifest_returns_empty_tuple(tmp_path: Path) -> None:
    assert load_semantic_layer_paths(tmp_path) == ()


def test_semantic_layer_paths_resolve_relative_and_absolute(tmp_path: Path) -> None:
    absolute = (tmp_path / "absolute" / "models").resolve()
    _write_manifest(
        tmp_path,
        f"""
        [project]
        name = "demo"

        [semantic]
        layer_paths = ["../shared/models", "{absolute}"]
        """,
    )

    assert load_semantic_layer_paths(tmp_path) == (
        (tmp_path / "../shared/models").resolve(),
        absolute,
    )


def test_semantic_layer_paths_reject_non_list(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        """
        [project]
        name = "demo"

        [semantic]
        layer_paths = "shared/models"
        """,
    )

    with pytest.raises(
        ValueError, match=r"marivo.toml \[semantic\]\.layer_paths must be a list of strings"
    ):
        load_semantic_layer_paths(tmp_path)


def test_semantic_layer_paths_reject_non_string_item(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        """
        [project]
        name = "demo"

        [semantic]
        layer_paths = ["shared/models", 42]
        """,
    )

    with pytest.raises(
        ValueError, match=r"marivo.toml \[semantic\]\.layer_paths\[1\] must be a string"
    ):
        load_semantic_layer_paths(tmp_path)


def _write_local_empty_project(root: Path, layer_paths: list[str]) -> None:
    quoted = ", ".join(repr(path) for path in layer_paths)
    _write_manifest(
        root,
        f"""
        [project]
        name = "demo"

        [semantic]
        layer_paths = [{quoted}]
        """,
    )
    (root / "models" / "datasources").mkdir(parents=True, exist_ok=True)
    (root / "models" / "semantic").mkdir(parents=True, exist_ok=True)


def _load_errors(project_root: Path) -> str:
    project = SemanticProject(workspace_dir=project_root)
    result = project.load()
    return "\n".join(error.message for error in result.errors)


def test_external_models_root_must_exist(tmp_path: Path) -> None:
    _write_local_empty_project(tmp_path, ["../missing/models"])

    message = _load_errors(tmp_path)

    assert "Configured semantic layer models root does not exist" in message
    assert str((tmp_path / "../missing/models").resolve()) in message


def test_external_models_root_must_be_directory(tmp_path: Path) -> None:
    bad_root = tmp_path / "bad-models"
    bad_root.write_text("not a directory", encoding="utf-8")
    _write_local_empty_project(tmp_path, [str(bad_root)])

    message = _load_errors(tmp_path)

    assert "Configured semantic layer models root is not a directory" in message
    assert str(bad_root) in message


def test_external_models_root_requires_datasources_dir(tmp_path: Path) -> None:
    external = tmp_path / "external" / "models"
    (external / "semantic").mkdir(parents=True)
    _write_local_empty_project(tmp_path, [str(external)])

    message = _load_errors(tmp_path)

    assert "Configured semantic layer models root is missing datasources/" in message
    assert str(external / "datasources") in message


def test_external_models_root_requires_semantic_dir(tmp_path: Path) -> None:
    external = tmp_path / "external" / "models"
    (external / "datasources").mkdir(parents=True)
    _write_local_empty_project(tmp_path, [str(external)])

    message = _load_errors(tmp_path)

    assert "Configured semantic layer models root is missing semantic/" in message
    assert str(external / "semantic") in message


def test_duplicate_configured_models_root_fails(tmp_path: Path) -> None:
    external = tmp_path / "external" / "models"
    (external / "datasources").mkdir(parents=True)
    (external / "semantic").mkdir(parents=True)
    _write_local_empty_project(tmp_path, [str(external), str(external)])

    message = _load_errors(tmp_path)

    assert "Configured semantic layer models root is listed more than once" in message
    assert str(external) in message


def test_local_models_root_repeated_in_layer_paths_fails(tmp_path: Path) -> None:
    _write_local_empty_project(tmp_path, ["models"])

    message = _load_errors(tmp_path)

    assert (
        "Configured semantic layer models root duplicates the local project models root" in message
    )
    assert str(tmp_path / "models") in message


def _write_models_root(
    models_root: Path,
    *,
    datasource_name: str,
    datasource_path: str,
    domain: str,
    entity: str,
    metric: str,
) -> None:
    datasource_dir = models_root / "datasources"
    semantic_dir = models_root / "semantic" / domain
    datasource_dir.mkdir(parents=True, exist_ok=True)
    semantic_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / f"{datasource_name}.py").write_text(
        textwrap.dedent(
            f"""
            import marivo.datasource as md

            md.duckdb(name={datasource_name!r}, path={datasource_path!r})
            """
        ),
        encoding="utf-8",
    )
    (semantic_dir / "_domain.py").write_text(
        textwrap.dedent(
            f"""
            import marivo.semantic as ms

            ms.domain(name={domain!r}, owner="Mina Zhang")
            """
        ),
        encoding="utf-8",
    )
    (semantic_dir / "objects.py").write_text(
        textwrap.dedent(
            f"""
            import marivo.datasource as md
            import marivo.semantic as ms

            source = md.ref("datasource.{datasource_name}")
            rows = ms.entity(name={entity!r}, datasource=source, source=md.table({entity!r}))

            @ms.metric(entities=[rows], additivity="additive")
            def {metric}(table):
                return table.amount.sum()
            """
        ),
        encoding="utf-8",
    )


def _write_project_with_external_layer(tmp_path: Path) -> tuple[Path, Path]:
    project_root = tmp_path / "project"
    external_models = tmp_path / "external" / "models"
    _write_manifest(
        project_root,
        """
        [project]
        name = "demo"

        [semantic]
        layer_paths = ["../external/models"]
        """,
    )
    _write_models_root(
        project_root / "models",
        datasource_name="local_warehouse",
        datasource_path=":memory:",
        domain="sales",
        entity="orders",
        metric="revenue",
    )
    _write_models_root(
        external_models,
        datasource_name="external_warehouse",
        datasource_path=":memory:",
        domain="finance",
        entity="refunds",
        metric="refunds_total",
    )
    return project_root, external_models


def test_ms_load_reads_local_and_external_models_roots(tmp_path: Path) -> None:
    import marivo.semantic as ms

    project_root, _ = _write_project_with_external_layer(tmp_path)

    catalog = ms.load(workspace_dir=project_root)
    top_level_refs = {obj.ref.id for obj in catalog.domains.items}
    top_level_refs |= {obj.ref.id for obj in catalog.datasources.items}

    assert {
        "sales",
        "finance",
        "datasource.local_warehouse",
        "datasource.external_warehouse",
    } <= top_level_refs
    assert catalog.get("metric.sales.revenue").ref.id == "sales.revenue"
    assert catalog.get("metric.finance.refunds_total").ref.id == "finance.refunds_total"


def test_domain_filter_applies_across_external_models_roots(tmp_path: Path) -> None:
    import marivo.semantic as ms

    project_root, _ = _write_project_with_external_layer(tmp_path)

    catalog = ms.load(workspace_dir=project_root, domains=["finance"])
    top_level_refs = {obj.ref.id for obj in catalog.domains.items}

    assert "finance" in top_level_refs
    assert "sales" not in top_level_refs
    assert catalog.get("metric.finance.refunds_total").ref.id == "finance.refunds_total"


def test_catalog_load_reloads_external_models_roots(tmp_path: Path) -> None:
    import marivo.semantic as ms
    from marivo.semantic.errors import SemanticRuntimeError

    project_root, external_models = _write_project_with_external_layer(tmp_path)
    catalog = ms.load(workspace_dir=project_root)

    with pytest.raises(SemanticRuntimeError):
        catalog.get("metric.finance.net_refunds")

    finance_objects = external_models / "semantic" / "finance" / "objects.py"
    finance_objects.write_text(
        finance_objects.read_text(encoding="utf-8")
        + textwrap.dedent(
            """

            @ms.metric(entities=[rows], additivity="additive")
            def net_refunds(table):
                return table.net_amount.sum()
            """
        ),
        encoding="utf-8",
    )

    catalog.load()

    assert catalog.get("metric.finance.net_refunds").ref.id == "finance.net_refunds"


def test_external_layer_datasource_supports_entity_verify_and_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marivo.semantic as ms

    project_root = tmp_path / "project"
    external_models = tmp_path / "external" / "models"
    db_path = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE refunds (amount DOUBLE)")
    con.execute("INSERT INTO refunds VALUES (100.0), (50.0)")
    con.close()
    _write_manifest(
        project_root,
        """
        [project]
        name = "demo"

        [semantic]
        layer_paths = ["../external/models"]
        """,
    )
    _write_models_root(
        external_models,
        datasource_name="warehouse",
        datasource_path=str(db_path),
        domain="finance",
        entity="refunds",
        metric="refunds_total",
    )

    catalog = ms.load(workspace_dir=project_root)
    entity_ref = catalog.get("entity.finance.refunds").ref
    metric_ref = catalog.get("metric.finance.refunds_total").ref
    monkeypatch.chdir(project_root)
    md.register(
        md.duckdb(name="warehouse", path=str(db_path)),
        project_root=project_root,
    )
    snapshot = md.inspect(md.ref("datasource.warehouse"), md.table("refunds")).sample(
        scope=md.unpruned(max_rows=2, timeout_seconds=30),
        columns=("amount",),
    )
    assert md.remove("warehouse") is True

    verify = catalog.verify_object(entity_ref)
    preview = catalog.preview(metric_ref, using=snapshot, limit=1)

    assert verify.status == "passed"
    assert preview.rows == ({"value": 150.0},)


def test_duplicate_datasource_across_roots_fails_with_paths(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    external_models = tmp_path / "external" / "models"
    _write_manifest(
        project_root,
        """
        [project]
        name = "demo"

        [semantic]
        layer_paths = ["../external/models"]
        """,
    )
    _write_models_root(
        project_root / "models",
        datasource_name="warehouse",
        datasource_path=":memory:",
        domain="sales",
        entity="orders",
        metric="revenue",
    )
    _write_models_root(
        external_models,
        datasource_name="warehouse",
        datasource_path="/tmp/other.duckdb",
        domain="finance",
        entity="refunds",
        metric="refunds_total",
    )

    message = _load_errors(project_root)

    assert "Duplicate datasource name: 'warehouse'" in message
    assert str(project_root / "models" / "datasources" / "warehouse.py") in message
    assert str(external_models / "datasources" / "warehouse.py") in message


def test_duplicate_domain_across_roots_fails_with_paths(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    external_models = tmp_path / "external" / "models"
    _write_manifest(
        project_root,
        """
        [project]
        name = "demo"

        [semantic]
        layer_paths = ["../external/models"]
        """,
    )
    _write_models_root(
        project_root / "models",
        datasource_name="local_warehouse",
        datasource_path=":memory:",
        domain="sales",
        entity="orders",
        metric="revenue",
    )
    _write_models_root(
        external_models,
        datasource_name="external_warehouse",
        datasource_path=":memory:",
        domain="sales",
        entity="refunds",
        metric="refunds_total",
    )

    message = _load_errors(project_root)

    assert "Duplicate domain name: 'sales'" in message
    assert str(project_root / "models" / "semantic" / "sales") in message
    assert str(external_models / "semantic" / "sales") in message


def test_duplicate_semantic_id_across_roots_fails_with_paths(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    external_models = tmp_path / "external" / "models"
    _write_manifest(
        project_root,
        """
        [project]
        name = "demo"

        [semantic]
        layer_paths = ["../external/models"]
        """,
    )
    _write_models_root(
        project_root / "models",
        datasource_name="local_warehouse",
        datasource_path=":memory:",
        domain="sales",
        entity="orders",
        metric="revenue",
    )
    _write_models_root(
        external_models,
        datasource_name="external_warehouse",
        datasource_path=":memory:",
        domain="finance",
        entity="orders",
        metric="orders",
    )
    external_objects = external_models / "semantic" / "finance" / "objects.py"
    external_objects.write_text(
        textwrap.dedent(
            """
            import marivo.datasource as md
            import marivo.semantic as ms

            source = md.ref("datasource.external_warehouse")
            sales_domain = ms.domain(name="sales", owner="External", default=False)
            rows = ms.entity(name="orders", datasource=source, source=md.table("orders"), domain=sales_domain)
            """
        ),
        encoding="utf-8",
    )

    message = _load_errors(project_root)

    assert "Duplicate semantic_id: 'sales.orders'" in message
    assert str(project_root / "models" / "semantic" / "sales" / "objects.py") in message
    assert str(external_objects) in message
