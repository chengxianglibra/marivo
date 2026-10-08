"""Public Event matching bound to the current AnalysisDomain."""

from dataclasses import dataclass
from datetime import datetime

from marivo._temporal import TimeScope
from marivo.analysis.core.domain_captures import fail
from marivo.analysis.domains.completeness import CompletenessDeclaration
from marivo.analysis.event import EventPattern, EveryStart, FirstPerSubject
from marivo.analysis.materialization.graph_journey import construct
from marivo.analysis.observation.contracts import ObservationOwner
from marivo.analysis.public_dsl import LogicalAnalysisDomain, LogicalJourneyResult, new_journeys
from marivo.refs import BusinessOrderKind, Ref


@dataclass(frozen=True, slots=True, repr=False)
class JourneyEvents:
    """Session-owned canonical Journey construction."""

    _owner: ObservationOwner

    def match(
        self,
        pattern: EventPattern,
        *,
        population: LogicalAnalysisDomain,
        cohort_window: TimeScope,
        completion_through: datetime,
        matching: FirstPerSubject | EveryStart,
        business_order: Ref[BusinessOrderKind] | None = None,
        completeness: tuple[CompletenessDeclaration, ...] = (),
    ) -> LogicalJourneyResult:
        """Match canonical Journeys using an explicit logical Subject population.

        Args: pattern: Ordered Event steps. population: Current AnalysisDomain.
            cohort_window: Half-open start window. completion_through: Exclusive follow-up limit.
            matching: First-per-subject or every-start assignment.
            business_order: Optional declared order for tied instants.
            completeness: Explicit bounded coverage assumptions.
        Returns: A LogicalJourneyResult in the same governed DAG.
        Example: ``journeys = session.events.match(pattern, population=members,
            cohort_window=window, completion_through=end, matching=mv.first_per_subject())``.
        Constraints: One Session; fixed populations reject before source reads.
        """
        if not isinstance(population, LogicalAnalysisDomain):
            fail("input_mode", "Journey matching requires a logical AnalysisDomain")
        node = construct(
            population._node,
            self._owner,
            pattern,
            cohort_window=cohort_window,
            completion_through=completion_through,
            matching=matching,
            business_order=business_order,
            completeness=completeness,
        )
        return new_journeys(node, population._runtime)

    def __repr__(self) -> str:
        return f"<JourneyEvents session={self._owner.session_id}; use marivo.help('analysis.events.match')>"
