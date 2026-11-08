"""Independent contracts for semantic guidance, failure facts, and declaration examples."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest
from ibis.common.deferred import Deferred

import marivo
import marivo.datasource as md
import marivo.semantic as ms
from marivo._authoring.model import AuthoringCapability, AuthoringRepair
from marivo._definition.expressions import DeclaredMetricRef
from marivo._help.render import help as show_help
from marivo._help.render import render_help_text
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import ref as ref_factory
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
from marivo.semantic.validation import ValidationCheckResult, ValidationReport
from tests.shared_fixtures import catalog_value
from tests.shared_fixtures import resolve_test_symbol as resolve
from tests.support.refs import as_ref


def _no_io(*args: object, **kwargs: object) -> object:
    raise AssertionError("Reading guidance must not load, connect, or run checks")


_REVENUE_MODEL = """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'), primary_key=['order_id'])
paid_at = ms.time_dimension_column(name='paid_at', entity=orders, column='paid_at', parse=ms.timestamp(timezone='UTC'))
amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all(), unit='USD')
amount_value = amount
paid_at_value = paid_at

@ms.metric(ai_context=ms.ai_context(business_definition='Sum of accepted order amounts.'))
def revenue():
    return amount_value.sum(time=paid_at_value)
"""


def test_definition_read_does_not_prescribe_readiness(
    tmp_path: Path,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch.setattr("marivo.semantic.catalog.load", _no_io)
    monkeypatch.setattr("marivo.semantic.catalog.SemanticCatalog.readiness", _no_io)
    monkeypatch.setattr("marivo.datasource.backends.build_backend", _no_io)

    contract = catalog.get("metric:sales.revenue").render()


@pytest.mark.parametrize("with_repair", (False, True))
def test_error_instance_keeps_concrete_facts(
    with_repair: bool,
    monkeypatch: pytest.MonkeyPatch,
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
    assert error.kind == "synthetic_failure"
    assert error.expected == "sales/_domain.py"
    assert error.received == "sales/metrics.py only"
    assert error.semantic_refs == ("sales.revenue",)
    assert (error.repair is not None) is with_repair


def test_readiness_card_discloses_scope_and_repair_without_empty_fix() -> None:
    report = ReadinessReport(
        status="blocked",
        _analysis_ready_inputs=(ref_factory.metric("sales.ready"),),
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
    assert report.status == "blocked"
    assert report.input_summary.datasources == ("warehouse",)
    assert report.blockers[0].refs == ("sales.missing",)


def test_validate_cards_disclose_failed_facts_and_scope_without_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity = ms.ref.entity("sales.orders")
    check = ValidationCheckResult(
        kind="not_null",
        status="failed",
        _datasource=ref_factory.datasource("warehouse"),
        source=TableSourceIR(table="orders"),
        _target_refs=(ref_factory.entity("sales.orders"),),
        _affected_refs=(ref_factory.metric("sales.revenue"),),
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
        _scopes=(
            (ref_factory.entity("sales.orders"), md.unpruned(max_rows=10, timeout_seconds=5)),
        ),
    )
    report = ValidationReport("failed", (check,), check.checked_at, "definition:fixture")
    monkeypatch.setattr("marivo.datasource.backends.build_backend", _no_io)
    assert check.observed == {"null_count": 2, "row_count": 10}
    assert check.user_data_queried is True


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
            "from marivo._definition.deferred import root_declaration\n"
            "from marivo.semantic._expression_values import _as_deferred\n"
            "from marivo.semantic._deferred_semantics import consume_metric, consume_row\n"
            "from marivo._definition.graph import computation_fingerprint\n"
            f"assert isinstance({symbol}, ms.{'RowExpression' if target == 'measure' else 'MetricExpression'})\n"
            f"assert root_declaration(_as_deferred({symbol})).name == {symbol!r}\n"
        )
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
    value = catalog_value(catalog, path, kind="metric" if target == "metric" else "field")
    if target in {"measure", "metric"}:
        # R1.4 publishes module-level declarations under their function name.
        if target == "measure":
            assert isinstance(value, ms.Measure)
            assert value.name == "order_amount"
            assert consume_row(value).unit == "CNY"
        else:
            assert isinstance(value, ms.Metric)
            metric = consume_metric(value)
            assert isinstance(metric.node, DeclaredMetricRef)
            assert metric.node.ref.ref == ref_factory.metric(path)
            assert metric.unit == "CNY"
        for dependency in ("quantity", "unit_price"):
            dependency_value = catalog_value(catalog, f"sales.orders.{dependency}", kind="field")
            assert isinstance(dependency_value, ms.Measure)
            assert consume_field(dependency_value).reference.ref == ref_factory.measure(
                f"sales.orders.{dependency}"
            )
        return
    assert isinstance(value, ms.TimeDimension if target == "time_dimension" else ms.Dimension)
    assert consume_field(value).reference.ref.path == path
    assert consume_field(value).reference.ref.kind.value == target


def test_decorator_example_validation_rejects_a_factory_call() -> None:
    with pytest.raises(AssertionError):
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
    report = expression_catalog.readiness(
        refs=[expression_catalog.get("metric:sales.zeta_revenue")]
    )
    assert report.requested_ids == ("metric:sales.zeta_revenue",)
    assert "measure:sales.orders.amount" in report.checked_ids
    for value in report.analysis_ready_inputs:
        reference = as_ref(value)
        assert reference is not None and reference.kind is not ms.SemanticKind.METRIC


def test_missing_path_ranks_real_collection_paths_before_bounding(
    expression_catalog: ms.SemanticCatalog,
) -> None:
    with pytest.raises(SemanticRuntimeError) as captured:
        _ = expression_catalog.get("metric:sales.zeta_revenu")
    repair = captured.value.repair
    assert repair is not None
    assert repair.candidates[0] == "metric:sales.zeta_revenue"
    for key in repair.candidates:
        assert expression_catalog.get(key).id == key


def test_row_filter_failure_points_to_its_exact_expression_role(
    expression_catalog: ms.SemanticCatalog,
) -> None:
    # The restricted declaration grammar still diagnoses invalid row-level filters.
    amount = resolve(expression_catalog, ms.ref.measure("sales.orders.amount"))
    assert isinstance(amount, Deferred)
    with pytest.raises(SemanticExpressionError) as captured:
        consume_metric(amount.where(amount > 0).sum())
    repair = captured.value.repair
    assert repair is not None
    assert repair.help_target.display == "semantic.metric.where"


@pytest.mark.parametrize("target", ("semantic.metric", "semantic.measure", "semantic.ref"))
def test_semantic_object_routes_are_unique_and_resolvable(target: str) -> None:
    page = render_help_text(target)[0]
    routes = re.findall(r'marivo\.help\("([a-z_][a-z0-9_.]*)"\)', page)
    assert len(routes) == len(set(routes))
