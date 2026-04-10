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

    def test_admin_split_assets_accessible(self) -> None:
        for path in [
            "/static/admin.css",
            "/static/admin/index.js",
            "/static/admin/shell.js",
            "/static/admin/api.js",
            "/static/admin/overview.js",
            "/static/admin/data-sources.js",
            "/static/admin/execution-engines.js",
            "/static/admin/analysis-ops.js",
            "/static/admin/runtime-jobs.js",
            "/static/admin/governance.js",
            "/static/admin/observability.js",
            "/static/admin/semantic-catalog.js",
        ]:
            with self.subTest(path=path):
                resp = self.client.get(path)
                self.assertEqual(resp.status_code, 200)

    def test_admin_uses_shared_assets(self) -> None:
        resp = self.client.get("/admin")
        self.assertIn("shared.css", resp.text)
        self.assertIn("shared.js", resp.text)
        self.assertIn("admin.css", resp.text)
        self.assertIn("/static/admin/index.js", resp.text)

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
        resp = self.client.get("/static/admin/shell.js")
        self.assertIn("normalizeAdminRoute", resp.text)
        self.assertIn("adminRouteFromLocation", resp.text)
        self.assertIn("writeAdminRoute", resp.text)
        self.assertIn("applyAdminRoute", resp.text)
        self.assertIn("modules.analysisOps.render", resp.text)
        self.assertIn("modules.analysisOps.hydrate", resp.text)
        self.assertIn("setActiveTab(currentAdminRoute.tab)", resp.text)
        self.assertIn("window.addEventListener('popstate'", resp.text)
        self.assertIn("tab", resp.text)
        self.assertIn("subtab", resp.text)
        self.assertIn("source_id", resp.text)
        self.assertIn("engine_id", resp.text)
        self.assertIn("binding_id", resp.text)
        self.assertIn("object_id", resp.text)
        self.assertIn("session_id", resp.text)
        self.assertIn("policy_id", resp.text)
        self.assertIn("rule_id", resp.text)
        self.assertIn("request_id", resp.text)
        self.assertIn("proposition_id", resp.text)
        self.assertIn("artifact_id", resp.text)
        self.assertIn("job_id", resp.text)

    def test_admin_overview_declares_t3_summary_cards_and_links(self) -> None:
        resp = self.client.get("/static/admin/overview.js")
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
        resp = self.client.get("/static/admin/overview.js")
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
        overview_resp = self.client.get("/static/admin/overview.js")
        shared_resp = self.client.get("/static/shared.js")
        self.assertIn("adminUiDeepLinks", overview_resp.text)
        self.assertIn("buildUiSessionsUrl", shared_resp.text)
        self.assertNotIn("function buildUiUrl", shared_resp.text)

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

    def test_admin_css_declares_observability_summary_layout(self) -> None:
        resp = self.client.get("/static/admin.css")
        self.assertIn(".observability-summary-grid", resp.text)
        self.assertIn(".observability-raw-text", resp.text)

    def test_admin_observability_declares_t11_health_metrics_and_refresh_contract(self) -> None:
        resp = self.client.get("/static/admin/observability.js")
        self.assertIn("Health Summary", resp.text)
        self.assertIn("Metrics Summary Cards", resp.text)
        self.assertIn("Key Metric Values", resp.text)
        self.assertIn("Health JSON", resp.text)
        self.assertIn("Metrics JSON", resp.text)
        self.assertIn("Metrics Raw Text", resp.text)
        self.assertIn("GET /health", resp.text)
        self.assertIn("GET /metrics", resp.text)
        self.assertIn("GET /metrics?format=prometheus", resp.text)
        self.assertIn("Auto Refresh", resp.text)
        self.assertIn("Manual Refresh", resp.text)
        self.assertIn("Every 15 seconds", resp.text)
        self.assertIn("No key metrics available yet.", resp.text)
        self.assertIn("Health Summary unavailable.", resp.text)
        self.assertIn("Metrics Summary unavailable.", resp.text)
        self.assertIn("Metrics raw text unavailable.", resp.text)
        self.assertIn("function scheduleAutoRefresh", resp.text)
        self.assertIn("async function hydrate(panel, route)", resp.text)

    def test_admin_index_and_shell_register_observability_module(self) -> None:
        index_resp = self.client.get("/static/admin/index.js")
        shell_resp = self.client.get("/static/admin/shell.js")
        self.assertIn("createObservabilityModule", index_resp.text)
        self.assertIn("observability: createObservabilityModule(ctx)", index_resp.text)
        self.assertIn("modules.observability.render", shell_resp.text)
        self.assertIn("modules.observability.hydrate", shell_resp.text)

    def test_admin_data_sources_declares_t4_inventory_detail_and_mutation_contracts(self) -> None:
        resp = self.client.get("/static/admin/data-sources.js")
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
        self.assertIn('data-role="source-row"', resp.text)
        self.assertIn("?tab=data-sources&source_id=", resp.text)
        self.assertIn("last_sync_at", resp.text)
        self.assertIn("GET /sources/{source_id}", resp.text)
        self.assertIn("POST /sources/{source_id}/sync", resp.text)
        self.assertIn("GET /sources/{source_id}/catalog/schemas", resp.text)
        self.assertIn("GET /sources/{source_id}/catalog/tables", resp.text)
        self.assertIn("GET /sources/{source_id}/objects?type=table", resp.text)

    def test_admin_data_sources_declares_empty_and_error_copy(self) -> None:
        resp = self.client.get("/static/admin/data-sources.js")
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
        resp = self.client.get("/static/admin/data-sources.js")
        self.assertIn("async function hydrate(panel, route)", resp.text)
        self.assertIn("function extractItems(payload)", resp.text)
        self.assertIn("Array.isArray(payload?.items)", resp.text)
        self.assertIn("let lastSources = []", resp.text)
        self.assertIn("lastSources = sources", resp.text)
        self.assertIn("Promise.allSettled", resp.text)
        self.assertIn("sourceError", resp.text)
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
        self.assertIn("target?.closest('[data-action=\"select-source\"]')", resp.text)

    def test_admin_execution_engines_declares_t5_inventory_detail_and_mutation_contracts(
        self,
    ) -> None:
        resp = self.client.get("/static/admin/execution-engines.js")
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
        resp = self.client.get("/static/admin/execution-engines.js")
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
        resp = self.client.get("/static/admin/execution-engines.js")
        self.assertIn("async function hydrate(panel, route)", resp.text)
        self.assertIn("function extractItems(payload)", resp.text)
        self.assertIn("let lastEngines = []", resp.text)
        self.assertIn("let lastBindings = []", resp.text)
        self.assertIn("let lastSources = []", resp.text)
        self.assertIn("function renderBody(viewModel)", resp.text)
        self.assertIn("ensureEngineFormModal", resp.text)
        self.assertIn("ensureBindingFormModal", resp.text)
        self.assertIn("openEngineFormModal", resp.text)
        self.assertIn("openBindingFormModal", resp.text)
        self.assertIn("handleDeleteBinding", resp.text)
        self.assertIn("refreshCurrentExecutionEngines", resp.text)

    def test_admin_semantic_catalog_declares_t7_object_page_contracts(self) -> None:
        module_resp = self.client.get("/static/admin/semantic-catalog/module.js")
        core_resp = self.client.get("/static/admin/semantic-catalog/core-config.js")
        self.assertIn("Entity Catalog", core_resp.text)
        self.assertIn("Object Summary", module_resp.text)
        self.assertIn("Interface / Payload Summary", module_resp.text)
        self.assertIn("Relationship Summary", module_resp.text)
        self.assertIn("Lifecycle Summary", module_resp.text)
        self.assertIn('singularLabel: "Entity"', core_resp.text)
        self.assertIn("Dependency Helpers", module_resp.text)
        self.assertIn("Resolve / View Related Bindings / View Catalog Graph", module_resp.text)
        self.assertIn("Raw JSON Panel", module_resp.text)
        self.assertIn("Hierarchy JSON", core_resp.text)
        self.assertIn("Stable Descriptors JSON", core_resp.text)
        self.assertIn("POST /semantic/entities", core_resp.text)
        self.assertIn("PUT /semantic/entities/{entity_id}", core_resp.text)
        self.assertIn("object_id", module_resp.text)
        self.assertIn("stable_ref", module_resp.text)
        self.assertIn("display_name", module_resp.text)
        self.assertIn("revision", module_resp.text)
        self.assertIn("updated_at", module_resp.text)
        self.assertIn("All statuses", module_resp.text)
        self.assertIn("Draft", module_resp.text)
        self.assertIn("Published", module_resp.text)

    def test_admin_semantic_catalog_entities_and_metrics_expose_front_four_contract_fields(
        self,
    ) -> None:
        core_resp = self.client.get("/static/admin/semantic-catalog/core-config.js")
        api_resp = self.client.get("/static/admin/api.js")
        self.assertIn("Identity Key Refs", core_resp.text)
        self.assertIn("Nullable Key Policy", core_resp.text)
        self.assertIn("Parent Entity", core_resp.text)
        self.assertIn("Primary Time", core_resp.text)
        self.assertIn("Metric Payload JSON", core_resp.text)
        self.assertIn("Observed Entity", core_resp.text)
        self.assertIn("Use View Related Bindings to inspect grounding.", core_resp.text)
        self.assertIn("createSemanticMetric", api_resp.text)
        self.assertIn("updateSemanticMetric", api_resp.text)
        self.assertIn("publishSemanticMetric", api_resp.text)

    def test_admin_semantic_catalog_process_objects_and_dimensions_expose_front_four_contract_fields(
        self,
    ) -> None:
        module_resp = self.client.get("/static/admin/semantic-catalog/module.js")
        supporting_resp = self.client.get("/static/admin/semantic-catalog/supporting-config.js")
        api_resp = self.client.get("/static/admin/api.js")
        self.assertIn("Interface Contract JSON", supporting_resp.text)
        self.assertIn("Process Payload JSON", supporting_resp.text)
        self.assertIn("Anchor Time", supporting_resp.text)
        self.assertIn("Exported ${ref}", supporting_resp.text)
        self.assertIn("Grouping JSON", supporting_resp.text)
        self.assertIn("Time Derived Requirement JSON", supporting_resp.text)
        self.assertIn("Required Time Anchor", supporting_resp.text)
        self.assertIn("Parent Dimension", supporting_resp.text)
        self.assertIn(
            '["metrics", "process-objects", "compatibility-profiles"].includes', module_resp.text
        )
        self.assertIn("createSemanticProcessObject", api_resp.text)
        self.assertIn("updateSemanticProcessObject", api_resp.text)
        self.assertIn("publishSemanticProcessObject", api_resp.text)
        self.assertIn("createSemanticDimension", api_resp.text)
        self.assertIn("updateSemanticDimension", api_resp.text)
        self.assertIn("publishSemanticDimension", api_resp.text)

    def test_admin_semantic_catalog_time_and_enum_sets_expose_supporting_contract_fields(
        self,
    ) -> None:
        module_resp = self.client.get("/static/admin/semantic-catalog/module.js")
        supporting_resp = self.client.get("/static/admin/semantic-catalog/supporting-config.js")
        api_resp = self.client.get("/static/admin/api.js")
        self.assertIn("Time Catalog", supporting_resp.text)
        self.assertIn("Enum Set Catalog", supporting_resp.text)
        self.assertIn("role_count", supporting_resp.text)
        self.assertIn("Time Usage Guidance", supporting_resp.text)
        self.assertIn("binding_surfaces", supporting_resp.text)
        self.assertIn("latest_value_count", supporting_resp.text)
        self.assertIn("latest_value_keys", supporting_resp.text)
        self.assertIn("Enum Governance Guidance", supporting_resp.text)
        self.assertIn("Operator Guidance", module_resp.text)
        self.assertIn("createSemanticTime", api_resp.text)
        self.assertIn("updateSemanticTime", api_resp.text)
        self.assertIn("publishSemanticTime", api_resp.text)
        self.assertIn("createSemanticEnumSet", api_resp.text)
        self.assertIn("updateSemanticEnumSet", api_resp.text)
        self.assertIn("publishSemanticEnumSet", api_resp.text)

    def test_admin_semantic_catalog_typed_bindings_and_profiles_expose_remaining_contract_fields(
        self,
    ) -> None:
        module_resp = self.client.get("/static/admin/semantic-catalog/module.js")
        core_resp = self.client.get("/static/admin/semantic-catalog/core-config.js")
        api_resp = self.client.get("/static/admin/api.js")
        self.assertIn("Typed Binding Catalog", core_resp.text)
        self.assertIn("Compatibility Profile Catalog", core_resp.text)
        self.assertIn("time_surfaces", core_resp.text)
        self.assertIn("imported_binding_refs", core_resp.text)
        self.assertIn("Binding Grounding Guidance", core_resp.text)
        self.assertIn("payload_kind", core_resp.text)
        self.assertIn("population_subject_refs", core_resp.text)
        self.assertIn("subject_freeze", core_resp.text)
        self.assertIn("Compile Compatibility Guidance", core_resp.text)
        self.assertIn(
            "All eight T7 object pages keep structured contract summaries", module_resp.text
        )
        self.assertIn("Object-specific operator guidance stays with each subpage", module_resp.text)
        self.assertIn("publishTypedSemanticBinding", api_resp.text)
        self.assertIn("publishCompatibilityProfile", api_resp.text)

    def test_admin_semantic_catalog_declares_publish_freeze_and_helper_http_contracts(self) -> None:
        module_resp = self.client.get("/static/admin/semantic-catalog/module.js")
        core_resp = self.client.get("/static/admin/semantic-catalog/core-config.js")
        self.assertIn("Published objects are frozen and stay read-only in T7.", module_resp.text)
        self.assertIn("Publish failures render structured error details", module_resp.text)
        self.assertIn("GET /semantic/bindings", core_resp.text)
        self.assertIn("POST /semantic/bindings", core_resp.text)
        self.assertIn("PUT /semantic/bindings/{binding_id}", core_resp.text)
        self.assertIn("GET /semantic/resolve/{name}", module_resp.text)
        self.assertIn("GET /catalog/graph", module_resp.text)
        self.assertIn("publishTypedSemanticBinding", module_resp.text)
        self.assertIn("createTypedSemanticBinding", module_resp.text)
        self.assertIn("updateTypedSemanticBinding", module_resp.text)
        self.assertIn("View Related Bindings", module_resp.text)
        self.assertIn("Catalog Graph", module_resp.text)
        self.assertIn("Resolve Result", module_resp.text)
        self.assertIn("Bound Semantic Object", core_resp.text)
        self.assertIn("published revision freeze", module_resp.text)
        self.assertIn("Execution Binding Contract", core_resp.text)

    def test_admin_semantic_catalog_uses_object_id_route_and_t7_client_helpers(self) -> None:
        shell_resp = self.client.get("/static/admin/shell.js")
        semantic_resp = self.client.get("/static/admin/semantic-catalog/module.js")
        wrapper_resp = self.client.get("/static/admin/semantic-catalog.js")
        api_resp = self.client.get("/static/admin/api.js")
        self.assertIn("params.get('object_id')", shell_resp.text)
        self.assertIn("params.set('object_id', route.objectId)", shell_resp.text)
        self.assertIn("objectLabel: 'object_id'", shell_resp.text)
        self.assertIn("export { createSemanticCatalogModule }", wrapper_resp.text)
        self.assertIn("async function hydrate(panel, route)", semantic_resp.text)
        self.assertIn("function renderBody(viewModel)", semantic_resp.text)
        self.assertIn("refreshCurrentSemanticCatalog", semantic_resp.text)
        self.assertIn("runSemanticResolve", semantic_resp.text)
        self.assertIn("runSemanticCatalogGraph", semantic_resp.text)
        self.assertIn("runSemanticPlannerContext", semantic_resp.text)
        self.assertIn("handleSemanticFormSubmit", semantic_resp.text)
        self.assertIn("handleJumpSemanticRef", semantic_resp.text)
        self.assertIn("handlePublishSemanticObject", semantic_resp.text)
        self.assertIn("relatedBindingsFilter", semantic_resp.text)
        self.assertIn("listCompatibilityProfiles", api_resp.text)
        self.assertIn("createCompatibilityProfile", api_resp.text)
        self.assertIn("updateCompatibilityProfile", api_resp.text)
        self.assertIn("publishCompatibilityProfile", api_resp.text)
        self.assertIn("GET /sessions/{session_id}/planner-context", semantic_resp.text)

    def test_admin_guide_documents_all_t7_semantic_catalog_object_pages(self) -> None:
        guide_path = Path(__file__).resolve().parents[1] / "docs" / "agent-guide.md"
        content = guide_path.read_text(encoding="utf-8")
        self.assertIn("all eight subtabs", content)
        self.assertIn("Time", content)
        self.assertIn("Enum Sets", content)
        self.assertIn("Typed Bindings", content)
        self.assertIn("Compatibility Profiles", content)

    def test_admin_guide_documents_t8_analysis_ops_boundary(self) -> None:
        guide_path = Path(__file__).resolve().parents[1] / "docs" / "agent-guide.md"
        content = guide_path.read_text(encoding="utf-8")
        self.assertIn("Analysis Ops", content)
        self.assertIn("Terminate Session", content)
        self.assertIn(
            "Do not add create-session, intent, step, or plan-management controls", content
        )

    def test_admin_index_registers_analysis_ops_module(self) -> None:
        resp = self.client.get("/static/admin/index.js")
        self.assertIn("createAnalysisOpsModule", resp.text)
        self.assertIn("analysisOps: createAnalysisOpsModule(ctx)", resp.text)

    def test_admin_index_registers_runtime_jobs_module(self) -> None:
        resp = self.client.get("/static/admin/index.js")
        self.assertIn("createRuntimeJobsModule", resp.text)
        self.assertIn("runtimeJobs: createRuntimeJobsModule(ctx)", resp.text)

    def test_admin_index_registers_governance_module(self) -> None:
        resp = self.client.get("/static/admin/index.js")
        self.assertIn("createGovernanceModule", resp.text)
        self.assertIn("governance: createGovernanceModule(ctx)", resp.text)

    def test_admin_analysis_ops_declares_t8_inventory_filters_and_actions(self) -> None:
        module_resp = self.client.get("/static/admin/analysis-ops.js")
        api_resp = self.client.get("/static/admin/api.js")
        self.assertIn("Session Inventory", module_resp.text)
        self.assertIn("Session Summary", module_resp.text)
        self.assertIn("Session Operations", module_resp.text)
        self.assertIn("Status", module_resp.text)
        self.assertIn("Search by session_id", module_resp.text)
        self.assertIn("All statuses", module_resp.text)
        self.assertIn("session_id", module_resp.text)
        self.assertIn("goal", module_resp.text)
        self.assertIn("status", module_resp.text)
        self.assertIn("created_at", module_resp.text)
        self.assertIn("updated_at", module_resp.text)
        self.assertIn("terminal_reason", module_resp.text)
        self.assertIn("rollover_from_session_id", module_resp.text)
        self.assertIn("Constraints JSON", module_resp.text)
        self.assertIn("Budget JSON", module_resp.text)
        self.assertIn("Policy JSON", module_resp.text)
        self.assertIn("Terminate Session", module_resp.text)
        self.assertIn("Open in /ui Sessions", module_resp.text)
        self.assertIn("View State in /ui", module_resp.text)
        self.assertIn("View Runtime in /ui", module_resp.text)
        self.assertIn("View Jobs in /ui", module_resp.text)
        self.assertIn("GET /sessions", module_resp.text)
        self.assertIn("GET /sessions/{session_id}", module_resp.text)
        self.assertIn("listSessions(params = {})", api_resp.text)
        self.assertIn("getSession(sessionId)", api_resp.text)
        self.assertIn("terminateSession(sessionId, payload)", api_resp.text)
        self.assertIn("POST /sessions/{session_id}/terminate", api_resp.text)

    def test_admin_analysis_ops_keeps_t8_boundaries_and_empty_error_copy(self) -> None:
        resp = self.client.get("/static/admin/analysis-ops.js")
        self.assertIn("No analysis sessions available yet.", resp.text)
        self.assertIn("No sessions match the current filters.", resp.text)
        self.assertIn("404 session not found.", resp.text)
        self.assertIn("Terminate Session failed.", resp.text)
        self.assertIn("Session detail unavailable.", resp.text)
        self.assertIn("Analysis Ops unavailable.", resp.text)
        self.assertIn(
            "Select a session to inspect goal, constraints, budget, policy, terminal reason, and rollover lineage.",
            resp.text,
        )
        self.assertIn("终止后将阻止新的 intent 写入", resp.text)
        self.assertIn(
            "does not expose create-session, intent, step, or plan-management controls", resp.text
        )
        self.assertNotIn("Create Session", resp.text)
        self.assertNotIn("Run Intent", resp.text)
        self.assertNotIn("Run Step", resp.text)
        self.assertNotIn("Plan management", resp.text)

    def test_admin_guide_documents_t9_runtime_jobs_boundary(self) -> None:
        guide_path = Path(__file__).resolve().parents[1] / "docs" / "agent-guide.md"
        content = guide_path.read_text(encoding="utf-8")
        self.assertIn("Runtime & Jobs", content)
        self.assertIn("runtime truth rather than canonical result", content)
        self.assertIn("do not add job submit/cancel, retry/replay, or publish controls", content)

    def test_admin_runtime_jobs_declares_t9_runtime_views_and_job_contracts(self) -> None:
        module_resp = self.client.get("/static/admin/runtime-jobs.js")
        api_resp = self.client.get("/static/admin/api.js")
        self.assertIn("Session Runtime", module_resp.text)
        self.assertIn("Proposition Runtime", module_resp.text)
        self.assertIn("Artifact Runtime", module_resp.text)
        self.assertIn("Jobs", module_resp.text)
        self.assertIn("resource type", module_resp.text)
        self.assertIn("overall_status", module_resp.text)
        self.assertIn("last_successful_stage", module_resp.text)
        self.assertIn("blocked_reason", module_resp.text)
        self.assertIn("backlog_summary", module_resp.text)
        self.assertIn("current_assessment_id", module_resp.text)
        self.assertIn("current_attempt", module_resp.text)
        self.assertIn("backlog_state", module_resp.text)
        self.assertIn("artifact_stage", module_resp.text)
        self.assertIn("extractor_key", module_resp.text)
        self.assertIn("correlation_id", module_resp.text)
        self.assertIn("attempt_id", module_resp.text)
        self.assertIn("job_id", module_resp.text)
        self.assertIn("job_type", module_resp.text)
        self.assertIn("created_at", module_resp.text)
        self.assertIn("updated_at", module_resp.text)
        self.assertIn("payload summary", module_resp.text)
        self.assertIn("error detail", module_resp.text)
        self.assertIn("linked session", module_resp.text)
        self.assertIn("Open in /ui Runtime", module_resp.text)
        self.assertIn("Open linked session in /ui", module_resp.text)
        self.assertIn("Open State in /ui", module_resp.text)
        self.assertIn("Open Context in /ui", module_resp.text)
        self.assertIn("GET /sessions/{session_id}/runtime-status", module_resp.text)
        self.assertIn(
            "GET /sessions/{session_id}/propositions/{proposition_id}/runtime-status",
            module_resp.text,
        )
        self.assertIn(
            "GET /sessions/{session_id}/artifacts/{artifact_id}/runtime-status",
            module_resp.text,
        )
        self.assertIn("GET /jobs", module_resp.text)
        self.assertIn("GET /jobs/{job_id}", module_resp.text)
        self.assertIn("getSessionRuntimeStatus(sessionId)", api_resp.text)
        self.assertIn("getPropositionRuntimeStatus(sessionId, propositionId)", api_resp.text)
        self.assertIn("getArtifactRuntimeStatus(sessionId, artifactId)", api_resp.text)
        self.assertIn("listJobs(params = {})", api_resp.text)
        self.assertIn("getJob(jobId)", api_resp.text)

    def test_admin_runtime_jobs_keeps_t9_read_only_boundary_and_error_copy(self) -> None:
        resp = self.client.get("/static/admin/runtime-jobs.js")
        self.assertIn("runtime truth", resp.text)
        self.assertIn("not canonical result", resp.text)
        self.assertIn(
            "No retry, replay, submit, cancel, or publish controls exist on this page.", resp.text
        )
        self.assertIn("No runtime status loaded yet.", resp.text)
        self.assertIn("404 runtime target not found.", resp.text)
        self.assertIn("404 job not found. Select another job from the list.", resp.text)
        self.assertIn("No background jobs recorded yet.", resp.text)
        self.assertIn("No jobs match the current filters.", resp.text)
        self.assertIn("Session Runtime unavailable.", resp.text)
        self.assertIn("Proposition Runtime unavailable.", resp.text)
        self.assertIn("Artifact Runtime unavailable.", resp.text)
        self.assertIn("Jobs unavailable.", resp.text)
        self.assertIn("Job detail unavailable.", resp.text)
        self.assertNotIn("POST /jobs", resp.text)
        self.assertNotIn("POST /jobs/{job_id}/cancel", resp.text)
        self.assertNotIn("Retry Job", resp.text)
        self.assertNotIn("Replay", resp.text)
        self.assertNotIn("Publish", resp.text)

    def test_admin_shell_wires_governance_module_and_locator_contract(self) -> None:
        resp = self.client.get("/static/admin/shell.js")
        self.assertIn("modules.governance.render(route)", resp.text)
        self.assertIn("modules.governance.hydrate(panel, route)", resp.text)
        self.assertIn("params.get('policy_id')", resp.text)
        self.assertIn("params.get('rule_id')", resp.text)
        self.assertIn("params.get('request_id')", resp.text)
        self.assertIn("params.set('policy_id', route.policyId)", resp.text)
        self.assertIn("params.set('rule_id', route.ruleId)", resp.text)
        self.assertIn("params.set('request_id', route.requestId)", resp.text)
        self.assertIn(
            "route.requestId || route.policyId || route.ruleId || route.sessionId", resp.text
        )

    def test_admin_governance_declares_t10_inventory_actions_and_helper_boundaries(self) -> None:
        module_resp = self.client.get("/static/admin/governance.js")
        api_resp = self.client.get("/static/admin/api.js")
        self.assertIn("Policy Inventory", module_resp.text)
        self.assertIn("Policy Summary", module_resp.text)
        self.assertIn("Policy Editor", module_resp.text)
        self.assertIn("Create Policy", module_resp.text)
        self.assertIn("Edit Definition", module_resp.text)
        self.assertIn("Delete Policy", module_resp.text)
        self.assertIn("Enable Policy", module_resp.text)
        self.assertIn("Disable Policy", module_resp.text)
        self.assertIn("Quality Rule Inventory", module_resp.text)
        self.assertIn("Quality Rule Summary", module_resp.text)
        self.assertIn("Quality Rule Create", module_resp.text)
        self.assertIn("Create Quality Rule", module_resp.text)
        self.assertIn("Delete Quality Rule", module_resp.text)
        self.assertIn("Approval Queue", module_resp.text)
        self.assertIn("Approval Summary", module_resp.text)
        self.assertIn("Approval Actions", module_resp.text)
        self.assertIn("Approve / Reject", module_resp.text)
        self.assertIn("Auto-flag Approvals", module_resp.text)
        self.assertIn("Governance Helpers", module_resp.text)
        self.assertIn("Governance Check", module_resp.text)
        self.assertIn("Routing Resolve", module_resp.text)
        self.assertIn("diagnostic only", module_resp.text)
        self.assertIn("GET /policies", module_resp.text)
        self.assertIn("PUT /policies/{policy_id}", module_resp.text)
        self.assertIn("DELETE /policies/{policy_id}", module_resp.text)
        self.assertIn("GET /quality-rules", module_resp.text)
        self.assertIn("DELETE /quality-rules/{rule_id}", module_resp.text)
        self.assertIn("GET /approvals/{request_id}", module_resp.text)
        self.assertIn("POST /approvals/{request_id}/approve", module_resp.text)
        self.assertIn("POST /approvals/{request_id}/reject", module_resp.text)
        self.assertIn("POST /sessions/{session_id}/approvals/auto-flag", module_resp.text)
        self.assertIn("POST /governance/check", module_resp.text)
        self.assertIn("POST /routing/resolve", module_resp.text)
        self.assertIn("listPolicies()", api_resp.text)
        self.assertIn("getPolicy(policyId)", api_resp.text)
        self.assertIn("updatePolicy(policyId, payload)", api_resp.text)
        self.assertIn("listQualityRules(params = {})", api_resp.text)
        self.assertIn("getApproval(requestId)", api_resp.text)
        self.assertIn("approveApproval(requestId, payload)", api_resp.text)
        self.assertIn("rejectApproval(requestId, payload)", api_resp.text)
        self.assertIn("autoFlagApprovals(sessionId, payload)", api_resp.text)
        self.assertIn("governanceCheck(payload)", api_resp.text)
        self.assertIn("routingResolve(payload)", api_resp.text)

    def test_admin_governance_declares_empty_states_and_scope_limits(self) -> None:
        resp = self.client.get("/static/admin/governance.js")
        self.assertIn("No policies configured yet.", resp.text)
        self.assertIn("No quality rules configured yet.", resp.text)
        self.assertIn("No approval requests match the current filters.", resp.text)
        self.assertIn("The default queue only shows pending requests.", resp.text)
        self.assertIn(
            "There is no update endpoint, so /admin does not expose edit actions.", resp.text
        )
        self.assertIn("name, policy_type, and scope remain immutable in /admin", resp.text)
        self.assertIn("Do not move approvals or runtime troubleshooting into this page.", resp.text)
        self.assertIn(
            "Run Governance Check or Routing Resolve to inspect diagnostic output.", resp.text
        )

    def test_admin_guide_documents_t10_governance_boundaries(self) -> None:
        guide_path = Path(__file__).resolve().parents[1] / "docs" / "agent-guide.md"
        content = guide_path.read_text(encoding="utf-8")
        self.assertIn("Governance", content)
        self.assertIn("Policies", content)
        self.assertIn("Quality Rules", content)
        self.assertIn("Approvals", content)
        self.assertIn("Governance Helpers", content)
        self.assertIn("policy_id", content)
        self.assertIn("rule_id", content)
        self.assertIn("request_id", content)
        self.assertIn("does not fake unsupported policy or quality-rule edit capabilities", content)

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
