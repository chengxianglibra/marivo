"""Private normalization and current-authority checks for analysis Artifacts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from marivo.analysis._artifact_integrity import load_canonical_frame_identity
from marivo.analysis._authority_inventory import ARTIFACT_AUTHORITY_INVENTORY
from marivo.analysis.errors import AnalysisRepair, EvidenceIntegrityError
from marivo.analysis.evidence.identity import canonical_json
from marivo.analysis.frames.base import BaseFrame
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import MetricKind, Ref, RefPayloadV1, SemanticKind, SemanticKindTag
from marivo.refs import ref as ref_factory
from marivo.semantic.errors import SemanticError
from marivo.semantic.metric_graph import SemanticDependencyEntryV1
from marivo.semantic.metric_graph_canonical import fingerprint as semantic_fingerprint
from marivo.semantic.metric_graph_lowering import dependency_digest

if TYPE_CHECKING:
    from marivo.analysis.session.core import Session

AuthorityFingerprintScheme = Literal[
    "dependency_entry",
    "definition",
    "candidate_readiness",
]


@dataclass(frozen=True, slots=True)
class ScopedDependencyAuthority:
    """One exact scoped semantic identity recorded by an Artifact."""

    semantic_ref: RefPayloadV1
    source_artifact_ref: str
    recorded_catalog_definition_fingerprint: str | None
    recorded_scoped_definition_fingerprint: str
    fingerprint_scheme: AuthorityFingerprintScheme

    @property
    def key(self) -> str:
        return f"{self.semantic_ref.kind.value}:{self.semantic_ref.path}"

    @property
    def ref(self) -> RefPayloadV1:
        """Return the semantic ref for compatibility consumers."""
        return self.semantic_ref

    @property
    def fingerprint(self) -> str:
        """Return the recorded scoped fingerprint for compatibility consumers."""
        return self.recorded_scoped_definition_fingerprint

    @property
    def scheme(self) -> AuthorityFingerprintScheme:
        """Return the fingerprint scheme for compatibility consumers."""
        return self.fingerprint_scheme


@dataclass(frozen=True, slots=True)
class CatalogOnlyDependencyAuthority:
    """A dependency that retained catalog identity but no scoped identity."""

    semantic_ref: RefPayloadV1
    source_artifact_ref: str
    recorded_catalog_definition_fingerprint: str
    scoped_authority_missing_reason: str


@dataclass(frozen=True, slots=True)
class UnresolvedDependencyAuthority:
    """A fail-closed dependency whose required identity fields are unavailable."""

    semantic_ref_if_known: RefPayloadV1 | None
    source_artifact_ref: str
    missing_authority_fields: tuple[str, ...]


SemanticDependencyAuthority = (
    ScopedDependencyAuthority | CatalogOnlyDependencyAuthority | UnresolvedDependencyAuthority
)


@dataclass(frozen=True, slots=True)
class ArtifactAuthorityContext:
    """Normalized private authority and evidence identity for one Artifact."""

    artifact_ref: str
    session_id: str
    content_hash: str
    semantic_dependencies: tuple[SemanticDependencyAuthority, ...]
    source_refs: tuple[str, ...]
    evidence_digest_fingerprint: str | None


@dataclass(frozen=True, slots=True)
class SemanticDependencyCheck:
    """Recorded/current comparison for one normalized semantic dependency."""

    semantic_ref: RefPayloadV1 | None
    source_artifact_ref: str
    fingerprint_scheme: AuthorityFingerprintScheme | None
    recorded_fingerprint: str | None
    current_fingerprint: str | None
    status: Literal["current", "stale", "indeterminate"]


@dataclass(frozen=True, slots=True)
class SemanticAuthorityEvaluation:
    """Deterministic current-authority result shared by public checks."""

    status: Literal["current", "stale", "indeterminate"]
    recorded_catalog_fingerprint: str | None
    current_catalog_fingerprint: str
    dependency_checks: tuple[SemanticDependencyCheck, ...]
    drifted_definition_refs: tuple[str, ...]
    indeterminate_definition_refs: tuple[str, ...]
    authority_fingerprint: str


@dataclass(frozen=True, slots=True)
class _SourceArtifactBinding:
    ref: str
    fingerprint: str | None = None


def _repair(action: str, *, snippet: str | None = None) -> AnalysisRepair:
    return AnalysisRepair(
        kind="inspect",
        action=action,
        help_target=LiveHelpTarget(surface="analysis", canonical_id="session.revalidate"),
        snippet=snippet,
    )


def _integrity_error(
    *,
    artifact_ref: str,
    expected: str,
    received: str,
    cause: Exception | None = None,
) -> EvidenceIntegrityError:
    error = EvidenceIntegrityError(
        message=f"committed evidence for artifact {artifact_ref!r} failed integrity validation",
        expected=expected,
        received=received,
        location="session.revalidate",
        repair=_repair(
            "Recover the exact artifact and re-run its producing operator in a fresh analysis "
            "session if the committed authority graph cannot be restored.",
            snippet=f"session.get_frame({artifact_ref!r})",
        ),
        context={"artifact_ref": artifact_ref},
    )
    if cause is not None:
        error.__cause__ = cause
    return error


def _payload_ref(payload: RefPayloadV1) -> Ref[SemanticKindTag]:
    factory = cast("Any", getattr(ref_factory, payload.kind.value))
    return cast("Ref[SemanticKindTag]", factory(payload.path))


def _entry_fingerprint(entry: SemanticDependencyEntryV1) -> str:
    return semantic_fingerprint(entry)


def candidate_readiness_fingerprint(
    session: Session,
    metric_ref: Ref[MetricKind],
) -> tuple[bool, str]:
    """Return the canonical readiness identity used by semantic candidates."""
    report = session.catalog.readiness(refs=[metric_ref])
    payload = {
        "catalog_definition_fingerprint": session.catalog.definition_fingerprint,
        "metric_ref": RefPayloadV1.from_ref(metric_ref).to_dict(),
        "status": report.status,
        "blockers": [{"kind": item.kind, "refs": list(item.refs)} for item in report.blockers],
        "warnings": [{"kind": item.kind, "refs": list(item.refs)} for item in report.warnings],
    }
    return report.status != "blocked", semantic_fingerprint(payload)


def _catalog_fingerprint(meta: object) -> str | None:
    for field_name in (
        "catalog_definition_fingerprint",
        "semantic_catalog_fingerprint",
    ):
        value = getattr(meta, field_name, None)
        if isinstance(value, str) and value:
            return value
    return None


def _direct_dependencies(
    *,
    meta: object,
    source_artifact_ref: str,
) -> tuple[ScopedDependencyAuthority, ...]:
    result: list[ScopedDependencyAuthority] = []
    catalog_fingerprint = _catalog_fingerprint(meta)
    digests: list[object] = []
    direct_digest = getattr(meta, "semantic_dependency_digest", None)
    if direct_digest is not None:
        digests.append(direct_digest)
    source_digests = getattr(meta, "source_dependency_digests", ())
    if isinstance(source_digests, tuple):
        digests.extend(source_digests)
    for digest in digests:
        for entry in getattr(digest, "entries", ()):
            if isinstance(entry, SemanticDependencyEntryV1):
                result.append(
                    ScopedDependencyAuthority(
                        semantic_ref=entry.ref,
                        source_artifact_ref=source_artifact_ref,
                        recorded_catalog_definition_fingerprint=catalog_fingerprint,
                        recorded_scoped_definition_fingerprint=_entry_fingerprint(entry),
                        fingerprint_scheme="dependency_entry",
                    )
                )

    event_fingerprints = getattr(meta, "event_fingerprints", None)
    if isinstance(event_fingerprints, dict):
        for path, fingerprint in event_fingerprints.items():
            result.append(
                ScopedDependencyAuthority(
                    semantic_ref=RefPayloadV1.from_ref(ref_factory.event(str(path))),
                    source_artifact_ref=source_artifact_ref,
                    recorded_catalog_definition_fingerprint=catalog_fingerprint,
                    recorded_scoped_definition_fingerprint=str(fingerprint),
                    fingerprint_scheme="definition",
                )
            )

    for ref_field, fingerprint_field in (
        ("state_model_ref", "state_model_fingerprint"),
        ("target_state_model_ref", "target_state_model_fingerprint"),
    ):
        ref_payload = getattr(meta, ref_field, None)
        fingerprint = getattr(meta, fingerprint_field, None)
        if isinstance(ref_payload, RefPayloadV1) and isinstance(fingerprint, str):
            result.append(
                ScopedDependencyAuthority(
                    semantic_ref=ref_payload,
                    source_artifact_ref=source_artifact_ref,
                    recorded_catalog_definition_fingerprint=catalog_fingerprint,
                    recorded_scoped_definition_fingerprint=fingerprint,
                    fingerprint_scheme="definition",
                )
            )

    readiness_bindings = getattr(meta, "readiness_bindings", ())
    if isinstance(readiness_bindings, tuple):
        for binding in readiness_bindings:
            metric_ref = getattr(binding, "metric_ref", None)
            fingerprint = getattr(binding, "fingerprint", None)
            if isinstance(metric_ref, RefPayloadV1) and isinstance(fingerprint, str):
                result.append(
                    ScopedDependencyAuthority(
                        semantic_ref=metric_ref,
                        source_artifact_ref=source_artifact_ref,
                        recorded_catalog_definition_fingerprint=catalog_fingerprint,
                        recorded_scoped_definition_fingerprint=fingerprint,
                        fingerprint_scheme="candidate_readiness",
                    )
                )

    return tuple(
        sorted(
            set(result),
            key=lambda item: (
                item.key,
                item.source_artifact_ref,
                item.fingerprint_scheme,
                item.recorded_scoped_definition_fingerprint,
            ),
        )
    )


def _source_bindings_from_value(value: object) -> tuple[_SourceArtifactBinding, ...]:
    if isinstance(value, str):
        return (_SourceArtifactBinding(ref=value),)
    if isinstance(value, tuple | list):
        return tuple(binding for item in value for binding in _source_bindings_from_value(item))
    result: list[_SourceArtifactBinding] = []
    for ref_name, fingerprint_name in (
        ("artifact_ref", "artifact_fingerprint"),
        ("source_artifact_ref", "source_artifact_fingerprint"),
    ):
        candidate = getattr(value, ref_name, None)
        fingerprint = getattr(value, fingerprint_name, None)
        if isinstance(candidate, str):
            result.append(
                _SourceArtifactBinding(
                    ref=candidate,
                    fingerprint=fingerprint if isinstance(fingerprint, str) else None,
                )
            )
    return tuple(result)


def _typed_source_artifact_bindings(
    meta: object,
) -> tuple[_SourceArtifactBinding, ...] | None:
    entry = next(
        (item for item in ARTIFACT_AUTHORITY_INVENTORY if type(meta) is item.meta_type),
        None,
    )
    if entry is None:
        return None
    bindings: list[_SourceArtifactBinding] = []
    for field_name in entry.source_identity_fields:
        if field_name.endswith("_fingerprint"):
            continue
        value = getattr(meta, field_name, None)
        if field_name.endswith("_ref") and isinstance(value, str):
            fingerprint_name = f"{field_name.removesuffix('_ref')}_fingerprint"
            fingerprint = getattr(meta, fingerprint_name, None)
            bindings.append(
                _SourceArtifactBinding(
                    ref=value,
                    fingerprint=fingerprint if isinstance(fingerprint, str) else None,
                )
            )
            continue
        bindings.extend(_source_bindings_from_value(value))
    return tuple(
        sorted(set(bindings), key=lambda binding: (binding.ref, binding.fingerprint or ""))
    )


def authority_context(
    frame: BaseFrame,
    *,
    session: Session,
    frames: dict[str, BaseFrame] | None = None,
) -> ArtifactAuthorityContext:
    """Normalize one committed Artifact and its typed source closure."""
    frames = {} if frames is None else frames
    root_ref = frame.meta.artifact_id or frame.meta.ref
    frames[root_ref] = frame
    dependencies: list[SemanticDependencyAuthority] = []
    source_refs: set[str] = set()
    visiting: set[str] = set()

    def visit(artifact_ref: str) -> None:
        if artifact_ref in visiting:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="acyclic typed source Artifact lineage",
                received="source lineage cycle",
            )
        visiting.add(artifact_ref)
        current = frames.get(artifact_ref)
        if current is None:
            try:
                current, _ = load_canonical_frame_identity(
                    session=session,
                    frame=session.get_frame(artifact_ref),
                )
            except Exception as exc:
                raise _integrity_error(
                    artifact_ref=artifact_ref,
                    expected="an intact source Artifact referenced by typed lineage",
                    received=type(exc).__name__,
                    cause=exc,
                ) from exc
            frames[artifact_ref] = current
        dependencies.extend(
            _direct_dependencies(meta=current.meta, source_artifact_ref=artifact_ref)
        )
        bindings = _typed_source_artifact_bindings(current.meta)
        if bindings is None:
            dependencies.append(
                UnresolvedDependencyAuthority(
                    semantic_ref_if_known=None,
                    source_artifact_ref=artifact_ref,
                    missing_authority_fields=("registered FrameMeta authority extractor",),
                )
            )
            visiting.remove(artifact_ref)
            return
        for binding in bindings:
            visit(binding.ref)
            source_frame = frames[binding.ref]
            if binding.fingerprint is not None and (
                source_frame.meta.content_hash != binding.fingerprint
            ):
                raise _integrity_error(
                    artifact_ref=binding.ref,
                    expected="the typed source Artifact to match its recorded content fingerprint",
                    received=(
                        f"recorded={binding.fingerprint!r}, "
                        f"actual={source_frame.meta.content_hash!r}"
                    ),
                )
            source_refs.add(binding.ref)
        visiting.remove(artifact_ref)

    visit(root_ref)
    normalized_dependencies = tuple(
        sorted(
            set(dependencies),
            key=lambda item: canonical_json(item),
        )
    )
    content_hash = frame.meta.content_hash
    if not isinstance(content_hash, str) or not content_hash:
        raise _integrity_error(
            artifact_ref=root_ref,
            expected="a committed Artifact content hash",
            received=repr(content_hash),
        )
    digest = frame.evidence_digest
    return ArtifactAuthorityContext(
        artifact_ref=root_ref,
        session_id=frame.meta.session_id,
        content_hash=content_hash,
        semantic_dependencies=normalized_dependencies,
        source_refs=tuple(sorted(source_refs)),
        evidence_digest_fingerprint=digest.fingerprint if digest is not None else None,
    )


def _current_scoped_fingerprint(
    dependency: ScopedDependencyAuthority,
    *,
    session: Session,
    dependency_entries: dict[tuple[str, str], str],
) -> str | None:
    if dependency.fingerprint_scheme == "dependency_entry":
        return dependency_entries.get((dependency.key, dependency.fingerprint_scheme))
    if dependency.fingerprint_scheme == "candidate_readiness":
        if dependency.semantic_ref.kind is not SemanticKind.METRIC:
            return None
        try:
            _, candidate_fingerprint = candidate_readiness_fingerprint(
                session,
                cast("Ref[MetricKind]", _payload_ref(dependency.semantic_ref)),
            )
        except (KeyError, SemanticError, TypeError, ValueError):
            return None
        return candidate_fingerprint
    try:
        details = session.catalog.require(_payload_ref(dependency.semantic_ref)).details()
        definition_fingerprint = getattr(details, "definition_fingerprint", None)
    except SemanticError:
        return None
    return definition_fingerprint if isinstance(definition_fingerprint, str) else None


def evaluate_semantic_authority(
    context: ArtifactAuthorityContext,
    *,
    session: Session,
) -> SemanticAuthorityEvaluation:
    """Compare normalized recorded dependencies with the current catalog."""
    scoped = tuple(
        dependency
        for dependency in context.semantic_dependencies
        if isinstance(dependency, ScopedDependencyAuthority)
    )
    dependency_entry_refs = tuple(
        _payload_ref(dependency.semantic_ref)
        for dependency in scoped
        if dependency.fingerprint_scheme == "dependency_entry"
    )
    dependency_entries: dict[tuple[str, str], str] = {}
    if dependency_entry_refs:
        reg = getattr(session.catalog, "_reg", None)
        state = getattr(session.catalog, "_state", None)
        if reg is not None and state is not None:
            try:
                current_digest = dependency_digest(
                    reg,
                    sidecar=state.sidecar,
                    semantic_refs=dependency_entry_refs,
                )
            except (KeyError, SemanticError, TypeError, ValueError):
                current_digest = None
            if current_digest is not None:
                dependency_entries = {
                    (
                        f"{entry.ref.kind.value}:{entry.ref.path}",
                        "dependency_entry",
                    ): _entry_fingerprint(entry)
                    for entry in current_digest.entries
                }

    checks: list[SemanticDependencyCheck] = []
    for dependency in context.semantic_dependencies:
        if isinstance(dependency, ScopedDependencyAuthority):
            current = _current_scoped_fingerprint(
                dependency,
                session=session,
                dependency_entries=dependency_entries,
            )
            status: Literal["current", "stale", "indeterminate"]
            if current is None:
                status = "indeterminate"
            elif current != dependency.recorded_scoped_definition_fingerprint:
                status = "stale"
            else:
                status = "current"
            checks.append(
                SemanticDependencyCheck(
                    semantic_ref=dependency.semantic_ref,
                    source_artifact_ref=dependency.source_artifact_ref,
                    fingerprint_scheme=dependency.fingerprint_scheme,
                    recorded_fingerprint=(dependency.recorded_scoped_definition_fingerprint),
                    current_fingerprint=current,
                    status=status,
                )
            )
        elif isinstance(dependency, CatalogOnlyDependencyAuthority):
            checks.append(
                SemanticDependencyCheck(
                    semantic_ref=dependency.semantic_ref,
                    source_artifact_ref=dependency.source_artifact_ref,
                    fingerprint_scheme=None,
                    recorded_fingerprint=None,
                    current_fingerprint=None,
                    status="indeterminate",
                )
            )
        else:
            checks.append(
                SemanticDependencyCheck(
                    semantic_ref=dependency.semantic_ref_if_known,
                    source_artifact_ref=dependency.source_artifact_ref,
                    fingerprint_scheme=None,
                    recorded_fingerprint=None,
                    current_fingerprint=None,
                    status="indeterminate",
                )
            )

    checks_tuple = tuple(
        sorted(
            checks,
            key=lambda item: (
                item.semantic_ref.kind.value if item.semantic_ref is not None else "",
                item.semantic_ref.path if item.semantic_ref is not None else "",
                item.source_artifact_ref,
                item.fingerprint_scheme or "",
                item.recorded_fingerprint or "",
            ),
        )
    )
    drifted = tuple(
        sorted(
            {
                f"{item.semantic_ref.kind.value}:{item.semantic_ref.path}"
                for item in checks_tuple
                if item.status == "stale" and item.semantic_ref is not None
            }
        )
    )
    indeterminate = tuple(
        sorted(
            {
                (
                    f"{item.semantic_ref.kind.value}:{item.semantic_ref.path}"
                    if item.semantic_ref is not None
                    else item.source_artifact_ref
                )
                for item in checks_tuple
                if item.status == "indeterminate"
            }
        )
    )
    if drifted:
        status = "stale"
    elif indeterminate or not checks_tuple:
        status = "indeterminate"
    else:
        status = "current"

    recorded_catalogs = {
        dependency.recorded_catalog_definition_fingerprint
        for dependency in context.semantic_dependencies
        if isinstance(
            dependency,
            ScopedDependencyAuthority | CatalogOnlyDependencyAuthority,
        )
        and dependency.recorded_catalog_definition_fingerprint is not None
    }
    recorded_catalog = next(iter(recorded_catalogs)) if len(recorded_catalogs) == 1 else None
    current_catalog = session.catalog.definition_fingerprint
    authority_payload = {
        "artifact_ref": context.artifact_ref,
        "recorded_authority": context.semantic_dependencies,
        "recorded_catalog_fingerprint": recorded_catalog,
        "current_catalog_fingerprint": current_catalog,
        "dependency_checks": checks_tuple,
        "status": status,
    }
    authority_fingerprint = hashlib.sha256(
        canonical_json(authority_payload).encode("utf-8")
    ).hexdigest()
    return SemanticAuthorityEvaluation(
        status=status,
        recorded_catalog_fingerprint=recorded_catalog,
        current_catalog_fingerprint=current_catalog,
        dependency_checks=checks_tuple,
        drifted_definition_refs=drifted,
        indeterminate_definition_refs=indeterminate,
        authority_fingerprint=authority_fingerprint,
    )


__all__ = [
    "ArtifactAuthorityContext",
    "CatalogOnlyDependencyAuthority",
    "ScopedDependencyAuthority",
    "SemanticAuthorityEvaluation",
    "SemanticDependencyAuthority",
    "UnresolvedDependencyAuthority",
    "authority_context",
    "candidate_readiness_fingerprint",
    "evaluate_semantic_authority",
]
