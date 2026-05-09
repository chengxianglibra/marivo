"""Proposition seeding template registry (Phase 4e-1).

Implements the canonical seed template registry described in
``docs/analysis/evidence-engine/finding-proposition-seeding.md``.

The registry is the **only** place that maps ``finding_type → proposition_type``.
It replaces ad-hoc if/else seeding logic with a versioned, auditable,
replay-safe contract.

## Registry invariants

- ``template_id`` is globally unique within a registry instance.
- ``template_version`` expresses template content version.
- ``derivation_version`` expresses the proposition identity boundary version:
  a breaking seeding upgrade (one that changes judgment semantics or identity
  fields) must bump ``derivation_version``; a non-breaking upgrade bumps only
  ``template_version``.
- ``observation`` findings have **no** system-seeded template in v1 by design;
  ``SingleFindingSeedTemplateSpec.trigger_finding_type`` is typed as
  ``TriggerFindingType``, which excludes ``"observation"``, preventing any
  conforming template from being registered for it.

## v1 Bootstrap

Six single-finding templates are registered at module load time via
``_bootstrap_seed_templates()``:

  T1  delta               → change          (change_assessment)
  T2  decomposition_item  → decomposition   (decomposition_assessment)
  T3  anomaly_candidate   → anomaly         (anomaly_assessment)
  T4  correlation_result  → correlation     (correlation_assessment)
  T5  test_result         → test_hypothesis (test_hypothesis_assessment)
  T6  forecast_point      → forecast        (forecast_assessment)
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict, Union

from marivo.evidence_engine.canonical_finding import FindingType

# Finding types that are eligible to trigger system-seeded propositions.
# ``"observation"`` is intentionally excluded: observation findings do not
# seed any system proposition in v1 by design.
TriggerFindingType = Literal[
    "delta",
    "decomposition_item",
    "anomaly_candidate",
    "correlation_result",
    "test_result",
    "forecast_point",
]

# ---------------------------------------------------------------------------
# Template spec TypedDicts
# ---------------------------------------------------------------------------

PropositionType = Literal[
    "change",
    "decomposition",
    "anomaly",
    "correlation",
    "test_hypothesis",
    "forecast",
]

AssessmentType = Literal[
    "change_assessment",
    "decomposition_assessment",
    "anomaly_assessment",
    "correlation_assessment",
    "test_hypothesis_assessment",
    "forecast_assessment",
]

SeedMatchMode = Literal["single_finding", "composite"]

SeedSlotRole = Literal["primary", "secondary", "context"]

SeedSlotCardinality = Literal["one", "many"]


class SeedTemplateBase(TypedDict):
    """Fields shared by all seed template variants."""

    template_id: str
    template_version: str
    #: Proposition identity boundary version.  Breaking seeding changes (those
    #: that alter judgment semantics or identity fields) must bump this value.
    derivation_version: str
    proposition_type: PropositionType
    assessment_type: AssessmentType
    schema_version: str
    match_mode: SeedMatchMode


class SingleFindingSeedTemplateSpec(SeedTemplateBase):
    """Seed template that binds a single trigger finding to one proposition.

    The ``primary finding = trigger finding`` rule is fixed; no additional
    finding search is allowed.
    """

    match_mode: Literal["single_finding"]  # type: ignore[misc]
    trigger_finding_type: TriggerFindingType


class SeedSlotSpec(TypedDict):
    """Slot declaration for composite seed templates."""

    slot_name: str
    finding_type: FindingType
    required: bool
    cardinality: SeedSlotCardinality
    role: SeedSlotRole
    #: Machine-readable match predicates (field path expressions).
    match_predicates: list[str]
    #: Sort key expression for stable winner selection / member ordering.
    sort_key: str


class CompositeSeedTemplateSpec(SeedTemplateBase):
    """Seed template that binds multiple findings via explicit slot matching.

    All slot matching must use canonical typed fields / refs; ad-hoc graph
    walks are not permitted.
    """

    match_mode: Literal["composite"]  # type: ignore[misc]
    trigger_slot: str
    slots: list[SeedSlotSpec]
    group_key: str


# Union of all concrete template spec variants.
# typing.Union is used (not X | Y syntax) for Python 3.9 get_args() compat.
SeedTemplateSpec = Union[  # noqa: UP007
    SingleFindingSeedTemplateSpec,
    CompositeSeedTemplateSpec,
]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class SeedTemplateRegistry:
    """Routes ``finding_type`` to the :class:`SeedTemplateSpec` that should seed it.

    Dispatch key
    ------------
    Primary key: ``template_id`` — globally unique stable string.

    Secondary index: ``trigger_finding_type`` for single-finding templates,
    maintained in ``_by_finding_type`` for fast ``find_by_finding_type()``
    lookups.

    ``observation`` finding type
    ----------------------------
    By v1 design, ``observation`` findings do not seed any system proposition.
    ``SingleFindingSeedTemplateSpec.trigger_finding_type`` is typed as
    ``TriggerFindingType``, which excludes ``"observation"``, so no conforming
    v1 template can be registered for it.  ``find_by_finding_type("observation")``
    therefore returns ``[]`` because no such template exists in the registry.

    Duplicate protection
    --------------------
    ``register()`` raises ``ValueError`` on duplicate ``template_id`` unless
    ``override=True`` is passed explicitly.
    """

    def __init__(self) -> None:
        self._registry: dict[str, SeedTemplateSpec] = {}
        # Secondary index: finding_type → sorted list of matching templates
        self._by_finding_type: dict[str, list[SeedTemplateSpec]] = {}

    def register(self, template: SeedTemplateSpec, *, override: bool = False) -> None:
        """Register *template* under its ``template_id``.

        Parameters
        ----------
        template:
            A :data:`SeedTemplateSpec` instance (either single-finding or
            composite).
        override:
            When ``True``, silently replaces an already-registered template
            for the same ``template_id``.  Defaults to ``False``.

        Raises
        ------
        ValueError
            If a template with the same ``template_id`` is already registered
            and ``override`` is ``False``.
        """
        tid = template["template_id"]
        if tid in self._registry and not override:
            existing = self._registry[tid]
            raise ValueError(
                f"Seed template {existing['template_id']!r} is already registered.  "
                f"Pass override=True to replace."
            )

        # Remove old secondary-index entry if overriding.
        if tid in self._registry and override:
            old = self._registry[tid]
            if old["match_mode"] == "single_finding":
                ftype = old["trigger_finding_type"]
                bucket = self._by_finding_type.get(ftype, [])
                self._by_finding_type[ftype] = [t for t in bucket if t["template_id"] != tid]

        self._registry[tid] = template

        # Update secondary index for single-finding templates.
        if template["match_mode"] == "single_finding":
            ftype = template["trigger_finding_type"]
            bucket = self._by_finding_type.setdefault(ftype, [])
            bucket.append(template)
            # Keep the bucket sorted by template_id for stability.
            self._by_finding_type[ftype] = sorted(bucket, key=lambda t: t["template_id"])

    def get(self, template_id: str) -> SeedTemplateSpec:
        """Strict lookup by ``template_id`` — raises ``KeyError`` if not registered.

        Raises
        ------
        KeyError
            If no template is registered for ``template_id``.
        """
        if template_id not in self._registry:
            registered = sorted(self._registry)
            raise KeyError(
                f"No seed template registered for template_id={template_id!r}.  "
                f"Registered ids: {registered}"
            )
        return self._registry[template_id]

    def find_by_finding_type(self, finding_type: str) -> list[SeedTemplateSpec]:
        """Return all single-finding templates whose trigger is *finding_type*.

        Returns an empty list for any finding type without a registered
        template — including ``"observation"``, which has no registered
        template in v1 by design (``TriggerFindingType`` excludes it, so no
        v1 bootstrap template can be registered for it).
        The returned list is stable-sorted by ``template_id``.
        """
        return list(self._by_finding_type.get(finding_type, []))

    def registered_template_ids(self) -> list[str]:
        """Return a sorted list of all registered ``template_id`` strings."""
        return sorted(self._registry)

    def snapshot(self) -> list[dict[str, Any]]:
        """Return an auditable, sorted snapshot of all registered templates.

        Sorted by ``template_id`` for stability across Python versions and
        insertion orders.  Each entry contains all fields from the template
        spec; composite templates additionally include ``slots``.
        """
        result: list[dict[str, Any]] = []
        for tid in sorted(self._registry):
            t = self._registry[tid]
            entry: dict[str, Any] = {
                "template_id": t["template_id"],
                "template_version": t["template_version"],
                "derivation_version": t["derivation_version"],
                "proposition_type": t["proposition_type"],
                "assessment_type": t["assessment_type"],
                "schema_version": t["schema_version"],
                "match_mode": t["match_mode"],
            }
            if t["match_mode"] == "single_finding":
                entry["trigger_finding_type"] = t["trigger_finding_type"]
            elif t["match_mode"] == "composite":
                entry["trigger_slot"] = t["trigger_slot"]
                entry["group_key"] = t["group_key"]
                entry["slots"] = list(t["slots"])
            result.append(entry)
        return result


# ---------------------------------------------------------------------------
# Module-level default registry singleton
# ---------------------------------------------------------------------------

default_seed_registry: SeedTemplateRegistry = SeedTemplateRegistry()


# ---------------------------------------------------------------------------
# v1 bootstrap (T1-T6)
# ---------------------------------------------------------------------------


def _bootstrap_seed_templates() -> None:
    """Register all v1 single-finding seed templates into ``default_seed_registry``.

    Template catalogue (v1):

    T1  seed.change_from_delta.v1              delta              → change
    T2  seed.decomposition_from_item.v1        decomposition_item → decomposition
    T3  seed.anomaly_from_candidate.v1         anomaly_candidate  → anomaly
    T4  seed.correlation_from_result.v1        correlation_result → correlation
    T5  seed.test_hypothesis_from_result.v1    test_result        → test_hypothesis
    T6  seed.forecast_from_point.v1            forecast_point     → forecast
    """
    _v = "1.0.0"
    _s = "v1"

    templates: list[SingleFindingSeedTemplateSpec] = [
        SingleFindingSeedTemplateSpec(
            template_id="seed.change_from_delta.v1",
            template_version=_v,
            derivation_version="seed.change_from_delta.identity.v1",
            proposition_type="change",
            assessment_type="change_assessment",
            schema_version=_s,
            match_mode="single_finding",
            trigger_finding_type="delta",
        ),
        SingleFindingSeedTemplateSpec(
            template_id="seed.decomposition_from_item.v1",
            template_version=_v,
            derivation_version="seed.decomposition_from_item.identity.v1",
            proposition_type="decomposition",
            assessment_type="decomposition_assessment",
            schema_version=_s,
            match_mode="single_finding",
            trigger_finding_type="decomposition_item",
        ),
        SingleFindingSeedTemplateSpec(
            template_id="seed.anomaly_from_candidate.v1",
            template_version=_v,
            derivation_version="seed.anomaly_from_candidate.identity.v1",
            proposition_type="anomaly",
            assessment_type="anomaly_assessment",
            schema_version=_s,
            match_mode="single_finding",
            trigger_finding_type="anomaly_candidate",
        ),
        SingleFindingSeedTemplateSpec(
            template_id="seed.correlation_from_result.v1",
            template_version=_v,
            derivation_version="seed.correlation_from_result.identity.v1",
            proposition_type="correlation",
            assessment_type="correlation_assessment",
            schema_version=_s,
            match_mode="single_finding",
            trigger_finding_type="correlation_result",
        ),
        SingleFindingSeedTemplateSpec(
            template_id="seed.test_hypothesis_from_result.v1",
            template_version=_v,
            derivation_version="seed.test_hypothesis_from_result.identity.v1",
            proposition_type="test_hypothesis",
            assessment_type="test_hypothesis_assessment",
            schema_version=_s,
            match_mode="single_finding",
            trigger_finding_type="test_result",
        ),
        SingleFindingSeedTemplateSpec(
            template_id="seed.forecast_from_point.v1",
            template_version=_v,
            derivation_version="seed.forecast_from_point.identity.v1",
            proposition_type="forecast",
            assessment_type="forecast_assessment",
            schema_version=_s,
            match_mode="single_finding",
            trigger_finding_type="forecast_point",
        ),
    ]

    for t in templates:
        default_seed_registry.register(t)


_bootstrap_seed_templates()


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "AssessmentType",
    "CompositeSeedTemplateSpec",
    "PropositionType",
    "SeedMatchMode",
    "SeedSlotCardinality",
    "SeedSlotRole",
    "SeedSlotSpec",
    "SeedTemplateBase",
    "SeedTemplateRegistry",
    "SeedTemplateSpec",
    "SingleFindingSeedTemplateSpec",
    "TriggerFindingType",
    "default_seed_registry",
]
