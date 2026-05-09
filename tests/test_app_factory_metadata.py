from __future__ import annotations

import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from marivo.api.app_factory import _resolve_storage
from marivo.config import MarivoConfig, MetadataConfig
from marivo.storage.dialect import SQLITE_METADATA_DIALECT, MetadataDialect
from marivo.storage.metadata import MetadataStore
from marivo.storage.sqlite_metadata import SQLiteMetadataStore


class DummyMetadataStore(MetadataStore):
    dialect: MetadataDialect = SQLITE_METADATA_DIALECT

    def __init__(self) -> None:
        self.initialized = False

    def initialize(self) -> None:
        self.initialized = True

    @contextmanager
    def connect(self) -> Iterator[Any]:
        yield object()

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        raise NotImplementedError

    def execute_many(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        raise NotImplementedError

    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    def query_one(self, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
        raise NotImplementedError


class RecordingMySQLStore(DummyMetadataStore):
    created_with: dict[str, Any] | None = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        RecordingMySQLStore.created_with = kwargs


class AppFactoryMetadataResolutionTests(unittest.TestCase):
    def test_injected_metadata_store_takes_precedence(self) -> None:
        store = DummyMetadataStore()
        analytics: Any = object()

        _resolved, metadata_store, _analytics = _resolve_storage(
            None,
            store,
            analytics,
            MarivoConfig(),
            Path("marivo.yaml"),
            False,
        )

        self.assertIs(metadata_store, store)
        self.assertTrue(store.initialized)

    def test_db_path_carveout_uses_sqlite_metadata_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            analytics: Any = object()
            db_path = Path(tmp) / "test.duckdb"

            _resolved, metadata_store, _analytics = _resolve_storage(
                db_path,
                None,
                analytics,
                MarivoConfig(),
                Path(tmp) / "missing.yaml",
                False,
            )

            self.assertIsInstance(metadata_store, SQLiteMetadataStore)
            self.assertEqual(metadata_store.db_path, db_path.with_suffix(".meta.sqlite"))

    def test_mysql_config_builds_mysql_metadata_store(self) -> None:
        config = MarivoConfig(
            metadata=MetadataConfig.model_validate(
                {
                    "engine": "mysql",
                    "host": "db.example",
                    "database": "marivo",
                    "user": "marivo",
                    "password": "secret",
                    "pool_size": 2,
                }
            )
        )
        analytics: Any = object()

        with patch("marivo.profiles.server.MySQLMetadataStore", RecordingMySQLStore):
            _resolve_storage(None, None, analytics, config, Path("marivo.yaml"), True)

        self.assertEqual(
            RecordingMySQLStore.created_with,
            {
                "host": "db.example",
                "port": 3306,
                "database": "marivo",
                "user": "marivo",
                "password": "secret",
                "connect_timeout": 10,
                "pool_size": 2,
                "ssl": None,
                "dsn": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
