"""Contract tests for the semantic live help surface.

The old ``_surface()`` / ``render()`` JSON infrastructure was removed in
These tests exercise the private semantic renderer through the unified router,
asserting bounded text with the expected native descriptor content.
"""

from __future__ import annotations

import pytest

import marivo.semantic as ms
from marivo._help.model import MarivoHelpTargetError
from marivo.introspection.live.model import SURFACE_LIMITS
from tests.shared_fixtures import rendered_help

_DATASOURCE_IMPORT = "import marivo.datasource as md"
_SEMANTIC_IMPORT = "import marivo.semantic as ms"


def _text(target: object | None = None) -> str:
    return rendered_help(target, owner="semantic")


# ---------------------------------------------------------------------------
# Root help
# ---------------------------------------------------------------------------


def test_root_help_contains_surface_label_and_capabilities_section() -> None:
    text = _text()
    assert "marivo.semantic" in text
    assert "Capabilities:" in text
    assert _SEMANTIC_IMPORT in text
    assert _DATASOURCE_IMPORT not in text


def test_root_help_within_line_budget() -> None:
    text = _text()
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
        "weighted_mean",
        "linear",
        "relationship",
    ],
)
def test_help_text_for_capability_contains_name_and_entrypoint(target: str) -> None:
    text = _text(target)
    assert target in text
    assert f"ms.{target}" in text
    assert _SEMANTIC_IMPORT in text
    assert (_DATASOURCE_IMPORT in text) == ("md." in text)


def test_help_text_entity_contains_signature_and_example() -> None:
    text = _text("entity")
    assert "ms.entity" in text
    assert "Signature:" in text
    assert "Example:" in text
    assert "Declaration fragment; execute only when ms.load()" in text
    assert "Source path: models/semantic/<domain>/<module>.py" in text
    assert 'marivo.help("semantic.domain")' in text
    assert 'marivo.help("datasource.authoring")' in text
    assert "Business judgments before authoring:" in text
    assert "entry = catalog.entities.get('<domain>.<entity>')" in text
    assert "entry.show()" in text
    assert "entry.contract().show()" in text


def test_domain_help_requires_accountable_owner_without_inventing_one() -> None:
    text = _text("domain")

    assert "Source path: models/semantic/<domain>/_domain.py" in text
    assert "accountable_owner" in text
    assert "Mina Zhang" not in text
    assert "entry = catalog.domains.get('<domain>')" in text


def test_help_text_metric_contains_entrypoint_and_variants() -> None:
    text = _text("metric")
    assert "ms.metric" in text
    assert "Signature:" in text


def test_help_text_measure_mentions_additivity() -> None:
    text = _text("measure")
    assert "additivity" in text


def test_help_text_cumulative_contains_constructor() -> None:
    text = _text("cumulative")
    assert "ms.cumulative" in text


def test_help_text_relationship_contains_keys_parameter() -> None:
    text = _text("relationship")
    assert "keys" in text


def test_help_text_ratio_contains_numerator_and_denominator() -> None:
    text = _text("ratio")
    assert "numerator" in text
    assert "denominator" in text


def test_help_text_linear_contains_add_and_subtract() -> None:
    text = _text("linear")
    assert "add" in text
    assert "subtract" in text


def test_help_text_count_contains_entity_parameter() -> None:
    text = _text("count")
    assert "entity" in text


def test_help_text_aggregate_contains_measure_parameter() -> None:
    text = _text("aggregate")
    assert "measure" in text


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("relationship", "keys=[ms.join_on(order_customer_id, customer_id)]"),
        ("join_on", "ms.join_on(order_customer_id, customer_id)"),
        ("snapshot", "partition_field=snapshot_date, grain='day'"),
        ("validity", "valid_from=valid_from, valid_to=valid_to"),
        ("semi_additive", "over=snapshot_date, fold='last'"),
        ("verify", "catalog.verify(revenue)"),
        (
            "preview",
            "catalog.preview(revenue, using=orders_snapshot)",
        ),
    ],
)
def test_help_examples_use_typed_inputs_and_required_evidence(
    target: str,
    expected: str,
) -> None:
    assert expected in _text(target)


def test_time_dimension_column_help_inlines_parse_selection() -> None:
    text = _text("time_dimension_column")
    assert "parse=ms.strptime('%Y%m%d')" in text
    assert "string/integer columns require ms.strptime(...)" in text
    assert "hour-only columns require ms.hour_prefix(...)" in text
    assert "naive datetime/timestamp columns require an explicit timezone-bearing parse" in text


# ---------------------------------------------------------------------------
# Type help
# ---------------------------------------------------------------------------


def test_help_text_semantic_catalog_type() -> None:
    text = _text(ms.SemanticCatalog)
    assert "SemanticCatalog" in text
    assert _SEMANTIC_IMPORT in text
    assert _DATASOURCE_IMPORT not in text


def test_help_text_metric_type_distinguishes_inspection_display_and_continuation() -> None:
    text = _text(ms.MetricEntry)

    assert ".details() for structured semantic metadata" in text
    assert ".details().show() for bounded readable detail" in text
    assert ".show() prints the same bounded card returned by .render()" in text
    assert ".contract() for mechanically valid next actions" in text


def test_help_text_verify_result_type() -> None:
    text = _text(ms.VerifyResult)
    assert "VerifyResult" in text


def test_help_text_readiness_report_type() -> None:
    text = _text(ms.ReadinessReport)
    assert "ReadinessReport" in text
    assert "analysis_ready_inputs" in text


def test_help_text_readiness_accepts_runtime_metric_expressions() -> None:
    text = _text("readiness")

    assert "Sequence[SemanticInput[SemanticKindTag] | RuntimeMetricExpr]" in text
    assert "_SemanticInput" not in text
    assert "subject: CatalogEntry | Ref | RuntimeMetricExpression" in text
    assert "catalog.readiness(refs=[revenue, runtime_revenue])" in text


# ---------------------------------------------------------------------------
# Error type help
# ---------------------------------------------------------------------------


def test_help_text_semantic_load_error_type() -> None:
    from marivo.semantic.errors import SemanticLoadError

    text = _text(SemanticLoadError)
    assert "SemanticLoadError" in text
    assert _SEMANTIC_IMPORT in text
    assert _DATASOURCE_IMPORT not in text


def test_help_text_semantic_decorator_error_type() -> None:
    from marivo.semantic.errors import SemanticDecoratorError

    text = _text(SemanticDecoratorError)
    assert "SemanticDecoratorError" in text


# ---------------------------------------------------------------------------
# Authoring topic
# ---------------------------------------------------------------------------


def test_help_lists_authoring_topic() -> None:
    text = _text()
    assert "authoring" in text


def test_authoring_topic_renders_semantic_stages_and_handoff() -> None:
    text = _text("authoring")
    assert "authoring" in text
    assert "browse" in text
    assert "verify" in text
    assert "readiness" in text
    assert "handoff" in text
    assert "semantic.ready" in text
    assert "analysis handoff" in text
    assert "models/datasources/<datasource>.py" in text
    assert "models/semantic/<domain>/_domain.py" in text
    assert "entry = catalog.<collection>.get('<canonical identity>')" in text
    assert _DATASOURCE_IMPORT not in text
    assert _SEMANTIC_IMPORT in text


# ---------------------------------------------------------------------------
# Bounded output
# ---------------------------------------------------------------------------


def test_help_text_for_target_is_within_codepoint_budget() -> None:
    for target in ("entity", "metric", "measure", "relationship", "authoring"):
        text = _text(target)
        assert len(text) <= SURFACE_LIMITS.focused_help_max_codepoints, (
            f"help_text({target!r}) exceeds codepoint budget"
        )


def test_all_focused_help_defines_every_alias_it_uses() -> None:
    from marivo.semantic._capabilities.registry import REGISTRY

    for target in REGISTRY.canonical_ids():
        text = _text(target)
        assert _SEMANTIC_IMPORT in text
        assert (_DATASOURCE_IMPORT in text) == ("md." in text), target


# ---------------------------------------------------------------------------
# Repair and discovery affordances
# ---------------------------------------------------------------------------


def test_help_text_unknown_target_raises_with_repair() -> None:
    with pytest.raises(MarivoHelpTargetError) as exc_info:
        _text("nonexistent_target")
    assert exc_info.value.outcome == "unknown"


def test_help_text_for_entity_mentions_consumers() -> None:
    text = _text("entity")
    assert "Consumers:" in text


def test_every_source_authored_constructor_has_placement_and_postcondition() -> None:
    from marivo.semantic._capabilities.registry import REGISTRY

    for canonical_id in REGISTRY._source_contracts:
        text = _text(canonical_id)
        assert "Loader placement:" in text, canonical_id
        assert "Source path: models/semantic/" in text, canonical_id
        assert "Business judgments before authoring:" in text, canonical_id
        assert "Postcondition after saving:" in text, canonical_id
        assert "entry.show()" in text, canonical_id
        assert "entry.contract().show()" in text, canonical_id
