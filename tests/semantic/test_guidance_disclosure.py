"""Independent contracts for semantic guidance, failure facts, and declaration examples."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

import marivo
import marivo.datasource as md
import marivo.semantic as ms
from marivo._authoring.model import AuthoringCapability, AuthoringRepair
from marivo._help.render import help as show_help
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic._capabilities.registry import REGISTRY
from marivo.semantic._capabilities.validation import _validate_minimal_example_signature
from marivo.semantic.errors import SemanticLoadError, SemanticLoadFailed
from marivo.semantic.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from marivo.semantic.readiness import ReadinessInputSummary, ReadinessIssue, ReadinessReport
from marivo.semantic.source_health import SourceHealthCheckResult, SourceHealthReport


def _no_io(*args: object, **kwargs: object) -> object:
    raise AssertionError("Reading guidance must not load, connect, or run checks")


def _assert_current_help_budget(text: str) -> None:
    budget = REGISTRY.render_budget("current_briefing")
    assert len(text.splitlines()) <= budget.max_lines
    assert len(text.rstrip("\n")) <= budget.max_codepoints
    routes = set(re.findall(r'marivo\.help\("([a-z]+\.[A-Za-z_.]+)"\)', text))
    assert len(routes) <= budget.max_outgoing_routes


def test_definition_read_does_not_prescribe_readiness(
    authoring_evidence_project: object,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    catalog = ms.load()
    assert marivo.help is show_help
    entry = catalog.require(ms.ref.metric("sales.revenue"))
    monkeypatch.setattr("marivo.semantic.catalog.load", _no_io)
    monkeypatch.setattr("marivo.semantic.catalog.SemanticCatalog.readiness", _no_io)
    monkeypatch.setattr("marivo.datasource.backends.build_backend", _no_io)

    assert "sales.revenue" in entry.render()
    assert "readiness(" not in entry.details().render()
    show_help(entry)
    briefing = capsys.readouterr().out
    assert "entry.details().show()" in briefing
    assert 'marivo.help("semantic.readiness")' in briefing
    assert "readiness(" not in briefing
    show_help("semantic.authoring")
    authoring = capsys.readouterr().out
    assert "catalog = ms.load()" not in authoring
    for target in ("semantic.objects", "semantic.builders", "semantic.checks"):
        assert target in authoring
    show_help("semantic.metric")
    constructor = capsys.readouterr().out
    assert "Postcondition after saving" in constructor
    assert "catalog = ms.load()" in constructor
    assert "catalog.metrics.get(" in constructor
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
    show_help(error)
    text = capsys.readouterr().out
    for fact in (error.kind, error.message, error.expected, error.received, "sales.revenue"):
        assert fact is not None and fact in text
    assert "semantic project" in text
    assert ('marivo.help("datasource.inspect")' in text) is with_repair
    _assert_current_help_budget(text)


def test_dynamic_error_values_are_bounded_with_full_read_path(
    capsys: pytest.CaptureFixture[str],
) -> None:
    error = SemanticLoadError(
        kind="synthetic_failure",
        message="large message " + "x" * 20_000,
        expected="one domain",
        received="none",
        repair=AuthoringRepair(
            kind="inspect",
            action="Inspect the current source.",
            help_target=LiveHelpTarget(surface="datasource", canonical_id="inspect"),
            candidates=tuple(f"candidate_{index}" for index in range(500)),
            snippet="\n".join("entry.show()" for _ in range(100)),
        ),
    )
    show_help(error)
    text = capsys.readouterr().out
    assert "large message" in text
    assert "Expected: one domain" in text
    assert "Received: none" in text
    assert 'marivo.help("datasource.inspect")' in text
    assert "omitted" in text
    assert "exc.message" in text and "exc.repair" in text
    _assert_current_help_budget(text)
    assert len(error.message) > 20_000


def test_aggregate_error_help_retains_order_and_reports_omissions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    error = SemanticLoadFailed(
        tuple(
            SemanticLoadError(
                kind="synthetic_failure",
                message=f"failure_{index:03d}",
                expected="declared root",
                received="missing root",
            )
            for index in range(100)
        )
    )
    show_help(error)
    text = capsys.readouterr().out
    assert text.index("failure_000") < text.index("failure_001")
    match = re.search(r"Omitted errors: (\d+)", text)
    assert match is not None
    shown = len(re.findall(r"Error \d+: SemanticLoadError", text))
    assert shown + int(match.group(1)) == len(error.errors)
    assert "for error in exc.errors: print(error)" in text
    _assert_current_help_budget(text)


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
                    help_target=LiveHelpTarget(surface="semantic", canonical_id="SemanticCatalog"),
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
    assert 'marivo.help("semantic.SemanticCatalog")' in text
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
            help_target=LiveHelpTarget(surface="semantic", canonical_id="measure_column"),
        ),
        user_data_queried=True,
        scopes=((entity, md.unpruned(max_rows=10, timeout_seconds=5)),),
    )
    report = SourceHealthReport("failed", (check,), check.checked_at, "definition:fixture")
    monkeypatch.setattr("marivo.datasource.backends.build_backend", _no_io)
    report_text = report.render()
    assert "sales.revenue" in report_text
    assert "Repair the declared amount field." in report_text
    assert 'marivo.help("semantic.measure_column")' in report_text
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
        ("measure", "sales.orders.amount"),
        ("metric", "sales.revenue"),
        ("event", "sales.order_created"),
    ),
)
def test_decorator_help_example_loads_the_declared_object(
    target: str,
    path: str,
    tmp_path: Path,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = REGISTRY.by_canonical_id(target)
    assert isinstance(descriptor, AuthoringCapability)
    assert descriptor.minimal_example is not None
    monkeypatch.setattr("marivo.datasource.backends.build_backend", _no_io)
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
                "column='event_time', granularity='second', parse=ms.timestamp(timezone='UTC'))\n"
                + descriptor.minimal_example
                + "\n"
            ),
        }
    )
    factory = {
        "dimension": ms.ref.dimension,
        "time_dimension": ms.ref.time_dimension,
        "measure": ms.ref.measure,
        "metric": ms.ref.metric,
        "event": ms.ref.event,
    }[target]
    entry = ms.load(workspace_dir=tmp_path).require(factory(path))
    assert entry.path == path
    assert entry.kind.value == target


def test_decorator_example_validation_rejects_a_factory_call() -> None:
    with pytest.raises(AssertionError, match="must declare a function decorated"):
        _validate_minimal_example_signature(
            example="ms.metric(name='revenue', entities=[orders], additivity=ms.additive_all())",
            public_entrypoint="ms.metric",
            callable_obj=ms.metric,
            decorator=True,
        )
