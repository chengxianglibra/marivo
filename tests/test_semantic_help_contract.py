"""Contract tests for the semantic live help surface.

The old ``_surface()`` / ``render()`` JSON infrastructure was removed in
Phase 3.  These tests now exercise the live ``ms.help_text()`` surface
directly, asserting that bounded text is returned with the expected content
for each capability target.
"""

from __future__ import annotations

import pytest

import marivo.semantic as ms
from marivo.introspection.live.model import SURFACE_LIMITS

_DATASOURCE_IMPORT = "import marivo.datasource as md"
_SEMANTIC_IMPORT = "import marivo.semantic as ms"

# ---------------------------------------------------------------------------
# Root help
# ---------------------------------------------------------------------------


def test_root_help_contains_surface_label_and_capabilities_section() -> None:
    text = ms.help_text()
    assert "marivo.semantic" in text
    assert "Capabilities:" in text
    assert _SEMANTIC_IMPORT in text
    assert _DATASOURCE_IMPORT not in text


def test_root_help_within_line_budget() -> None:
    text = ms.help_text()
    assert text.count("\n") + 1 <= SURFACE_LIMITS.root_help_max_lines
    assert len(text) <= SURFACE_LIMITS.root_help_max_codepoints


# ---------------------------------------------------------------------------
# Focused capability help
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "domain",
        "entity",
        "dimension",
        "dimension_column",
        "time_dimension",
        "time_dimension_column",
        "measure",
        "measure_column",
        "aggregate",
        "count",
        "ratio",
        "weighted_average",
        "linear",
        "relationship",
    ],
)
def test_help_text_for_capability_contains_name_and_entrypoint(target: str) -> None:
    text = ms.help_text(target)
    assert target in text
    assert f"ms.{target}" in text
    assert _SEMANTIC_IMPORT in text
    assert (_DATASOURCE_IMPORT in text) == ("md." in text)


def test_help_text_entity_contains_signature_and_example() -> None:
    text = ms.help_text("entity")
    assert "ms.entity" in text
    assert "Signature:" in text
    assert "Example:" in text


def test_help_text_metric_contains_entrypoint_and_variants() -> None:
    text = ms.help_text("metric")
    assert "ms.metric" in text
    assert "Signature:" in text


def test_help_text_measure_mentions_additivity() -> None:
    text = ms.help_text("measure")
    assert "additivity" in text


def test_help_text_cumulative_contains_constructor() -> None:
    text = ms.help_text("cumulative")
    assert "ms.cumulative" in text


def test_help_text_relationship_contains_keys_parameter() -> None:
    text = ms.help_text("relationship")
    assert "keys" in text


def test_help_text_ratio_contains_numerator_and_denominator() -> None:
    text = ms.help_text("ratio")
    assert "numerator" in text
    assert "denominator" in text


def test_help_text_linear_contains_add_and_subtract() -> None:
    text = ms.help_text("linear")
    assert "add" in text
    assert "subtract" in text


def test_help_text_count_contains_entity_parameter() -> None:
    text = ms.help_text("count")
    assert "entity" in text


def test_help_text_aggregate_contains_measure_parameter() -> None:
    text = ms.help_text("aggregate")
    assert "measure" in text


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("relationship", "keys=[ms.join_on(order_customer_id, customer_id)]"),
        ("join_on", "ms.join_on(order_customer_id, customer_id)"),
        ("snapshot", "partition_field=snapshot_date, grain='day'"),
        ("validity", "valid_from=valid_from, valid_to=valid_to"),
        ("semi_additive", "over=snapshot_date, fold='last'"),
        ("verify_object", "catalog.verify_object(revenue.ref)"),
        (
            "preview",
            "catalog.preview(refs=report.preview_required_refs, using=orders_snapshot)",
        ),
    ],
)
def test_help_examples_use_typed_refs_and_required_evidence(target: str, expected: str) -> None:
    assert expected in ms.help_text(target)


def test_time_dimension_column_help_inlines_parse_selection() -> None:
    text = ms.help_text("time_dimension_column")
    assert "parse=ms.strptime('%Y%m%d')" in text
    assert "string/integer columns require ms.strptime(...)" in text
    assert "hour-only columns require ms.hour_prefix(...)" in text
    assert "naive datetime/timestamp columns require an explicit timezone-bearing parse" in text


# ---------------------------------------------------------------------------
# Type help
# ---------------------------------------------------------------------------


def test_help_text_semantic_catalog_type() -> None:
    text = ms.help_text(ms.SemanticCatalog)
    assert "SemanticCatalog" in text
    assert _SEMANTIC_IMPORT in text
    assert _DATASOURCE_IMPORT not in text


def test_help_text_metric_type_distinguishes_inspection_display_and_continuation() -> None:
    text = ms.help_text(ms.Metric)

    assert ".details() for structured semantic metadata" in text
    assert ".details().show() for bounded readable detail" in text
    assert ".show() prints the same bounded card returned by .render()" in text
    assert (
        ".contract() only exposes mechanically executable verify, preview, and readiness actions"
        in text
    )


def test_help_text_verify_result_type() -> None:
    text = ms.help_text(ms.VerifyResult)
    assert "VerifyResult" in text


def test_help_text_readiness_report_type() -> None:
    text = ms.help_text(ms.ReadinessReport)
    assert "ReadinessReport" in text


# ---------------------------------------------------------------------------
# Error type help
# ---------------------------------------------------------------------------


def test_help_text_semantic_load_error_type() -> None:
    from marivo.semantic.errors import SemanticLoadError

    text = ms.help_text(SemanticLoadError)
    assert "SemanticLoadError" in text
    assert _SEMANTIC_IMPORT in text
    assert _DATASOURCE_IMPORT not in text


def test_help_text_semantic_decorator_error_type() -> None:
    from marivo.semantic.errors import SemanticDecoratorError

    text = ms.help_text(SemanticDecoratorError)
    assert "SemanticDecoratorError" in text


# ---------------------------------------------------------------------------
# Authoring topic
# ---------------------------------------------------------------------------


def test_help_lists_authoring_topic() -> None:
    text = ms.help_text()
    assert "authoring" in text


def test_authoring_topic_renders_semantic_stages_and_handoff() -> None:
    text = ms.help_text("authoring")
    assert "authoring" in text
    assert "browse" in text
    assert "verify" in text
    assert "readiness" in text
    assert "handoff" in text
    assert "semantic.ready" in text
    assert "analysis handoff" in text
    assert _DATASOURCE_IMPORT in text
    assert _SEMANTIC_IMPORT in text


# ---------------------------------------------------------------------------
# Bounded output
# ---------------------------------------------------------------------------


def test_help_text_for_target_is_within_codepoint_budget() -> None:
    for target in ("entity", "metric", "measure", "relationship", "authoring"):
        text = ms.help_text(target)
        assert len(text) <= SURFACE_LIMITS.focused_help_max_codepoints, (
            f"help_text({target!r}) exceeds codepoint budget"
        )


def test_all_focused_help_defines_every_alias_it_uses() -> None:
    from marivo.semantic._capabilities.registry import REGISTRY

    for target in REGISTRY.canonical_ids():
        text = ms.help_text(target)
        assert _SEMANTIC_IMPORT in text
        assert (_DATASOURCE_IMPORT in text) == ("md." in text), target


# ---------------------------------------------------------------------------
# Repair and discovery affordances
# ---------------------------------------------------------------------------


def test_help_text_unknown_target_raises_with_repair() -> None:
    from marivo.semantic.errors import SemanticHelpTargetError

    with pytest.raises(SemanticHelpTargetError) as exc_info:
        ms.help_text("nonexistent_target")
    assert exc_info.value.repair is not None


def test_help_text_for_entity_mentions_consumers() -> None:
    text = ms.help_text("entity")
    assert "Consumers:" in text
