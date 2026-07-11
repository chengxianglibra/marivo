"""Read-only catalog over configured project datasources."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from marivo.datasource import store as _store
from marivo.datasource.errors import DatasourceMissingError
from marivo.datasource.ir import AiContextIR
from marivo.datasource.manage import (
    DatasourceConnection,
    DatasourceDescription,
    DatasourceList,
    DatasourceSummary,
    DatasourceTestResult,
    connect,
    describe,
    test,
)
from marivo.render import Card, RenderableResult


def _summary_list(project_root: Path) -> DatasourceList:
    return DatasourceList(
        tuple(
            DatasourceSummary(name=p.name, backend_type=p.backend_type)
            for p in sorted(_store.load_all(project_root).values(), key=lambda item: item.name)
        )
    )


def _format_mapping(mapping: dict[str, object]) -> str:
    if not mapping:
        return "(none)"
    return ", ".join(f"{key}: {value}" for key, value in sorted(mapping.items()))


def _format_env_refs(mapping: dict[str, str]) -> str:
    if not mapping:
        return "(none)"
    return ", ".join(f"{key}_env={value}" for key, value in sorted(mapping.items()))


def _format_tuple(values: tuple[str, ...]) -> str:
    if not values:
        return "(none)"
    return ", ".join(values)


def _ai_context_lines(context: AiContextIR) -> tuple[str, ...]:
    return (
        f"business_definition: {context.business_definition or '(none)'}",
        f"guardrails: {_format_tuple(context.guardrails)}",
        f"synonyms: {_format_tuple(context.synonyms)}",
        f"examples: {_format_tuple(context.examples)}",
        f"instructions: {context.instructions or '(none)'}",
        f"owner_notes: {context.owner_notes or '(none)'}",
    )


@dataclass(frozen=True, repr=False)
class DatasourceCatalog(RenderableResult):
    """Read-only catalog over configured project datasources.

    Provides browsing methods that delegate to the existing ``md.*``
    functions, giving a ``ms.load()``-like entry point for datasource
    discovery.

    Args:
        workspace_dir: Project root directory. Defaults to cwd.

    Returns:
        DatasourceCatalog with list(), get(), describe(), connect(), and
        test() methods.

    Example:
        >>> import marivo.datasource as md
        >>> catalog = md.load()
        >>> catalog.list()
        >>> catalog.get("wh")
        >>> md.inspect(md.ref("datasource.wh"), md.table("orders"))

    Constraints:
        catalog is obtained via md.load(), not constructed directly.
    """

    workspace_dir: Path

    def list(self) -> DatasourceList:
        """List configured project datasources as a displayable DatasourceList.

        Returns:
            ``DatasourceList`` containing sorted ``DatasourceSummary`` rows.

        Example:
            >>> catalog = md.load()
            >>> catalog.list().show()
        """
        return _summary_list(self.workspace_dir)

    def get(self, name: str) -> DatasourceSummary:
        """Retrieve a single datasource summary by name.

        Args:
            name: The datasource name to look up.

        Returns:
            A ``DatasourceSummary`` for the named datasource.

        Raises:
            DatasourceMissingError: When the name has no project file.

        Example:
            >>> catalog = md.load()
            >>> catalog.get("wh")
            DatasourceSummary(name='wh', ...)
        """
        datasource = _store.load_one(name, self.workspace_dir)
        if datasource is None:
            raise DatasourceMissingError(
                message=f"datasource {name!r} is not configured",
                details={"datasource": name, "available": _store.list_names()},
            )
        return DatasourceSummary(
            name=datasource.name,
            backend_type=datasource.backend_type,
        )

    def describe(self, name: str) -> DatasourceDescription:
        """Show literal fields and env refs for one datasource.

        Args:
            name: The datasource name to describe.

        Returns:
            A ``DatasourceDescription`` with literal_fields and env_refs.

        Example:
            >>> catalog.describe("wh")
        """
        return describe(name)

    def connect(self, name: str) -> DatasourceConnection:
        """Connect to a datasource by name.

        Args:
            name: The datasource name to connect to.

        Returns:
            A ``DatasourceConnection`` proxy for the datasource backend.

        Example:
            >>> with catalog.connect("wh") as con:
            ...     con.raw_sql("SELECT 1")
        """
        return connect(name)

    def test(self, name: str) -> DatasourceTestResult:
        """Test connectivity to a datasource.

        Args:
            name: The datasource name to test.

        Returns:
            A ``DatasourceTestResult`` with ok/error/latency.

        Example:
            >>> result = catalog.test("wh")
        """
        return test(name)

    def _repr_identity(self) -> str:
        count = len(_store.load_all(self.workspace_dir))
        return f"DatasourceCatalog datasources={count}"

    def _card(self) -> Card:
        datasources = sorted(
            _store.load_all(self.workspace_dir).values(),
            key=lambda item: item.name,
        )
        card = Card(
            identity=self._repr_identity(),
            available=(
                ".list()",
                ".get(name)",
                ".describe(name)",
                ".connect(name)",
                ".test(name)",
                ".render()",
                ".show()",
            ),
        )
        if not datasources:
            card = card.field(label="datasources", value="none")
        for datasource in datasources:
            card = card.listing(
                label=datasource.name,
                items=(
                    f"backend_type={datasource.backend_type}",
                    f"fields={_format_mapping(datasource.fields)}",
                    f"env_refs={_format_env_refs(datasource.env_refs)}",
                    *_ai_context_lines(datasource.ai_context),
                ),
            )
        return card


def load(
    *,
    workspace_dir: str | Path | None = None,
) -> DatasourceCatalog:
    """Load the project datasource catalog.

    Returns a ``DatasourceCatalog`` for browsing and inspecting configured
    project datasources, providing an ``ms.load()``-consistent entry point.

    Args:
        workspace_dir: Optional project root directory; defaults to cwd.

    Returns:
        A ``DatasourceCatalog`` for browsing configured datasources.

    Example:
        >>> import marivo.datasource as md
        >>> catalog = md.load()
        >>> catalog.list()
        >>> catalog.get("wh")
        >>> md.inspect(md.ref("datasource.wh"), md.table("orders"))

    Constraints:
        The catalog is read-only; use ``md.register()`` and ``md.remove()``
        to modify project datasources.
    """
    if workspace_dir is None:
        workspace_dir = Path.cwd()
    elif isinstance(workspace_dir, str):
        workspace_dir = Path(workspace_dir)
    return DatasourceCatalog(workspace_dir=workspace_dir)
