"""Independent contracts for semantic guidance, failure facts, and declaration examples."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from ibis.common.deferred import Deferred

import marivo
import marivo.datasource as md
import marivo.semantic as ms
from marivo._authoring.model import AuthoringCapability, AuthoringRepair
from marivo._definition.deferred import root_declaration
from marivo._definition.expressions import DeclaredMetricRef
from marivo._help.render import help as show_help
from marivo._help.render import render_help_text
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic._capabilities.registry import REGISTRY
from marivo.semantic._capabilities.validation import _validate_minimal_example_signature
from marivo.semantic._deferred_semantics import consume_field, consume_metric, consume_row
from marivo.semantic.errors import (
    SemanticExpressionError,
    SemanticLoadError,
    SemanticRuntimeError,
)
from marivo.semantic.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from marivo.semantic.readiness import ReadinessInputSummary, ReadinessIssue, ReadinessReport
from marivo.semantic.source_health import SourceHealthCheckResult, SourceHealthReport


def _no_io(*args: object, **kwargs: object) -> object:
    raise AssertionError("Reading guidance must not load, connect, or run checks")


_REVENUE_MODEL = """\
import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'), primary_key=['order_id'])
paid_at = ms.time_dimension_column(name='paid_at', entity=orders, column='paid_at', parse=ms.timestamp(timezone='UTC'), is_default=True)
amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all(), unit='USD')
finalized = ms.finalize()
amount_value = finalized.resolve(amount)
paid_at_value = finalized.resolve(paid_at)

@ms.metric(ai_context=ms.ai_context(business_definition='Sum of accepted order amounts.'))
def revenue():
    return amount_value.sum(time=paid_at_value)
"""


def test_definition_read_does_not_prescribe_readiness(
    tmp_path: Path,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Analytics', default=True)\n"
            ),
            "sales/models.py": _REVENUE_MODEL,
        }
    )
    catalog = ms.load(workspace_dir=tmp_path)
    assert marivo.help is show_help
    revenue = catalog.metrics.get("sales.revenue")
    monkeypatch.setattr("marivo.semantic.catalog.load", _no_io)
    monkeypatch.setattr("marivo.semantic.catalog.SemanticCatalog.readiness", _no_io)
    monkeypatch.setattr("marivo.datasource.backends.build_backend", _no_io)

    contract = catalog.describe(ms.ref.metric("sales.revenue")).render()
    assert "metric:sales.revenue" in contract
    assert "readiness(" not in contract
    show_help(type(catalog.describe(ms.ref.metric("sales.revenue"))))
    briefing = capsys.readouterr().out
    assert ".show()" in briefing
    assert "readiness(" not in briefing
    show_help("semantic.authoring")
    authoring = capsys.readouterr().out
    assert "catalog = ms.load()" not in authoring
    for target in ("semantic.objects", "semantic.builders", "semantic.checks"):
        assert target in authoring
    show_help("semantic.metric.define")
    assert "readiness(" not in capsys.readouterr().out
    show_help("semantic.dimension.from_column")
    constructor = capsys.readouterr().out
    assert "Postcondition after saving" in constructor
    assert "catalog = ms.load()" in constructor
    assert "catalog.fields.get(" in constructor
    assert "readiness(" not in constructor


@pytest.mark.parametrize("with_repair", (False, True))
def test_error_instance_keeps_concrete_facts(
    with_repair: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    error = SemanticLoadError(
        kind="synthetic_failure",
        message="The requested domain is missing",
        expected="sales/_domain.py",
        received="sales/metrics.py only",
        refs=("sales.revenue",),
        location_label="semantic project",
        repair=(
            AuthoringRepair(
                kind="inspect",
                action="Inspect the physical source.",
                help_target=LiveHelpTarget(surface="datasource", canonical_id="inspect"),
            )
            if with_repair
            else None
        ),
    )
    monkeypatch.setattr("marivo.semantic.reader.SemanticProject.load", _no_io)
    monkeypatch.setattr("marivo.datasource.backends.build_backend", _no_io)
    # Concrete facts are owned by the exception itself; Help accepts its type.
    text = str(error)
    for fact in (error.kind, error.message, error.expected, error.received, "sales.revenue"):
        assert fact is not None and fact in text
    assert "semantic project" in text
    assert ("marivo.help('datasource.inspect')" in text) is with_repair
    show_help(type(error))
    assert "SemanticLoadError" in capsys.readouterr().out


def test_readiness_card_discloses_scope_and_repair_without_empty_fix() -> None:
    report = ReadinessReport(
        status="blocked",
        analysis_ready_inputs=(ms.ref.metric("sales.ready"),),
        blockers=(ReadinessIssue("unknown_ref", "blocker", ("sales.missing",), "Not found"),),
        warnings=(
            ReadinessIssue(
                "fragile_string_ref",
                "warning",
                ("sales.ready",),
                "Use the exact current identity",
                repair=AuthoringRepair(
                    kind="inspect",
                    action="Browse current metrics.",
                    help_target=LiveHelpTarget(surface="semantic", canonical_id="semantic_catalog"),
                ),
            ),
        ),
        input_summary=ReadinessInputSummary(("warehouse",), ("sales.ready",), ("orders",)),
        checked_at="2026-10-07T00:00:00Z",
    )
    text = report.render()
    assert "checked refs" in text and "warehouse" in text and "orders" in text
    assert "affected refs: sales.missing" in text
    assert "Not found -> fix:" not in text
    assert "analysis_ready: metric:sales.ready" in text
    assert "Browse current metrics." in text
    assert 'marivo.help("semantic.semantic_catalog")' in text
    large = replace(
        report,
        input_summary=ReadinessInputSummary(
            (), tuple(f"sales.metric_{i}" for i in range(2_000)), ()
        ),
    ).render()
    assert "affected refs: sales.missing" in large
    assert "omitted" in large.lower()


def test_source_health_cards_disclose_failed_facts_and_scope_without_queries(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    entity = ms.ref.entity("sales.orders")
    check = SourceHealthCheckResult(
        kind="not_null",
        status="failed",
        datasource=ms.ref.datasource("warehouse"),
        source=TableSourceIR(table="orders"),
        target_refs=(entity,),
        affected_refs=(ms.ref.metric("sales.revenue"),),
        checked_at="2026-10-07T00:00:00Z",
        observed_schema_fingerprint=None,
        observed_capability_fingerprint=None,
        observed={"null_count": 2, "row_count": 10},
        repair=AuthoringRepair(
            kind="reauthor",
            action="Repair the declared amount field.",
            help_target=LiveHelpTarget(surface="semantic", canonical_id="measure.from_column"),
        ),
        user_data_queried=True,
        scopes=((entity, md.unpruned(max_rows=10, timeout_seconds=5)),),
    )
    report = SourceHealthReport("failed", (check,), check.checked_at, "definition:fixture")
    monkeypatch.setattr("marivo.datasource.backends.build_backend", _no_io)
    report_text = report.render()
    assert "sales.revenue" in report_text
    assert "Repair the declared amount field." in report_text
    assert 'marivo.help("semantic.measure.from_column")' in report_text
    text = check.render()
    assert "user_data_queried=True" in text
    for value in ('"null_count": 2', '"max_rows": 10', '"timeout_seconds": 5'):
        assert value in text
    assert capsys.readouterr().out == ""
    check.show()
    assert capsys.readouterr().out == text + "\n"
    large = replace(
        report,
        checks=(*(replace(check, status="current", repair=None) for _ in range(1_000)), check),
    ).render()
    assert "Repair the declared amount field." in large
    assert "omitted" in large.lower()


@pytest.mark.parametrize(
    ("target", "path"),
    (
        ("dimension", "sales.orders.region"),
        ("time_dimension", "sales.orders.log_date"),
        ("measure", "sales.orders.order_amount"),
        ("metric", "sales.revenue"),
    ),
)
def test_decorator_help_example_loads_the_declared_object(
    target: str,
    path: str,
    tmp_path: Path,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.semantic._capabilities.naming import semantic_target

    descriptor = REGISTRY.by_canonical_id(semantic_target(target))
    assert isinstance(descriptor, AuthoringCapability)
    assert descriptor.minimal_example is not None
    monkeypatch.setattr("marivo.datasource.backends.build_backend", _no_io)
    local_assertions = ""
    if target in {"measure", "metric"}:
        symbol = path.rsplit(".", 1)[-1]
        local_assertions = (
            "from ibis.common.deferred import Deferred\n"
            "from marivo._definition.deferred import root_declaration, inspect_deferred\n"
            "from marivo.semantic._deferred_semantics import consume_metric, consume_row\n"
            "from marivo._definition.graph import computation_fingerprint\n"
            f"assert isinstance({symbol}, Deferred)\n"
            f"assert root_declaration({symbol}).name == {symbol!r}\n"
            f"assert consume_{'row' if target == 'measure' else 'metric'}({symbol}).unit == 'CNY'\n"
        )
        if target == "measure":
            local_assertions += (
                f"assert consume_row(order_amount).root.ref.path + '.' + root_declaration(order_amount).name == {path!r}\n"
                "assert consume_metric(order_amount.sum()).unit == 'CNY'\n"
            )
        else:
            local_assertions += "assert inspect_deferred(revenue).fingerprint == inspect_deferred(amount.sum()).fingerprint\n"
    semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Analytics', default=True)\n"
            ),
            "sales/model.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
                "source=md.table('orders'), primary_key=['event_id'])\n"
                "event_id = ms.dimension_column(name='event_id', entity=orders, column='event_id')\n"
                "event_time = ms.time_dimension_column(name='event_time', entity=orders, "
                "column='event_time', parse=ms.timestamp(timezone='UTC'))\n"
                + descriptor.minimal_example
                + "\n"
                + local_assertions
            ),
        }
    )
    catalog = ms.load(workspace_dir=tmp_path)
    collection = catalog.metrics if target == "metric" else catalog.fields
    value = collection.get(path)
    if target in {"measure", "metric"}:
        # R1.4 publishes module-level declarations under their function name.
        if target == "measure":
            assert isinstance(value, Deferred)
            declaration = root_declaration(value)
            assert declaration is not None and declaration.name == "order_amount"
            assert consume_row(value).unit == "CNY"
        else:
            assert isinstance(value, Deferred)
            metric = consume_metric(value)
            assert isinstance(metric.node, DeclaredMetricRef)
            assert metric.node.ref.ref == ms.ref.metric(path)
            assert metric.unit == "CNY"
        for dependency in ("quantity", "unit_price"):
            dependency_value = catalog.fields.get(f"sales.orders.{dependency}")
            assert isinstance(dependency_value, Deferred)
            assert consume_field(dependency_value).reference.ref == ms.ref.measure(
                f"sales.orders.{dependency}"
            )
        return
    assert isinstance(value, Deferred)
    assert consume_field(value).reference.ref.path == path
    assert consume_field(value).reference.ref.kind.value == target


def test_decorator_example_validation_rejects_a_factory_call() -> None:
    with pytest.raises(AssertionError, match="must declare a function decorated"):
        _validate_minimal_example_signature(
            example="ms.metric(name='revenue', entities=[orders], additivity=ms.additive_all())",
            public_entrypoint="ms.metric",
            callable_obj=ms.metric,
            decorator=True,
        )


@pytest.fixture
def expression_catalog(
    tmp_path: Path,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> ms.SemanticCatalog:
    extra = "\n".join(
        f"@ms.metric()\ndef other_{index:02d}():\n    return amount_value.sum(time=paid_at_value)\n"
        for index in range(16)
    )
    semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Analytics', default=True)\n",
            "sales/models.py": _REVENUE_MODEL.replace("def revenue():", "def zeta_revenue():")
            + extra,
        }
    )
    return ms.load(workspace_dir=tmp_path)


def test_readiness_repair_names_admissible_physical_dependencies(
    expression_catalog: ms.SemanticCatalog,
) -> None:
    with pytest.raises(SemanticRuntimeError) as captured:
        expression_catalog.readiness(refs=[ms.ref.metric("sales.zeta_revenue")])
    repair = captured.value.repair
    assert repair is not None and repair.snippet is not None
    assert "metric:sales.zeta_revenue" not in repair.candidates
    assert "measure:sales.orders.amount" in repair.candidates
    # Run the concrete public repair. It may report readiness blockers but must
    # not repeat the invalid-expression input rejection.
    eval(repair.snippet, {"catalog": expression_catalog, "ms": ms})


def test_missing_path_ranks_real_collection_paths_before_bounding(
    expression_catalog: ms.SemanticCatalog,
) -> None:
    with pytest.raises(SemanticRuntimeError) as captured:
        expression_catalog.metrics.get("sales.zeta_revenu")
    repair = captured.value.repair
    assert repair is not None and repair.snippet is not None
    assert repair.candidates[0] == "sales.zeta_revenue"
    assert "catalog.metrics.show()" in str(captured.value)
    assert "catalog.domains.show()" not in str(captured.value)
    assert eval(repair.snippet, {"catalog": expression_catalog, "ms": ms}) is not None
    for key in repair.candidates:
        assert key in {ref.path for ref in expression_catalog.metrics.refs}


def test_row_filter_failure_points_to_its_exact_expression_role(
    expression_catalog: ms.SemanticCatalog,
) -> None:
    amount = expression_catalog.fields.get("sales.orders.amount")
    with pytest.raises(SemanticExpressionError) as captured:
        consume_metric(amount.where(amount > 0).sum())
    repair = captured.value.repair
    assert repair is not None
    assert repair.help_target.display == "semantic.metric.where"
    assert "business-authorized" in repair.action
    text, _, _ = render_help_text(repair.help_target.display)
    assert text.startswith("Target: semantic.metric.where\n")


@pytest.mark.parametrize("target", ("semantic.metric", "semantic.measure", "semantic.ref"))
def test_semantic_object_routes_are_unique_and_resolvable(target: str) -> None:
    page = render_help_text(target)[0]
    routes = re.findall(r'marivo\.help\("([a-z_][a-z0-9_.]*)"\)', page)
    assert len(routes) == len(set(routes))
    for route in routes:
        assert render_help_text(route)[0].startswith(f"Target: {route}\n")


@pytest.mark.parametrize(
    ("target", "result_target"),
    (
        ("semantic.load", "semantic.semantic_catalog"),
        ("semantic.readiness", "semantic.readiness_report"),
        ("datasource.register", "datasource.datasource_summary"),
        ("datasource.inspect", "datasource.source_inspection"),
    ),
)
def test_callable_result_links_name_the_registered_public_type(
    target: str,
    result_target: str,
) -> None:
    page = render_help_text(target)[0]
    assert f'Result Help: marivo.help("{result_target}")' in page
    assert "Consumers:" not in page
    assert render_help_text(result_target)[0].startswith(f"Target: {result_target}\n")
