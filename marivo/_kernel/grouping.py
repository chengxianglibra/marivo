"""Dependency-neutral public family for immutable contribution grouping rules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from marivo._kernel.input_context import InputContext


class Grouping(ABC):
    """An immutable field, complete identity, calendar or membership grouping.

    Obtain values from ``mv.identity``, ``mv.membership``, ``scope.by`` or
    ``population.axes``. Pass ordinary grouping rules in ``observe(..., by=(grouping,))``
    or ``Metric.per(grouping)`` where supported. Pass CalendarAxis directly in observe.by or use calendar coordinates in
    a complete Population.
    Membership expands its full ordered field tuple;
    explicit non-first fields can locate interleaved coordinates.
    A rule carries no selected Population keys. Concrete variants are private;
    constructing or extending this family is not a public operation.
    """

    __slots__ = ()
    _identity_keys: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def _context(self) -> InputContext:
        return InputContext()

    @property
    @abstractmethod
    def kind(
        self,
    ) -> Literal["field", "identity", "calendar", "interval", "membership"]:
        """Return the captured grouping kind without source access."""

    @abstractmethod
    def show(self) -> None:
        """Print bounded captured grouping facts.

        Args: None.
        Returns: None.
        Example: ``population.axes[0].show()``.
        Constraints: Reads frozen metadata only; never enumerates domain keys.
        """
