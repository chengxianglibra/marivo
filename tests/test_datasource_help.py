"""Focused regression tests for datasource live help rendering."""

from __future__ import annotations

import inspect

import pytest

import marivo.datasource as md
from marivo.datasource.errors import DatasourceHelpTargetError
from marivo.introspection.live.model import SURFACE_LIMITS

_DATASOURCE_IMPORT = "import marivo.datasource as md"
_SEMANTIC_IMPORT = "import marivo.semantic as ms"


def test_datasource_root_help_lists_live_capabilities_and_bounded_effects() -> None:
    text = md.help_text()

    for target in ("inspect", "SourceInspection.sample", "raw_sql", "partition", "unpruned"):
        assert target in text
    assert "output:" in text
    assert "effects:" in text
    assert _DATASOURCE_IMPORT in text
    assert _SEMANTIC_IMPORT not in text
    assert text.count("\n") + 1 <= SURFACE_LIMITS.root_help_max_lines
    assert len(text) <= SURFACE_LIMITS.root_help_max_codepoints


@pytest.mark.parametrize(
    ("target", "needles"),
    [
        ("inspect", ("Entrypoint: md.inspect", "Signature:", "Output family: SourceInspection")),
        ("SourceInspection.sample", ("Required state:", "Effects:", "Example:")),
        ("raw_sql", ("potentially_unbounded_read", "requires_positive_row_guard")),
    ],
)
def test_focused_help_renders_live_contract(target: str, needles: tuple[str, ...]) -> None:
    text = md.help_text(target)
    for needle in needles:
        assert needle in text
    assert _DATASOURCE_IMPORT in text
    assert _SEMANTIC_IMPORT not in text
    assert text.count("\n") + 1 <= SURFACE_LIMITS.focused_help_max_lines
    assert len(text) <= SURFACE_LIMITS.focused_help_max_codepoints


def test_authoring_is_a_generated_datasource_state_boundary() -> None:
    text = md.help_text("authoring")

    assert "datasource.declared" in text
    assert "evidence.projected" in text
    assert _DATASOURCE_IMPORT in text
    assert _SEMANTIC_IMPORT in text
    assert 'ms.help("authoring")' in text
    assert "1." not in text


def test_consumed_type_help_uses_only_registered_public_contract() -> None:
    text = md.help_text(md.SourceInspection)

    assert "Producers: inspect" in text
    assert "Public fields:" in text
    assert "Public consumption:" in text
    assert _DATASOURCE_IMPORT in text
    assert _SEMANTIC_IMPORT not in text
    assert "Signature:" not in text
    assert "_" not in "\n".join(line for line in text.splitlines() if line.strip().startswith("_"))


def test_help_rejects_legacy_topics_and_private_names() -> None:
    for target in ("snapshot.entity", "ai_context", "datasource_name_global", "_surface"):
        with pytest.raises(DatasourceHelpTargetError):
            md.help_text(target)


def test_help_keeps_public_callable_signatures_authoritative() -> None:
    for callable_target in (md.duckdb, md.partition, md.SourceInspection.sample):
        assert str(inspect.signature(callable_target)) in md.help_text(callable_target)


def test_error_help_includes_only_the_datasource_import() -> None:
    text = md.help_text(DatasourceHelpTargetError)

    assert _DATASOURCE_IMPORT in text
    assert _SEMANTIC_IMPORT not in text


def test_all_focused_help_defines_every_alias_it_uses() -> None:
    from marivo.datasource._capabilities.registry import REGISTRY

    for target in REGISTRY.canonical_ids():
        text = md.help_text(target)
        assert _DATASOURCE_IMPORT in text
        assert (_SEMANTIC_IMPORT in text) == ("ms." in text), target
