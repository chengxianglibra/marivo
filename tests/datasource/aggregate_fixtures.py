"""Disconnected native compiler builders shared by source compilation tests."""

from importlib import import_module

from ibis.backends.sql import SQLBackend

from marivo.datasource.engines._aggregate_compilers import configure_aggregate_compiler
from marivo.datasource.engines._mysql_backend import MySQLBackend


def compiler_backend(name: str) -> SQLBackend:
    backend = (
        MySQLBackend() if name == "mysql" else import_module(f"ibis.backends.{name}").Backend()
    )
    configure_aggregate_compiler(backend)
    return backend
