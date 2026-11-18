"""Source trees shared by declarations during one semantic project load."""

from __future__ import annotations

import ast
import inspect
import textwrap
from functools import cached_property
from importlib.util import decode_source
from io import StringIO
from pathlib import Path


def _first_line(function: ast.FunctionDef) -> int:
    return function.decorator_list[0].lineno if function.decorator_list else function.lineno


class _ModelSource:
    def __init__(self, filename: str, data: bytes) -> None:
        self._text = decode_source(data)
        # Match dedent(inspect.getsource(fn)) for top-level bodies, including
        # whitespace-only lines inside literals. Python executes the raw bytes.
        self.tree = ast.parse(textwrap.dedent(self._text), filename=filename)

    @cached_property
    def functions(self) -> dict[tuple[int, str], ast.FunctionDef]:
        # Indented functions use the existing dedented source path. Dedenting a
        # snippet can also affect multiline string literals inside the body.
        return {
            (_first_line(node), node.name): node
            for node in self.tree.body
            if isinstance(node, ast.FunctionDef)
        }

    @cached_property
    def _lines(self) -> list[str]:
        return StringIO(self._text).readlines()

    def function_source(self, first_line: int) -> str:
        if not 1 <= first_line <= len(self._lines):
            raise OSError("Source line is out of bounds")
        return "".join(inspect.getblock(self._lines[first_line - 1 :]))

    @cached_property
    def named_decorator_lines(self) -> frozenset[int]:
        return frozenset(
            line
            for node in ast.walk(self.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and any(keyword.arg == "name" for keyword in decorator.keywords)
            for line in range(decorator.lineno, (decorator.end_lineno or decorator.lineno) + 1)
        )


class _ModelSources:
    def __init__(self) -> None:
        self._executed: dict[str, bytes] = {}
        self._files: dict[str, _ModelSource | None] = {}

    def capture(self, filename: str, data: bytes) -> None:
        self._executed[filename] = data
        self._files.pop(filename, None)

    def captured(self, filename: str) -> _ModelSource | None:
        return self.get(filename) if filename in self._executed else None

    def get(self, filename: str) -> _ModelSource | None:
        if filename not in self._files:
            try:
                data = self._executed.get(filename)
                if data is None:
                    data = Path(filename).read_bytes()
                self._files[filename] = _ModelSource(filename, data)
            except (OSError, SyntaxError, UnicodeError):
                self._files[filename] = None
        return self._files[filename]
