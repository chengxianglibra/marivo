"""Read-only catalog over configured project datasources."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marivo.datasource import store as _store
from marivo.datasource.errors import DatasourceMissingError
from marivo.datasource.ir import AiContextIR
from marivo.datasource.manage import (
    DatasourceDescription,
    DatasourceList,
    DatasourceSummary,
    DatasourceTestResult,
    connect,
    describe,
    inspect_columns,
    inspect_table,
    preview,
    test,
)
from marivo.datasource.metadata import TableMetadata
from marivo.datasource.scan import ColumnInspection, ScanScope
from marivo.preview import PreviewResult
from marivo.render import result_repr


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
class DatasourceCatalog:
    """Read-only catalog over configured project datasources.

    Provides browsing and inspection methods that delegate to the existing
    ``md.*`` functions, giving a ``ms.load()``-like entry point for
    datasource discovery.

    Args:
        workspace_dir: Project root directory. Defaults to cwd.

    Returns:
        DatasourceCatalog with list(), get(), and inspection methods.

    Example:
        >>> import marivo.datasource as md
        >>> catalog = md.load()
        >>> catalog.list()
        >>> catalog.get("wh")
        >>> catalog.inspect_table("wh", md.table("orders"))

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

    def connect(self, name: str) -> Any:
        """Connect to a datasource by name.

        Args:
            name: The datasource name to connect to.

        Returns:
            An ibis backend for the datasource.

        Example:
            >>> backend = catalog.connect("wh")
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

    def inspect_table(
        self,
        datasource: str,
        table: str | Any | None = None,
        *,
        source: Any = None,
        database: str | tuple[str, ...] | None = None,
        include_partitions: bool = True,
    ) -> TableMetadata:
        """Schema, comments, nullability, and partition metadata for a table.

        Args:
            datasource: Name of the project datasource.
            table: Table name within the datasource (alternative to source).
            source: An ``EntitySourceIR`` (from ``md.table()``, ``md.parquet()``, or ``md.csv()``).
            database: Optional database/catalog path.
            include_partitions: Whether to include partition hints.

        Returns:
            A ``TableMetadata`` with columns, warnings, and optional partitions.

        Example:
            >>> catalog.inspect_table("wh", "orders")
        """
        return inspect_table(
            datasource,
            source=source,
            table=table,
            database=database,
            include_partitions=include_partitions,
            project_root=self.workspace_dir,
        )

    def inspect_columns(
        self,
        datasource: str,
        source: Any,
        *,
        columns: tuple[str, ...] | None = None,
        scope: ScanScope | None = None,
    ) -> ColumnInspection:
        """Profile selected columns from a datasource source.

        Args:
            datasource: Name of the project datasource.
            source: An ``EntitySourceIR`` (from ``md.table()``, ``md.parquet()``, or ``md.csv()``).
            columns: Column names to profile; None profiles all.
            scope: Bounded scan configuration; defaults to ScanScope().

        Returns:
            A ``ColumnInspection`` with per-column profiles and a ScanReport.

        Example:
            >>> catalog.inspect_columns("wh", md.table("orders"))
        """
        return inspect_columns(
            datasource,
            source,
            columns=columns,
            scope=scope,
            project_root=self.workspace_dir,
        )

    def preview(
        self,
        datasource: str,
        *,
        table: str,
        database: str | tuple[str, ...] | None = None,
        columns: Any = None,
        limit: int = 100,
        where: Any = None,
        order_by: Any = None,
        include_types: bool = True,
    ) -> PreviewResult:
        """Bounded, filtered preview of one datasource table.

        Args:
            datasource: Name of the project datasource.
            table: Table name within the datasource.
            database: Optional database/catalog path.
            columns: Optional column subset to select.
            limit: Maximum rows to return (default 100).
            where: Structured filter mappings.
            order_by: Structured order mappings.
            include_types: Whether to include column type information.

        Returns:
            A ``PreviewResult`` with rows, columns, types, and sample metadata.

        Example:
            >>> catalog.preview("wh", table="orders", limit=5)

        Note:
            Unlike ``inspect_table`` and ``inspect_columns``, ``preview``
            resolves the project root internally and does not forward
            ``workspace_dir``.
        """
        return preview(
            datasource,
            table=table,
            database=database,
            columns=columns,
            limit=limit,
            where=where,
            order_by=order_by,
            include_types=include_types,
        )

    def _repr_identity(self) -> str:
        count = len(_store.load_all(self.workspace_dir))
        return f"DatasourceCatalog datasources={count}"

    def render(self) -> str:
        datasources = sorted(
            _store.load_all(self.workspace_dir).values(),
            key=lambda item: item.name,
        )
        lines = [self._repr_identity()]
        if not datasources:
            lines.append("datasources: (none)")
        for datasource in datasources[:5]:
            lines.append(f"- name: {datasource.name}")
            lines.append(f"  backend_type: {datasource.backend_type}")
            lines.append(f"  fields: {_format_mapping(datasource.fields)}")
            lines.append(f"  env_refs: {_format_env_refs(datasource.env_refs)}")
            for line in _ai_context_lines(datasource.ai_context):
                lines.append(f"  {line}")
        if len(datasources) > 5:
            lines.append(f"... {len(datasources) - 5} more datasources; inspect md.list().items")
        lines.append("available:")
        lines.append("- .list()")
        lines.append("- .render()")
        lines.append("- .show()")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def show(self) -> None:
        print(self.render())


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
        >>> catalog.inspect_table("wh", "orders")

    Constraints:
        The catalog is read-only; use ``md.register()`` and ``md.remove()``
        to modify project datasources.
    """
    if workspace_dir is None:
        workspace_dir = Path.cwd()
    elif isinstance(workspace_dir, str):
        workspace_dir = Path(workspace_dir)
    return DatasourceCatalog(workspace_dir=workspace_dir)
