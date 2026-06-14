"""Guard: skills must not re-transcribe contract the library can emit.

Drift-prone contract (public dataclass field tables, error catalogs) belongs in
help()/structured errors, not in skill markdown. This test scans the skill
markdown for such transcription. Deliberate mentions are recorded in
``_ALLOWLIST`` and reviewed there, never silenced by weakening the heuristic.

See docs/superpowers/specs/2026-06-13-skill-library-surface-coordination-design.md.
"""

from __future__ import annotations

import dataclasses
import inspect
import re
from pathlib import Path
from types import ModuleType
from typing import get_args

import marivo.analysis as ma
import marivo.analysis.errors as analysis_errors
import marivo.analysis.intents.observe_errors as observe_errors
import marivo.datasource as md
import marivo.datasource.errors as datasource_errors
import marivo.semantic as ms
import marivo.semantic.errors as semantic_errors

_SKILLS_ROOT = Path(__file__).resolve().parent.parent / "marivo/skills"

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
    names: set[str] = set()
    for module in (analysis_errors, semantic_errors, datasource_errors):
        names.update(_public_exception_names(module))
    return frozenset(names)


def _public_error_catalog_tokens() -> frozenset[str]:
    error_names = _public_error_names()
    return error_names | _public_error_kind_tokens(error_names) | _public_structured_error_codes()


def _public_error_kind_tokens(error_names: frozenset[str]) -> frozenset[str]:
    kind_tokens = {name[:-5] if name.endswith("Error") else name for name in error_names}
    kind_tokens.update(kind.value for kind in semantic_errors.ErrorKind)
    return frozenset(kind_tokens)


def _public_exception_names(module: ModuleType) -> set[str]:
    names: set[str] = set()
    for symbol, obj in inspect.getmembers(module, inspect.isclass):
        if symbol.startswith("_"):
            continue
        if issubclass(obj, Exception) and (
            symbol.endswith("Error") or obj.__module__ == module.__name__
        ):
            names.add(symbol)
    return names


def _public_structured_error_codes() -> frozenset[str]:
    return frozenset(
        code for code in get_args(observe_errors.ObserveErrorCode) if isinstance(code, str)
    )


def _table_cells(stripped_line: str) -> list[str] | None:
    if "|" not in stripped_line:
        return None
    cells = [c.strip() for c in stripped_line.strip("|").split("|")]
    if len(cells) < 2:
        return None
    return cells


def _is_separator_row(cells: list[str]) -> bool:
    return all(cell and set(cell) <= {"-", ":"} for cell in cells)


def _first_column_token(cell: str) -> str:
    stripped = cell.strip()
    wrapper_patterns = (
        r"`([^`]+)`",
        r"<code>(.*?)</code>",
        r"\[([^\]]+)\]\([^)]+\)",
        r"\*\*(.*?)\*\*",
        r"\*(.*?)\*",
        r"__(.*?)__",
        r"_(.*?)_",
    )
    previous = None
    while stripped and stripped != previous:
        previous = stripped
        for pattern in wrapper_patterns:
            if match := re.fullmatch(pattern, stripped):
                stripped = match.group(1).strip()
                break
    return re.split(r"\s+-\s+|:\s+|\s+|\(", stripped.strip("` "), maxsplit=1)[0].strip("` ")


def _table_first_column_tokens(text: str) -> list[set[str]]:
    """Return, per contiguous markdown table, the set of first-column tokens.

    A first-column token is the first ``|``-delimited cell, stripped of code
    backticks, common decorations, and surrounding whitespace. Separator rows
    (---) are skipped.
    """

    tables: list[set[str]] = []
    current: set[str] | None = None
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        cells = _table_cells(stripped)
        if cells is None:
            if current is not None:
                tables.append(current)
                current = None
            continue
        if current is None and not stripped.startswith("|"):
            next_cells = _table_cells(lines[index + 1].strip()) if index + 1 < len(lines) else None
            if next_cells is None or not _is_separator_row(next_cells):
                continue
        first = _first_column_token(cells[0])
        if _is_separator_row(cells):
            continue
        if current is None:
            current = set()
        if first:
            current.add(first)
    if current is not None:
        tables.append(current)
    return tables


def _bullet_list_tokens(text: str) -> list[set[str]]:
    """Return, per contiguous markdown bullet list, normalized leading tokens."""

    lists: list[set[str]] = []
    current: set[str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        match = re.match(r"(?:[-*]|\d+[.)])\s+(.+)", stripped)
        if match is None:
            if current is not None:
                lists.append(current)
                current = None
            continue
        first = _first_column_token(match.group(1))
        if current is None:
            current = set()
        if first:
            current.add(first)
    if current is not None:
        lists.append(current)
    return lists


def test_no_public_dataclass_field_tables_in_skills() -> None:
    field_map = _public_dataclasses()
    violations: list[str] = []
    for path in _markdown_files():
        rel = str(path.relative_to(_SKILLS_ROOT))
        tables = _table_first_column_tokens(path.read_text(encoding="utf-8"))
        for type_name, field_names in field_map.items():
            if (rel, type_name) in _ALLOWLIST:
                continue
            threshold = min(_FIELD_MATCH_THRESHOLD, len(field_names))
            for tokens in tables:
                overlap = tokens & field_names
                if len(overlap) >= threshold:
                    violations.append(
                        f"{rel}: table restates {len(overlap)} fields of {type_name} "
                        f"({sorted(overlap)}) -- point to help('{type_name}') instead"
                    )
    assert not violations, "Skill field-table transcription detected:\n" + "\n".join(violations)


def test_no_error_catalog_in_skills() -> None:
    # Target catalog tables/lists keyed by error name or structured code, not
    # prose that names errors during recovery guidance (pitfalls.md legitimately
    # discusses errors).
    error_tokens = _public_error_catalog_tokens()
    assert error_tokens, "expected to discover public error names/codes from the surfaces"
    violations: list[str] = []
    for path in _markdown_files():
        rel = str(path.relative_to(_SKILLS_ROOT))
        if (rel, "ERROR_CATALOG") in _ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        groups = _table_first_column_tokens(text) + _bullet_list_tokens(text)
        for tokens in groups:
            present = tokens & error_tokens
            if len(present) >= _ERROR_MATCH_THRESHOLD:
                violations.append(
                    f"{rel}: a table/list catalogs {len(present)} public error tokens "
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


def test_public_error_names_discovers_analysis_errors_without_all() -> None:
    assert "SemanticKindMismatchError" in _public_error_names()


def test_public_error_catalog_tokens_discovers_observe_codes() -> None:
    tokens = _public_error_catalog_tokens()
    assert "component-axis-unreachable" in tokens
    assert "nested-derived-unsupported" in tokens


def test_public_error_catalog_tokens_discovers_analysis_error_kinds() -> None:
    tokens = _public_error_catalog_tokens()
    assert "MetricNotFound" in tokens
    assert "SemanticKindMismatch" in tokens
    assert "NoBackendFactory" in tokens


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


def test_field_table_detector_flags_complete_small_dataclass() -> None:
    field_map = {"TinyBrief": frozenset({"status", "issues", "questions"})}
    text = (
        "| Field | Type |\n"
        "| --- | --- |\n"
        "| status | str |\n"
        "| issues | list |\n"
        "| questions | list |\n"
    )
    tables = _table_first_column_tokens(text)
    threshold = min(_FIELD_MATCH_THRESHOLD, len(field_map["TinyBrief"]))
    hit = any(len(tokens & field_map["TinyBrief"]) >= threshold for tokens in tables)
    assert hit


def test_field_table_detector_ignores_unrelated_table() -> None:
    field_map = {"WidgetBrief": frozenset({"alpha", "beta", "gamma", "delta"})}
    text = "| Status | Action |\n| --- | --- |\n| blocked | fix it |\n| sufficient | author |\n"
    tables = _table_first_column_tokens(text)
    hit = any(len(tokens & field_map["WidgetBrief"]) >= _FIELD_MATCH_THRESHOLD for tokens in tables)
    assert not hit


def test_table_detector_handles_no_leading_pipe_and_decorated_tokens() -> None:
    text = (
        "Field | Type\n"
        "--- | ---\n"
        "`status` (required) | str\n"
        "`MetricNotFoundError` - observe failure | str\n"
        "**SemanticKindMismatchError** | str\n"
        "*WindowInvalidError* | str\n"
        "__TimezoneInvalidError__ | str\n"
        "_DataTypeMismatchError_ | str\n"
        "[component-axis-unreachable](#x) | str\n"
        "<code>nested-derived-unsupported</code> | str\n"
    )
    tables = _table_first_column_tokens(text)
    assert tables == [
        {
            "Field",
            "status",
            "MetricNotFoundError",
            "SemanticKindMismatchError",
            "WindowInvalidError",
            "TimezoneInvalidError",
            "DataTypeMismatchError",
            "component-axis-unreachable",
            "nested-derived-unsupported",
        }
    ]
    hit = any(
        len(tokens & _public_error_catalog_tokens()) >= _ERROR_MATCH_THRESHOLD for tokens in tables
    )
    assert hit


def test_table_detector_unwraps_linked_code_tokens() -> None:
    text = (
        "Token | Recovery\n"
        "--- | ---\n"
        "[`MetricNotFoundError`](#x) | Load metric metadata\n"
        "[<code>component-axis-unreachable</code>](#x) | Check dimensions\n"
    )
    tables = _table_first_column_tokens(text)
    assert tables == [
        {
            "Token",
            "MetricNotFoundError",
            "component-axis-unreachable",
        }
    ]


def test_table_detector_unwraps_emphasized_code_tokens() -> None:
    text = (
        "Token | Recovery\n"
        "--- | ---\n"
        "**`MetricNotFoundError`** | Load metric metadata\n"
        "*<code>component-axis-unreachable</code>* | Check dimensions\n"
    )
    tables = _table_first_column_tokens(text)
    assert tables == [
        {
            "Token",
            "MetricNotFoundError",
            "component-axis-unreachable",
        }
    ]


def test_table_detector_preserves_bare_hyphenated_error_codes() -> None:
    text = (
        "Code | Recovery\n"
        "--- | ---\n"
        "component-axis-unreachable | Check dimensions\n"
        "`nested-derived-unsupported` - observe failure | Flatten metric\n"
    )
    tables = _table_first_column_tokens(text)
    assert tables == [{"Code", "component-axis-unreachable", "nested-derived-unsupported"}]


def test_error_catalog_detector_flags_transcription() -> None:
    error_names = frozenset(
        {
            "AlphaError",
            "BetaError",
            "GammaError",
            "DeltaError",
        }
    )
    text = (
        "Error | Recovery\n"
        "--- | ---\n"
        "AlphaError | Fix alpha\n"
        "BetaError | Fix beta\n"
        "GammaError | Fix gamma\n"
        "DeltaError | Fix delta\n"
    )
    tables = _table_first_column_tokens(text)
    hit = any(len(tokens & error_names) >= _ERROR_MATCH_THRESHOLD for tokens in tables)
    assert hit


def test_error_code_catalog_detector_flags_transcription() -> None:
    text = (
        "Code | Recovery\n"
        "--- | ---\n"
        "component-axis-unreachable | Check dimensions\n"
        "component-filter-unreachable | Check filters\n"
        "component-version-mismatch | Check versions\n"
        "nested-derived-unsupported | Flatten metric\n"
    )
    tables = _table_first_column_tokens(text)
    hit = any(
        len(tokens & _public_error_catalog_tokens()) >= _ERROR_MATCH_THRESHOLD for tokens in tables
    )
    assert hit


def test_error_code_bullet_list_detector_flags_transcription() -> None:
    text = (
        "- `component-axis-unreachable`: Check dimensions\n"
        "- `component-filter-unreachable`: Check filters\n"
        "- `component-version-mismatch`: Check versions\n"
        "- `nested-derived-unsupported`: Flatten metric\n"
    )
    lists = _bullet_list_tokens(text)
    hit = any(
        len(tokens & _public_error_catalog_tokens()) >= _ERROR_MATCH_THRESHOLD for tokens in lists
    )
    assert hit


def test_analysis_error_kind_catalog_detector_flags_transcription() -> None:
    text = (
        "Error kind | What it means\n"
        "--- | ---\n"
        "MetricNotFound | Unknown metric id\n"
        "SemanticKindMismatch | Wrong frame semantic kind\n"
        "SegmentDimensionMismatch | Segment columns differ\n"
        "PanelGrainMismatch | Panel grains differ\n"
    )
    tables = _table_first_column_tokens(text)
    hit = any(
        len(tokens & _public_error_catalog_tokens()) >= _ERROR_MATCH_THRESHOLD for tokens in tables
    )
    assert hit


def test_ordered_error_list_detector_flags_transcription() -> None:
    text = (
        "1. `MetricNotFound`: Unknown metric id\n"
        "2. `SemanticKindMismatch`: Wrong frame semantic kind\n"
        "3. `SegmentDimensionMismatch`: Segment columns differ\n"
        "4. `PanelGrainMismatch`: Panel grains differ\n"
    )
    lists = _bullet_list_tokens(text)
    hit = any(
        len(tokens & _public_error_catalog_tokens()) >= _ERROR_MATCH_THRESHOLD for tokens in lists
    )
    assert hit
