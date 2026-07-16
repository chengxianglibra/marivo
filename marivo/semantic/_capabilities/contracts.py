"""Runtime contracts derived from the semantic capability registry."""

from __future__ import annotations

from typing import Protocol

from marivo._authoring.model import (
    AuthoringContract,
    AuthoringEffects,
    AuthoringInputRequirement,
    AuthoringStateId,
    AuthoringStateRef,
    AuthoringTransition,
    TransitionKind,
)
from marivo._authoring.normalize import normalize_contract as _normalize_contract
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic._capabilities.registry import REGISTRY


class ReadinessBlocker(Protocol):
    """Structural type for readiness blockers accessed by contract builders.

    Avoids importing :class:`~marivo.semantic.readiness.ReadinessIssue` to
    prevent a circular dependency.
    """

    @property
    def refs(self) -> tuple[str, ...]: ...

    @property
    def kind(self) -> str: ...


def _state(state_id: AuthoringStateId, subject_refs: tuple[str, ...]) -> AuthoringStateRef:
    return AuthoringStateRef(id=state_id, subject_refs=subject_refs)


def _effects(canonical_id: str) -> AuthoringEffects:
    effects = REGISTRY.by_canonical_id(canonical_id).effects
    assert effects is not None
    return effects.model_copy(deep=True)


def _transition(
    canonical_id: str,
    *,
    kind: TransitionKind,
    subject_refs: tuple[str, ...],
    required_states: tuple[AuthoringStateRef, ...] = (),
    produced_state: AuthoringStateRef | None = None,
    available: bool,
    input_requirements: tuple[AuthoringInputRequirement, ...] = (),
    blocked_by: tuple[str, ...] = (),
    help_target: LiveHelpTarget | None = None,
) -> AuthoringTransition:
    descriptor = REGISTRY.by_canonical_id(canonical_id)
    resolved_target = help_target or LiveHelpTarget(
        surface="semantic", canonical_id=descriptor.canonical_id
    )
    return AuthoringTransition(
        kind=kind,
        help_target=resolved_target,
        subject_refs=subject_refs,
        required_states=required_states,
        produced_state=produced_state,
        effects=_effects(canonical_id),
        available=available,
        input_requirements=input_requirements,
        blocked_by=blocked_by,
    )


def contract_for_verify_result(ref: str) -> AuthoringContract:
    """Expose preview as a result-local continuation after explicit verification.

    ``PreviewResult.contract()`` is deliberately deferred: the type lives in the
    shared ``marivo.preview`` module, not ``marivo.semantic``, and is not part of
    ``ms.__all__``.  When the type is promoted into the semantic public surface,
    add ``contract_for_preview_result`` here, register the type contract, and
    add ``PreviewResult`` to ``OUTPUT_FAMILIES``.


    Parameters
    ----------
    ref:
        Canonical semantic ref of the verified object.

    Returns
    -------
    AuthoringContract
        A normalized contract with a ``semantic.verified`` current state and a
        ``preview`` transition that produces ``semantic.previewed``.
    """
    subject_refs = (ref,)
    verified = _state("semantic.verified", subject_refs)
    return _normalize_contract(
        AuthoringContract(
            subject_refs=subject_refs,
            states=(verified,),
            transitions=(
                _transition(
                    "preview",
                    kind="preview",
                    subject_refs=subject_refs,
                    required_states=(verified,),
                    produced_state=_state("semantic.previewed", subject_refs),
                    available=True,
                    input_requirements=(
                        AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
                        AuthoringInputRequirement(role="subject", family="CatalogObject"),
                        AuthoringInputRequirement(role="evidence", family="DiscoverySnapshot"),
                    ),
                ),
            ),
        )
    )


def contract_for_preview_batch_result(refs: tuple[str, ...]) -> AuthoringContract:
    """Expose query-free readiness after a successful preview batch."""
    previewed = _state("semantic.previewed", refs)
    return _normalize_contract(
        AuthoringContract(
            subject_refs=refs,
            states=(previewed,),
            transitions=(
                _transition(
                    "readiness",
                    kind="readiness",
                    subject_refs=refs,
                    required_states=(previewed,),
                    produced_state=_state("semantic.ready", refs),
                    available=True,
                    input_requirements=(
                        AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
                        AuthoringInputRequirement(
                            role="subject",
                            family="SemanticRef",
                            subject_refs=refs,
                            min_count=len(refs),
                            max_count=len(refs),
                        ),
                    ),
                ),
            ),
        )
    )


def contract_for_catalog_object(ref: str, kind: str) -> AuthoringContract:
    """Expose verify, preview, and readiness continuations for one catalog object.

    Parameters
    ----------
    ref:
        Canonical semantic ref id of the catalog object.
    kind:
        Semantic kind string (e.g. ``"entity"``, ``"metric"``).

    Returns
    -------
    AuthoringContract
        A normalized contract with a ``semantic.loaded`` current state and
        ``verify``, ``preview`` (for executable kinds), and ``readiness``
        transitions.
    """
    subject_refs = (ref,)
    loaded = _state("semantic.loaded", subject_refs)
    transitions: list[AuthoringTransition] = [
        _transition(
            "verify_object",
            kind="verify",
            subject_refs=subject_refs,
            required_states=(loaded,),
            produced_state=_state("semantic.verified", subject_refs),
            available=True,
            input_requirements=(
                AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
                AuthoringInputRequirement(
                    role="subject", family="CatalogObject", subject_refs=subject_refs
                ),
            ),
        ),
    ]
    executable_kinds = {
        "entity",
        "dimension",
        "time_dimension",
        "measure",
        "metric",
        "relationship",
    }
    if kind in executable_kinds:
        transitions.append(
            _transition(
                "preview",
                kind="preview",
                subject_refs=subject_refs,
                required_states=(loaded,),
                produced_state=_state("semantic.previewed", subject_refs),
                available=True,
                input_requirements=(
                    AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
                    AuthoringInputRequirement(
                        role="subject", family="CatalogObject", subject_refs=subject_refs
                    ),
                    AuthoringInputRequirement(role="evidence", family="DiscoverySnapshot"),
                ),
            )
        )
    transitions.append(
        _transition(
            "readiness",
            kind="readiness",
            subject_refs=subject_refs,
            required_states=(loaded,),
            produced_state=_state("semantic.ready", subject_refs),
            available=True,
            input_requirements=(
                AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
                AuthoringInputRequirement(
                    role="subject", family="CatalogObject", subject_refs=subject_refs
                ),
            ),
        )
    )
    return _normalize_contract(
        AuthoringContract(
            subject_refs=subject_refs,
            states=(loaded,),
            transitions=tuple(transitions),
        )
    )


def contract_for_semantic_catalog() -> AuthoringContract:
    """Expose bounded catalog-level browse/load affordances.

    Returns
    -------
    AuthoringContract
        A normalized contract with a single ``load`` transition and no
        per-object state.
    """
    return _normalize_contract(
        AuthoringContract(
            subject_refs=("semantic.catalog",),
            states=(),
            transitions=(
                _transition(
                    "load",
                    kind="load",
                    subject_refs=("semantic.catalog",),
                    produced_state=_state("semantic.loaded", ("semantic.catalog",)),
                    available=True,
                    input_requirements=(
                        AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
                    ),
                ),
            ),
        )
    )


def contract_for_readiness_report(
    analysis_ready_refs: tuple[str, ...],
    blockers: tuple[ReadinessBlocker, ...],
) -> AuthoringContract:
    """Expose analysis handoff transitions for ready and blocked refs.

    Parameters
    ----------
    analysis_ready_refs:
        Semantic refs that passed readiness certification.
    blockers:
        Readiness issues blocking handoff. Each blocker must expose ``.refs``
        and ``.kind`` attributes.

    Returns
    -------
    AuthoringContract
        A normalized contract with ``analysis_handoff`` transitions: one
        available transition per ready ref, and one blocked transition per
        ref on each blocker.
    """
    analysis_target = LiveHelpTarget(surface="analysis", canonical_id="boundary.semantic_handoff")
    transitions: list[AuthoringTransition] = []
    preview_refs = tuple(
        dict.fromkeys(
            ref
            for blocker in blockers
            if blocker.kind == "runtime_preview_missing"
            for ref in blocker.refs
        )
    )
    if preview_refs:
        transitions.append(
            _transition(
                "preview",
                kind="preview",
                subject_refs=preview_refs,
                produced_state=_state("semantic.previewed", preview_refs),
                available=True,
                input_requirements=(
                    AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
                    AuthoringInputRequirement(
                        role="subject",
                        family="CatalogObject",
                        subject_refs=preview_refs,
                        min_count=len(preview_refs),
                        max_count=len(preview_refs),
                    ),
                    AuthoringInputRequirement(
                        role="evidence",
                        family="DiscoverySnapshot",
                        min_count=1,
                        max_count=None,
                    ),
                ),
            )
        )
    for ref in analysis_ready_refs:
        transitions.append(
            _transition(
                "analysis_handoff",
                kind="analysis_handoff",
                subject_refs=(ref,),
                produced_state=_state("semantic.ready", (ref,)),
                available=True,
                input_requirements=(
                    AuthoringInputRequirement(
                        role="subject",
                        family="SemanticRef",
                        subject_refs=(ref,),
                    ),
                ),
                help_target=analysis_target,
            )
        )
    for blocker in blockers:
        for ref in blocker.refs:
            transitions.append(
                _transition(
                    "analysis_handoff",
                    kind="analysis_handoff",
                    subject_refs=(ref,),
                    available=False,
                    blocked_by=(blocker.kind,),
                    help_target=analysis_target,
                )
            )
    return _normalize_contract(
        AuthoringContract(
            subject_refs=tuple(analysis_ready_refs),
            states=tuple(_state("semantic.ready", (ref,)) for ref in analysis_ready_refs),
            transitions=tuple(transitions),
        )
    )
