"""Static signature of the lazy public Help export."""

from collections.abc import Callable
from typing import Protocol

from typing_extensions import TypeAliasType

PublicHelpTarget = TypeAliasType(
    "PublicHelpTarget",
    str | Callable[..., object] | type[object] | None,
)


class PublicHelp(Protocol):
    def __call__(self, target: PublicHelpTarget = None) -> None: ...
