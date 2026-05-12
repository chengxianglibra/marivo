from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from marivo.contracts.ids import UserId
from marivo.contracts.semantic import SemanticModel
from tests.contracts.contract_cases import ContractCase


def _run_missing_selector(adapter, _: Path) -> None:
    selector = SimpleNamespace(model_id=None, name="absent")
    assert adapter.get(selector) is None


def _expect_roundtrip(adapter, _: Path) -> None:
    model = SemanticModel(
        name="roundtrip",
        osi_model={
            "name": "roundtrip",
            "datasets": [{"name": "orders", "source": "analytics.orders"}],
        },
    )
    model_id = adapter.save(model, actor=UserId("owner1"))
    assert model_id is not None
    selector = SimpleNamespace(model_id=None, name="roundtrip")
    result = adapter.get(selector)
    assert result is not None
    assert result.name == "roundtrip"


def _expect_summary_fields(adapter, _: Path) -> None:
    model = SemanticModel(
        name="summary",
        description="summary model",
        visibility="public",
        owner=UserId("owner1"),
    )
    adapter.save(model, actor=UserId("owner1"))
    results = adapter.list(
        SimpleNamespace(
            owner=UserId("owner1"),
            visibility=None,
            include_public=True,
            include_private=False,
        )
    )
    assert len(results) >= 1
    names = {summary.name for summary in results}
    assert "summary" in names
    summary = next(summary for summary in results if summary.name == "summary")
    assert summary.description == "summary model"
    assert summary.visibility == "public"
    assert summary.owner == UserId("owner1")


MODEL_STORE_CASES = [
    ContractCase(name="missing_selector_returns_none", run=_run_missing_selector),
    ContractCase(name="save_then_get_roundtrip", run=_expect_roundtrip),
    ContractCase(name="list_returns_summary_fields", run=_expect_summary_fields),
]
