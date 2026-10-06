"""Independent retirement boundaries for the unified R7 execution surface."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.materialization.contracts import canonical_json
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_protocol import DESCRIPTOR, decode

RETIRED_MODULES = (
    "domains.event",
    "domains.event_reducers",
    "domains.contracts",
    "compiler.event",
    "compiler.event_sources",
    "compiler.event_time",
    "compiler.event_axes",
    "compiler.event_reducers",
    "compiler.event_continuation",
    "materialization.event_codec",
    "materialization.event_publication",
    "materialization.event_reducer_codec",
    "materialization.event_reducer_publication",
    "materialization.event_bundle",
    "materialization.postgres_event_sql",
    "materialization.trino_event_sql",
    "materialization.clickhouse_event_sql",
    "domains.funnel_delta",
    "domains.funnel_attribution",
    "domains.funnel_registry",
    "domains.event_comparison",
    "domains.event_attribution",
    "domains.event_comparison_values",
    "domains.event_attribution_values",
    "compiler.event_comparison",
    "compiler.event_attribution",
    "materialization.event_comparison_codec",
    "materialization.event_comparison_publication",
    "domains.lifecycle",
    "domains.lifecycle_reducers",
    "compiler.lifecycle",
    "compiler.lifecycle_array",
    "compiler.lifecycle_reducers",
    "materialization.lifecycle_codec",
    "materialization.lifecycle_publication",
    "materialization.lifecycle_reducer_codec",
    "materialization.lifecycle_reducer_publication",
    "materialization.lifecycle_bundle",
    "materialization.lifecycle_integrity",
)


def test_retired_modules_have_no_file_import_or_package_consumer() -> None:
    root = Path(mv.__file__).resolve().parent
    modules = {"marivo.analysis." + name for name in RETIRED_MODULES}
    for name in RETIRED_MODULES:
        assert not root.joinpath(*name.split(".")).with_suffix(".py").exists()
        assert importlib.util.find_spec("marivo.analysis." + name) is None
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in modules, (path, node.lineno)
            elif isinstance(node, ast.Import):
                assert not modules.intersection(alias.name for alias in node.names)


def test_retired_families_have_no_public_export_facade_or_registration() -> None:
    public_names = mv.__all__
    assert isinstance(public_names, list)
    for name in (
        "LogicalEventDataset",
        "MaterializedEventDataset",
        "LogicalLifecycleDataset",
        "MaterializedLifecycleDataset",
    ):
        assert name not in public_names
        assert not hasattr(mv, name)
    from marivo.analysis.observation import contracts

    for name in ("make_family_registry", "producer_contract", "owner_of"):
        assert not hasattr(contracts, name)


@pytest.mark.parametrize(
    "producer",
    ["session.events.match", "event.funnel", "event.time_to_event", "event.select_subjects"],
)
def test_retired_producer_registration_rejects(producer: str) -> None:
    with pytest.raises(MaterializationError):
        decode(canonical_json({"producer": producer}), DESCRIPTOR)


@pytest.mark.parametrize(
    "kind",
    ["event/journey@v1", "event/funnel@v1", "event/time-to-event@v1", "lifecycle/history@v1"],
)
def test_retired_semantics_cannot_enter_current_descriptor_decode(kind: str) -> None:
    with pytest.raises(MaterializationError):
        decode(canonical_json({"kind": kind}), DESCRIPTOR)
