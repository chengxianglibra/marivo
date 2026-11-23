"""Semantic loading capability specifications."""

from __future__ import annotations

from marivo._authoring.model import AuthoringCapability
from marivo._kernel.refs import SemanticKind
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic._help_specs.builders import (
    _LOCAL,
    _NONE,
    _capability,
    _parameter_input,
    _symbol_family,
    _target,
)


def _ref_role(kind: SemanticKind) -> tuple[str, tuple[LiveHelpTarget, ...]]:
    """Keep each native lookup's static role with its owning operations."""
    roles: dict[SemanticKind, tuple[str, tuple[LiveHelpTarget, ...]]] = {
        SemanticKind.DOMAIN: (
            "Parameter-only Domain identity for domain= and Catalog navigation; no numeric operations.",
            (_target("domain"),),
        ),
        SemanticKind.DATASOURCE: (
            "Parameter-only Datasource identity for datasource= and datasource inspection; no numeric operations.",
            (_target("entity"), LiveHelpTarget(surface="datasource", canonical_id="inspect")),
        ),
        SemanticKind.ENTITY: (
            "Entity identity supports count() and identity parameters; it is not a numeric row field.",
            (_target("Entity.count"), _target("entity")),
        ),
        SemanticKind.DIMENSION: (
            "Categorical field for comparisons and grouping; numeric casts do not grant Measure sum permission.",
            (_target("field"), _target("dimension")),
        ),
        SemanticKind.TIME_DIMENSION: (
            "TimeField for time=, time predicates and grouping; its role and ownership are checked at binding.",
            (_target("expressions"), _target("time_dimension")),
        ),
        SemanticKind.MEASURE: (
            "Numeric row field or declared Measure; arithmetic and source reductions retain unit and sum permission.",
            (_target("row.sum"), _target("measure")),
        ),
        SemanticKind.METRIC: (
            "Completed Metric for Metric arithmetic, filtering and per(...).reducer(); never a row-field identity.",
            (_target("Metric.per"), _target("metric")),
        ),
        SemanticKind.RELATIONSHIP: (
            "Directed relationship for mv.path(...) and relationship checks.",
            (_target("Field.via"), _target("relationship"), _target("Relationship.at")),
        ),
    }
    return roles[kind]


def _ref_factory_capabilities() -> tuple[AuthoringCapability, ...]:
    """Build the closed public ``ms.ref`` factory contract and exact leaves."""

    kinds = tuple(SemanticKind)
    parent = _capability(
        "ref",
        None,
        "Create a native typed Deferred lookup symbol for an exact path; no lookup or source read occurs.",
        kind="boundary",
        output="LookupSymbol",
        effects=_NONE,
        see_also=tuple(_target(f"ref.{kind.value}") for kind in kinds),
    )
    leaves = tuple(
        _capability(
            f"ref.{kind.value}",
            f"marivo._kernel.symbolic._SymbolFactory.{kind.value}",
            f"Create a native {kind.value} Deferred symbol from an exact path. "
            + _ref_role(kind)[0]
            + " Compose supported recipes directly; the loader binds them. Metadata selectors require a native leaf. Equality builds a recipe; bool, hash, dictionary keys and container membership are not identity operations. Invalid attributes and methods fail when a declaration or parameter captures the recipe; unconsumed recipes and formatted strings have no such guarantee.",
            kind="method",
            output=_symbol_family(kind),
            inputs=(_parameter_input("subject", "Text", "path"),),
            effects=_NONE,
            constraints=("ref_shape",),
            example=f"ms.ref.{kind.value}({_ref_example_path(kind)!r})",
            public_entrypoint=f"ms.ref.{kind.value}",
            see_also=_ref_role(kind)[1],
            telemetry=False,
        )
        for kind in kinds
    )
    return (parent, *leaves)


def _ref_example_path(kind: SemanticKind) -> str:
    if kind in {SemanticKind.DOMAIN, SemanticKind.DATASOURCE}:
        return "sales" if kind is SemanticKind.DOMAIN else "warehouse"
    if kind in {
        SemanticKind.DIMENSION,
        SemanticKind.TIME_DIMENSION,
        SemanticKind.MEASURE,
    }:
        return f"sales.orders.{kind.value}"
    return f"sales.{kind.value}"


SPECS: tuple[AuthoringCapability, ...] = (
    _capability(
        "load",
        "marivo.semantic.catalog_load.load",
        "Load the read-only semantic catalog.\n  workspace_dir is an exact project root; when omitted: MARIVO_PROJECT_ROOT > nearest ancestor manifest > current directory.\n  Always loads the local models/ root; marivo.toml [semantic].layer_paths adds external models/ roots containing datasources/ and semantic/.\n  domains=None or an empty sequence loads all domains. A name or nonempty sequence selects only those domain directories without automatic dependency expansion. Include every required domain. Filtered Relationship references may warn. Fields require their owning Entity, and Metrics require their dependencies in scope.",
        output="SemanticCatalog",
        effects=_LOCAL,
        preconditions=(
            "Each declaration load uses current source in fresh namespaces for relative model imports, without modifying sys.path. Strict loads retain modules until process exit. Model absolute imports are unsupported.",
            "Restart Python after package upgrades or ordinary dependency changes; ordinary imports retain Python module caching.",
            "Strict by default. allow_partial=True permits discovery of the validated subset after datasource file execution failures. Inspect page.scope.complete, catalog.load_errors and catalog.excluded_refs. Readiness, validation and preview require a successful strict load.",
            "Execution failures report the declaration location and keep the original traceback on the error.",
        ),
        example="catalog = ms.load()\ncatalog.show()",
    ),
    *_ref_factory_capabilities(),
)
