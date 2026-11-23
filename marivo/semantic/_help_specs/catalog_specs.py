"""Semantic catalog capability specifications."""

from __future__ import annotations

from marivo._authoring.model import AuthoringCapability, AuthoringEffects, AuthoringInputRequirement
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic._help_specs.builders import (
    _LOCAL,
    _capability,
    _parameter_input,
)

SPECS: tuple[AuthoringCapability, ...] = (
    _capability(
        "SemanticCatalog.get",
        "marivo.semantic.catalog.SemanticCatalog.get",
        "Retrieve one immutable typed semantic object by canonical kind:path string or uncomposed ms.ref.* lookup symbol. get resolves the identity in this Catalog and returns a closed analysis input. Check object.kind before kind-specific operations. No model calls or datasource reads; object.show() and object.definition disclose captured facts.",
        kind="method",
        output="SemanticObject",
        inputs=(
            AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
            _parameter_input("subject", "str | LookupSymbol", "id"),
        ),
        effects=_LOCAL,
        example="revenue = catalog.get(ms.ref.metric('sales.revenue'))\nassert revenue.kind == 'metric'\nrevenue.show()",
        public_entrypoint="catalog.get",
        see_also=(LiveHelpTarget(surface="analysis", canonical_id="analysis_frame.show"),),
    ),
    _capability(
        "SemanticCatalog.search",
        "marivo.semantic.catalog.SemanticCatalog.search",
        "Search business meaning or list loaded definitions through this one entrypoint. Omit query or pass '' to browse all kinds; kind/domain/entity narrow the list. Empty query sorts by canonical identity, includes every filtered object in the loaded Catalog scope and calls no model. Each call returns one page (default limit=10; any positive integer). Use print(page) for a bounded card with Entity datasource bindings. items retains the complete current page; follow next_cursor with the same query and filters until None to enumerate all results. For empty query, candidate_count is the filtered total. Text uses the frozen lexical or configured hybrid profile; filters precede both recall channels. Hybrid may call models and write derived local caches. Scores express relevance, not execution authority; text candidate_count counts the fixed candidate pool, not every possible semantic match.",
        kind="method",
        output="CatalogPage",
        inputs=(AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),),
        effects=AuthoringEffects(
            data_access="local_metadata_read",
            connection="none",
            mutations=("project_state",),
            flags=("may_call_model", "may_write_search_cache"),
        ),
        example="page = catalog.search(kind='metric')\nprint(page)",
        public_entrypoint="catalog.search",
    ),
)


def _result_method_capabilities() -> tuple[AuthoringCapability, ...]:
    """Register native consumption methods advertised by semantic result types."""
    rows: list[AuthoringCapability] = []
    for module, owner, name, output, example in (
        (
            "catalog_model.objects",
            "SemanticObject",
            "related",
            "CatalogPage",
            'entry.related("dependencies").show()',
        ),
        (
            "catalog_model.objects",
            "SemanticObject",
            "to_dict",
            "dict[str, JsonValue]",
            "data = entry.to_dict()",
        ),
        ("catalog_model.objects", "SemanticObject", "to_json", "Text", "text = entry.to_json()"),
        (
            "catalog_model.objects",
            "SemanticObject",
            "show",
            "None",
            'entry.show(section="context", max_output_bytes=None)',
        ),
        (
            "catalog_entries",
            "CatalogPage",
            "to_dict",
            "dict[str, JsonValue]",
            "data = page.to_dict()",
        ),
        ("catalog_entries", "CatalogPage", "to_json", "Text", "text = page.to_json()"),
        ("catalog_entries", "CatalogPage", "show", "None", "page.show(max_output_bytes=None)"),
        (
            "catalog_entries",
            "CatalogPage",
            "render",
            "Text",
            "text = page.render(max_output_bytes=None)",
        ),
    ):
        rows.append(
            _capability(
                f"{owner}.{name}",
                f"marivo.semantic.{module}.{'_SemanticObject' if owner == 'SemanticObject' else owner}.{name}",
                "Display the current page with exact shown/omitted counts. max_output_bytes=None removes the display budget, not pagination; items/to_json contain the page and next_cursor retrieves later pages. No datasource or model calls."
                if owner == "CatalogPage" and name in ("show", "render")
                else "Read complete captured directory data or display a bounded projection. Related navigation stays within the loaded scope; no datasource or model calls.",
                kind="method",
                output=output,
                inputs=(AuthoringInputRequirement(role="receiver", family=owner),),
                effects=_LOCAL,
                example=example,
                public_entrypoint=f"{'page' if owner == 'CatalogPage' else 'entry'}.{name}",
            )
        )
    for module, owner in (
        ("readiness", "ReadinessReport"),
        ("readiness", "ReadinessInputSummary"),
        ("readiness", "ReadinessIssue"),
        ("runtime_check.results", "ValidationReport"),
        ("runtime_check.results", "ValidationCheckResult"),
    ):
        rows.append(
            _capability(
                f"{owner}.to_dict",
                f"marivo.semantic.{module}.{owner}.to_dict",
                "Serialize this result's current facts to a dictionary without rerunning checks or reading business data.",
                kind="method",
                output="dict[str, object]",
                inputs=(AuthoringInputRequirement(role="receiver", family=owner),),
                example="facts = result.to_dict()",
                public_entrypoint="result.to_dict",
            )
        )
    return tuple(rows)
