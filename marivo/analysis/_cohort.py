"""Closed full-opportunity quantifiers for exact cohort membership."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from marivo.analysis.core.model import reject


@dataclass(frozen=True, slots=True, repr=False)
class EmptyOpportunityPolicy:
    """An explicit decision for an empty universal opportunity domain."""

    decision: Literal["true", "false", "undefined"]

    def __post_init__(self) -> None:
        if self.decision not in ("true", "false", "undefined"):
            reject(
                "a closed empty opportunity decision",
                repr(self.decision),
                "Use empty_opportunity.true/false/undefined().",
                "analysis.cohort",
            )

    def __repr__(self) -> str:
        return f"<EmptyOpportunityPolicy kind={self.decision}; use .show()>"

    def show(self) -> None:
        """Display the empty decision.

        Args: None.
        Returns: None.
        Example: ``policy.show()``.
        Constraints: Applies only to all_instances.
        """
        print(repr(self))


class empty_opportunity:  # noqa: N801 - public namespace spelling is contractual
    """Explicit empty-domain decisions, distinct from Metric empty contributions."""

    @staticmethod
    def true() -> EmptyOpportunityPolicy:
        """Accept an empty opportunity domain.

        Args: None.
        Returns: A true empty opportunity policy.
        Example: ``mv.all_instances(empty=mv.empty_opportunity.true())``.
        Constraints: Universal cohort quantification only.
        """
        return EmptyOpportunityPolicy("true")

    @staticmethod
    def false() -> EmptyOpportunityPolicy:
        """Reject membership on an empty opportunity domain.

        Args: None.
        Returns: A false empty opportunity policy.
        Example: ``mv.all_instances(empty=mv.empty_opportunity.false())``.
        Constraints: Universal cohort quantification only.
        """
        return EmptyOpportunityPolicy("false")

    @staticmethod
    def undefined() -> EmptyOpportunityPolicy:
        """Require a decision error for an empty opportunity domain.

        Args: None.
        Returns: An undefined empty opportunity policy.
        Example: ``mv.all_instances(empty=mv.empty_opportunity.undefined())``.
        Constraints: An exact cohort cannot silently drop undecidable targets.
        """
        return EmptyOpportunityPolicy("undefined")


@dataclass(frozen=True, slots=True, repr=False)
class AnyInstance:
    """Existential quantification over every opportunity."""

    def __repr__(self) -> str:
        return "<AnyInstance kind=any; use .show()>"

    def show(self) -> None:
        """Display this rule.

        Args: None.
        Returns: None.
        Example: ``mv.any_instance().show()``.
        Constraints: Requires complete opportunities.
        """
        print(repr(self))


@dataclass(frozen=True, slots=True, repr=False)
class AtLeast:
    """A positive threshold on true opportunities."""

    count: int

    def __post_init__(self) -> None:
        if type(self.count) is not int or self.count < 1 or self.count > 2**63 - 1:
            reject(
                "a positive int64 count excluding bool",
                repr(self.count),
                "Pass a positive integer to at_least().",
                "analysis.cohort",
            )

    def __repr__(self) -> str:
        return f"<AtLeast kind=threshold count={self.count}; use .show()>"

    def show(self) -> None:
        """Display this rule.

        Args: None.
        Returns: None.
        Example: ``mv.at_least(3).show()``.
        Constraints: Requires complete opportunities.
        """
        print(repr(self))


@dataclass(frozen=True, slots=True, repr=False)
class AllInstances:
    """Universal quantification with an explicit empty decision."""

    empty: EmptyOpportunityPolicy

    def __post_init__(self) -> None:
        if type(self.empty) is not EmptyOpportunityPolicy:
            reject(
                "an EmptyOpportunityPolicy",
                repr(self.empty),
                "Use mv.empty_opportunity.true/false/undefined().",
                "analysis.cohort",
            )

    def __repr__(self) -> str:
        return f"<AllInstances kind=all empty={self.empty.decision}; use .show()>"

    def show(self) -> None:
        """Display this rule.

        Args: None.
        Returns: None.
        Example: ``mv.all_instances(empty=mv.empty_opportunity.false()).show()``.
        Constraints: Requires complete opportunities.
        """
        print(repr(self))


CohortRule: TypeAlias = AnyInstance | AtLeast | AllInstances


def any_instance() -> AnyInstance:
    """Select a Subject with at least one true opportunity.

    Args: None.
    Returns: An existential rule.
    Example: ``members.cohort(values.value.gt(0), rule=mv.any_instance())``.
    Constraints: Existing Unknown is decidable only when a true instance exists.
    """
    return AnyInstance()


def at_least(count: int) -> AtLeast:
    """Select a Subject with a required number of true opportunities.

    Args: count: A positive integer excluding bool.
    Returns: A threshold rule.
    Example: ``members.cohort(values.value.gt(0), rule=mv.at_least(3))``.
    Constraints: Every target must have a decidable qualification.
    """
    return AtLeast(count)


def all_instances(*, empty: EmptyOpportunityPolicy) -> AllInstances:
    """Select a Subject whose complete opportunities are all true.

    Args: empty: The explicit policy for zero opportunities.
    Returns: A universal rule.
    Example: ``mv.all_instances(empty=mv.empty_opportunity.false())``.
    Constraints: Null/Undefined scalar operands remain consumption errors.
    """
    return AllInstances(empty)


def decide(
    rule: Literal["any", "at_least", "all"],
    count: int,
    empty: Literal["true", "false", "undefined"],
    t: int,
    u: int,
    f: int,
) -> bool | None:
    """Decide membership from complete counts without interpreting missing rows."""
    if rule == "any":
        return True if t else False if not u else None
    if rule == "at_least":
        return True if t >= count else False if t + u < count else None
    if not t + u + f:
        return True if empty == "true" else False if empty == "false" else None
    return False if f else True if not u else None
