from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class SourceLocation:
    file: str
    line: int


@dataclass(frozen=True)
class SemanticError(Exception):
    phase: Literal["decorator", "assembly", "load", "runtime", "parity"]
    kind: str
    location: SourceLocation | None
    function: str | None
    message: str
    hint: str | None = None
    refs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        Exception.__init__(self, str(self))

    def _template_fields(self) -> dict[str, str]:
        return {}

    def _resolved(self, key: str) -> str | None:
        value = self._template_fields().get(key)
        if value:
            return value
        return None

    def short_form(self) -> str:
        prefix = f"{self.phase}:{self.kind}"
        if self.location is not None:
            prefix = f"{prefix} at {self.location.file}:{self.location.line}"
        return f"{prefix}: {self.message}"

    def __str__(self) -> str:
        lines = [f"{self.__class__.__name__}: {self.message}"]

        detail_lines: list[str] = []
        location = self._resolved("location")
        if location is None and self.location is not None:
            location = f"{self.location.file}:{self.location.line}"
            if self.function is not None:
                location = f"{location} (in {self.function})"
        elif location is None and self.function is not None:
            location = f"(in {self.function})"
        if location is not None:
            detail_lines.append(f"发生位置: {location}")

        cause = self._resolved("cause") or f"{self.phase}:{self.kind}"
        detail_lines.append(f"原因: {cause}")
        if self.hint is not None:
            detail_lines.append(f"建议: {self.hint}")
        if detail_lines:
            lines.extend(["", *detail_lines])

        fix_snippet = self._resolved("fix_snippet")
        if fix_snippet:
            lines.extend(["", "正确写法:"])
            lines.extend(f"  {line}" for line in fix_snippet.splitlines())

        doc = self._resolved("doc")
        if doc is None and self.refs:
            doc = ", ".join(self.refs)
        if doc:
            lines.extend(["", f"相关文档: {doc}"])

        return "\n".join(lines)


class SemanticDecoratorError(SemanticError, ValueError):
    pass


class SemanticAssemblyError(SemanticError):
    pass


class SemanticRuntimeError(SemanticError):
    pass


class SemanticParityError(SemanticError):
    pass


class DatasourceNotRegisteredError(SemanticAssemblyError):
    """Dataset references a datasource that was never declared with @ms.datasource."""

    def _template_fields(self) -> dict[str, str]:
        return {
            "fix_snippet": (
                "import ibis\n"
                "import marivo.semantic_py as ms\n"
                "\n"
                '@ms.datasource(name="tiny_orders", backend_type="duckdb")\n'
                "def tiny_orders():\n"
                '    return ibis.duckdb.connect(":memory:")\n'
                "\n"
                '@ms.dataset(name="orders", datasource=tiny_orders)\n'
                "def orders(backend): ...\n"
            ),
            "doc": "marivo-skill/marivo-py-semantic/references/pitfalls.md",
        }


class IRReloadRequiredError(SemanticRuntimeError):
    """Loaded IR is stale; the caller must run ms.reload() before continuing.

    The v1 SDK does not auto-detect mtime drift - this class is provided so
    future code paths (Phase 4+) can raise it without changing the agent
    contract. Today it is only used for documentation and template tests.
    """

    def _template_fields(self) -> dict[str, str]:
        return {
            "fix_snippet": (
                "import marivo.semantic_py as ms\n"
                "ms.reload()  # rebuilds the IR from the current .py sources"
            ),
            "doc": "marivo-skill/marivo-py-semantic/references/pitfalls.md",
        }


class PySemanticNotFoundError(KeyError):
    def __init__(self, entity: str, name: str) -> None:
        super().__init__(f"{entity} '{name}' not found")
        self.entity = entity
        self.name = name


PySemanticNotFound = PySemanticNotFoundError


class SemanticLoadError(Exception):
    def __init__(self, errors: list[SemanticError]) -> None:
        self.errors = errors
        joined = "; ".join(str(error) for error in errors)
        super().__init__(joined)
