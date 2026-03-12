from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


class UIBothEnabledTests(unittest.TestCase):
    """ui.enabled: true -> both /admin and /ui should return 200."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        config_path = Path(cls.tmp.name) / "omnidb.yaml"
        config_path.write_text("ui:\n  enabled: true\n")
        db_path = Path(cls.tmp.name) / "test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.app = create_app(db_path=db_path, config_path=config_path)
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.tmp.cleanup()

    def test_admin_returns_html(self) -> None:
        resp = self.client.get("/admin")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("OmniDB", resp.text)

    def test_ui_returns_html(self) -> None:
        resp = self.client.get("/ui")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("OmniDB", resp.text)

    def test_static_admin_accessible(self) -> None:
        resp = self.client.get("/static/admin.html")
        self.assertEqual(resp.status_code, 200)

    def test_static_user_accessible(self) -> None:
        resp = self.client.get("/static/user.html")
        self.assertEqual(resp.status_code, 200)


class UIBothDisabledTests(unittest.TestCase):
    """No config file -> both /admin and /ui should return 404."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        db_path = Path(cls.tmp.name) / "test.duckdb"
        config_path = Path(cls.tmp.name) / "nonexistent.yaml"
        get_seeded_duckdb_path(db_path)
        cls.app = create_app(db_path=db_path, config_path=config_path)
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.tmp.cleanup()

    def test_admin_returns_404(self) -> None:
        resp = self.client.get("/admin")
        self.assertEqual(resp.status_code, 404)

    def test_ui_returns_404(self) -> None:
        resp = self.client.get("/ui")
        self.assertEqual(resp.status_code, 404)

    def test_static_returns_404(self) -> None:
        resp = self.client.get("/static/admin.html")
        self.assertEqual(resp.status_code, 404)


class UIAdminOnlyTests(unittest.TestCase):
    """admin_enabled: true, user_enabled: false -> /admin 200, /ui 404."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        config_path = Path(cls.tmp.name) / "omnidb.yaml"
        config_path.write_text("ui:\n  admin_enabled: true\n  user_enabled: false\n")
        db_path = Path(cls.tmp.name) / "test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.app = create_app(db_path=db_path, config_path=config_path)
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.tmp.cleanup()

    def test_admin_returns_html(self) -> None:
        resp = self.client.get("/admin")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("OmniDB", resp.text)

    def test_ui_returns_404(self) -> None:
        resp = self.client.get("/ui")
        self.assertEqual(resp.status_code, 404)


class UIUserOnlyTests(unittest.TestCase):
    """user_enabled: true, admin_enabled: false -> /admin 404, /ui 200."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        config_path = Path(cls.tmp.name) / "omnidb.yaml"
        config_path.write_text("ui:\n  user_enabled: true\n  admin_enabled: false\n")
        db_path = Path(cls.tmp.name) / "test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.app = create_app(db_path=db_path, config_path=config_path)
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.tmp.cleanup()

    def test_admin_returns_404(self) -> None:
        resp = self.client.get("/admin")
        self.assertEqual(resp.status_code, 404)

    def test_ui_returns_html(self) -> None:
        resp = self.client.get("/ui")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("OmniDB", resp.text)


if __name__ == "__main__":
    unittest.main()
