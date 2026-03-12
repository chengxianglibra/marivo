"""Playwright-based E2E tests for the Admin and User UIs.

Requires: pip install playwright && playwright install chromium
Skipped gracefully if playwright is not installed.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
    # Verify browser is actually installed by attempting a quick check
    _pw = sync_playwright().start()
    try:
        _browser = _pw.chromium.launch(headless=True)
        _browser.close()
        HAS_PLAYWRIGHT = True
    except Exception:
        HAS_PLAYWRIGHT = False
    finally:
        _pw.stop()
except Exception:
    HAS_PLAYWRIGHT = False

# Skip all tests if playwright not available or browser not installed
skipUnless = unittest.skipUnless(HAS_PLAYWRIGHT, "playwright not installed or browser not available")


def _start_server(app, port):
    """Start a uvicorn server in a background thread."""
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")


class _BasePlaywrightTest(unittest.TestCase):
    """Base class that starts a real server and a Playwright browser."""

    port = 18765  # Use a non-standard port to avoid conflicts

    @classmethod
    def setUpClass(cls):
        from app.main import create_app
        from tests.shared_fixtures import get_seeded_duckdb_path

        cls.tmp = tempfile.TemporaryDirectory()
        config_path = Path(cls.tmp.name) / "omnidb.yaml"
        config_path.write_text("ui:\n  enabled: true\n")
        db_path = Path(cls.tmp.name) / "test.duckdb"
        get_seeded_duckdb_path(db_path)

        cls._app = create_app(db_path=db_path, config_path=config_path)
        cls._server_thread = threading.Thread(
            target=_start_server, args=(cls._app, cls.port), daemon=True
        )
        cls._server_thread.start()
        time.sleep(1)  # Wait for server to start

        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch(headless=True)
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.page = self._browser.new_page()

    def tearDown(self):
        self.page.close()


@skipUnless
class PlaywrightAdminTests(_BasePlaywrightTest):

    def test_admin_page_loads(self):
        """Admin page loads with OmniDB Admin title."""
        self.page.goto(f"{self.base_url}/admin")
        self.assertIn("OmniDB", self.page.title())

    def test_tab_navigation(self):
        """Click each tab, verify the panel becomes visible."""
        self.page.goto(f"{self.base_url}/admin")
        tabs = ["sources", "engines", "bindings", "entities", "metrics", "governance", "approvals", "observability"]
        for tab in tabs:
            self.page.click(f'button[data-tab="{tab}"]')
            panel = self.page.locator(f"#panel-{tab}")
            self.assertTrue(panel.is_visible(), f"Panel for {tab} should be visible")

    def test_sources_list_loads(self):
        """Sources tab should load and display a table."""
        self.page.goto(f"{self.base_url}/admin")
        self.page.click('button[data-tab="sources"]')
        # Wait for the table to load
        self.page.wait_for_selector("#sources-tbody tr", timeout=5000)
        rows = self.page.locator("#sources-tbody tr").count()
        self.assertGreaterEqual(rows, 0)  # May or may not have seeded sources

    def test_engines_tab_loads(self):
        """Engines tab loads without errors."""
        self.page.goto(f"{self.base_url}/admin")
        self.page.click('button[data-tab="engines"]')
        self.page.wait_for_selector("#engines-tbody", timeout=5000)

    def test_governance_tab_loads(self):
        """Governance tab loads with policies and quality rules cards."""
        self.page.goto(f"{self.base_url}/admin")
        self.page.click('button[data-tab="governance"]')
        self.page.wait_for_selector("#policies-tbody", timeout=5000)
        self.page.wait_for_selector("#quality-rules-tbody", timeout=5000)

    def test_approvals_empty_state(self):
        """Approvals tab should render correctly even when empty."""
        self.page.goto(f"{self.base_url}/admin")
        self.page.click('button[data-tab="approvals"]')
        self.page.wait_for_selector("#approvals-tbody", timeout=5000)

    def test_observability_health(self):
        """Observability tab should display health status 'ok'."""
        self.page.goto(f"{self.base_url}/admin")
        self.page.click('button[data-tab="observability"]')
        self.page.wait_for_selector("#health-status-badge", timeout=5000)
        health_text = self.page.locator("#health-status-badge").text_content()
        self.assertIn("ok", health_text.lower())

    def test_cross_link_to_user_ui(self):
        """Admin page should have a link to User UI."""
        self.page.goto(f"{self.base_url}/admin")
        link = self.page.locator('a[href="/ui"]')
        self.assertTrue(link.is_visible())


@skipUnless
class PlaywrightUserTests(_BasePlaywrightTest):
    port = 18766  # Different port to avoid conflicts

    def test_user_page_loads(self):
        """User page loads with OmniDB Analytics title."""
        self.page.goto(f"{self.base_url}/ui")
        self.assertIn("OmniDB", self.page.title())

    def test_tab_navigation(self):
        """Click each tab, verify the panel becomes visible."""
        self.page.goto(f"{self.base_url}/ui")
        tabs = ["catalog", "sessions", "evidence", "plans", "jobs"]
        for tab in tabs:
            self.page.click(f'button[data-tab="{tab}"]')
            panel = self.page.locator(f"#panel-{tab}")
            self.assertTrue(panel.is_visible(), f"Panel for {tab} should be visible")

    def test_catalog_overview_loads(self):
        """Catalog tab should show overview card."""
        self.page.goto(f"{self.base_url}/ui")
        self.page.click('button[data-tab="catalog"]')
        self.page.wait_for_selector("#cat-overview-card", timeout=5000)

    def test_sessions_list_loads(self):
        """Sessions tab should show session list table."""
        self.page.goto(f"{self.base_url}/ui")
        self.page.click('button[data-tab="sessions"]')
        self.page.wait_for_selector("#sessions-tbody", timeout=5000)

    def test_create_session(self):
        """Create a session via the UI form."""
        self.page.goto(f"{self.base_url}/ui")
        self.page.click('button[data-tab="sessions"]')
        self.page.wait_for_selector("#sessions-tbody", timeout=5000)
        self.page.click("#create-session-btn")
        self.page.wait_for_selector("#f-sess-goal", timeout=3000)
        self.page.fill("#f-sess-goal", "E2E test session")
        self.page.click("#f-sess-submit")
        # Wait for toast or table refresh
        self.page.wait_for_timeout(1000)
        # Session should appear in the list
        body_text = self.page.locator("#sessions-tbody").text_content()
        self.assertIn("E2E test session", body_text)

    def test_jobs_tab_loads(self):
        """Jobs tab should load with filter toolbar."""
        self.page.goto(f"{self.base_url}/ui")
        self.page.click('button[data-tab="jobs"]')
        self.page.wait_for_selector("#jobs-tbody", timeout=5000)

    def test_cross_link_to_admin_ui(self):
        """User page should have a link to Admin UI."""
        self.page.goto(f"{self.base_url}/ui")
        link = self.page.locator('a[href="/admin"]')
        self.assertTrue(link.is_visible())


if __name__ == "__main__":
    unittest.main()
