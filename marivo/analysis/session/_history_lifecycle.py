"""The sole Session-owned public Lifecycle replay constructor."""

from dataclasses import dataclass

from marivo._temporal import TimeScope
from marivo.analysis.core.domain_captures import fail
from marivo.analysis.domains.completeness import CompletenessDeclaration
from marivo.analysis.lifecycle import FromInception
from marivo.analysis.materialization.graph_history import construct
from marivo.analysis.observation.contracts import ObservationOwner
from marivo.analysis.public_dsl import LogicalAnalysisDomain, LogicalHistoryResult, new_history
from marivo.refs import Ref, StateModelKind


@dataclass(frozen=True, slots=True, repr=False)
class HistoryLifecycle:
    """Session-owned source-free canonical History construction."""

    _owner: ObservationOwner

    def replay(
        self,
        model: Ref[StateModelKind],
        *,
        population: LogicalAnalysisDomain,
        window: TimeScope,
        seed: FromInception,
        completeness: tuple[CompletenessDeclaration, ...] = (),
    ) -> LogicalHistoryResult:
        """Replay a governed StateModel from real inception over explicit Subjects.

        Args: model: Exact loaded StateModel Ref. population: Logical Subject domain.
            window: Half-open report window. seed: Required from_inception().
            completeness: Exact Event coverage declarations; bounded claims cannot prove origin.
        Returns: A LogicalHistoryResult retaining every input Subject.
        Example: ``history = session.lifecycle.replay(model, population=members,
            window=window, seed=mv.from_inception(), completeness=claims)``.
        Constraints: One Session, source-only; the StateModel owns business order.
        """
        if not isinstance(population, LogicalAnalysisDomain):
            fail("input_mode", "Lifecycle replay requires a logical AnalysisDomain")
        node = construct(
            population._node,
            self._owner,
            model,
            window=window,
            seed=seed,
            completeness=completeness,
        )
        return new_history(node, population._runtime)

    def __repr__(self) -> str:
        return f"<HistoryLifecycle session={self._owner.session_id}; use marivo.help('analysis.lifecycle.replay')>"
