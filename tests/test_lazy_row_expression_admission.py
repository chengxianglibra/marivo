"""Pure static admission checks for computed measures, Linear, and decimal units.

The five SQL backends admit computed Measure row expressions and Linear graphs
through their concrete ``unsupported_reason`` owners, and composed decimal
results decide per published unit through the derived facts walk. The fixture
reuses the shared execution registry, rewrites the orders ``amount`` to
``decimal(12,2)``, and adds one computed Measure plus linear/mean/ratio
metrics; no service is started because nothing executes.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from marivo.analysis import engine_sample
from marivo.analysis import runtime_metric as rm
from marivo.analysis.operators.clickhouse_support import (
    unsupported_reason as ch_reason,
)
from marivo.analysis.operators.mysql_support import unsupported_reason as my_reason
from marivo.analysis.operators.postgres_support import unsupported_reason as pg_reason
from marivo.analysis.operators.sqlite_support import unsupported_reason as lite_reason
from marivo.analysis.operators.trino_support import unsupported_reason as trino_reason
from marivo.refs import SemanticKind, _create_ref, ref
from marivo.semantic._expression_binding import (
    CompiledExpressionSidecar,
    ExpressionBody,
)
from marivo.semantic.ir import (
    AiContextIR,
    LinearComposition,
    LinearTerm,
    MeasureIR,
    MetricIR,
    RatioComposition,
)
from marivo.semantic.metric_graph_canonical import (
    MetricGraphContractError,
    metric_graph_from_value,
)
from marivo.semantic.validator import Registry
from tests.lazy_observation_fixtures import (
    _LOCATION,
    NoIoActionPort,
    make_lazy_sources,
)
from tests.lazy_scalar_source_fixtures import registry_for

Backends = Literal["postgres", "mysql", "sqlite", "trino", "clickhouse"]
BACKENDS: tuple[Backends, ...] = ("postgres", "mysql", "sqlite", "trino", "clickhouse")
REASONS = {
    "postgres": pg_reason,
    "mysql": my_reason,
    "sqlite": lite_reason,
    "trino": trino_reason,
    "clickhouse": ch_reason,
}
# Published composed-decimal units per backend (plan §4). PostgreSQL and
# ClickHouse open only the add/sub-level linear cell. MySQL keeps mean/div
# closed this stage: the mean pipeline still publishes a float-labeled
# sum/count division that the value-exact transport rule refuses (verified on
# the live service), and no decimal-rooted ratio is constructible while engine
# division inference labels results float64. Trino (probe: lossy AVG at the
# input scale) and SQLite (no decimal storage) keep the conservative rejection.
UNITS: dict[Backends, frozenset[str]] = {
    "postgres": frozenset({"linear"}),
    "mysql": frozenset({"linear"}),
    "sqlite": frozenset(),
    "trino": frozenset(),
    "clickhouse": frozenset({"linear"}),
}


def _decimal_registry(
    engine: Backends, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Registry, CompiledExpressionSidecar]:
    """The shared execution registry with decimal facts plus computed measures."""
    table = "c4_" + _unique()
    base, base_sidecar = registry_for(tmp_path / "unused", engine=engine, table=table)
    entity = base.entities["sales.orders"]
    entities = dict(base.entities)
    entities["sales.orders"] = replace(
        entity,
        source=replace(
            entity.source,
            columns=tuple(
                (
                    key,
                    replace(binding, data_type="decimal(12,2)")
                    if key == "amount"
                    else replace(binding, data_type="int64")
                    if key == "weight"
                    else binding,
                )
                for key, binding in entity.source.columns
            ),
        ),
    )

    def net(orders_table):  # type: ignore[no-untyped-def]
        return orders_table.amount * orders_table.weight

    computed = ExpressionBody(
        callable=net,
        body_ast_hash="sha256:net-row-expression",
        parameter_count=1,
        bindings=(),
        source_columns=("amount", "weight"),
        source_syntax=(
            "def expression_body(entity_0):\n    return entity_0.amount * entity_0.weight"
        ),
    )
    measure = MeasureIR(
        "sales.orders.net",
        "sales",
        "sales.orders",
        "net",
        AiContextIR(),
        "additive",
        None,
        "net",
        _LOCATION,
        body_ast_hash=computed.body_ast_hash,
    )
    registry = replace(
        base,
        entities=entities,
        measures={**base.measures, "sales.orders.net": measure},
    )
    net_ref = _create_ref(SemanticKind.MEASURE, "sales.orders.net")
    bodies = dict(base_sidecar.bodies)
    owners = dict(base_sidecar.field_owners)
    bodies[net_ref] = computed
    owners[net_ref] = _create_ref(SemanticKind.ENTITY, "sales.orders")
    sidecar = CompiledExpressionSidecar(
        bodies=bodies,
        field_owners=owners,
        catalog_refs=base_sidecar.catalog_refs | {net_ref},
    )

    def simple(name: str, agg: str, target: str) -> MetricIR:
        return MetricIR(
            f"sales.{name}",
            "sales",
            name,
            "simple",
            ("sales.orders",),
            agg,
            target,
            None,
            None,
            None,
            AiContextIR(),
            "direct",
            name,
            _LOCATION,
            aggregation_target=target,
            aggregation_target_kind="measure",
        )

    metrics = dict(registry.metrics)
    metrics["sales.net_sum"] = simple("net_sum", "sum", "sales.orders.net")
    metrics["sales.amount_sum"] = simple("amount_sum", "sum", "sales.orders.amount")
    metrics["sales.weight_sum"] = simple("weight_sum", "sum", "sales.orders.weight")
    metrics["sales.amount_mean"] = replace(
        metrics["sales.mean_amount"],
        semantic_id="sales.amount_mean",
        name="amount_mean",
        aggregation_target="sales.orders.amount",
    )
    metrics["sales.revenue_linear"] = replace(
        metrics["sales.revenue"],
        semantic_id="sales.revenue_linear",
        name="revenue_linear",
        metric_type="derived",
        entities=(),
        aggregation=None,
        measure=None,
        aggregation_target=None,
        aggregation_target_kind=None,
        composition=LinearComposition(
            terms=(
                LinearTerm("+", "sales.amount_sum"),
                LinearTerm("-", "sales.amount_sum"),
            )
        ),
    )
    metrics["sales.weight_linear"] = replace(
        metrics["sales.revenue"],
        semantic_id="sales.weight_linear",
        name="weight_linear",
        metric_type="derived",
        entities=(),
        aggregation=None,
        measure=None,
        aggregation_target=None,
        aggregation_target_kind=None,
        composition=LinearComposition(
            terms=(
                LinearTerm("+", "sales.weight_sum"),
                LinearTerm("-", "sales.weight_sum"),
            )
        ),
    )
    metrics["sales.revenue_ratio"] = replace(
        metrics["sales.revenue"],
        semantic_id="sales.revenue_ratio",
        name="revenue_ratio",
        metric_type="derived",
        entities=(),
        aggregation=None,
        measure=None,
        aggregation_target=None,
        aggregation_target_kind=None,
        composition=RatioComposition("sales.amount_sum", "sales.order_count"),
    )
    registry = replace(registry, metrics=metrics)
    registry.freeze()
    return registry, sidecar


def _sources(
    engine: Backends, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: str
) -> object:
    registry, sidecar = _decimal_registry(engine, tmp_path, monkeypatch)
    return make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id=session,
        store_id=session,
    )


def _unique() -> str:
    from uuid import uuid4

    return uuid4().hex


@pytest.mark.parametrize("backend", BACKENDS)
def test_computed_measure_row_expression_is_admitted(
    backend: Backends, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if backend == "sqlite":
        pytest.skip("SQLite declares no decimal storage for this fixture")
    observed = _sources(backend, tmp_path, monkeypatch, "row-expr").observe(
        ref.metric("sales.net_sum")
    )
    assert REASONS[backend](observed.aggregate()) is None


@pytest.mark.parametrize("backend", BACKENDS)
def test_linear_graph_is_admitted(
    backend: Backends, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed = _sources(backend, tmp_path, monkeypatch, "linear").observe(
        ref.metric("sales.weight_linear")
    )
    assert REASONS[backend](observed.aggregate()) is None


@pytest.mark.parametrize(
    "backend,admitted",
    [
        ("postgres", True),
        ("mysql", True),
        ("sqlite", False),
        ("trino", False),
        ("clickhouse", True),
    ],
)
def test_decimal_linear_admission_matches_declared_units(
    backend: Backends,
    admitted: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if backend == "sqlite":
        pytest.skip("SQLite declares no decimal storage for this fixture")
    observed = _sources(backend, tmp_path, monkeypatch, "dec-linear").observe(
        ref.metric("sales.revenue_linear")
    )
    reason = REASONS[backend](observed.aggregate())
    assert (reason is None) == admitted


@pytest.mark.parametrize(
    "backend,admitted",
    [
        ("postgres", False),
        ("mysql", False),
        ("sqlite", False),
        ("trino", False),
        ("clickhouse", False),
    ],
)
def test_decimal_mean_admission_matches_declared_units(
    backend: Backends,
    admitted: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if backend == "sqlite":
        pytest.skip("SQLite declares no decimal storage for this fixture")
    observed = _sources(backend, tmp_path, monkeypatch, "dec-mean").observe(
        ref.metric("sales.amount_mean")
    )
    reason = REASONS[backend](observed.aggregate())
    assert (reason is None) == admitted


def test_kept_rejection_reasons_name_their_engine_fact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed = _sources("trino", tmp_path, monkeypatch, "dec-trino").observe(
        ref.metric("sales.revenue_linear")
    )
    reason = trino_reason(observed.aggregate())
    assert reason is not None
    observed = _sources("postgres", tmp_path, monkeypatch, "dec-pg").observe(
        ref.metric("sales.amount_mean")
    )
    reason = pg_reason(observed.aggregate())
    assert "AVG scale is a public contract" in (reason or "")
    observed = _sources("clickhouse", tmp_path, monkeypatch, "dec-ch").observe(
        ref.metric("sales.amount_mean")
    )
    reason = ch_reason(observed.aggregate())
    assert "AVG scale is a public contract" in (reason or "")


@pytest.mark.parametrize("backend", BACKENDS)
def test_direct_column_decimal_measures_stay_admitted(
    backend: Backends, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if backend == "sqlite":
        pytest.skip("SQLite declares no decimal storage for this fixture")
    observed = _sources(backend, tmp_path, monkeypatch, "direct").observe(
        ref.metric("sales.amount_sum")
    )
    assert REASONS[backend](observed.aggregate()) is None


@pytest.mark.parametrize("backend", BACKENDS)
def test_sampling_stays_unqualified(
    backend: Backends, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.lazy_temporal_backend_fixtures import _declared_source

    registry, sidecar = _declared_source(backend, tmp_path, monkeypatch, "c4_" + _unique())
    sampled = (
        make_lazy_sources(
            semantic_registry=registry,
            sidecar=sidecar,
            action_port=NoIoActionPort(),
            session_id=f"sampling-{backend}",
            store_id=f"sampling-{backend}",
        )
        .population(ref.entity("sales.orders"))
        .sample(engine_sample(target_rows=2, seed=1))
    )
    assert REASONS[backend](sampled) is not None


def test_non_pm1_linear_coefficient_payloads_are_rejected_at_persistence() -> None:
    def payload(coefficient: float) -> dict[str, object]:
        return {
            "schema": "metric-expression/v1",
            "roots": ["root"],
            "nodes": [
                {
                    "node_id": "root",
                    "node": {
                        "kind": "linear",
                        "terms": [{"child_id": "left", "coefficient": coefficient}],
                        "unit_override": None,
                    },
                }
            ],
            "occurrences": [],
        }

    with pytest.raises(MetricGraphContractError, match="coefficient"):
        metric_graph_from_value(payload(2.0))
    with pytest.raises(MetricGraphContractError, match="coefficient"):
        metric_graph_from_value(payload(0.5))


def test_runtime_linear_projection_passes_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A projected dataset that carries a Linear ancestor passes the whitelist."""
    revenue = ref.metric("sales.weight_sum")
    linear = rm.linear(add=(revenue,), subtract=(ref.metric("sales.weight_sum"),), label="net")
    observed = (
        _sources("mysql", tmp_path, monkeypatch, "runtime-lin")
        .observe((revenue, linear))
        .metric(revenue)
    )
    assert my_reason(observed.aggregate()) is None
