"""Marivo's public datasource, semantic, analysis, and help surfaces.

Use the three supported imports:

    import marivo.datasource as md
    import marivo.semantic as ms
    import marivo.analysis as mv

Inspect the installed environment with:

    python -m marivo help

Then use ``marivo.help(...)`` for all focused help.
"""

from importlib.metadata import version as _metadata_version

__version__ = _metadata_version("marivo")

__all__ = ["__version__", "help"]


def __getattr__(name: str) -> object:
    """Load the cross-surface help coordinator only when it is requested."""
    if name == "help":
        from importlib import import_module

        return import_module("marivo._help").help
    raise AttributeError(name)


def __dir__() -> list[str]:
    """Expose only the deliberate top-level public surface to discovery."""
    return sorted(__all__)
