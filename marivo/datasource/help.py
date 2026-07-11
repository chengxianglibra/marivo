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


def _authoring_topic() -> Descriptor:
    return Descriptor(
        surface="marivo.datasource",
        kind="topic",
        symbol="authoring",
        summary="Datasource authoring workflow: declare, register, inspect, sample, project, then author semantics.",
        see_also=(
            "ms.help('authoring')",
            "md.help('clickhouse')",
            "md.help('ai_context')",
        ),
        doc="\n".join(
            (
                "Datasource authoring workflow:",
                "",
                "  import marivo.datasource as md",
                "",
                '1. Pick a backend and read its constructor help: md.help("<backend>")',
                "   (clickhouse, trino, postgres, mysql, duckdb). md.help() lists every entry.",
                '   Example DuckDB datasource: spec = md.duckdb(name="warehouse", path="warehouse.duckdb").',
                '2. Declare a typed spec, e.g. md.clickhouse(name=..., host_env="HOST", ...).',
                "   Credentials are *_env references: host_env, port_env, user_env, password_env.",
                "   Environment variables provide the secrets; never inline literal secrets.",
                "   Do not import internal secret classes or backend builders; author via",
                "   the public spec constructors only.",
                "3. Persist the datasource: md.register(spec) writes a model file under",
                "   models/datasources/, or author that file directly.",
                "4. Validate the live round trip with md.test(ref). After a validated round",
                "   trip, md.test may cache env-sourced secrets in plaintext user-global",
                "   state at ~/.marivo/secrets.toml; unresolved *_env refs stay an error.",
                "5. Choose a source descriptor; it is not a datasource declaration:",
                '   md.table("orders") for an internal table or view inside the datasource;',
                '   md.parquet("data/orders/*.parquet") for a self-describing DuckDB file source;',
                '   md.csv("data/orders.csv", schema={"id": "string", "amount": "decimal(18,2)"});',
                '   md.json("data/events.json", schema={"id": "string", "occurred_at": "timestamp"})',
                "   for typed CSV/JSON DuckDB file sources. Typed schema= is required for both.",
                "6. Inspect metadata without reading user rows:",
                '   inspection = md.inspect(spec.ref, md.table("orders"))',
                "   inspection.show(); inspection.partitions().show()",
                "   Read physical extent, partition state, schema, and timeout capability first.",
                "7. Acquire one explicitly scoped snapshot with required positive guards and",
                "   explicit columns. Prefer a known partition; acknowledge broad reads explicitly:",
                '   scope = md.partition({"dt": "20260710"}, max_rows=1000, timeout_seconds=30)',
                "   # or md.unpruned(max_rows=1000, timeout_seconds=30)",
                '   snapshot = inspection.sample(scope=scope, columns=("id", "status", "created_at", "amount"),',
                "                                persist_values=False, refresh=False)",
                "   LIMIT bounds returned rows, not bytes scanned; a partition may still be large.",
                "   Values stay in memory by default. persist_values=True writes bounded value",
                "   evidence to a plaintext project-local cache; use only when privacy permits.",
                "   Pass refresh=True to reacquire. md.remove(spec.name) removes the datasource",
                "   declaration; remove .marivo/authoring cache state separately when required.",
                "8. Project semantic-shaped evidence from that immutable snapshot without queries:",
                '   snapshot.entity(columns=("id",)).show()',
                '   snapshot.dimensions(columns=("status",)).show()',
                '   snapshot.time_dimensions(columns=("created_at",)).show()',
                '   snapshot.measures(columns=("amount",)).show()',
                '   snapshot.values("status", limit=10).show()',
                '   snapshot.relationships(other, left=("customer_id",), right=("id",)).show()',
                "9. Uncommon formats and semantic judgments remain agent-owned; projections",
                "   report deterministic observations and never select semantic meaning.",
                "10. md.raw_sql(ref, sql, reason=...) is a potentially expensive read-only",
                "   diagnostic escape hatch. A returned-row limit does not bound backend work.",
                "",
                "Once the datasource is registered and validated, hand off to semantics:",
                '  ms.help("authoring")',
            )
        ),
    )


def _snapshot_topics() -> dict[str, Descriptor]:
    docs = {
        "snapshot.entity": (
            "Project local entity evidence with snapshot.entity(columns=(...)); "
            "columns must already exist in the snapshot."
        ),
        "snapshot.dimensions": (
            "Project local dimension evidence with snapshot.dimensions(columns=(...)); "
            "Marivo reports observations, not semantic recommendations."
        ),
        "snapshot.values": (
            "Read bounded local value evidence with snapshot.values(column, limit=...). "
            "Reloaded values exist only when sample(..., persist_values=True) was used "
            "and privacy permits plaintext project-local caching."
        ),
        "snapshot.time_dimensions": (
            "Project deterministic time evidence with snapshot.time_dimensions(columns=(...)); "
            "uncommon formats remain agent-owned."
        ),
        "snapshot.measures": (
            "Project local numeric evidence with snapshot.measures(columns=(...)); "
            "aggregation, unit, and additivity remain agent-owned judgments."
        ),
        "snapshot.relationships": (
            "Compare acquired key evidence with snapshot.relationships(other, left=(...), "
            "right=(...)); cross-source scope comparability remains unresolved."
        ),
    }
    return {
        symbol: Descriptor(
            surface="marivo.datasource",
            kind="topic",
            symbol=symbol,
            summary=doc.split(". ", 1)[0] + ".",
            doc=doc,
            see_also=("md.help('authoring')",),
        )
        for symbol, doc in docs.items()
    }


def _ai_context_topic() -> Descriptor:
    return Descriptor(
        surface="marivo.datasource",
        kind="topic",
        symbol="ai_context",
        summary=(
            "ai_context values accepted by datasource specs are built with "
            "ms.ai_context(...); the datasource module has no ai_context constructor."
        ),
        see_also=(
            "ms.help('ai_context')",
            "md.help('authoring')",
        ),
        doc="\n".join(
            (
                "Datasource specs accept ai_context=... values that annotate a",
                "table with business meaning for agents. Those values are built",
                "with ms.ai_context(...), not on the datasource surface.",
                "",
                "Accepted fields on ms.ai_context(...):",
                "  business_definition  plain-language meaning of the table",
                "  guardrails           do/don't notes for agents using the data",
                "  synonyms             alternate names for the entity",
                "  examples             representative values or rows",
                "  instructions         operational guidance for agents",
                "  owner_notes          ownership and stewardship context",
                "",
                "Invalid in the current API: raw dicts, summary=, and glossary=.",
                "Build a value with ms.ai_context(...) and pass it as ai_context=...",
                "to a datasource spec constructor (e.g. md.clickhouse(..., ai_context=...)).",
                "",
                'See ms.help("ai_context") for the canonical contract.',
            )
        ),
    )


def _resolve(symbol: str) -> object | None:
    import marivo.datasource as md

    if symbol in md.__all__ and hasattr(md, symbol):
        return cast("object", getattr(md, symbol))
    return None


_HELP_ONLY_ENTRIES: tuple[str, ...] = ("authoring", "ai_context")


@lru_cache(maxsize=1)
def _surface() -> Surface:
    import marivo.datasource as md

    all_names = tuple(dict.fromkeys((*md.__all__, *_HELP_ONLY_ENTRIES)))
    topics = {
        "constraints": _constraint_topic(),
        "authoring": _authoring_topic(),
        "ai_context": _ai_context_topic(),
        **_snapshot_topics(),
    }
    summaries = derive_summaries(
        all_names,
        _resolve,
        topics,
        overrides={
            "TableSource": (
                "Union of table, parquet, CSV, and JSON source IRs returned by "
                "md.table(), md.parquet(), md.csv(), and md.json(); source descriptors "
                "are not datasource declarations."
            ),
            "json": (
                "Build a DuckDB file source JsonSourceIR for local files, glob patterns, "
                "or http(s):// URLs; not a datasource declaration."
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
            "DiscoverySnapshot",
            "SourceInspection",
            "TableSource",
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
