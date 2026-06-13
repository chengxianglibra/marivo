"""Guard: skills must not re-transcribe contract the library can emit.

Drift-prone contract (public dataclass field tables, error catalogs) belongs in
help()/structured errors, not in skill markdown. This test scans the skill
markdown for such transcription. Deliberate mentions are recorded in
``_ALLOWLIST`` and reviewed there, never silenced by weakening the heuristic.

See docs/superpowers/specs/2026-06-13-skill-library-surface-coordination-design.md.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import marivo.analysis as ma
import marivo.datasource as md
import marivo.semantic as ms

_SKILLS_ROOT = Path(__file__).resolve().parent.parent / "marivo-skills"

# (relative_md_path, type_or_marker) pairs that are intentionally allowed.
_ALLOWLIST: frozenset[tuple[str, str]] = frozenset()

# A field table is "transcribed" when this many of a public type's field names
# appear as first-column cells of a single markdown table.
_FIELD_MATCH_THRESHOLD = 4
# An error catalog is "transcribed" when this many public exception names appear
# in one markdown file.
_ERROR_MATCH_THRESHOLD = 4


def _markdown_files() -> list[Path]:
    return sorted(p for p in _SKILLS_ROOT.rglob("*.md"))


def _public_dataclasses() -> dict[str, frozenset[str]]:
    """Map public dataclass name -> its field names, across the three surfaces."""

    names: dict[str, frozenset[str]] = {}
    for module in (ms, md, ma):
        for symbol in getattr(module, "__all__", ()):
            obj = getattr(module, symbol, None)
            if isinstance(obj, type) and dataclasses.is_dataclass(obj):
                names[symbol] = frozenset(f.name for f in dataclasses.fields(obj))
    return names


def _public_error_names() -> frozenset[str]:
    errors_mod = getattr(ma, "errors", None)
    names: set[str] = set()
    for module in (errors_mod, getattr(ms, "errors", None), getattr(md, "errors", None)):
        if module is None:
            continue
        for symbol in getattr(module, "__all__", ()):
            if symbol.endswith("Error"):
                names.add(symbol)
    return frozenset(names)


def _table_first_column_tokens(text: str) -> list[set[str]]:
    """Return, per contiguous markdown table, the set of first-column tokens.

    A first-column token is the first ``|``-delimited cell, stripped of code
    backticks and surrounding whitespace. Separator rows (---) are skipped.
    """

    tables: list[set[str]] = []
    current: set[str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not cells:
                continue
            first = cells[0].strip("` ").strip()
            if set(first) <= {"-", ":"}:  # separator row
                continue
            if current is None:
                current = set()
            if first:
                current.add(first)
        else:
            if current is not None:
                tables.append(current)
                current = None
    if current is not None:
        tables.append(current)
    return tables


def test_no_public_dataclass_field_tables_in_skills() -> None:
    field_map = _public_dataclasses()
    violations: list[str] = []
    for path in _markdown_files():
        rel = str(path.relative_to(_SKILLS_ROOT))
        tables = _table_first_column_tokens(path.read_text(encoding="utf-8"))
        for type_name, field_names in field_map.items():
            if (rel, type_name) in _ALLOWLIST:
                continue
            for tokens in tables:
                overlap = tokens & field_names
                if len(overlap) >= _FIELD_MATCH_THRESHOLD:
                    violations.append(
                        f"{rel}: table restates {len(overlap)} fields of {type_name} "
                        f"({sorted(overlap)}) -- point to help('{type_name}') instead"
                    )
    assert not violations, "Skill field-table transcription detected:\n" + "\n".join(violations)


def test_no_error_catalog_in_skills() -> None:
    # Target catalog *tables* keyed by error name, not prose that names errors
    # during recovery guidance (pitfalls.md legitimately discusses errors).
    error_names = _public_error_names()
    assert error_names, "expected to discover public *Error names from the surfaces"
    violations: list[str] = []
    for path in _markdown_files():
        rel = str(path.relative_to(_SKILLS_ROOT))
        if (rel, "ERROR_CATALOG") in _ALLOWLIST:
            continue
        tables = _table_first_column_tokens(path.read_text(encoding="utf-8"))
        for tokens in tables:
            present = tokens & error_names
            if len(present) >= _ERROR_MATCH_THRESHOLD:
                violations.append(
                    f"{rel}: a table catalogs {len(present)} public error types "
                    f"({sorted(present)}) -- structured errors teach the fix at raise "
                    f"time; do not catalog them in a table"
                )
    assert not violations, "Skill error-catalog transcription detected:\n" + "\n".join(violations)


def test_brief_fields_carry_descriptions_for_help() -> None:
    # The library must be able to emit the gloss the skills no longer carry.
    brief_names = [
        "DomainBrief",
        "EntityBrief",
        "DimensionBrief",
        "TimeDimensionBrief",
        "MetricBrief",
        "RelationshipBrief",
        "CrossEntityMetricBrief",
        "DerivedMetricBrief",
    ]
    missing: list[str] = []
    for name in brief_names:
        cls = getattr(ms, name)
        for f in dataclasses.fields(cls):
            if not f.metadata.get("description"):
                missing.append(f"{name}.{f.name}")
    assert not missing, f"Brief fields without a help-visible description: {missing}"


# --- Detector unit tests: prove the heuristics actually catch transcription ---


def test_field_table_detector_flags_transcription() -> None:
    field_map = {"WidgetBrief": frozenset({"alpha", "beta", "gamma", "delta"})}
    text = (
        "| Field | Type |\n"
        "| --- | --- |\n"
        "| alpha | str |\n"
        "| beta | int |\n"
        "| gamma | bool |\n"
        "| delta | float |\n"
    )
    tables = _table_first_column_tokens(text)
    hit = any(len(tokens & field_map["WidgetBrief"]) >= _FIELD_MATCH_THRESHOLD for tokens in tables)
    assert hit


def test_field_table_detector_ignores_unrelated_table() -> None:
    field_map = {"WidgetBrief": frozenset({"alpha", "beta", "gamma", "delta"})}
    text = "| Status | Action |\n| --- | --- |\n| blocked | fix it |\n| sufficient | author |\n"
    tables = _table_first_column_tokens(text)
    hit = any(len(tokens & field_map["WidgetBrief"]) >= _FIELD_MATCH_THRESHOLD for tokens in tables)
    assert not hit
