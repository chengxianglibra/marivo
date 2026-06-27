"""md.help - agent-facing introspection of the datasource surface."""

from __future__ import annotations

from functools import lru_cache
from typing import cast

from marivo.datasource.constraints import iter_constraints
from marivo.introspection.render import format_family_block
from marivo.introspection.schema import Descriptor
from marivo.introspection.surface import Surface, derive_summaries, render, top_level_families


def _constraint_topic() -> Descriptor:
    constraints = [
        {
            "id": constraint.id,
            "title": constraint.title,
        }
        for constraint in iter_constraints()
    ]
    return Descriptor(
        surface="marivo.datasource",
        kind="topic",
        symbol="constraints",
        summary="Datasource authoring and validation constraints. Drill into an id for full rule details.",
        content={"constraints": constraints},
        doc="\n".join(
            (
                "marivo.datasource constraints:",
                "",
                *(f"  {constraint['id']:<36} {constraint['title']}" for constraint in constraints),
                "",
                'Call md.help("<constraint_id>") for full rule details.',
            )
        ),
    )


def _resolve(symbol: str) -> object | None:
    import marivo.datasource as md

    if symbol in md.__all__ and hasattr(md, symbol):
        return cast("object", getattr(md, symbol))
    return None


@lru_cache(maxsize=1)
def _surface() -> Surface:
    import marivo.datasource as md

    all_names = tuple(md.__all__)
    topics = {
        "constraints": _constraint_topic(),
    }
    summaries = derive_summaries(
        all_names,
        _resolve,
        topics,
        overrides={
            "TableSource": (
                "Union of table, parquet, and csv source IRs returned by "
                "md.table(), md.parquet(), and md.csv()."
            ),
        },
    )
    catalog = {constraint.id: constraint for constraint in iter_constraints()}
    return Surface(
        name="marivo.datasource",
        all_names=all_names,
        summaries=summaries,
        resolve=_resolve,
        catalog=catalog,
        topics=topics,
        type_aliases={"TableSource"},
        pinned_entries=(
            "DatasourceCatalog",
            "JoinSide",
            "ScanScope",
            "TableSource",
            "ColumnDiscovery",
            "TimeColumnDiscovery",
            "PrimaryKeyCandidate",
            "FormatCandidate",
        ),
        family_suffixes=(("Result", "Results"),),
        hidden_names=frozenset(),
    )


def _format_top_level_text() -> str:
    data = cast("dict[str, object]", render(_surface(), None, "json"))
    entries = cast("list[dict[str, str]]", data["entries"])
    lines = ["marivo.datasource - top-level entries:", ""]
    for entry in entries:
        lines.append(f"  md.{entry['name']:<24} [{entry['kind']}]  {entry['summary']}")
    lines.extend(format_family_block(top_level_families(_surface()), help_call="md.help"))
    lines.append("")
    lines.append('Call md.help("<name>") for detail on any entry.')
    return "\n".join(lines)


def help_text(symbol: str | None = None) -> str:
    """Return help text as a string instead of printing it."""

    normalized = None if symbol == "" else symbol
    if normalized is None:
        return _format_top_level_text()
    return cast("str", render(_surface(), normalized, "text"))


def help(  # noqa: A001, RUF100
    symbol: str | None = None,
) -> None:
    """Print bounded agent-facing help for the datasource surface and return None.

    Args:
        symbol: Symbol name, constraint id, or topic. None prints the
            top-level datasource surface listing.

    Returns:
        None

    Raises:
        TypeError: When called with ``format=``, ``print=``, or other
            unsupported keyword arguments.

    Example:
        >>> md.help()
        >>> md.help("trino")
    """

    normalized = None if symbol == "" else symbol
    print(help_text(normalized))
