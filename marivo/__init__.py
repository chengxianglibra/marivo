"""Marivo's public datasource, semantic, analysis, ontology, and help surfaces.

Use the four supported imports:

    import marivo.datasource as md
    import marivo.semantic as ms
    import marivo.analysis as mv
    import marivo.ontology as mo

Inspect the installed environment with:

    python -m marivo help

Then use ``marivo.help(...)`` for all focused help.
"""

from importlib import import_module
from typing import TYPE_CHECKING

from marivo._kernel.environment import MARIVO_VERSION

if TYPE_CHECKING:
    from marivo._help._typing import PublicHelp

    help: PublicHelp

__version__ = MARIVO_VERSION
__all__ = ["__version__", "help"]


def __getattr__(name: str) -> object:
    """Load the cross-surface help coordinator only when it is requested."""
    if name == "help":
        return import_module("marivo._help").help
    raise AttributeError(name)


def __dir__() -> list[str]:
    """Expose only the deliberate top-level public surface to discovery."""
    return sorted(__all__)
