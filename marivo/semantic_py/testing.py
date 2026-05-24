from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from marivo.semantic_py.registry import SemanticProject, use_registry


@contextmanager
def scoped_project(root: str = "/tmp/marivo-semantic-py-test") -> Iterator[SemanticProject]:
    project = SemanticProject(root=root)
    with use_registry(project.registry):
        yield project
