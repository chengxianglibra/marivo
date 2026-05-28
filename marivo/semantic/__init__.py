"""marivo.semantic - Python-native semantic layer (v1.1).

Public surface::

    import marivo.semantic as ms

    project = ms.find_project()        # or ms.SemanticProject(root)
    project.load()

    ms.model(name="sales", default=True)
    orders = ms.dataset(name="orders", datasource="warehouse")
    ms.metric(name="revenue", datasets=[orders], decomposition=ms.sum())
"""

from __future__ import annotations

from marivo.semantic import errors as errors
from marivo.semantic import typing as typing
from marivo.semantic.authoring import (
    component,
    dataset,
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
from marivo.semantic.help import help
from marivo.semantic.loader import find_project
from marivo.semantic.reader import SemanticProject

__all__ = [
    "SemanticProject",
    "component",
    "dataset",
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
