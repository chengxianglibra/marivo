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
        config_path = Path(cls.tmp.name) / "factum.yaml"
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
        self.assertIn("Factum", resp.text)

    def test_ui_returns_html(self) -> None:
        resp = self.client.get("/ui")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("Factum", resp.text)

    def test_static_admin_accessible(self) -> None:
        resp = self.client.get("/static/admin.html")
        self.assertEqual(resp.status_code, 200)

    def test_static_user_accessible(self) -> None:
        resp = self.client.get("/static/user.html")
        self.assertEqual(resp.status_code, 200)

    def test_shared_css_accessible(self) -> None:
        resp = self.client.get("/static/shared.css")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/css", resp.headers["content-type"])

    def test_shared_js_accessible(self) -> None:
        resp = self.client.get("/static/shared.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("javascript", resp.headers["content-type"])

    def test_admin_uses_shared_assets(self) -> None:
        resp = self.client.get("/admin")
        self.assertIn("shared.css", resp.text)
        self.assertIn("shared.js", resp.text)

    def test_user_uses_shared_assets(self) -> None:
        resp = self.client.get("/ui")
        self.assertIn("shared.css", resp.text)
        self.assertIn("shared.js", resp.text)

    def test_ui_uses_new_query_workbench_navigation(self) -> None:
        resp = self.client.get("/ui")
        self.assertIn("Query Workbench", resp.text)
        self.assertIn('data-tab="sessions"', resp.text)
        self.assertIn('data-tab="state"', resp.text)
        self.assertIn('data-tab="context"', resp.text)
        self.assertIn('data-tab="runtime"', resp.text)
        self.assertIn('data-tab="grounding"', resp.text)
        self.assertIn('data-tab="jobs"', resp.text)
        self.assertNotIn('data-tab="catalog"', resp.text)
        self.assertNotIn('data-tab="evidence"', resp.text)
        self.assertNotIn('data-tab="plans"', resp.text)

    def test_ui_defaults_to_sessions_panel(self) -> None:
        resp = self.client.get("/ui")
        self.assertIn('<span class="current">Sessions</span>', resp.text)
        self.assertIn('<div class="panel active" id="panel-sessions"></div>', resp.text)
        self.assertIn('<div class="panel" id="panel-state"></div>', resp.text)
        self.assertIn('<div class="panel" id="panel-context"></div>', resp.text)
        self.assertIn('<div class="panel" id="panel-runtime"></div>', resp.text)
        self.assertIn('<div class="panel" id="panel-grounding"></div>', resp.text)
        self.assertIn('<div class="panel" id="panel-jobs"></div>', resp.text)

    def test_ui_sessions_page_declares_session_filters_and_drill_ins(self) -> None:
        resp = self.client.get("/ui")
        self.assertIn("Search by session_id", resp.text)
        self.assertIn("View State", resp.text)
        self.assertIn("View Runtime", resp.text)
        self.assertIn("View Jobs", resp.text)
        self.assertIn("Open Grounding Helper", resp.text)
        self.assertIn("当前没有可查看的分析会话", resp.text)

    def test_ui_sessions_page_uses_url_driven_state(self) -> None:
        resp = self.client.get("/ui")
        self.assertIn("session_query", resp.text)
        self.assertIn("session_id", resp.text)
        self.assertIn("proposition_id", resp.text)
        self.assertIn("runtime_scope", resp.text)
        self.assertIn("404 session not found. Returned to session list.", resp.text)
        self.assertIn("setActiveTab(currentRoute.tab)", resp.text)

    def test_ui_state_page_declares_canonical_filters_and_drill_ins(self) -> None:
        resp = self.client.get("/ui")
        self.assertIn("Canonical Filters", resp.text)
        self.assertIn("assessment_presence", resp.text)
        self.assertIn("assessment_status", resp.text)
        self.assertIn("has_blocking_gaps", resp.text)
        self.assertIn("origin_kind", resp.text)
        self.assertIn("proposition_type", resp.text)
        self.assertIn("Run Query", resp.text)
        self.assertIn("Open Context", resp.text)
        self.assertIn("Open Proposition Runtime", resp.text)
        self.assertIn("GET /sessions/{session_id}/state", resp.text)

    def test_ui_state_page_keeps_canonical_runtime_boundary_copy(self) -> None:
        resp = self.client.get("/ui")
        self.assertIn("does not expose runtime queue, claim, retry, or backlog fields", resp.text)
        self.assertIn("Slice filters are intentionally excluded from this T4 UI.", resp.text)
        self.assertIn("当前 session 没有对外可见 proposition", resp.text)
        self.assertIn("当前筛选条件下无命中", resp.text)

    def test_ui_context_page_declares_locator_contract_and_runtime_drill_ins(self) -> None:
        resp = self.client.get("/ui")
        self.assertIn("Context Locator", resp.text)
        self.assertIn("GET /sessions/{session_id}/propositions/{proposition_id}/context", resp.text)
        self.assertIn("Check Proposition Runtime", resp.text)
        self.assertIn("Artifact Runtime", resp.text)
        self.assertIn("Open State", resp.text)

    def test_ui_context_page_keeps_canonical_boundary_and_error_copy(self) -> None:
        resp = self.client.get("/ui")
        self.assertIn("latest_assessment = null is a canonical result only", resp.text)
        self.assertIn(
            "Use Runtime to distinguish untriggered, failed, or migration-blocked states.",
            resp.text,
        )
        self.assertIn("404 proposition not found. Returned to State.", resp.text)
        self.assertIn(
            "latest_assessment = null 时，blocking_gaps 按 canonical contract 为 null。", resp.text
        )
        self.assertIn(
            "latest_assessment = null 时，relevant_findings 按 canonical contract 为 [].", resp.text
        )

    def test_ui_removes_legacy_write_entrypoints(self) -> None:
        resp = self.client.get("/ui")
        self.assertNotIn("Create Session", resp.text)
        self.assertNotIn("Run Step", resp.text)
        self.assertNotIn("Run Intent", resp.text)
        self.assertNotIn("Evidence Dashboard", resp.text)
        self.assertNotIn("Submit Job", resp.text)
        self.assertNotIn("Cancel Job", resp.text)

    def test_ui_removes_legacy_analysis_modules(self) -> None:
        resp = self.client.get("/ui")
        self.assertNotIn('data-tab="plans"', resp.text)
        self.assertNotIn('data-tab="catalog"', resp.text)
        self.assertNotIn('data-tab="evidence"', resp.text)
        self.assertNotIn("listPlans", resp.text)
        self.assertNotIn("runStep", resp.text)
        self.assertNotIn("submitJob", resp.text)
        self.assertNotIn("cancelJob", resp.text)

    def test_ui_keeps_read_only_positioning_and_admin_link(self) -> None:
        resp = self.client.get("/ui")
        self.assertIn("read-only", resp.text)
        self.assertIn("Admin UI", resp.text)
        self.assertIn('href="/admin"', resp.text)

    def test_admin_has_sidebar(self) -> None:
        resp = self.client.get("/admin")
        self.assertIn("sidebar", resp.text)

    def test_user_has_sidebar(self) -> None:
        resp = self.client.get("/ui")
        self.assertIn("sidebar", resp.text)

    def test_stale_index_html_removed(self) -> None:
        resp = self.client.get("/static/index.html")
        self.assertEqual(resp.status_code, 404)


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
        config_path = Path(cls.tmp.name) / "factum.yaml"
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
        self.assertIn("Factum", resp.text)

    def test_ui_returns_404(self) -> None:
        resp = self.client.get("/ui")
        self.assertEqual(resp.status_code, 404)


class UIUserOnlyTests(unittest.TestCase):
    """user_enabled: true, admin_enabled: false -> /admin 404, /ui 200."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        config_path = Path(cls.tmp.name) / "factum.yaml"
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
        self.assertIn("Factum", resp.text)


if __name__ == "__main__":
    unittest.main()
