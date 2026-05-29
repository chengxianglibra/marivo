"""Project-level datasource authoring API."""

from __future__ import annotations

from marivo.datasource.authoring import datasource
from marivo.datasource.help import help
from marivo.datasource.ir import (
    AiContextIR,
    DatasourceAiContextIR,
    DatasourceIR,
    DatasourceSourceLocation,
)
from marivo.datasource.loader import load_datasources

__all__ = [
    "AiContextIR",
    "DatasourceAiContextIR",
    "DatasourceIR",
    "DatasourceSourceLocation",
    "datasource",
    "help",
    "load_datasources",
]
