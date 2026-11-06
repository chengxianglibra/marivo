"""Lexical time parameter capture, publication, and actual query behavior."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import duckdb
import pytest

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.semantic.errors import SemanticError, SemanticLoadFailed
from tests.shared_fixtures import write_minimal_catalog_project

_TEMPLATES = """
from marivo.semantic import TimeParameter as Clock
created_at = ms.time_dimension_column(name="created_at", entity=orders, column="created_at",
    parse=ms.timestamp(timezone="UTC"))

@ms.metric()
def amount_at(*, event: "Clock"):
    return amount.sum(time=event)

@ms.metric()
def count_at(*, event: ms.TimeParameter):
    return orders.count(time=event)

@ms.metric()
def mean_at(*, event: ms.TimeParameter):
    numerator = amount_at.bind_time(event=event)
    denominator = count_at.bind_time(event=event)
    return numerator / denominator

@ms.metric()
def difference(*, booked: ms.TimeParameter, settled: ms.TimeParameter):
    paid = amount_at.bind_time(event=settled)
    booked_amount = amount_at.bind_time(event=booked)
    return paid - booked_amount
"""


def _project(path: Path, *, extra: str = "", database: bool = False) -> None:
    write_minimal_catalog_project(path)
    model = path / "models/semantic/sales/objects.py"
    model.write_text(model.read_text() + _TEMPLATES + extra)
    if database:
        database_path = path / "facts.duckdb"
        with duckdb.connect(str(database_path)) as connection:
            connection.execute(
                """CREATE TABLE orders AS SELECT *
                   FROM (VALUES (1, 'paid', TIMESTAMP '2026-11-02', TIMESTAMP '2026-10-31', 100.0),
                                (2, 'paid', TIMESTAMP '2026-11-03', TIMESTAMP '2026-11-03', 200.0))
                   AS rows(order_id, status, paid_at, created_at, amount)"""
            )
        (path / "models/datasources/warehouse.py").write_text(
            f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database_path)!r})\n"
        )


def _reject_source_io(*_args: object, **_kwargs: object) -> NoReturn:
    pytest.fail("The open Metric reached a datasource before its time parameters were bound.")


def test_template_catalog_disclosure_and_bind_rejections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path)
    monkeypatch.setattr(DatasourceConnectionService, "backend_for", _reject_source_io)
    monkeypatch.setattr(DatasourceConnectionService, "source_session", _reject_source_io)
    catalog = ms.load(workspace_dir=tmp_path)
    amount = catalog.get("metric:sales.amount_at")
    count = catalog.get("metric:sales.count_at")
    paid = catalog.get("time_dimension:sales.orders.paid_at")
    assert amount.kind == count.kind == "metric" and paid.kind == "time_dimension"
    (parameter,) = amount.definition.time_parameters
    assert (parameter.name, parameter.roots) == ("event", ("entity:sales.orders",))
    assert [(item.use, item.root) for item in parameter.constraints] == [
        ("contribution", "entity:sales.orders")
    ]
    (branch,) = amount.definition.time_bindings
    assert isinstance(branch, ms.MetricTimeParameterBinding) and branch.parameter == "event"
    assert ".bind_time(event=...)" in amount.render()
    assert amount.id in {item.id for item in catalog.search("event", kind="metric").items}
    assert amount.id not in {
        entry.id for entry in catalog.readiness(refs=[amount]).analysis_ready_inputs
    }
    with pytest.raises(SemanticError, match="unbound time parameters") as preview_error:
        catalog.preview(amount, scope=md.unpruned(max_rows=10, timeout_seconds=10))
    assert preview_error.value.details["query_executed"] is False
    assert not callable(amount)
    with pytest.raises(TypeError):
        amount(event=paid)  # type: ignore[operator]  # Metrics are not callable.
    with pytest.raises(SemanticError, match="same name") as caught:
        _ = amount + count
    assert caught.value.details["parameters"] == ("event",)
    assert caught.value.repair is not None and "each operand" in caught.value.repair.action
    for invalid in ("paid_at", None, mv.grain("day")):
        with pytest.raises(SemanticError, match="require a resolved time field"):
            amount.bind_time(event=invalid)  # type: ignore[arg-type]  # Exercise invalid bindings.
    with pytest.raises(SemanticError, match="unknown or already bound"):
        amount.bind_time(unknown=paid)
    with pytest.raises(SemanticError, match="No Metric time parameters"):
        amount.bind_time()
    with pytest.raises(SemanticError, match="unknown or already bound"):
        amount.bind_time(event=paid).bind_time(event=paid)
    bound_boolean = (amount > 0).bind_time(event=paid)
    assert "role: boolean" in bound_boolean.render()


def test_time_parameter_help_routes_show_the_binding_entrypoint() -> None:
    from marivo._help.render import render_help_text

    declaration = render_help_text("semantic.metric.define")[0]
    annotation = render_help_text("semantic.time_parameter")[0]
    method = render_help_text("semantic.metric.bind_time")[0]
    attribute = render_help_text("semantic.metric_definition.time_parameters")[0]
    assert ".bind_time(event=paid_at)" in declaration
    assert "Target: semantic.time_parameter" in annotation
    assert ".bind_time(event=paid_at)" in annotation
    assert "Kind: callable" in method and "**bindings" in method
    assert "Kind: attribute" in attribute and "tuple[MetricTimeParameter, ...]" in attribute


def test_repeated_and_renamed_slots_bind_to_independent_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, database=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create(name="lexical_slots", timezone="UTC")
    amount = session.catalog.get("metric:sales.amount_at")
    concrete = session.catalog.get("metric:sales.revenue")
    measure = session.catalog.get("measure:sales.orders.amount")
    mean = session.catalog.get("metric:sales.mean_at")
    diff = session.catalog.get("metric:sales.difference")
    created = session.catalog.get("time_dimension:sales.orders.created_at")
    paid = session.catalog.get("time_dimension:sales.orders.paid_at")
    assert amount.kind == mean.kind == diff.kind == concrete.kind == "metric"
    assert measure.kind == "measure"
    assert created.kind == paid.kind == "time_dimension"
    assert tuple(parameter.name for parameter in diff.definition.time_parameters) == (
        "settled",
        "booked",
    )
    with monkeypatch.context() as guard:
        guard.setattr(DatasourceConnectionService, "backend_for", _reject_source_io)
        guard.setattr(DatasourceConnectionService, "source_session", _reject_source_io)
        with pytest.raises(AnalysisError, match="unbound time parameters"):
            session.observe(amount, by=())
        partial = diff.bind_time(settled=paid)
        assert ".bind_time(booked=...)" in partial.render()
        with pytest.raises(AnalysisError, match="booked"):
            session.observe({"partial": partial}, by=())
    scope = session.time_scope(start="2026-11-01", end="2026-12-01")
    frame = session.observe(
        {
            "created": amount.bind_time(event=created),
            "paid": amount.bind_time(event=paid),
            "twice": (amount + amount).bind_time(event=paid),
            "mean_created": mean.bind_time(event=created),
            "mean_paid": mean.bind_time(event=paid),
            "difference": partial.bind_time(booked=created),
            "one_step": diff.bind_time(booked=created, settled=paid),
            "concrete": concrete,
        },
        by=(scope,),
    ).compute()
    expected = {
        "created": 200,
        "paid": 300,
        "twice": 600,
        "mean_created": 200,
        "mean_paid": 150,
        "difference": 100,
        "one_step": 100,
        "concrete": 300,
    }
    rows = frame.to_pandas()
    assert rows.loc[:, list(expected)].iloc[0].to_dict() == expected
    assert [rows.iloc[0][key].isoformat() for key in ("interval.start", "interval.end")] == [
        "2026-11-01T00:00:00+00:00",
        "2026-12-01T00:00:00+00:00",
    ]
    assert tuple(slot.name for slot in amount.definition.time_parameters) == ("event",)
    assert session.observe({"amount": measure.sum()}).compute().to_pandas()["amount"].tolist() == [
        300
    ]
    with pytest.raises(AnalysisError, match="declared business time role"):
        session.observe({"amount": measure.sum()}, by=(scope,))
    with pytest.raises(SemanticError, match="time=None"):
        session.observe({"amount": measure.sum(time=None)})  # type: ignore[arg-type]  # Invalid time.


def test_sample_parameter_retains_the_native_status_axis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(
        tmp_path,
        database=True,
        extra="""
stock = ms.measure_column(name="stock", entity=orders, column="amount", unit="USD",
    additivity=ms.additive_except(paid_at), status_time_dimension=paid_at)

@ms.metric()
def last_stock(*, clock: ms.TimeParameter):
    return stock.sum(time=clock).per(clock).last(order_by=clock)
""",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create(name="native_state_parameter", timezone="UTC")
    stock = session.catalog.get("metric:sales.last_stock")
    created = session.catalog.get("time_dimension:sales.orders.created_at")
    paid = session.catalog.get("time_dimension:sales.orders.paid_at")
    assert stock.kind == "metric" and created.kind == paid.kind == "time_dimension"
    (slot,) = stock.definition.time_parameters
    assert [(item.use, item.required_field) for item in slot.constraints] == [
        ("contribution", None),
        ("native_sample", paid.id),
        ("sample_order", None),
    ]
    with pytest.raises(SemanticError, match="constraints") as caught:
        stock.bind_time(clock=created)
    assert caught.value.expected == paid.id
    assert caught.value.details["parameters"] == ("clock",)
    frame = session.observe({"last": stock.bind_time(clock=paid)}).compute()
    assert frame.to_pandas()["last"].tolist() == [200]


def test_parameter_identity_survives_cold_load_and_scopes_definition_invalidation(
    tmp_path: Path,
) -> None:
    from marivo.refs import ref

    _project(tmp_path)
    old = ms.load(workspace_dir=tmp_path)
    template = ref.metric("sales.amount_at")
    dependent = ref.metric("sales.mean_at")
    unrelated = ref.metric("sales.revenue")
    expected = old._state._definition_version(template)
    script = """
import sys
import marivo.semantic as ms
from marivo.refs import ref
catalog = ms.load(workspace_dir=sys.argv[1])
metric = catalog.get("metric:sales.amount_at")
paid = catalog.get("time_dimension:sales.orders.paid_at")
assert ".bind_time(event=...)" in metric.render()
assert "session.observe" in metric.bind_time(event=paid).render()
print(catalog._state._definition_version(ref.metric("sales.amount_at")))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, check=True
    )
    assert completed.stdout.strip() == expected
    source = tmp_path / "models/semantic/sales/objects.py"
    source.write_text(
        source.read_text().replace(
            "return amount.sum(time=event)", "return amount.sum(time=event) * 2"
        )
    )
    current = ms.load(workspace_dir=tmp_path)
    assert current._state._definition_version(template) != expected
    assert current._state._definition_version(dependent) != old._state._definition_version(
        dependent
    )
    assert current._state._definition_version(unrelated) == old._state._definition_version(
        unrelated
    )


def test_complete_scope_checks_every_slot_use_and_concrete_field_root(tmp_path: Path) -> None:
    other = """
refunds = ms.entity(name="refunds", datasource=ms.ref.datasource("warehouse"),
    source=md.table("refunds"), primary_key=["refund_id"])
refunded_at = ms.time_dimension_column(name="refunded_at", entity=refunds, column="refunded_at",
    parse=ms.timestamp(timezone="UTC"))
refund_count = ms.ref.entity("sales.refunds")
"""
    _project(tmp_path, extra=other)
    catalog = ms.load(workspace_dir=tmp_path)
    count = catalog.get("metric:sales.count_at")
    refunded = catalog.get("time_dimension:sales.refunds.refunded_at")
    assert count.kind == "metric" and refunded.kind == "time_dimension"
    with pytest.raises(SemanticError, match="constraints") as bad_binding:
        count.bind_time(event=refunded)
    assert bad_binding.value.details["parameters"] == ("event",)
    assert bad_binding.value.expected == "a TimeDimension on entity:sales.orders"
    source = tmp_path / "models/semantic/sales/objects.py"
    source.write_text(
        source.read_text()
        + """
@ms.metric()
def incompatible(*, event: ms.TimeParameter):
    return orders.count(time=event) + refund_count.count(time=event)
"""
    )
    with pytest.raises(SemanticLoadFailed, match="incompatible contribution roots"):
        ms.load(workspace_dir=tmp_path)


@pytest.mark.parametrize(
    ("declaration", "message"),
    [
        ("def bad(event: ms.TimeParameter):\n    return amount.sum(time=event)", "signature"),
        (
            "def bad(*, event: ms.TimeParameter = None):\n    return amount.sum(time=event)",
            "signature",
        ),
        ("def bad(*, event: int):\n    return amount.sum(time=event)", "annotation"),
        ('def bad(*, event: "explode()"):\n    return amount.sum(time=event)', "annotation"),
        ("def bad(*, event: ms.TimeParameter):\n    return amount.sum()", "unused: event"),
        ("def bad():\n    return amount_at", "foreign: event"),
        (
            "def bad(*, event: ms.TimeParameter):\n    return amount.sum(time=event) + (event + 1)",
            "outside a supported position",
        ),
    ],
)
def test_invalid_time_declarations_are_rejected_at_load(
    tmp_path: Path, declaration: str, message: str
) -> None:
    _project(tmp_path, extra=f"\n@ms.metric()\n{declaration}\n")
    with pytest.raises(SemanticLoadFailed, match=message):
        ms.load(workspace_dir=tmp_path)
