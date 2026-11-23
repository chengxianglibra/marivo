"""Exact repairs shared by semantic exceptions and the Help registry."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from marivo._authoring.model import RepairKind
from marivo.introspection.live.model import LiveHelpTarget


def _target(identity: str) -> LiveHelpTarget:
    return LiveHelpTarget(surface="semantic", canonical_id=identity)


@dataclass(frozen=True)
class SemanticRepairContract:
    """Deterministic structured repair for one semantic authoring error kind."""

    error_kind: str
    kind: RepairKind
    help_target: LiveHelpTarget
    action: str
    snippet: str | None = None
    preserves_evidence: bool | None = None


_ROWS = (
    SemanticRepairContract(
        error_kind="outside_loader_context",
        kind="reauthor",
        help_target=_target("load"),
        action=(
            "Move physical declarations into a semantic model loaded through "
            "ms.load(); compose native symbols directly and retrieve closed inputs from the Catalog."
        ),
        snippet=(
            "# models/semantic/<domain>/<module>.py\n"
            "import marivo.semantic as ms\n"
            "# declaration fragment evaluated by ms.load()"
        ),
        preserves_evidence=True,
    ),
    SemanticRepairContract(
        error_kind="missing_domain",
        kind="reauthor",
        help_target=_target("objects.domain"),
        action=(
            "Declare the domain with an accountable owner in its _domain.py, "
            "or pass an existing typed domain ref."
        ),
        snippet=(
            "# models/semantic/<domain>/_domain.py\n"
            "import marivo.semantic as ms\n"
            "ms.domain(name='<domain>', owner=accountable_owner)"
        ),
        preserves_evidence=True,
    ),
    SemanticRepairContract(
        error_kind="invalid_binding_ref",
        kind="reauthor",
        help_target=_target("bind"),
        action=(
            "Assign a native field symbol or closed physical field value to a module-level "
            "name before the decorator, then pass that bare name to ms.bind. For a "
            "cross-file field, use ms.ref.<kind>(the field's exact existing path). "
            "Use ms.bind only in the retained Ibis Dimension/TimeDimension bodies."
        ),
        preserves_evidence=True,
    ),
    SemanticRepairContract(
        error_kind="missing_time_fold",
        kind="reauthor",
        help_target=_target("Metric.per"),
        action=(
            "Keep the sampled status_time_dimension in additive_except(...) "
            "and express the temporal reduction with per(time_field).reducer()."
        ),
        preserves_evidence=True,
    ),
    SemanticRepairContract(
        error_kind="binding_target_missing",
        kind="reauthor",
        help_target=_target("bind"),
        action=(
            "Check the field Ref path named in this error against the loaded catalog. "
            "Declare the missing field or correct its module-level Ref, then reload "
            "before binding it to its owning Entity parameter."
        ),
        preserves_evidence=True,
    ),
    SemanticRepairContract(
        error_kind="binding_alias_not_direct",
        kind="reauthor",
        help_target=_target("bind"),
        action=(
            "Call ms.bind(field_ref, entity_parameter) with two positional bare names: "
            "an exact field Ref visible before decoration and a direct Entity parameter. "
            "Move Ref construction outside the body; remove attribute/factory arguments "
            "and transformed or locally aliased Entity arguments."
        ),
        preserves_evidence=True,
    ),
    SemanticRepairContract(
        error_kind="binding_entity_mismatch",
        kind="reauthor",
        help_target=_target("bind"),
        action=(
            "Use the direct Entity parameter that owns the referenced field. Compare "
            "the expected and received Entity refs in this error before changing either "
            "the field Ref or the metric's Entity parameters."
        ),
        preserves_evidence=True,
    ),
    SemanticRepairContract(
        error_kind="invalid_filter",
        kind="reauthor",
        help_target=_target("Metric.where"),
        action=(
            "Declare every filter dimension on the metric's target entity, then use "
            "one finite scalar equality value or a non-empty tuple/list of finite "
            "membership values."
        ),
        snippet=(
            "status = ms.dimension_column(\n"
            "    name='status', entity=orders, column='physical_status'\n"
            ")\n"
            "failed = orders.count().where(\n"
            "    (status == 'FAILED') | (status == 'ERROR')\n"
            ")"
        ),
        preserves_evidence=True,
    ),
    SemanticRepairContract(
        error_kind="filter_value_runtime_incompatible",
        kind="user_choice",
        help_target=_target("Metric.where"),
        action=(
            "Preserve the authored filter literals. Do not replace business codes with "
            "physical labels or other sampled values. Ask the user or accountable business "
            "owner to confirm any code-to-physical-value mapping; until then, report runtime "
            "evidence as unavailable and continue only query-free static verification and "
            "semantic_static readiness."
        ),
        preserves_evidence=True,
    ),
    SemanticRepairContract(
        error_kind="invalid_project",
        kind="configure",
        help_target=_target("load"),
        action=(
            "Point ms.load(workspace_dir=...) at the intended project root and repair "
            "any explicit invalid configuration or authored model files. Missing "
            "marivo.toml and models/ are valid empty-project defaults."
        ),
        snippet="catalog = ms.load(workspace_dir='<project root>')",
        preserves_evidence=True,
    ),
    SemanticRepairContract(
        error_kind="domain_file_missing",
        kind="reauthor",
        help_target=_target("objects.domain"),
        action=(
            "Create the required domain entrypoint and supply its accountable owner "
            "before loading again."
        ),
        snippet=(
            "# models/semantic/<domain>/_domain.py\n"
            "import marivo.semantic as ms\n"
            "ms.domain(name='<domain>', owner=accountable_owner)"
        ),
        preserves_evidence=True,
    ),
    SemanticRepairContract(
        error_kind="domain_file_mismatch",
        kind="reauthor",
        help_target=_target("domain"),
        action="Make the domain directory and ms.domain(name=...) use the same exact name.",
        snippet=(
            "# models/semantic/<domain>/_domain.py\n"
            "ms.domain(name='<domain>', owner=accountable_owner)"
        ),
        preserves_evidence=True,
    ),
    SemanticRepairContract(
        error_kind="entity_constructor_as_decorator",
        kind="reauthor",
        help_target=_target("entity"),
        action="Assign ms.entity(name=...) to a name, or omit name and return one Table expression.",
        preserves_evidence=True,
    ),
    SemanticRepairContract(
        error_kind="duplicate_identity_key",
        kind="reauthor",
        help_target=_target("entity"),
        action="Remove repeated names from primary_key.",
        preserves_evidence=True,
    ),
    SemanticRepairContract(
        error_kind="missing_identity_key_column",
        kind="reauthor",
        help_target=_target("entity"),
        action="Add each missing identity key to columns=... or omit the source projection.",
        preserves_evidence=True,
    ),
    SemanticRepairContract(
        error_kind="identity_version_overlap",
        kind="reauthor",
        help_target=_target("entity"),
        action="Remove version coordinates from primary_key and keep them in versioning.",
        preserves_evidence=True,
    ),
    SemanticRepairContract(
        error_kind="organization_error",
        kind="reauthor",
        help_target=_target("load"),
        action=(
            "Read the original exception and traceback when present, repair the failing "
            "declaration or import, then restart Python and reload the catalog."
        ),
        preserves_evidence=True,
    ),
)

ERROR_REPAIRS: Mapping[str, SemanticRepairContract] = MappingProxyType(
    {row.error_kind: row for row in _ROWS}
)
