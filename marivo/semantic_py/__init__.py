"""marivo.semantic_py - Python-native semantic layer (v1.1).

Public surface::

    import marivo.semantic_py as ms

    project = ms.find_project()        # or ms.SemanticProject(root)
    project.load()

    ms.model(name="sales", default=True)
    wh = ms.datasource(name="warehouse", backend_type="duckdb")
    orders = ms.dataset(name="orders", datasource=wh)
    ms.metric(name="revenue", datasets=[orders], decomposition=ms.sum())
"""

from __future__ import annotations

from marivo.semantic_py import errors as errors
from marivo.semantic_py import typing as typing
from marivo.semantic_py.authoring import (
    component,
    dataset,
    datasource,
    field,
    metric,
    model,
    ratio,
    ref,
    relationship,
    sum,
    time_field,
    weighted_average,
)
from marivo.semantic_py.help import help
from marivo.semantic_py.loader import find_project
from marivo.semantic_py.reader import SemanticProject

__all__ = [
    "SemanticProject",
    "component",
    "dataset",
    "datasource",
    "errors",
    "field",
    "find_project",
    "help",
    "metric",
    "model",
    "ratio",
    "ref",
    "relationship",
    "sum",
    "time_field",
    "typing",
    "weighted_average",
]
