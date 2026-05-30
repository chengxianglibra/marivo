"""Project-level datasource authoring API."""

from __future__ import annotations

from marivo.datasource.authoring import DatasourceRef, DatasourceSpec, datasource, ref
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
    "DatasourceRef",
    "DatasourceSourceLocation",
    "DatasourceSpec",
    "datasource",
    "load_datasources",
    "ref",
]
