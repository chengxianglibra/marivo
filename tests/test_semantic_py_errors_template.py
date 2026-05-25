"""Structured exception template applied across SemanticError subclasses."""

from __future__ import annotations

from marivo.semantic_py.errors import (
    SemanticAssemblyError,
    SemanticDecoratorError,
    SemanticError,
    SourceLocation,
)


def test_base_template_renders_all_sections() -> None:
    err = SemanticAssemblyError(
        phase="assembly",
        kind="DatasetDatasourceMissing",
        location=SourceLocation(file="my_model.py", line=42),
        function="orders",
        message="Dataset 'orders' references missing datasource 'tiny_orders'.",
        hint="Register the datasource before declaring the dataset.",
        refs=["dataset:orders", "datasource:tiny_orders"],
    )
    rendered = str(err)
    assert rendered.startswith(
        "SemanticAssemblyError: Dataset 'orders' references missing datasource 'tiny_orders'."
    )
    assert "发生位置: my_model.py:42 (in orders)" in rendered
    assert "原因: assembly:DatasetDatasourceMissing" in rendered
    assert "建议: Register the datasource before declaring the dataset." in rendered
    assert "相关文档:" in rendered
    assert "dataset:orders" in rendered


def test_base_template_omits_missing_sections() -> None:
    err = SemanticError(
        phase="runtime",
        kind="x",
        location=None,
        function=None,
        message="bare error",
    )
    rendered = str(err)
    assert rendered.startswith("SemanticError: bare error")
    assert "发生位置:" not in rendered
    assert "建议:" not in rendered
    assert "正确写法:" not in rendered
    assert "相关文档:" not in rendered


def test_subclass_template_fields_hook_drives_fix_snippet() -> None:
    class HookedError(SemanticAssemblyError):
        def _template_fields(self) -> dict[str, str]:
            return {
                "fix_snippet": "fix_line_one\nfix_line_two",
                "doc": "marivo-skill/marivo-py-semantic/references/pitfalls.md",
            }

    err = HookedError(phase="assembly", kind="K", location=None, function=None, message="m")
    rendered = str(err)
    assert "正确写法:" in rendered
    assert "  fix_line_one" in rendered
    assert "  fix_line_two" in rendered
    assert "相关文档: marivo-skill/marivo-py-semantic/references/pitfalls.md" in rendered


def test_subclass_template_fields_override_location_and_cause() -> None:
    class HookedError(SemanticAssemblyError):
        def _template_fields(self) -> dict[str, str]:
            return {
                "location": "orders.py:7 (in declared_orders)",
                "cause": "assembly:CustomCause",
            }

    err = HookedError(
        phase="assembly",
        kind="OriginalKind",
        location=SourceLocation(file="fallback.py", line=99),
        function="fallback_function",
        message="m",
    )
    rendered = str(err)
    assert "发生位置: orders.py:7 (in declared_orders)" in rendered
    assert "发生位置: fallback.py:99 (in fallback_function)" not in rendered
    assert "原因: assembly:CustomCause" in rendered
    assert "原因: assembly:OriginalKind" not in rendered


def test_empty_template_fields_are_unresolved_and_fallbacks_render() -> None:
    class HookedError(SemanticAssemblyError):
        def _template_fields(self) -> dict[str, str]:
            return {
                "location": "",
                "cause": "",
                "doc": "",
            }

    err = HookedError(
        phase="assembly",
        kind="FallbackKind",
        location=SourceLocation(file="fallback.py", line=8),
        function="orders",
        message="m",
        refs=["dataset:orders", "datasource:orders"],
    )
    rendered = str(err)
    assert "发生位置: fallback.py:8 (in orders)" in rendered
    assert "原因: assembly:FallbackKind" in rendered
    assert "相关文档: dataset:orders, datasource:orders" in rendered


def test_function_only_location_renders_when_location_is_missing() -> None:
    err = SemanticAssemblyError(
        phase="assembly",
        kind="K",
        location=None,
        function="orders",
        message="m",
    )
    assert "发生位置: (in orders)" in str(err)


def test_existing_short_form_still_present_for_compat() -> None:
    err = SemanticError(
        phase="assembly",
        kind="X",
        location=SourceLocation(file="f.py", line=1),
        function=None,
        message="msg",
    )
    assert err.short_form() == "assembly:X at f.py:1: msg"


def test_decorator_error_inherits_template() -> None:
    err = SemanticDecoratorError(
        phase="decorator",
        kind="DuplicateDataset",
        location=None,
        function="orders",
        message="Dataset 'orders' is already registered.",
    )
    rendered = str(err)
    assert rendered.startswith("SemanticDecoratorError: Dataset 'orders' is already registered.")
    assert "原因: decorator:DuplicateDataset" in rendered


def test_datasource_not_registered_default_template_fields() -> None:
    from marivo.semantic_py.errors import DatasourceNotRegisteredError

    err = DatasourceNotRegisteredError(
        phase="assembly",
        kind="DatasetDatasourceMissing",
        location=None,
        function="orders",
        message="Dataset 'orders' references missing datasource 'tiny_orders'.",
        refs=["dataset:orders", "datasource:tiny_orders"],
    )
    rendered = str(err)
    assert rendered.startswith(
        "DatasourceNotRegisteredError: Dataset 'orders' references missing datasource"
    )
    assert "正确写法:" in rendered
    assert "@ms.datasource" in rendered
    assert "@ms.dataset" in rendered
    assert "return ibis.duckdb.connect" in rendered
    assert "def tiny_orders() -> None" not in rendered


def test_datasource_not_registered_is_assembly_subclass() -> None:
    from marivo.semantic_py.errors import (
        DatasourceNotRegisteredError,
        SemanticAssemblyError,
    )

    err = DatasourceNotRegisteredError(
        phase="assembly",
        kind="DatasetDatasourceMissing",
        location=None,
        function=None,
        message="x",
    )
    assert isinstance(err, SemanticAssemblyError)


def test_ir_reload_required_default_template_fields() -> None:
    from marivo.semantic_py.errors import IRReloadRequiredError

    err = IRReloadRequiredError(
        phase="runtime",
        kind="StaleIR",
        location=None,
        function=None,
        message="Python source under .marivo/semantic/ changed since last load.",
    )
    rendered = str(err)
    assert rendered.startswith(
        "IRReloadRequiredError: Python source under .marivo/semantic/ changed"
    )
    assert "正确写法:" in rendered
    assert "ms.reload()" in rendered


def test_ir_reload_required_is_runtime_subclass() -> None:
    from marivo.semantic_py.errors import (
        IRReloadRequiredError,
        SemanticRuntimeError,
    )

    err = IRReloadRequiredError(
        phase="runtime",
        kind="StaleIR",
        location=None,
        function=None,
        message="x",
    )
    assert isinstance(err, SemanticRuntimeError)
