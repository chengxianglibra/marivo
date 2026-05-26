"""Project-level datasource authoring API."""

from __future__ import annotations

from marivo.datasource_py.authoring import datasource
from marivo.datasource_py.ir import DatasourceAiContextIR, DatasourceIR, DatasourceSourceLocation
from marivo.datasource_py.loader import load_datasources

__all__ = [
    "DatasourceAiContextIR",
    "DatasourceIR",
    "DatasourceSourceLocation",
    "datasource",
    "load_datasources",
]
