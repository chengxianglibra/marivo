"""Project-independent definitions for built-in datasources; no connection state."""

from __future__ import annotations

from marivo.datasource.errors import DatasourceDuplicateError, DatasourceFieldInvalidError, repair
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation

DEFAULT_DATASOURCE_NAME = "default"
BUILTIN_SOURCE_LOCATION = "<built-in>"
DEFAULT_DATASOURCE_DESCRIPTION = "Built-in DuckDB; in-memory, non-persistent, no credentials."


def default_datasource() -> DatasourceIR:
    """Return a fresh definition so projects never share mutable configuration."""
    return DatasourceIR(
        semantic_id=DEFAULT_DATASOURCE_NAME,
        name=DEFAULT_DATASOURCE_NAME,
        backend_type="duckdb",
        fields={"path": ":memory:", "read_only": False},
        env_refs={},
        ai_context=AiContextIR(),
        python_symbol="",
        location=DatasourceSourceLocation(file=BUILTIN_SOURCE_LOCATION, line=0),
    )


def require_user_datasource_name(name: str, *, location: str, deleting: bool = False) -> None:
    """Reject mutations and authored declarations of the reserved built-in name."""
    if name != DEFAULT_DATASOURCE_NAME:
        return
    error_type = DatasourceFieldInvalidError if deleting else DatasourceDuplicateError
    raise error_type(
        message=(
            "Built-in datasource 'default' cannot be removed."
            if deleting
            else "Datasource name 'default' is reserved for the built-in in-memory DuckDB."
        ),
        expected="a user-defined datasource name other than 'default'",
        received=name,
        location=location,
        repair=repair(
            kind="reauthor",
            canonical_id="remove" if deleting else "register",
            action=(
                "Keep the built-in datasource; remove only a user-defined datasource."
                if deleting
                else "Rename the authored datasource; reference ms.ref.datasource('default') "
                "directly to use the built-in datasource without registration."
            ),
        ),
    )
