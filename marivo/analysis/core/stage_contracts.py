"""Read-only stage metadata shared by compilation and execution."""

from __future__ import annotations

from typing import Protocol

from typing_extensions import Never, TypeVar

from marivo.analysis.core.data_types import DataColumn


class CheckedStageInput(Protocol):
    """Checked columns and keys of a retained invocation input."""

    @property
    def columns(self) -> tuple[DataColumn, ...]: ...

    @property
    def keys(self) -> tuple[str, ...]: ...


# An empty plan carries no inputs. When populated, the type tracks the
# original carrier through compilation so execution receives it unchanged.
CheckedStageInputT = TypeVar(
    "CheckedStageInputT", bound=CheckedStageInput, covariant=True, default=Never
)
