"""Dependency-neutral public family for immutable contribution grouping rules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from marivo._definition.relations import MembershipBinding
    from marivo._definition.types import AxisBinding, CalendarCoordinate, IntervalCoordinate


class Grouping(ABC):
    """An immutable field, complete identity, calendar or membership grouping.

    Obtain values from ``mv.identity``, ``mv.membership``, ``scope.by`` or
    ``population.axes``. Pass coordinate rules as ``by=(grouping,)``; membership
    rules use the consuming ``memberships=(grouping,)`` parameter.
    A rule carries no selected Population keys. Concrete variants are private;
    constructing or extending this family is not a public operation.
    """

    __slots__ = ()

    @property
    @abstractmethod
    def kind(self) -> Literal["field", "identity", "calendar", "interval", "membership"]:
        """Return the captured grouping kind without source access."""

    @property
    @abstractmethod
    def _binding(self) -> AxisBinding | CalendarCoordinate | IntervalCoordinate | MembershipBinding:
        """Return the canonical inert definition consumed by source capture."""

    def __repr__(self) -> str:
        from marivo._definition.relations import MembershipBinding
        from marivo._definition.types import CalendarCoordinate, IntervalCoordinate

        binding = self._binding
        if isinstance(binding, (CalendarCoordinate, IntervalCoordinate)):
            identity = binding.identity[:48]
        elif isinstance(binding, MembershipBinding):
            identity = f"fields={len(binding.fields)} paths={len(binding.paths)}"
        else:
            identity = (binding.field or binding.entity).ref.key[:96]
        return f"<Grouping kind={self.kind} id={identity}; use .show()>"

    def show(self) -> None:
        """Print bounded captured grouping facts.

        Args: None.
        Returns: None.
        Example: ``population.axes[0].show()``.
        Constraints: Reads frozen metadata only; never enumerates domain keys.
        """
        from marivo._definition.relations import MembershipBinding
        from marivo._definition.types import CalendarCoordinate, IntervalCoordinate

        binding = self._binding
        print(repr(self))
        if isinstance(binding, CalendarCoordinate):
            print(f"grain={binding.grain} timezone={binding.timezone} cells={len(binding.cells)}")
        elif isinstance(binding, IntervalCoordinate):
            print(f"start={binding.start} end={binding.end} timezone={binding.timezone}")
        elif isinstance(binding, MembershipBinding):
            print(f"unmatched={binding.unmatched}")
            for read in binding.fields[:8]:
                print(read.field.ref.key[:200])
            for path in binding.paths[:8]:
                print("via=" + " -> ".join(step.ref.ref.key for step in path)[:400])
        else:
            print(f"path={tuple(step.ref.key for step in binding.path)!r}"[:600])
            print(f"at={binding.at}")
