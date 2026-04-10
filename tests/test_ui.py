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

    def test_admin_uses_redesigned_primary_navigation(self) -> None:
        resp = self.client.get("/admin")
        self.assertIn('<span class="nav-label">Overview</span>', resp.text)
        self.assertIn('<span class="nav-label">Data Sources</span>', resp.text)
        self.assertIn('<span class="nav-label">Execution Engines</span>', resp.text)
        self.assertIn('<span class="nav-label">Semantic Catalog</span>', resp.text)
        self.assertIn('<span class="nav-label">Analysis Ops</span>', resp.text)
        self.assertIn('<span class="nav-label">Runtime &amp; Jobs</span>', resp.text)
        self.assertIn('<span class="nav-label">Governance</span>', resp.text)
        self.assertIn('<span class="nav-label">Observability</span>', resp.text)
        self.assertNotIn('data-tab="sources"', resp.text)
        self.assertNotIn('data-tab="engines"', resp.text)
        self.assertNotIn('data-tab="bindings"', resp.text)
        self.assertNotIn('data-tab="entities"', resp.text)
        self.assertNotIn('data-tab="metrics"', resp.text)
        self.assertNotIn('data-tab="approvals"', resp.text)

    def test_admin_defaults_to_overview_panel(self) -> None:
        resp = self.client.get("/admin")
        self.assertIn('<span class="current">Overview</span>', resp.text)
        self.assertIn('<div class="panel active" id="panel-overview"></div>', resp.text)
        self.assertIn('<div class="panel" id="panel-data-sources"></div>', resp.text)
        self.assertIn('<div class="panel" id="panel-execution-engines"></div>', resp.text)
        self.assertIn('<div class="panel" id="panel-semantic-catalog"></div>', resp.text)
        self.assertIn('<div class="panel" id="panel-analysis-ops"></div>', resp.text)
        self.assertIn('<div class="panel" id="panel-runtime-jobs"></div>', resp.text)
        self.assertIn('<div class="panel" id="panel-governance"></div>', resp.text)
        self.assertIn('<div class="panel" id="panel-observability"></div>', resp.text)

    def test_admin_declares_url_driven_shell_contract(self) -> None:
        resp = self.client.get("/admin")
        self.assertIn("normalizeAdminRoute", resp.text)
        self.assertIn("adminRouteFromLocation", resp.text)
        self.assertIn("writeAdminRoute", resp.text)
        self.assertIn("applyAdminRoute", resp.text)
        self.assertIn("setActiveTab(currentAdminRoute.tab)", resp.text)
        self.assertIn("window.addEventListener('popstate'", resp.text)
        self.assertIn("tab", resp.text)
        self.assertIn("subtab", resp.text)
        self.assertIn("source_id", resp.text)
        self.assertIn("engine_id", resp.text)
        self.assertIn("binding_id", resp.text)
        self.assertIn("session_id", resp.text)
        self.assertIn("job_id", resp.text)

    def test_admin_overview_declares_t3_summary_cards_and_links(self) -> None:
        resp = self.client.get("/admin")
        self.assertIn("Source Summary", resp.text)
        self.assertIn("Engine / Binding Summary", resp.text)
        self.assertIn("Semantic Summary", resp.text)
        self.assertIn("Session Operations Summary", resp.text)
        self.assertIn("Runtime / Jobs Summary", resp.text)
        self.assertIn("Governance / Approvals Summary", resp.text)
        self.assertIn("Observability Summary", resp.text)
        self.assertIn("?tab=data-sources", resp.text)
        self.assertIn("?tab=execution-engines", resp.text)
        self.assertIn("?tab=semantic-catalog&subtab=entities", resp.text)
        self.assertIn("?tab=analysis-ops", resp.text)
        self.assertIn("?tab=runtime-jobs&subtab=jobs", resp.text)
        self.assertIn("?tab=governance&subtab=approvals", resp.text)
        self.assertIn("?tab=observability", resp.text)
        self.assertNotIn("Shared Foundation Checklist", resp.text)
        self.assertNotIn("Preview Delete Binding confirmation", resp.text)

    def test_admin_overview_declares_card_level_loading_empty_and_error_states(self) -> None:
        resp = self.client.get("/admin")
        self.assertIn("Loading Source Summary...", resp.text)
        self.assertIn("No data sources configured yet.", resp.text)
        self.assertIn("No analysis sessions available yet.", resp.text)
        self.assertIn("No background jobs recorded yet.", resp.text)
        self.assertIn("Source Summary unavailable.", resp.text)
        self.assertIn("Semantic Summary unavailable.", resp.text)
        self.assertIn("Observability Summary unavailable.", resp.text)
        self.assertIn("GET /sources", resp.text)
        self.assertIn("GET /jobs", resp.text)
        self.assertIn("GET /health", resp.text)

    def test_admin_routes_ui_drill_ins_through_shared_helpers(self) -> None:
        resp = self.client.get("/admin")
        self.assertIn("adminUiDeepLinks", resp.text)
        self.assertIn("buildUiSessionsUrl", resp.text)
        self.assertNotIn("function buildUiUrl", resp.text)

    def test_shared_js_declares_admin_t2_primitives(self) -> None:
        resp = self.client.get("/static/shared.js")
        self.assertIn("function renderAdminListDetailLayout", resp.text)
        self.assertIn("function renderAdminTableCard", resp.text)
        self.assertIn("function renderAdminDetailCard", resp.text)
        self.assertIn("function renderStructuredError", resp.text)
        self.assertIn("function openDangerConfirm", resp.text)
        self.assertIn("function buildFactumUiUrl", resp.text)
        self.assertIn("function buildUiStateUrl", resp.text)
        self.assertIn("function buildUiContextUrl", resp.text)
        self.assertIn("function buildUiRuntimeUrl", resp.text)
        self.assertIn("function buildUiJobsUrl", resp.text)
        self.assertIn("function normalizeApiError", resp.text)
        self.assertIn("function pollAsync", resp.text)
        self.assertIn("function formatKeyValueSummary", resp.text)

    def test_shared_css_declares_admin_t2_layout_and_states(self) -> None:
        resp = self.client.get("/static/shared.css")
        self.assertIn(".admin-list-detail-layout", resp.text)
        self.assertIn(".detail-empty", resp.text)
        self.assertIn(".detail-error", resp.text)
        self.assertIn(".list-error", resp.text)
        self.assertIn(".danger-confirm-modal", resp.text)
        self.assertIn(".catalog-browser-grid", resp.text)
        self.assertIn(".selectable-list-item", resp.text)
        self.assertIn(".checklist-grid", resp.text)

    def test_admin_data_sources_declares_t4_inventory_detail_and_mutation_contracts(self) -> None:
        resp = self.client.get("/admin?tab=data-sources")
        self.assertIn("Source Inventory", resp.text)
        self.assertIn("Source Summary", resp.text)
        self.assertIn("Sync & Jobs", resp.text)
        self.assertIn("Sync Selections", resp.text)
        self.assertIn("Catalog Browser", resp.text)
        self.assertIn("Synced Source Objects", resp.text)
        self.assertIn("Create Source", resp.text)
        self.assertIn("Edit Source", resp.text)
        self.assertIn("Delete Source", resp.text)
        self.assertIn("Run Sync", resp.text)
        self.assertIn("Manage Selections", resp.text)
        self.assertIn("Clear All", resp.text)
        self.assertIn("Browse Catalog", resp.text)
        self.assertIn("last_sync_at", resp.text)
        self.assertIn("GET /sources/{source_id}", resp.text)
        self.assertIn("POST /sources/{source_id}/sync", resp.text)
        self.assertIn("GET /sources/{source_id}/catalog/schemas", resp.text)
        self.assertIn("GET /sources/{source_id}/catalog/tables", resp.text)
        self.assertIn("GET /sources/{source_id}/objects?type=table", resp.text)

    def test_admin_data_sources_declares_empty_and_error_copy(self) -> None:
        resp = self.client.get("/admin?tab=data-sources")
        self.assertIn("No data sources configured yet.", resp.text)
        self.assertIn("No sync selections configured yet.", resp.text)
        self.assertIn("No schema available from the live catalog for this source.", resp.text)
        self.assertIn("No table found for the selected schema.", resp.text)
        self.assertIn("Sync request failed.", resp.text)
        self.assertIn("Catalog schemas unavailable.", resp.text)
        self.assertIn("Catalog tables unavailable.", resp.text)
        self.assertIn(
            "No synced source objects yet. Run Sync or configure selections first.", resp.text
        )

    def test_admin_data_sources_declares_t4_client_helpers_and_modals(self) -> None:
        resp = self.client.get("/admin?tab=data-sources")
        self.assertIn("hydrateDataSources", resp.text)
        self.assertIn("openSourceFormModal", resp.text)
        self.assertIn("openSelectionModal", resp.text)
        self.assertIn("handleRunSourceSync", resp.text)
        self.assertIn("handleDeleteSource", resp.text)
        self.assertIn("ensureSourceFormModal", resp.text)
        self.assertIn("ensureSelectionModal", resp.text)
        self.assertIn("Manage Selections writes the full selection set back", resp.text)
        self.assertIn("Connection JSON", resp.text)
        self.assertIn("source-form-modal", resp.text)
        self.assertIn("selection-modal", resp.text)

    def test_admin_execution_engines_declares_t5_inventory_detail_and_mutation_contracts(
        self,
    ) -> None:
        resp = self.client.get("/admin?tab=execution-engines")
        self.assertIn("Engine Inventory", resp.text)
        self.assertIn("Binding Inventory", resp.text)
        self.assertIn("Engine Summary", resp.text)
        self.assertIn("Binding Summary", resp.text)
        self.assertIn("Source-engine Relationship", resp.text)
        self.assertIn("Execution Binding Contract", resp.text)
        self.assertIn("Create Engine", resp.text)
        self.assertIn("Create Binding", resp.text)
        self.assertIn("Delete Binding", resp.text)
        self.assertIn("engine_id", resp.text)
        self.assertIn("display_name", resp.text)
        self.assertIn("engine_type", resp.text)
        self.assertIn("binding_id", resp.text)
        self.assertIn("source_id", resp.text)
        self.assertIn("priority", resp.text)
        self.assertIn("GET /engines", resp.text)
        self.assertIn("GET /engines/{engine_id}", resp.text)
        self.assertIn("GET /bindings", resp.text)
        self.assertIn("GET /bindings/{binding_id}", resp.text)
        self.assertIn("DELETE /bindings/{binding_id}", resp.text)
        self.assertIn("GET /sources/{source_id}/engines", resp.text)

    def test_admin_execution_engines_declares_boundary_copy_empty_states_and_modals(
        self,
    ) -> None:
        resp = self.client.get("/admin?tab=execution-engines")
        self.assertIn("No execution engines configured yet.", resp.text)
        self.assertIn("No source-engine bindings configured yet.", resp.text)
        self.assertIn(
            "Create at least one data source before creating an execution binding.", resp.text
        )
        self.assertIn(
            "Create at least one execution engine before creating an execution binding.", resp.text
        )
        self.assertIn(
            "Execution engine bindings connect a source to an execution backend.", resp.text
        )
        self.assertIn("semantic typed bindings stay in Semantic Catalog", resp.text)
        self.assertIn("Advanced Namespace JSON", resp.text)
        self.assertIn("engine-form-modal", resp.text)
        self.assertIn("binding-form-modal", resp.text)
        self.assertNotIn("still waits for its task-specific data sources", resp.text)

    def test_admin_execution_engines_declares_t5_client_helpers(self) -> None:
        resp = self.client.get("/admin?tab=execution-engines")
        self.assertIn("hydrateExecutionEngines", resp.text)
        self.assertIn("renderExecutionEnginesBody", resp.text)
        self.assertIn("ensureEngineFormModal", resp.text)
        self.assertIn("ensureBindingFormModal", resp.text)
        self.assertIn("openEngineFormModal", resp.text)
        self.assertIn("openBindingFormModal", resp.text)
        self.assertIn("handleDeleteBinding", resp.text)
        self.assertIn("refreshCurrentExecutionEngines", resp.text)

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

    def test_ui_keeps_stable_primary_page_names(self) -> None:
        resp = self.client.get("/ui")
        self.assertIn('<span class="nav-label">Sessions</span>', resp.text)
        self.assertIn('<span class="nav-label">State</span>', resp.text)
        self.assertIn('<span class="nav-label">Context</span>', resp.text)
        self.assertIn('<span class="nav-label">Runtime</span>', resp.text)
        self.assertIn('<span class="nav-label">Grounding</span>', resp.text)
        self.assertIn('<span class="nav-label">Jobs</span>', resp.text)
        self.assertNotIn('<span class="nav-label">Catalog</span>', resp.text)
        self.assertNotIn('<span class="nav-label">Evidence</span>', resp.text)
        self.assertNotIn('<span class="nav-label">Plans</span>', resp.text)

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
        self.assertIn("artifact_id", resp.text)
        self.assertIn("runtime_scope", resp.text)
        self.assertIn("404 session not found. Returned to session list.", resp.text)
        self.assertIn("function goToSessions(sessionId = '', historyMode = 'push')", resp.text)
        self.assertIn("function handleSessionNotFound(setError)", resp.text)
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
        self.assertIn("当前 session 尚无对外可见 proposition", resp.text)
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
        self.assertIn("该 proposition 当前尚无 latest assessment，请查看 runtime", resp.text)
        self.assertIn("latest_assessment = null is a canonical result only", resp.text)
        self.assertIn(
            "Use Runtime to distinguish untriggered, failed, or migration-blocked states.",
            resp.text,
        )
        self.assertIn("404 proposition not found. Returned to State.", resp.text)
        self.assertIn("function handlePropositionNotFound(setError)", resp.text)
        self.assertIn(
            "latest_assessment = null 时，blocking_gaps 按 canonical contract 为 null。", resp.text
        )
        self.assertIn(
            "latest_assessment = null 时，relevant_findings 按 canonical contract 为 [].", resp.text
        )

    def test_ui_runtime_page_declares_three_runtime_views_and_contracts(self) -> None:
        resp = self.client.get("/ui")
        self.assertIn("Session Runtime", resp.text)
        self.assertIn("Proposition Runtime", resp.text)
        self.assertIn("Artifact Runtime", resp.text)
        self.assertIn("GET /sessions/{session_id}/runtime-status", resp.text)
        self.assertIn(
            "GET /sessions/{session_id}/propositions/{proposition_id}/runtime-status",
            resp.text,
        )
        self.assertIn(
            "GET /sessions/{session_id}/artifacts/{artifact_id}/runtime-status",
            resp.text,
        )

    def test_ui_runtime_page_declares_operator_boundary_and_back_links(self) -> None:
        resp = self.client.get("/ui")
        self.assertIn("operator-facing runtime truth", resp.text)
        self.assertIn("Open Session", resp.text)
        self.assertIn("Open State", resp.text)
        self.assertIn("Open Context", resp.text)
        self.assertIn("goToState(currentRoute.sessionId, currentRoute.propositionId)", resp.text)
        self.assertIn("goToJobs(currentRoute.sessionId)", resp.text)
        self.assertIn("No retry, cancel, or terminate controls exist on this page.", resp.text)
        self.assertIn(
            "Runtime lookup failures stay on this page instead of forcing a jump away from the canonical chain.",
            resp.text,
        )

    def test_ui_grounding_page_declares_helper_views_and_http_contracts(self) -> None:
        resp = self.client.get("/ui")
        self.assertIn("Catalog Search", resp.text)
        self.assertIn("Semantic Resolve", resp.text)
        self.assertIn("Catalog Graph", resp.text)
        self.assertIn("Planner Context", resp.text)
        self.assertIn("GET /catalog/search", resp.text)
        self.assertIn("GET /semantic/resolve/{name}", resp.text)
        self.assertIn("GET /catalog/graph", resp.text)
        self.assertIn("GET /sessions/{session_id}/planner-context", resp.text)
        self.assertIn("Open Grounding Helper from Sessions to inspect planner context.", resp.text)

    def test_ui_grounding_page_keeps_read_only_helper_boundary(self) -> None:
        resp = self.client.get("/ui")
        self.assertIn("secondary to State / Context for analysis outcomes", resp.text)
        self.assertIn("Results never turn into executable analysis actions.", resp.text)
        self.assertIn("It does not expose any Run Analysis or session creation action.", resp.text)
        self.assertIn(
            "Grounding is a helper surface for semantic object discovery and session grounding.",
            resp.text,
        )

    def test_ui_jobs_page_declares_read_only_filters_and_http_contracts(self) -> None:
        resp = self.client.get("/ui")
        self.assertIn("Inspect background jobs related to analysis sessions", resp.text)
        self.assertIn("Filter by session_id", resp.text)
        self.assertIn("GET /jobs", resp.text)
        self.assertIn("GET /jobs/{job_id}", resp.text)
        self.assertIn("View Linked Session", resp.text)
        self.assertIn("payload summary", resp.text)

    def test_ui_jobs_page_keeps_auxiliary_boundary_and_empty_copy(self) -> None:
        resp = self.client.get("/ui")
        self.assertIn("Jobs is an auxiliary troubleshooting surface", resp.text)
        self.assertIn("It does not replace runtime status diagnosis for blocked work.", resp.text)
        self.assertIn("当前筛选条件下无相关后台任务", resp.text)
        self.assertIn(
            "function goToJobs(sessionId = '', status = '', historyMode = 'push')", resp.text
        )
        self.assertIn("No submit, cancel, or retry controls exist on this page.", resp.text)
        self.assertIn("T8 aligns the read-only jobs view with created_at / updated_at", resp.text)

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
