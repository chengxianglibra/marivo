"""Project-level datasource authoring API."""

from __future__ import annotations

from marivo.datasource.authoring import datasource
from marivo.datasource.ir import DatasourceAiContextIR, DatasourceIR, DatasourceSourceLocation
from marivo.datasource.loader import load_datasources

__all__ = [
    "DatasourceAiContextIR",
    "DatasourceIR",
    "DatasourceSourceLocation",
    "datasource",
    "load_datasources",
]
