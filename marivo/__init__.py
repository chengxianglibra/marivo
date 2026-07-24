"""Marivo's public datasource, semantic, and analysis surfaces.

Use the three supported imports:

    import marivo.datasource as md
    import marivo.semantic as ms
    import marivo.analysis as mv

Inspect the environment-bound help for each surface with:

    python -m marivo help datasource
    python -m marivo help semantic
    python -m marivo help analysis

The top-level package exports only ``__version__``.
"""

from importlib.metadata import version as _metadata_version

__version__ = _metadata_version("marivo")
