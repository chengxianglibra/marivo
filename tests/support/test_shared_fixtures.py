from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.shared_fixtures import make_temp_metadata_store


class TestManagedMetadataStoreFixture(unittest.TestCase):
    def test_temp_metadata_store_cleanup_removes_sqlite_directory(self) -> None:
        prefix = "marivo_shared_fixture_cleanup_"
        temp_root = Path(tempfile.gettempdir())
        before = set(temp_root.glob(f"{prefix}*"))

        store = make_temp_metadata_store(prefix=prefix)
        created = set(temp_root.glob(f"{prefix}*")) - before

        self.assertEqual(len(created), 1)
        temp_dir = created.pop()
        self.assertTrue((temp_dir / "meta.sqlite").exists())

        store.close()

        self.assertFalse(temp_dir.exists())
