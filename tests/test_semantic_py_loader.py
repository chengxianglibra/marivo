from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from marivo.semantic_py.errors import SemanticLoadError
from marivo.semantic_py.loader import _namespace, load_project
from marivo.semantic_py.registry import SemanticProject


def _write_sales_model(root: Path, metric_body: str = "return orders.amount.sum()") -> None:
    model_dir = root / "sales"
    model_dir.mkdir(parents=True)
    (model_dir / "__init__.py").write_text("", encoding="utf-8")
    (model_dir / "_model.py").write_text(
        "import marivo.semantic_py as ms\nms.model(name='sales')\n",
        encoding="utf-8",
    )
    (model_dir / "datasources.py").write_text(
        "import marivo.semantic_py as ms\n"
        "@ms.datasource(name='warehouse')\n"
        "def warehouse():\n"
        "    ...\n",
        encoding="utf-8",
    )
    (model_dir / "datasets.py").write_text(
        "import marivo.semantic_py as ms\n"
        "from .datasources import warehouse\n"
        "@ms.dataset(name='orders', datasource=warehouse)\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n",
        encoding="utf-8",
    )
    (model_dir / "metrics.py").write_text(
        "import marivo.semantic_py as ms\n"
        "@ms.metric(decomposition=ms.sum())\n"
        "def revenue(orders):\n"
        f"    {metric_body}\n",
        encoding="utf-8",
    )


def _write_simple_model(root: Path, name: str, table: str) -> None:
    model_dir = root / name
    model_dir.mkdir(parents=True)
    (model_dir / "__init__.py").write_text("", encoding="utf-8")
    (model_dir / "_model.py").write_text(
        f"import marivo.semantic_py as ms\nms.model(name='{name}')\n",
        encoding="utf-8",
    )
    (model_dir / "datasources.py").write_text(
        "import marivo.semantic_py as ms\n"
        "@ms.datasource(name='warehouse')\n"
        "def warehouse():\n"
        "    ...\n",
        encoding="utf-8",
    )
    (model_dir / "datasets.py").write_text(
        "import marivo.semantic_py as ms\n"
        "from .datasources import warehouse\n"
        f"@ms.dataset(name='{table}', datasource=warehouse)\n"
        "def dataset(backend):\n"
        f"    return backend.table('{table}')\n",
        encoding="utf-8",
    )


def _write_inline_model(root: Path, name: str, table: str) -> None:
    model_dir = root / name
    model_dir.mkdir(parents=True)
    (model_dir / "__init__.py").write_text("", encoding="utf-8")
    (model_dir / "_model.py").write_text(
        "import marivo.semantic_py as ms\n"
        f"ms.model(name='{name}')\n"
        "@ms.datasource(name='warehouse')\n"
        "def warehouse():\n"
        "    ...\n"
        f"@ms.dataset(name='{table}', datasource=warehouse)\n"
        "def dataset(backend):\n"
        f"    return backend.table('{table}')\n",
        encoding="utf-8",
    )


def _write_hyphen_named_inline_model(
    root: Path, directory: str, model_name: str, table: str
) -> None:
    model_dir = root / directory
    model_dir.mkdir(parents=True)
    (model_dir / "__init__.py").write_text("", encoding="utf-8")
    (model_dir / "_model.py").write_text(
        "import marivo.semantic_py as ms\n"
        f"ms.model(name='{model_name}')\n"
        "@ms.datasource(name='warehouse')\n"
        "def warehouse():\n"
        "    ...\n"
        f"@ms.dataset(name='{table}', datasource=warehouse)\n"
        "def dataset(backend):\n"
        f"    return backend.table('{table}')\n",
        encoding="utf-8",
    )


def _write_unregistered_inline_model(root: Path, name: str, table: str) -> None:
    model_dir = root / name
    model_dir.mkdir(parents=True)
    (model_dir / "__init__.py").write_text("", encoding="utf-8")
    (model_dir / "_model.py").write_text(
        "import marivo.semantic_py as ms\n"
        "@ms.datasource(name='warehouse')\n"
        "def warehouse():\n"
        "    ...\n"
        f"@ms.dataset(name='{table}', datasource=warehouse)\n"
        "def dataset(backend):\n"
        f"    return backend.table('{table}')\n",
        encoding="utf-8",
    )


def _write_extra_model_registration(root: Path, name: str) -> None:
    model_dir = root / name
    model_dir.mkdir(parents=True)
    (model_dir / "__init__.py").write_text("", encoding="utf-8")
    (model_dir / "_model.py").write_text(
        f"import marivo.semantic_py as ms\nms.model(name='{name}')\nms.model(name='shadow')\n",
        encoding="utf-8",
    )


def _write_duplicate_metric_model(root: Path) -> None:
    model_dir = root / "sales"
    model_dir.mkdir(parents=True)
    (model_dir / "__init__.py").write_text("", encoding="utf-8")
    (model_dir / "_model.py").write_text(
        "import marivo.semantic_py as ms\n"
        "ms.model(name='sales')\n"
        "@ms.metric(decomposition=ms.sum())\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
        "@ms.metric(decomposition=ms.sum())\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n",
        encoding="utf-8",
    )


def _write_invalid_ref_model(root: Path) -> None:
    model_dir = root / "sales"
    model_dir.mkdir(parents=True)
    (model_dir / "__init__.py").write_text("", encoding="utf-8")
    (model_dir / "_model.py").write_text(
        "import marivo.semantic_py as ms\n"
        "ms.model(name='sales')\n"
        "@ms.metric(decomposition=ms.ratio(numerator=ms.ref('metrics.revenue'), denominator=ms.ref('metric.orders')))\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n",
        encoding="utf-8",
    )


def _project_namespace(root: Path) -> str:
    return _namespace(root.resolve() if root.exists() else root)


def _project_modules(root: Path) -> list[str]:
    namespace = _project_namespace(root)
    return [name for name in sys.modules if name == namespace or name.startswith(f"{namespace}.")]


def test_load_project_imports_semantic_directory(tmp_path: Path) -> None:
    _write_sales_model(tmp_path)
    project = SemanticProject(root=str(tmp_path))

    load_project(project)

    assert project.registry.state == "ready"
    assert sorted(project.registry.models) == ["sales"]
    assert sorted(project.registry.models["sales"].metrics) == ["revenue"]


def test_load_project_imports_multiple_model_directories(tmp_path: Path) -> None:
    _write_simple_model(tmp_path, "marketing", "campaigns")
    _write_simple_model(tmp_path, "sales", "orders")
    project = SemanticProject(root=str(tmp_path))

    load_project(project)

    assert project.registry.state == "ready"
    assert sorted(project.registry.models) == ["marketing", "sales"]
    assert sorted(project.registry.models["marketing"].datasets) == ["campaigns"]
    assert sorted(project.registry.models["sales"].datasets) == ["orders"]


def test_load_project_imports_multiple_inline_model_directories(tmp_path: Path) -> None:
    _write_inline_model(tmp_path, "marketing", "campaigns")
    _write_inline_model(tmp_path, "sales", "orders")
    project = SemanticProject(root=str(tmp_path))

    load_project(project)

    assert project.registry.state == "ready"
    assert sorted(project.registry.models) == ["marketing", "sales"]
    assert sorted(project.registry.models["marketing"].datasets) == ["campaigns"]
    assert sorted(project.registry.models["sales"].datasets) == ["orders"]


def test_load_project_supports_hyphen_model_name_for_inline_model_file(tmp_path: Path) -> None:
    _write_hyphen_named_inline_model(tmp_path, "sales_model", "sales-model", "orders")
    project = SemanticProject(root=str(tmp_path))

    load_project(project)

    assert project.registry.state == "ready"
    assert sorted(project.registry.models) == ["sales-model"]
    assert sorted(project.registry.models["sales-model"].datasets) == ["orders"]


def test_load_project_supports_underscore_model_name_for_inline_model_file(
    tmp_path: Path,
) -> None:
    _write_hyphen_named_inline_model(tmp_path, "sales_model", "sales_model", "orders")
    project = SemanticProject(root=str(tmp_path))

    load_project(project)

    assert project.registry.state == "ready"
    assert sorted(project.registry.models) == ["sales_model"]
    assert sorted(project.registry.models["sales_model"].datasets) == ["orders"]


def test_model_directory_must_register_matching_model(tmp_path: Path) -> None:
    _write_inline_model(tmp_path, "sales", "orders")
    _write_unregistered_inline_model(tmp_path, "z_marketing", "campaigns")
    project = SemanticProject(root=str(tmp_path))

    with pytest.raises(SemanticLoadError) as exc_info:
        load_project(project)

    assert [error.kind for error in exc_info.value.errors] == ["ModelRegistrationMissing"]
    assert project.registry.state == "errored"


def test_model_directory_must_not_register_extra_models(tmp_path: Path) -> None:
    _write_extra_model_registration(tmp_path, "sales")
    project = SemanticProject(root=str(tmp_path))

    with pytest.raises(SemanticLoadError) as exc_info:
        load_project(project)

    assert [error.kind for error in exc_info.value.errors] == ["UnexpectedModelRegistration"]
    assert project.registry.state == "errored"
    assert project.registry.models == {}


def test_reload_reexecutes_changed_modules(tmp_path: Path) -> None:
    _write_sales_model(tmp_path)
    project = SemanticProject(root=str(tmp_path))

    load_project(project)
    first_line = project.registry.models["sales"].metrics["revenue"].source_location.line

    metrics = tmp_path / "sales" / "metrics.py"
    metrics.write_text(
        "import marivo.semantic_py as ms\n\n\n"
        "@ms.metric(decomposition=ms.sum())\n"
        "def revenue(orders):\n"
        "    return orders.net_amount.sum()\n",
        encoding="utf-8",
    )

    load_project(project, reload=True)
    second_line = project.registry.models["sales"].metrics["revenue"].source_location.line

    assert second_line != first_line


def test_missing_model_file_raises_structured_load_error(tmp_path: Path) -> None:
    model_dir = tmp_path / "sales"
    model_dir.mkdir()

    project = SemanticProject(root=str(tmp_path))

    with pytest.raises(SemanticLoadError) as exc_info:
        load_project(project)

    assert [error.kind for error in exc_info.value.errors] == ["ModelFileMissing"]
    assert project.registry.state == "errored"
    assert [error.kind for error in project.registry.load_errors] == ["ModelFileMissing"]


def test_existing_file_root_raises_structured_load_error(tmp_path: Path) -> None:
    root_file = tmp_path / "semantic.py"
    root_file.write_text("not a semantic directory\n", encoding="utf-8")
    project = SemanticProject(root=str(root_file))

    with pytest.raises(SemanticLoadError) as exc_info:
        load_project(project)

    assert [error.kind for error in exc_info.value.errors] == ["ProjectRootInvalid"]
    assert project.registry.state == "errored"
    assert [error.kind for error in project.registry.load_errors] == ["ProjectRootInvalid"]
    assert project.registry.models == {}
    assert _project_modules(root_file) == []


def test_raw_import_exception_is_wrapped_and_cleans_partial_modules(tmp_path: Path) -> None:
    _write_sales_model(tmp_path)
    (tmp_path / "sales" / "metrics.py").write_text(
        "import sys\n"
        "import marivo.semantic_py as ms\n"
        "sys.path.append('/tmp/marivo-loader-leak')\n"
        "raise RuntimeError('boom')\n",
        encoding="utf-8",
    )
    original_path = list(sys.path)
    project = SemanticProject(root=str(tmp_path))

    with pytest.raises(SemanticLoadError) as exc_info:
        load_project(project)

    assert [error.kind for error in exc_info.value.errors] == ["ModuleLoadFailed"]
    assert project.registry.state == "errored"
    assert [error.kind for error in project.registry.load_errors] == ["ModuleLoadFailed"]
    assert project.registry.models == {}
    assert _project_modules(tmp_path) == []
    assert sys.path == original_path


def test_semantic_import_error_preserves_original_kind(tmp_path: Path) -> None:
    _write_sales_model(
        tmp_path,
        metric_body="total = orders.amount.sum()\n    return total",
    )
    project = SemanticProject(root=str(tmp_path))

    with pytest.raises(SemanticLoadError) as exc_info:
        load_project(project)

    assert [error.kind for error in exc_info.value.errors] == ["AstNodeForbidden"]
    assert project.registry.state == "errored"
    assert [error.kind for error in project.registry.load_errors] == ["AstNodeForbidden"]
    assert project.registry.models == {}
    assert _project_modules(tmp_path) == []


def test_semantic_decorator_error_preserves_duplicate_kind(tmp_path: Path) -> None:
    _write_duplicate_metric_model(tmp_path)
    project = SemanticProject(root=str(tmp_path))

    with pytest.raises(SemanticLoadError) as exc_info:
        load_project(project)

    assert [error.kind for error in exc_info.value.errors] == ["DuplicateMetric"]
    assert project.registry.state == "errored"
    assert [error.kind for error in project.registry.load_errors] == ["DuplicateMetric"]
    assert project.registry.models == {}
    assert _project_modules(tmp_path) == []


def test_semantic_ref_error_preserves_original_kind(tmp_path: Path) -> None:
    _write_invalid_ref_model(tmp_path)
    project = SemanticProject(root=str(tmp_path))

    with pytest.raises(SemanticLoadError) as exc_info:
        load_project(project)

    assert [error.kind for error in exc_info.value.errors] == ["ReferenceInvalid"]
    assert project.registry.state == "errored"
    assert [error.kind for error in project.registry.load_errors] == ["ReferenceInvalid"]
    assert project.registry.models == {}
    assert _project_modules(tmp_path) == []


def test_missing_root_clears_stale_project_modules_and_marks_ready(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing"
    namespace = _project_namespace(missing_root)
    sys.modules[namespace] = type(sys)(namespace)
    sys.modules[f"{namespace}.sales"] = type(sys)(f"{namespace}.sales")
    project = SemanticProject(root=str(missing_root))

    load_project(project)

    assert _project_modules(missing_root) == []
    assert project.registry.state == "ready"
    assert project.registry.load_errors == []


def test_deleted_relative_root_clears_stale_project_modules_and_marks_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    relative_root = Path("semantic_project")
    _write_sales_model(relative_root)
    project = SemanticProject(root=str(relative_root))

    load_project(project)
    resolved_root = relative_root.resolve()
    assert _project_modules(resolved_root) != []

    shutil.rmtree(relative_root)
    load_project(project)

    assert _project_modules(resolved_root) == []
    assert project.registry.state == "ready"
    assert project.registry.load_errors == []


def test_sys_path_is_restored_when_loaded_module_mutates_it(tmp_path: Path) -> None:
    _write_sales_model(tmp_path)
    (tmp_path / "sales" / "metrics.py").write_text(
        "import sys\n"
        "import marivo.semantic_py as ms\n"
        "sys.path.append('/tmp/marivo-loader-success-leak')\n"
        "@ms.metric(decomposition=ms.sum())\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n",
        encoding="utf-8",
    )
    original_path = list(sys.path)
    project = SemanticProject(root=str(tmp_path))

    load_project(project)

    assert project.registry.state == "ready"
    assert sys.path == original_path
