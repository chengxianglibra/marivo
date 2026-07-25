"""Focused regression tests for datasource live help rendering."""

from __future__ import annotations

import inspect

import pytest

import marivo.datasource as md
from marivo._help.model import MarivoHelpTargetError
from marivo.datasource.errors import DatasourceHelpTargetError
from marivo.introspection.live.model import SURFACE_LIMITS
from tests.shared_fixtures import rendered_help

_DATASOURCE_IMPORT = "import marivo.datasource as md"
_SEMANTIC_IMPORT = "import marivo.semantic as ms"


def _text(target: object | None = None) -> str:
    return rendered_help(target, owner="datasource")


def test_datasource_root_help_lists_live_capabilities_and_bounded_effects() -> None:
    text = _text()

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
        (
            "raw_sql",
            (
                "potentially_unbounded_read",
                "requires_positive_row_guard",
                "semantic-gap escape",
                "cannot become canonical metrics",
            ),
        ),
    ],
)
def test_focused_help_renders_live_contract(target: str, needles: tuple[str, ...]) -> None:
    text = _text(target)
    for needle in needles:
        assert needle in text
    assert _DATASOURCE_IMPORT in text
    assert (_SEMANTIC_IMPORT in text) == (
        target in {"inspect", "raw_sql", "SourceInspection.sample"}
    )
    assert text.count("\n") + 1 <= SURFACE_LIMITS.focused_help_max_lines
    assert len(text) <= SURFACE_LIMITS.focused_help_max_codepoints


def test_inspection_help_teaches_result_reads_from_an_assigned_value() -> None:
    inspect_text = _text("inspect")
    partitions_text = _text("SourceInspection.partitions")
    sample_text = _text("SourceInspection.sample")

    assert "inspection = md.inspect(" in inspect_text
    assert "inspection.show()" in inspect_text
    assert "inspection.partitions().show()" in partitions_text
    assert "inspection = md.inspect(" in sample_text
    assert "inspection.sample(" in sample_text
    assert ").show()" in sample_text


def test_authoring_is_a_generated_datasource_state_boundary() -> None:
    text = _text("authoring")

    assert "datasource.declared" in text
    assert "evidence.projected" in text
    assert _DATASOURCE_IMPORT in text
    assert _SEMANTIC_IMPORT not in text
    assert 'marivo.help("semantic.authoring")' in text
    assert "1." not in text


def test_consumed_type_help_uses_only_registered_public_contract() -> None:
    text = _text(md.SourceInspection)

    assert "Producers: inspect" in text
    assert "Public fields:" in text
    assert "Public consumption:" in text
    assert _DATASOURCE_IMPORT in text
    assert _SEMANTIC_IMPORT not in text
    assert "Signature:" not in text
    assert "_" not in "\n".join(line for line in text.splitlines() if line.strip().startswith("_"))


def test_help_accepts_registered_receiver_path_and_rejects_private_names() -> None:
    assert _text("snapshot.entity").startswith("DiscoverySnapshot.entity\n")

    for target in ("ai_context", "datasource_name_global", "_surface"):
        with pytest.raises(MarivoHelpTargetError):
            _text(target)


def test_help_keeps_public_callable_signatures_authoritative() -> None:
    for callable_target in (md.duckdb, md.partition, md.SourceInspection.sample):
        assert str(inspect.signature(callable_target)) in _text(callable_target)


def test_error_help_includes_only_the_datasource_import() -> None:
    text = _text(DatasourceHelpTargetError)

    assert _DATASOURCE_IMPORT in text
    assert _SEMANTIC_IMPORT not in text


def test_all_focused_help_defines_every_alias_it_uses() -> None:
    from marivo.datasource._capabilities.registry import REGISTRY

    for target in REGISTRY.canonical_ids():
        text = _text(target)
        assert _DATASOURCE_IMPORT in text
        assert (_SEMANTIC_IMPORT in text) == ("ms." in text), target
