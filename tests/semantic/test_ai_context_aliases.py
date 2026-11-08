"""Author-declared search aliases survive complete immutable definition capture."""

from __future__ import annotations

import json
from collections import UserList
from collections.abc import Callable, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import cast

import pytest

import marivo.semantic as ms
from marivo._definition.deferred import inspect_deferred
from marivo.datasource import store as datasource_store
from marivo.datasource.authoring import DuckDBSpec
from marivo.datasource.ir import AiContextIR
from marivo.datasource.typing import AiContextValue
from marivo.semantic._compiled_state import PublishedDeclaration, build_compiled_state
from marivo.semantic.errors import SemanticDecoratorError
from marivo.semantic.reader import SemanticProject

ProjectFactory = Callable[..., SemanticProject]

_DOMAIN = """import marivo.semantic as ms
ms.domain(name='sales', owner='Analytics', ai_context=ms.ai_context(aliases=['Domain alias']))
"""
_DATASOURCE = """import marivo.datasource as md
import marivo.semantic as ms
md.duckdb(name='warehouse', wall_clock_timezone='UTC', path=':memory:', ai_context=ms.ai_context(aliases=['Datasource alias']))
"""
_OBJECTS = "from datetime import date\nimport marivo.datasource as md\nimport marivo.semantic as ms\norders = ms.entity(\n    name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'),\n    primary_key=['id'], ai_context=ms.ai_context(aliases=['Entity alias']),\n)\nidentifier = ms.dimension_column(\n    name='id', entity=orders, column='id', ai_context=ms.ai_context(aliases=['Dimension alias']),\n)\nday = ms.time_dimension_column(\n    name='day', entity=orders, column='day', parse=ms.strptime('%Y-%m-%d'),\n    ai_context=ms.ai_context(aliases=['Time alias']),\n)\nend_day = ms.time_dimension_column(\n    name='end_day', entity=orders, column='end_day', parse=ms.strptime('%Y-%m-%d'),\n)\namount = ms.measure_column(\n    name='amount', entity=orders, column='amount', additivity=ms.additive_all(),\n    ai_context=ms.ai_context(aliases=['Physical measure alias']),\n)\n@ms.measure(ai_context=ms.ai_context(aliases=['Measure alias']))\ndef doubled():\n    return amount * 2\n@ms.metric(ai_context=ms.ai_context(aliases=['Metric alias']))\ndef revenue():\n    return doubled.sum()\nidentity = ms.relationship(\n    name='identity', from_entity=orders, to_entity=orders,\n    keys=[ms.join_on(identifier, identifier)],\n    ai_context=ms.ai_context(aliases=['Relationship alias']),\n)\n"


def _project(factory: ProjectFactory, *, old: str = "", new: str = "") -> SemanticProject:
    files = {
        "sales/_domain.py": _DOMAIN,
        "sales/objects.py": _OBJECTS,
        "datasources/warehouse.py": _DATASOURCE,
    }
    if old:
        files = {path: text.replace(old, new) for path, text in files.items()}
    project = factory(files)
    assert project.errors() == (), tuple(str(error) for error in project.errors())
    return project


def test_aliases_normalize_deduplicate_and_capture_immutable_input() -> None:
    authored = ["  ＧＭＶ  ", "gmv", "销售\t金额", "销售 金额", "Gross   Sales"]
    context = ms.ai_context(aliases=authored)
    authored.append("Later mutation")
    assert context.aliases == ("GMV", "销售 金额", "Gross Sales")
    assert ms.ai_context(aliases=UserList(["Revenue"])).aliases == ("Revenue",)
    assert ms.ai_context().aliases == ms.ai_context(aliases=[]).aliases == ()


@pytest.mark.parametrize(
    "aliases",
    ["Revenue", b"Revenue", {"Revenue"}, {"alias": "Revenue"}, [""], ["  \t"], ["valid", 7]],
)
def test_aliases_reject_nonsequences_and_empty_or_nontext_items(aliases: object) -> None:
    with pytest.raises(SemanticDecoratorError) as caught:
        ms.ai_context(aliases=cast("Sequence[str]", aliases))

    assert caught.value.kind == "invalid_ai_context"
    assert caught.value.location is not None
    assert caught.value.location.file == __file__


@pytest.mark.parametrize("constructor", [AiContextValue, AiContextIR])
def test_direct_context_values_keep_complete_normalized_json_metadata(
    constructor: type[AiContextValue] | type[AiContextIR],
) -> None:
    long_text = "business " * 200
    context = constructor(
        business_definition=long_text,
        guardrails=tuple(f"Guardrail {index}" for index in range(20)),
        aliases=("  ＧＭＶ ", "gmv", long_text),
    )
    payload = json.loads(json.dumps(asdict(context)))

    assert payload == {
        "business_definition": long_text,
        "guardrails": [f"Guardrail {index}" for index in range(20)],
        "aliases": ["GMV", long_text.strip()],
    }
    with pytest.raises(TypeError):
        constructor(aliases=cast("tuple[str, ...]", ["Mutable"]))
    with pytest.raises(TypeError):
        constructor(aliases=cast("tuple[str, ...]", (1,)))
    with pytest.raises(ValueError):
        constructor(aliases=("  ",))


def test_datasource_source_file_round_trip_preserves_alias_only_context(tmp_path: Path) -> None:
    datasource_store.save_one(
        DuckDBSpec(name="warehouse", ai_context=ms.ai_context(aliases=["Warehouse", "数据仓库"])),
        project_root=tmp_path,
    )
    restored = datasource_store.load_one("warehouse", project_root=tmp_path)

    assert restored is not None
    assert restored.ai_context.aliases == ("Warehouse", "数据仓库")
    assert (
        "aliases=['Warehouse', '数据仓库']"
        in datasource_store.datasource_path("warehouse", tmp_path).read_text()
    )


def test_all_context_owners_capture_aliases_in_loaded_definitions(
    semantic_project_factory: ProjectFactory,
) -> None:
    project = _project(semantic_project_factory)
    state = project._compiled_state
    assert state is not None
    expected = {
        "domain:sales": "Domain alias",
        "datasource:warehouse": "Datasource alias",
        "entity:sales.orders": "Entity alias",
        "dimension:sales.orders.id": "Dimension alias",
        "time_dimension:sales.orders.day": "Time alias",
        "measure:sales.orders.amount": "Physical measure alias",
        "measure:sales.orders.doubled": "Measure alias",
        "metric:sales.revenue": "Metric alias",
        "relationship:sales.identity": "Relationship alias",
    }

    definitions = {ref.key: definition for ref, definition in state.definitions.items()}
    for identity, alias in expected.items():
        context = getattr(definitions[identity], "ai_context", None)
        assert isinstance(context, AiContextIR)
        assert context.aliases == (alias,)
        assert json.loads(json.dumps(asdict(context)))["aliases"] == [alias]

    # Rebuilding from the frozen registry exercises the separate direct capture path.
    rebuilt = build_compiled_state(
        registry=state.registry, sidecar=state.sidecar, selected_root_roles=(), filtered_domains=()
    )
    for ref, definition in rebuilt.definitions.items():
        if isinstance(definition, PublishedDeclaration):
            assert definition.ai_context == getattr(state.definitions[ref], "ai_context", None)


@pytest.mark.parametrize(
    "alias", ["Domain alias", "Datasource alias", "Entity alias", "Measure alias", "Metric alias"]
)
def test_alias_changes_invalidate_definition_snapshot_without_new_identity_or_computation(
    semantic_project_factory: ProjectFactory, alias: str
) -> None:
    before = _project(semantic_project_factory)
    after = _project(semantic_project_factory, old=alias, new="A changed search label")
    first, second = before._compiled_state, after._compiled_state
    assert first is not None and second is not None

    assert first.definition_fingerprint != second.definition_fingerprint
    assert set(first.definitions) == set(second.definitions)
    if alias == "Metric alias":
        assert inspect_deferred(first.registry.declared_metrics["sales.revenue"]).fingerprint == (
            inspect_deferred(second.registry.declared_metrics["sales.revenue"]).fingerprint
        )
