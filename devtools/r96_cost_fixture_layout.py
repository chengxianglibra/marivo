"""Explicit, independently recorded indexed deployments for R9.6 fixtures."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Literal, TypeAlias

import pytest

from devtools import r96_cost_scenarios as scenarios
from marivo.datasource.ir import TableSourceIR
from tests.r9_source_cases import Case

FixtureLayout: TypeAlias = Literal["unindexed", "indexed-keys"]


def _index_statements(case: Case, other: str) -> list[str]:
    if not isinstance(case.source, TableSourceIR):
        raise ValueError("Indexed cost deployment requires ordinary tables")
    return [
        f"CREATE INDEX {table}_full_key ON {table} (tenant, id, revision)"
        for table in (case.source.table, other)
    ]


def _apply_indexes(backend: str, root: Path, statements: list[str]) -> None:
    if backend == "sqlite":
        with closing(sqlite3.connect(root / "r96.sqlite")) as admin, admin:
            for statement in statements:
                admin.execute(statement)
    elif backend == "mysql":
        from tests.multisource_environment import mysql_analysis as mysql

        with mysql.connection(admin=True) as admin, admin.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
    else:
        raise ValueError("Indexed cost deployment is defined only for SQLite and MySQL")


@contextmanager
def fixture_layout(layout: FixtureLayout) -> Iterator[None]:
    if layout == "unindexed":
        yield
        return
    if layout != "indexed-keys":
        raise ValueError("Unknown R9.6 fixture layout")
    original = scenarios._tables

    @contextmanager
    def indexed_tables(
        backend: str,
        profile: str,
        root: Path,
        monkeypatch: pytest.MonkeyPatch,
        rows: list[dict[str, object]],
    ) -> Iterator[tuple[Case, str, str]]:
        if backend not in ("sqlite", "mysql") or profile not in ("table", "ordinary-table"):
            raise ValueError("Indexed cost deployment requires SQLite or MySQL ordinary tables")
        with original(backend, profile, root, monkeypatch, rows) as (case, subjects, other):
            statements = _index_statements(case, other)
            _apply_indexes(backend, root, statements)
            case.environment.update(
                fixture_layout="indexed-keys",
                fixture_index_ddl=statements,
                fixture_index_unique=False,
                fixture_setup_measured=False,
            )
            yield case, subjects, other

    with pytest.MonkeyPatch.context() as layout_patch:
        layout_patch.setattr(scenarios, "_tables", indexed_tables)
        yield
