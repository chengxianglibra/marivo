"""Loading a project where a dataset names an unregistered datasource raises
DatasourceNotRegisteredError (a SemanticAssemblyError subclass)."""

from __future__ import annotations

import pytest

import marivo.semantic_py as ms
from marivo.semantic_py.errors import (
    DatasourceNotRegisteredError,
    SemanticAssemblyError,
    SemanticLoadError,
)
from marivo.semantic_py.registry import SemanticProject, use_registry
from marivo.semantic_py.validator import validate_all


def _build_broken_project() -> SemanticProject:
    project = SemanticProject(root=":phase2_validator_test:")
    with use_registry(project.registry):
        ms.model(name="phase2_broken")

        @ms.dataset(name="orders", datasource=ms.ref("datasource.not_registered"))
        def orders(backend):  # type: ignore[no-untyped-def]
            return backend.table("orders")

    return project


def test_load_raises_datasource_not_registered_error() -> None:
    project = _build_broken_project()
    with pytest.raises(SemanticLoadError) as excinfo:
        validate_all(project.registry)
    matching = [
        err for err in excinfo.value.errors if isinstance(err, DatasourceNotRegisteredError)
    ]
    assert matching, (
        "validator should raise DatasourceNotRegisteredError, not plain "
        f"SemanticAssemblyError; got: {[type(e).__name__ for e in excinfo.value.errors]}"
    )
    assert isinstance(matching[0], SemanticAssemblyError)
    rendered = str(matching[0])
    assert "DatasourceNotRegisteredError" in rendered
    assert "正确写法:" in rendered
