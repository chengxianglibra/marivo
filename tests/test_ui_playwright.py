"""Playwright-based E2E tests for the redesigned Admin and User UIs.

The new UI uses sidebar navigation with lazy-rendered panels. Panels are
empty <div> elements that get populated by async JS renderers when their
sidebar tab is clicked (or on initial boot for the default tab).

Requires: pip install playwright && playwright install chromium
Skipped gracefully if playwright is not installed.
"""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
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

skipUnless = unittest.skipUnless(HAS_PLAYWRIGHT, "playwright not installed or browser not available")


def _start_server(app, port):
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")


class _BasePlaywrightTest(unittest.TestCase):
    """Base class that starts a real server and a Playwright browser."""

    port = 18765

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
        time.sleep(1.5)

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

    def _click_tab(self, tab_name):
        """Click a sidebar tab and wait for the panel to become active."""
        self.page.click(f'button[data-tab="{tab_name}"]')
        # Wait for the tab-change event and renderer to fire
        self.page.wait_for_timeout(800)

    def _assert_panel_active(self, tab_name):
        """Assert the panel has the 'active' CSS class."""
        panel = self.page.locator(f"#panel-{tab_name}")
        has_active = panel.evaluate("el => el.classList.contains('active')")
        self.assertTrue(has_active, f"Panel for {tab_name} should have active class")


@skipUnless
class PlaywrightAdminTests(_BasePlaywrightTest):

    def test_admin_page_loads(self):
        """Admin page loads with correct title."""
        self.page.goto(f"{self.base_url}/admin")
        self.assertIn("OmniDB", self.page.title())

    def test_sidebar_visible(self):
        """Sidebar navigation is visible on load."""
        self.page.goto(f"{self.base_url}/admin")
        sidebar = self.page.locator(".sidebar")
        self.assertTrue(sidebar.is_visible())

    def test_shared_assets_loaded(self):
        """Page includes shared.css and shared.js references."""
        self.page.goto(f"{self.base_url}/admin")
        html = self.page.content()
        self.assertIn("shared.css", html)
        self.assertIn("shared.js", html)

    def test_tab_navigation(self):
        """Click each sidebar tab, verify the panel becomes active."""
        self.page.goto(f"{self.base_url}/admin")
        self.page.wait_for_timeout(1000)  # Wait for boot rendering
        tabs = ["sources", "engines", "bindings", "entities", "metrics",
                "governance", "approvals", "observability"]
        for tab in tabs:
            self._click_tab(tab)
            self._assert_panel_active(tab)

    def test_default_tab_renders(self):
        """Sources is the default tab and renders content on load."""
        self.page.goto(f"{self.base_url}/admin")
        # Sources panel should be active by default and have content after boot
        self.page.wait_for_function(
            "() => document.querySelector('#panel-sources')?.innerHTML.trim().length > 0",
            timeout=10000,
        )
        self._assert_panel_active("sources")

    def test_sources_table_exists(self):
        """Sources panel renders a table with sources-tbody."""
        self.page.goto(f"{self.base_url}/admin")
        self.page.wait_for_function(
            "() => document.querySelector('#sources-tbody') !== null",
            timeout=10000,
        )

    def test_engines_panel_renders(self):
        """Engines panel renders content when activated."""
        self.page.goto(f"{self.base_url}/admin")
        self._click_tab("engines")
        self.page.wait_for_function(
            "() => document.querySelector('#engines-tbody') !== null",
            timeout=10000,
        )

    def test_governance_panel_renders(self):
        """Governance panel renders policies and quality rules tables."""
        self.page.goto(f"{self.base_url}/admin")
        self._click_tab("governance")
        self.page.wait_for_function(
            "() => document.querySelector('#policies-tbody') !== null && "
            "document.querySelector('#quality-rules-tbody') !== null",
            timeout=10000,
        )

    def test_approvals_panel_renders(self):
        """Approvals panel renders a table."""
        self.page.goto(f"{self.base_url}/admin")
        self._click_tab("approvals")
        self.page.wait_for_function(
            "() => document.querySelector('#approvals-tbody') !== null",
            timeout=10000,
        )

    def test_observability_health(self):
        """Observability panel displays health status badge."""
        self.page.goto(f"{self.base_url}/admin")
        self._click_tab("observability")
        self.page.wait_for_function(
            "() => document.querySelector('#health-status-badge') !== null",
            timeout=10000,
        )
        health_text = self.page.locator("#health-status-badge").text_content()
        self.assertIn("ok", health_text.lower())

    def test_breadcrumb_updates(self):
        """Breadcrumb updates when switching tabs."""
        self.page.goto(f"{self.base_url}/admin")
        self._click_tab("engines")
        crumb = self.page.locator(".breadcrumb .current")
        self.assertIn("Engine", crumb.text_content())

    def test_cross_link_to_analytics(self):
        """Admin page has a link to Analytics UI (/ui)."""
        self.page.goto(f"{self.base_url}/admin")
        link = self.page.locator('a[href="/ui"]')
        self.assertTrue(link.is_visible())


@skipUnless
class PlaywrightUserTests(_BasePlaywrightTest):
    port = 18766

    def test_user_page_loads(self):
        """User page loads with correct title."""
        self.page.goto(f"{self.base_url}/ui")
        self.assertIn("OmniDB", self.page.title())

    def test_sidebar_visible(self):
        """Sidebar navigation is visible on load."""
        self.page.goto(f"{self.base_url}/ui")
        sidebar = self.page.locator(".sidebar")
        self.assertTrue(sidebar.is_visible())

    def test_tab_navigation(self):
        """Click each sidebar tab, verify the panel becomes active."""
        self.page.goto(f"{self.base_url}/ui")
        self.page.wait_for_timeout(1000)
        tabs = ["catalog", "sessions", "evidence", "plans", "jobs"]
        for tab in tabs:
            self._click_tab(tab)
            self._assert_panel_active(tab)

    def test_catalog_default_renders(self):
        """Catalog is the default tab and renders an overview card."""
        self.page.goto(f"{self.base_url}/ui")
        self.page.wait_for_function(
            "() => document.querySelector('#cat-overview-card') !== null",
            timeout=10000,
        )

    def test_sessions_panel_renders(self):
        """Sessions panel renders with create button and table."""
        self.page.goto(f"{self.base_url}/ui")
        self._click_tab("sessions")
        self.page.wait_for_function(
            "() => document.querySelector('#sessions-tbody') !== null && "
            "document.querySelector('#create-session-btn') !== null",
            timeout=10000,
        )

    def test_create_session(self):
        """Create a session via the UI form."""
        self.page.goto(f"{self.base_url}/ui")
        self._click_tab("sessions")
        self.page.wait_for_function(
            "() => document.querySelector('#create-session-btn') !== null",
            timeout=10000,
        )
        self.page.click("#create-session-btn")
        self.page.wait_for_selector("#f-sess-goal", timeout=5000)
        self.page.fill("#f-sess-goal", "E2E test session")
        self.page.click("#f-sess-submit")
        self.page.wait_for_timeout(2000)
        body_text = self.page.locator("#sessions-tbody").text_content()
        self.assertIn("E2E test session", body_text)

    def test_jobs_panel_renders(self):
        """Jobs panel renders a table."""
        self.page.goto(f"{self.base_url}/ui")
        self._click_tab("jobs")
        self.page.wait_for_function(
            "() => document.querySelector('#jobs-tbody') !== null",
            timeout=10000,
        )

    def test_cross_link_to_admin(self):
        """User page has a link to Admin UI (/admin)."""
        self.page.goto(f"{self.base_url}/ui")
        link = self.page.locator('a[href="/admin"]')
        self.assertTrue(link.is_visible())


if __name__ == "__main__":
    unittest.main()
