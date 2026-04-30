from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.api.models.compatibility_profile import (
    CompatibilityProfileCreateRequest,
    EntityRelationshipCreateRequest,
)
from app.api.models.dimension import DimensionCreateRequest
from app.api.models.domain import DomainCatalogCreateRequest
from app.api.models.entity import TypedEntityCreateRequest
from app.api.models.metric import TypedMetricCreateRequest
from app.api.models.predicate import PredicateCreateRequest
from app.api.models.process_object import ProcessObjectCreateRequest
from app.api.models.time import TimeCreateRequest

ROOT = Path(__file__).resolve().parents[1]

_JSON_BLOCK_PARSE_EXCEPTIONS = {
    (
        "docs/api/semantic.md",
        "POST /sessions/{session_id}/intents/observe",
    )
}


def _json_blocks(relative_path: str) -> list[dict[str, Any]]:
    text = (ROOT / relative_path).read_text(encoding="utf-8")
    blocks: list[dict[str, Any]] = []
    for match in re.finditer(r"```json\n(.*?)\n```", text, flags=re.DOTALL):
        body = match.group(1)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as error:
            first_line = body.splitlines()[0].strip() if body.splitlines() else ""
            if (relative_path, first_line) in _JSON_BLOCK_PARSE_EXCEPTIONS:
                continue
            raise AssertionError(
                f"Invalid JSON block in {relative_path} near line "
                f"{text.count(chr(10), 0, match.start()) + error.lineno}: {error.msg}"
            ) from error
        if isinstance(payload, dict):
            blocks.append(payload)
    return blocks


def _find_block(relative_path: str, key: str, value: str) -> dict[str, Any]:
    for block in _json_blocks(relative_path):
        if _contains_pair(block, key, value):
            return block
    raise AssertionError(f"Could not find JSON block with {key}={value} in {relative_path}")


def _contains_pair(value: Any, key: str, expected: str) -> bool:
    if isinstance(value, dict):
        if value.get(key) == expected:
            return True
        return any(_contains_pair(item, key, expected) for item in value.values())
    if isinstance(value, list):
        return any(_contains_pair(item, key, expected) for item in value)
    return False


@pytest.mark.parametrize(
    ("key", "value", "model"),
    [
        ("domain_ref", "domain.growth", DomainCatalogCreateRequest),
        ("entity_ref", "entity.user", TypedEntityCreateRequest),
        ("time_ref", "time.signup_at", TimeCreateRequest),
        ("dimension_ref", "dimension.country", DimensionCreateRequest),
        ("predicate_ref", "predicate.active_user", PredicateCreateRequest),
        ("metric_ref", "metric.active_users", TypedMetricCreateRequest),
        ("metric_ref", "metric.signup_conversion_rate", TypedMetricCreateRequest),
        ("process_ref", "process.signup_cohort", ProcessObjectCreateRequest),
        ("relationship_ref", "relationship.exposure_to_signup", EntityRelationshipCreateRequest),
        (
            "profile_ref",
            "compiler_profile.signup_conversion_requirement",
            CompatibilityProfileCreateRequest,
        ),
    ],
)
def test_docs_api_semantic_walkthrough_payloads_validate(key, value, model):
    payload = _find_block("docs/api/semantic.md", key, value)

    model.model_validate(payload)


@pytest.mark.parametrize(
    ("key", "value", "model"),
    [
        ("time_ref", "time.event_date", TimeCreateRequest),
        ("dimension_ref", "dimension.country", DimensionCreateRequest),
        ("entity_ref", "entity.user", TypedEntityCreateRequest),
        ("metric_ref", "metric.daily_active_users", TypedMetricCreateRequest),
        ("metric_ref", "metric.conversion_rate", TypedMetricCreateRequest),
    ],
)
def test_marivo_skill_payload_snippets_validate(key, value, model):
    payload = _find_block("marivo-skill/marivo/references/payload-cheatsheet.md", key, value)

    model.model_validate(payload)


@pytest.mark.parametrize(
    ("key", "value", "model"),
    [
        ("entity_ref", "entity.user", TypedEntityCreateRequest),
        ("metric_ref", "metric.watch_time", TypedMetricCreateRequest),
    ],
)
def test_marivo_mcp_semantic_payload_snippets_validate(key, value, model):
    payload = _find_block("marivo-mcp/README.md", key, value)

    model.model_validate(payload)


def test_docs_payload_snippets_reject_legacy_physical_grounding_on_non_entity_objects():
    checked = [
        _find_block("docs/api/semantic.md", "metric_ref", "metric.active_users"),
        _find_block("docs/api/semantic.md", "metric_ref", "metric.signup_conversion_rate"),
        _find_block("docs/api/semantic.md", "process_ref", "process.signup_cohort"),
        _find_block(
            "marivo-skill/marivo/references/payload-cheatsheet.md",
            "metric_ref",
            "metric.daily_active_users",
        ),
        _find_block("marivo-mcp/README.md", "metric_ref", "metric.watch_time"),
    ]

    forbidden = {"binding", "carrier_bindings", "field_bindings", "physical_column"}
    for payload in checked:
        assert not _contains_any_key(payload, forbidden)


def test_docs_json_blocks_are_parseable_except_explicit_http_examples():
    for relative_path in (
        "docs/api/semantic.md",
        "marivo-skill/marivo/references/payload-cheatsheet.md",
        "marivo-mcp/README.md",
    ):
        assert _json_blocks(relative_path)


def _contains_any_key(value: Any, keys: set[str]) -> bool:
    if isinstance(value, dict):
        if any(key in keys for key in value):
            return True
        return any(_contains_any_key(item, keys) for item in value.values())
    if isinstance(value, list):
        return any(_contains_any_key(item, keys) for item in value)
    return False
