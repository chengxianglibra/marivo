from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.engines import EngineService
from app.mappings import MappingService
from app.registry import EngineRegistry, MappingRegistry, RegistrySyncEngine, SourceRegistry
from app.sources import SourceService
from app.storage.sqlite_metadata import SQLiteMetadataStore
from app.sync import SyncEngine


class RegistryBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.meta_path = Path(self.temp_dir.name) / "registry-boundaries.meta.sqlite"
        self.metadata = SQLiteMetadataStore(self.meta_path)
        self.metadata.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_legacy_facades_subclass_registry_layer(self) -> None:
        self.assertIsInstance(SourceService(self.metadata), SourceRegistry)
        self.assertIsInstance(EngineService(self.metadata), EngineRegistry)
        self.assertIsInstance(MappingService(self.metadata), MappingRegistry)
        self.assertIsInstance(SyncEngine(self.metadata), RegistrySyncEngine)


if __name__ == "__main__":
    unittest.main()
