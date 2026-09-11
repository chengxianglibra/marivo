"""Small governed Lifecycle model and source setup for private acceptance."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from marivo.analysis import time_scope
from marivo.analysis.domains.completeness import SourceOriginCompletenessDeclarationV1
from marivo.analysis.domains.lifecycle import LogicalLifecycleDataset
from marivo.analysis.domains.subject import PopulationInput
from marivo.analysis.lifecycle import FromInception
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.session._lazy_sources import LazySources, make_lazy_sources
from marivo.datasource.ir import AiContextIR
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.ir import (
    LifecycleStateIR,
    SourceLocation,
    StateInceptionIR,
    StateModelIR,
    StateTransitionIR,
    StateTriggerIR,
)
from marivo.semantic.validator import Registry
from tests.lazy_event_fixtures import make_event_registry
from tests.lazy_event_runtime_fixtures import setup_event
from tests.lazy_observation_fixtures import NoIoActionPort

START = datetime(2026, 2, 1, tzinfo=timezone.utc)
END = datetime(2026, 2, 2, tzinfo=timezone.utc)
MODEL = ref.state_model("sales.purchase")


def lifecycle_registry(database: Path) -> tuple[Registry, CompiledExpressionSidecar]:
    registry, sidecar = make_event_registry(database)
    model = StateModelIR(
        "sales.purchase",
        "sales",
        "purchase",
        "sales.customers",
        (LifecycleStateIR("open", True, False), LifecycleStateIR("done", False, True)),
        (StateInceptionIR(StateTriggerIR("sales.started", "buyer")),),
        (StateTransitionIR("open", StateTriggerIR("sales.finished", "buyer"), "done"),),
        AiContextIR(),
        "purchase",
        SourceLocation("lazy_lifecycle_fixture.py", 1),
    )
    registry = replace(registry, state_models={model.semantic_id: model})
    registry.freeze()
    return registry, replace(sidecar, catalog_refs=sidecar.catalog_refs | {MODEL})


def sources_without_io() -> LazySources:
    registry, sidecar = lifecycle_registry(Path("/nonexistent/lifecycle.duckdb"))
    return make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="lifecycle",
        store_id="lifecycle",
    )


def setup_lifecycle(
    project: Path, *, engine: bool = False, event: Callable[[str], None] | None = None
) -> tuple[DatasetRuntime, LazySources, Path]:
    runtime, _, database = setup_event(project, engine=engine, event=event)
    registry, sidecar = lifecycle_registry(database)
    return runtime, runtime.sources(semantic_registry=registry, sidecar=sidecar), database


def history(
    sources: LazySources, *, complete: bool = True, population: PopulationInput | None = None
) -> LogicalLifecycleDataset:
    return sources.lifecycle.replay(
        MODEL,
        window=time_scope(start=START.isoformat(), end=END.isoformat()),
        seed=FromInception(),
        population=population,
        completeness=(
            SourceOriginCompletenessDeclarationV1(
                inputs=(ref.event("sales.started"), ref.event("sales.finished")),
                source_origin_ref=ref.datasource("warehouse"),
                complete_through=END,
                rationale="Fixture source-origin completeness.",
            ),
        )
        if complete
        else (),
    )
