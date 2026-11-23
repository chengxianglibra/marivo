"""Import probes for optional provider modules without opening connections."""

from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType


@dataclass(frozen=True)
class ModuleRequirement:
    """A selected provider's optional import probe, without opening a connection."""

    name: str
    load: Callable[[], ModuleType]


def _load_duckdb() -> ModuleType:
    import ibis.backends.duckdb as driver  # noqa: PLC0415  # Optional provider installed by this extra.

    module: ModuleType = driver
    return module


def _load_postgres() -> ModuleType:
    import ibis.backends.postgres as driver  # noqa: PLC0415  # Optional provider installed by this extra.

    module: ModuleType = driver
    return module


def _load_mysql() -> ModuleType:
    import pymysql as driver  # noqa: PLC0415  # Optional provider installed by this extra.

    module: ModuleType = driver
    return module


def _load_trino() -> ModuleType:
    import ibis.backends.trino as driver  # noqa: PLC0415  # Optional provider installed by this extra.

    module: ModuleType = driver
    return module


def _load_clickhouse() -> ModuleType:
    import ibis.backends.clickhouse as driver  # noqa: PLC0415  # Optional provider installed by this extra.

    module: ModuleType = driver
    return module


DUCKDB_REQUIREMENT = ModuleRequirement("ibis.backends.duckdb", _load_duckdb)
POSTGRES_REQUIREMENT = ModuleRequirement("ibis.backends.postgres", _load_postgres)
MYSQL_REQUIREMENT = ModuleRequirement("pymysql", _load_mysql)
TRINO_REQUIREMENT = ModuleRequirement("ibis.backends.trino", _load_trino)
CLICKHOUSE_REQUIREMENT = ModuleRequirement("ibis.backends.clickhouse", _load_clickhouse)
