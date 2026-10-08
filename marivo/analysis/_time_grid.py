"""Public bounded time-grid values and exact receiver-bound handles."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from threading import RLock
from typing import Literal

from marivo._temporal import Grain, PeriodCalendarSnapshotV1, TimeScope
from marivo.analysis.core.time_grid import BoundTimeGrid, bind_grid
from marivo.analysis.datasets.errors import DatasetConstructionError

_TOKEN = object()


def _invalid(received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected="a grid and handles bound to one exact temporal authority",
        received=received,
        repair="Construct with mv.time_grid and pass the grid to observe(during=grid) or its endpoint to read/observe(at=endpoint).",
        location="analysis.time_grid",
    )


@dataclass(frozen=True, slots=True, repr=False)
class GridEndpoint:
    """An exact endpoint handle acquired from its owning TimeGrid."""

    _grid: TimeGrid
    _side: Literal["start", "end", "before_end"]

    def __post_init__(self) -> None:
        if self._side not in ("start", "end", "before_end"):
            raise _invalid("unknown endpoint interpretation")

    def show(self) -> None:
        """Display bounded endpoint identity and interpretation.

        Args: None.
        Returns: None; prints the owning grid and endpoint side.
        Example: ``grid.before_end.show()``.
        Constraints: Before-end is a symbolic left limit, never a timestamp tick.
        """
        print(
            f"GridEndpoint grid={self._grid._identity}; side={self._side}; use at= on members.read/observe"
        )

    def __repr__(self) -> str:
        return f"GridEndpoint(grid={self._grid._identity}, side={self._side}; use .show())"


class TimeGrid:
    """Finite time-coordinate specification; acquire with mv.time_grid()."""

    __slots__ = ("_bound", "_during", "_grain", "_identity", "_lock", "_timezone")

    def __init__(
        self, token: object, during: TimeScope, grain: Grain, timezone: str | None
    ) -> None:
        if token is not _TOKEN or not isinstance(during, TimeScope) or not isinstance(grain, Grain):
            raise _invalid("grid was not constructed with a TimeScope and Grain")
        self._during = during
        self._grain = grain
        self._timezone = timezone
        self._bound: BoundTimeGrid | None = None
        self._lock = RLock()
        self._identity = sha256(repr((during, grain, timezone)).encode()).hexdigest()[:16]

    @property
    def start(self) -> GridEndpoint:
        """Return the inclusive start endpoint handle.

        Args: None.
        Returns: A GridEndpoint bound to each clipped cell start.
        Example: ``endpoint = grid.start``.
        Constraints: This does not select a member or attribute version implicitly.
        """
        return GridEndpoint(self, "start")

    @property
    def end(self) -> GridEndpoint:
        """Return the exclusive end endpoint handle.

        Args: None.
        Returns: A GridEndpoint bound to each clipped cell end.
        Example: ``endpoint = grid.end``.
        Constraints: The endpoint is exact, without subtraction.
        """
        return GridEndpoint(self, "end")

    @property
    def before_end(self) -> GridEndpoint:
        """Return the symbolic left-limit endpoint handle.

        Args: None.
        Returns: A GridEndpoint bound to the left limit of each clipped cell end.
        Example: ``endpoint = grid.before_end``.
        Constraints: Never implemented by subtracting an epsilon.
        """
        return GridEndpoint(self, "before_end")

    def _bind(self, report: str, snapshot: PeriodCalendarSnapshotV1 | None = None) -> BoundTimeGrid:
        with self._lock:
            if self._bound is not None:
                if report != self._bound.report_timezone or (
                    snapshot is not None and snapshot.snapshot_digest != self._bound.snapshot_digest
                ):
                    raise _invalid("grid was already consumed with conflicting authority")
                return self._bound
            result = bind_grid(
                self._during,
                self._grain,
                report_timezone=report,
                explicit_timezone=self._timezone,
                snapshot=snapshot,
            )
            self._bound = result
            return result

    def show(self) -> None:
        """Display bounded grid identity and adopted authority.

        Args: None.
        Returns: None; prints scope, grain and authority status.
        Example: ``grid.show()``.
        Constraints: Does not resolve host timezone or read business rows.
        """
        state = (
            "unbound"
            if self._bound is None
            else f"timezone={self._bound.boundary_timezone}; cells={len(self._bound.cells)}"
        )
        print(f"TimeGrid {self._identity}; grain={self._grain.to_token()}; {state}")

    def __repr__(self) -> str:
        return f"TimeGrid(id={self._identity}; use .show())"


def time_grid(*, during: TimeScope, grain: Grain, timezone: str | None = None) -> TimeGrid:
    """Construct a finite time grid for interval observations and endpoint reads.

    Args:
        during: Finite half-open TimeScope.
        grain: Builtin or certified calendar Grain.
        timezone: Optional explicit boundary timezone; calendar authority must agree.
    Returns: A TimeGrid selecting every bucket window, with exact endpoint handles.
    Example: ``grid = mv.time_grid(during=mv.time_scope(start="2026-08-01", end="2026-09-01"), grain=mv.grain("day"))``.
    Constraints: The consuming Session binds its persisted report timezone once; no source read occurs.
    """
    return TimeGrid(_TOKEN, during, grain, timezone)
