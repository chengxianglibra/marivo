"""Small governed Lifecycle model and source setup for private acceptance."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

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
from tests.event_semantic_fixtures import make_event_registry

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
