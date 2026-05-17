"""Canonical finding extractor registry (Phase 4b-2).

Implements Decision D1 from
``docs/analysis/evidence-engine/artifact-finding-generation-rules.md``::

    D1 (approved): extractor dispatch key is
    ``(artifact_type, artifact_schema_version)``

    NOT the runtime ``step_type`` used by earlier observation extractors.

NULL ``artifact_schema_version`` values (artifacts created before versioning was
added) are normalised to ``"v1"`` by the lenient ``find()`` lookup.  The strict
``get()`` lookup does NOT apply this normalisation and will raise ``KeyError``
if called with an unregistered key.

Module-level singleton
----------------------
``default_finding_registry`` starts empty at module load time.  Actual
per-family extractors (4d-1 through 4d-4) call
``default_finding_registry.register(...)`` to populate it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from marivo.core.evidence.canonical_finding import FindingExtractionResult, StepRef
from marivo.core.evidence.family_contract import check_finding_count as _check_finding_count

# Canonical fallback version for artifacts that pre-date schema versioning.
_NULL_VERSION_FALLBACK = "v1"


class FindingExtractor(ABC):
    """Abstract base for canonical finding extractors (Phase 4b-2).

    Each concrete extractor handles exactly one
    ``(artifact_type, artifact_schema_version)`` pair and converts the
    corresponding artifact payload into a ``FindingExtractionResult``.

    Class-level attributes
    ----------------------
    artifact_type:
        The artifact type this extractor handles.
        Example: ``"observation_artifact"``, ``"compare_artifact"``.
    artifact_schema_version:
        The schema version string this extractor handles.
        Example: ``"v1"``.  NULL database values are normalised to ``"v1"``
        by the registry's ``find()`` method before dispatch.
    extractor_name:
        Stable, human-readable name used in ``FindingExtractionResult`` and
        registry snapshots.  Must be globally unique across all registered
        extractors.
    extractor_version:
        Extractor implementation version.  Used for audit/replay only;
        must NOT enter ``finding_id`` generation.
    finding_schema_version:
        Optional.  Records which finding schema contract this extractor
        targets.  Defaults to ``None`` if the extractor does not declare it.
    """

    artifact_type: ClassVar[str]
    artifact_schema_version: ClassVar[str]
    extractor_name: ClassVar[str]
    extractor_version: ClassVar[str]
    family: ClassVar[str]
    finding_schema_version: ClassVar[str | None] = None

    # Required ClassVar names validated by __init_subclass__.
    _REQUIRED_CLASSVARS: ClassVar[tuple[str, ...]] = (
        "artifact_type",
        "artifact_schema_version",
        "extractor_name",
        "extractor_version",
        "family",
    )

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        missing = [attr for attr in cls._REQUIRED_CLASSVARS if not hasattr(cls, attr)]
        if missing:
            raise TypeError(
                f"{cls.__name__} must define class-level attributes: " + ", ".join(missing)
            )

    @abstractmethod
    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        """Extract canonical findings from an artifact payload.

        Allowed inputs
        --------------
        - ``artifact_id``      — used with ``canonical_item_key`` to compute
                                 ``finding_id`` via ``make_finding_id``
        - ``artifact_payload`` — the stored ``content_json`` dict
        - ``step_ref``         — written into every ``FindingBase.step_ref``
        - ``session_id``       — for canonical ref ID construction only (e.g.
                                 populating ``FindingRef.session_id``); must
                                 NOT be used to look up other session state

        Not allowed
        -----------
        The extractor must NOT access other session artifacts or findings,
        UI projections, top-k truncation results, narrative text, or any
        model-generated output.  Extraction must be fully deterministic.
        """


class FindingExtractorRegistry:
    """Routes ``(artifact_type, artifact_schema_version)`` to a ``FindingExtractor``.

    Dispatch key
    ------------
    Both fields form the composite key:

    - ``artifact_type``           — e.g. ``"observation_artifact"``
    - ``artifact_schema_version`` — e.g. ``"v1"``

    The key is NOT ``step_type``; the same artifact type may evolve across
    schema versions and route to different extractors without changing the
    producing step type.

    NULL version normalisation
    --------------------------
    ``artifact_schema_version`` may be NULL in the database for artifacts
    created before schema versioning was added.  The lenient ``find()``
    method normalises ``None`` → ``"v1"`` before lookup.  The strict
    ``get()`` method does not apply this normalisation.

    Duplicate protection
    --------------------
    ``register()`` raises ``ValueError`` on duplicate keys unless
    ``override=True`` is passed explicitly, preventing accidental double-
    registration from silently shadowing an extractor.
    """

    def __init__(self) -> None:
        self._registry: dict[tuple[str, str], FindingExtractor] = {}

    def register(self, extractor: FindingExtractor, *, override: bool = False) -> None:
        """Register *extractor* under its ``(artifact_type, artifact_schema_version)`` key.

        Parameters
        ----------
        extractor:
            A concrete ``FindingExtractor`` instance.
        override:
            When ``True``, silently replaces an already-registered extractor
            for the same key.  Defaults to ``False``.

        Raises
        ------
        ValueError
            If a different extractor is already registered for the same key
            and ``override`` is ``False``.
        """
        key = (extractor.artifact_type, extractor.artifact_schema_version)
        if key in self._registry and not override:
            existing = self._registry[key]
            raise ValueError(
                f"Extractor {existing.extractor_name!r} is already registered for "
                f"key {key!r}.  Pass override=True to replace."
            )
        self._registry[key] = extractor

    def get(self, artifact_type: str, artifact_schema_version: str) -> FindingExtractor:
        """Strict lookup — raises ``KeyError`` if the key is not registered.

        Does NOT normalise ``None`` → ``"v1"``.  For NULL-safe lookup use
        ``find()`` instead.

        Raises
        ------
        KeyError
            If no extractor is registered for
            ``(artifact_type, artifact_schema_version)``.
        """
        key = (artifact_type, artifact_schema_version)
        if key not in self._registry:
            registered = sorted(self._registry)
            raise KeyError(
                f"No finding extractor registered for "
                f"artifact_type={artifact_type!r}, "
                f"artifact_schema_version={artifact_schema_version!r}.  "
                f"Registered keys: {registered}"
            )
        return self._registry[key]

    def find(
        self,
        artifact_type: str,
        artifact_schema_version: str | None,
    ) -> FindingExtractor | None:
        """Lenient lookup with NULL → ``"v1"`` normalisation.

        Returns ``None`` if no extractor is registered for the resolved key.
        Only ``None`` is normalised; an explicit empty string ``""`` is NOT
        treated as ``None`` and will be looked up as-is.

        This is the preferred method for the commit path, which must handle
        artifacts without a registered extractor.
        """
        version = (
            artifact_schema_version
            if artifact_schema_version is not None
            else _NULL_VERSION_FALLBACK
        )
        return self._registry.get((artifact_type, version))

    def registered_keys(self) -> list[tuple[str, str]]:
        """Return all registered ``(artifact_type, artifact_schema_version)`` pairs, sorted."""
        return sorted(self._registry)

    def snapshot(self) -> list[dict[str, Any]]:
        """Return an auditable, sorted snapshot of all registered extractors.

        Sorted by ``(artifact_type, artifact_schema_version)`` for stability
        across Python versions and insertion orders.  The snapshot can be
        compared between registry states to detect version drift.

        Each entry contains:

        - ``artifact_type``
        - ``artifact_schema_version``
        - ``extractor_name``
        - ``extractor_version``
        - ``finding_schema_version`` (``None`` if not declared by extractor)
        """
        return [
            {
                "artifact_type": e.artifact_type,
                "artifact_schema_version": e.artifact_schema_version,
                "family": e.family,
                "extractor_name": e.extractor_name,
                "extractor_version": e.extractor_version,
                "finding_schema_version": e.finding_schema_version,
            }
            for _, e in sorted(self._registry.items())
        ]


# ---------------------------------------------------------------------------
# Extraction result validator
# ---------------------------------------------------------------------------


def validate_extraction_result(result: FindingExtractionResult) -> None:
    """Validate the internal consistency of a ``FindingExtractionResult``.

    Enforces the invariant that ``finding_count == len(findings)``.
    The commit path must call this before invoking
    ``family_contract.check_finding_count`` so that the count used for the
    family-level empty-semantics check is guaranteed to be accurate.

    Raises
    ------
    ValueError
        If ``finding_count != len(result["findings"])``.
    """
    actual = len(result["findings"])
    declared = result["finding_count"]
    if actual != declared:
        raise ValueError(
            f"FindingExtractionResult invariant violated: "
            f"finding_count={declared!r} but len(findings)={actual!r}.  "
            f"Extractor {result['extractor_name']!r} must return "
            f"finding_count == len(findings)."
        )


# ---------------------------------------------------------------------------
# Unified commit-path validation gate (Phase 4b-4)
# ---------------------------------------------------------------------------


def validate_for_commit(family: str, result: FindingExtractionResult) -> None:
    """Unified pre-commit validation gate: internal consistency + family empty contract.

    The commit path (4c-1) calls this once per extraction result before writing
    any committed state to the canonical store.  Two sequential checks are run
    in order:

    1. ``validate_extraction_result(result)`` — ``finding_count == len(findings)``
       so the family-level check operates on an accurate count.
    2. ``family_contract.check_finding_count(family, result["finding_count"])``
       — enforces D4: ``"observe"`` / ``"detect"`` allow success-empty; all
       other canonical families require at least one finding.

    When this function returns without raising, the extraction result satisfies
    both the internal-consistency invariant and the family-level empty-semantics
    contract and may be written as a committed artifact + finding set.

    When this function raises, the artifact must remain in ``staged`` state and
    the extraction attempt must be recorded as ``failed`` per the runtime
    lifecycle contract (``runtime-lifecycle.md``).

    Parameters
    ----------
    family:
        Artifact family string (e.g. ``"compare"``).  Unknown families are
        treated as non-empty-required (fail-safe, same as
        ``check_finding_count``).
    result:
        The ``FindingExtractionResult`` returned by a ``FindingExtractor``.

    Raises
    ------
    ValueError
        If ``result["finding_count"] != len(result["findings"])`` (internal
        consistency check raised by ``validate_extraction_result``).
    FamilyEmptyError
        If ``finding_count == 0`` and the artifact family does not allow a
        success-empty committed finding set (raised by
        ``family_contract.check_finding_count``).
    """
    validate_extraction_result(result)
    _check_finding_count(family, result["finding_count"])


# ---------------------------------------------------------------------------
# Module-level default registry
#
# Starts empty at import time.  4d-* extractor modules populate it by calling:
#     default_finding_registry.register(<ConcreteExtractor>())
# following the same bootstrap pattern as _bootstrap() in registry.py.
# ---------------------------------------------------------------------------

default_finding_registry: FindingExtractorRegistry = FindingExtractorRegistry()


__all__ = [
    "FindingExtractor",
    "FindingExtractorRegistry",
    "default_finding_registry",
    "validate_extraction_result",
    "validate_for_commit",
]


# ---------------------------------------------------------------------------
# 4d-* extractor bootstrap (same pattern as registry.py:_bootstrap)
#
# Import is deferred inside a function to avoid circular imports:
# observe_extractor imports FindingExtractor from this module, but by the time
# _bootstrap_finding_extractors() is called, FindingExtractor is already defined.
# ---------------------------------------------------------------------------


def _bootstrap_finding_extractors() -> None:
    """Import and register all 4d-* finding extractors into default_finding_registry."""
    from marivo.runtime.evidence.compare_extractor import CompareArtifactExtractor
    from marivo.runtime.evidence.correlate_extractor import CorrelateArtifactExtractor
    from marivo.runtime.evidence.decompose_extractor import DecomposeArtifactExtractor
    from marivo.runtime.evidence.detect_extractor import DetectArtifactExtractor
    from marivo.runtime.evidence.forecast_extractor import ForecastArtifactExtractor
    from marivo.runtime.evidence.observe_extractor import ObserveArtifactExtractor

    default_finding_registry.register(ObserveArtifactExtractor())
    default_finding_registry.register(DetectArtifactExtractor())
    default_finding_registry.register(CompareArtifactExtractor())
    default_finding_registry.register(DecomposeArtifactExtractor())
    default_finding_registry.register(CorrelateArtifactExtractor())
    default_finding_registry.register(ForecastArtifactExtractor())


_bootstrap_finding_extractors()
