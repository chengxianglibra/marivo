"""Physical definition coordinates shared by declarations and diagnostics."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DatasourceSourceLocation:
    """Absolute source location for datasource error reporting."""

    file: str
    line: int
