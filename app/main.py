from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from starlette.responses import PlainTextResponse

from app.approvals import ApprovalService
from app.bindings import BindingService
from app.config import load_config
from app.engines import EngineService
from app.governance import GovernanceService
from app.jobs import JobService
from app.observability import MetricsCollector, TimingMiddleware, setup_logging
from app.planning import PlanningService
from app.models import (
    ApprovalCreateRequest,
    ApprovalDecisionRequest,
    AutoFlagRequest,
    BindingCreateRequest,
    EngineRegisterRequest,
    EntityCreateRequest,
    EntityUpdateRequest,
    GovernanceCheckRequest,
    JobSubmitRequest,
    MappingCreateRequest,
    MetricCreateRequest,
    MetricUpdateRequest,
    PolicyCreateRequest,
    PolicyUpdateRequest,
    QualityRuleCreateRequest,
    RouteResolveRequest,
    SessionCreateRequest,
    SourceRegisterRequest,
    SyncSelectionRequest,
)
from app.routing import QueryRouter
from app.service import SemanticLayerService, default_db_path
from app.sources import SourceService
from app.storage.analytics import AnalyticsEngine
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.metadata import MetadataStore
from app.storage.sqlite_metadata import SQLiteMetadataStore
from app.sync import SyncEngine


logger = logging.getLogger(__name__)


def create_app(
    db_path: str | Path | None = None,
    metadata_store: MetadataStore | None = None,
    analytics_engine: AnalyticsEngine | None = None,
    config_path: str | Path | None = None,
) -> FastAPI:
    resolved_path = Path(db_path) if db_path is not None else Path(os.getenv("DUCKDB_MVP_DB", default_db_path()))

    if metadata_store is None:
        metadata_db = resolved_path.with_suffix(".meta.sqlite")
        metadata_store = SQLiteMetadataStore(metadata_db)
    if analytics_engine is None:
        analytics_engine = DuckDBAnalyticsEngine(resolved_path)

    metadata_store.initialize()
    analytics_engine.initialize()

    # ── Observability ────────────────────────────────────────────
    config = load_config(Path(config_path) if config_path is not None else None)
    setup_logging(level=config.observability.log_level)
    metrics_collector = MetricsCollector() if config.observability.metrics_enabled else None

    # ── Governance ───────────────────────────────────────────────
    governance_service = GovernanceService(metadata_store, analytics_engine) if config.governance.enabled else None

    # ── Approval service ─────────────────────────────────────────
    approval_service = ApprovalService(metadata_store)

    service = SemanticLayerService(
        metadata_store, analytics_engine,
        governance=governance_service,
        metrics=metrics_collector,
        approvals=approval_service,
    )  # query_router wired below after config
    source_service = SourceService(metadata_store)
    sync_engine = SyncEngine(metadata_store)

    # ── Auto-register sources from config ──────────────────────
    for src_cfg in config.sources:
        try:
            source = source_service.ensure_source(
                source_type=src_cfg.type,
                display_name=src_cfg.name,
                connection=src_cfg.connection,
                sync_mode=src_cfg.sync.mode,
            )
            sync_mode = source.get("sync_mode", src_cfg.sync.mode)
            if sync_mode == "none":
                logger.info("Config source '%s' registered (sync disabled)", src_cfg.name)
            elif sync_mode == "by_select":
                selections = source_service.list_sync_selections(source["source_id"])
                if selections:
                    sel_dicts = [{"schema_name": s["schema_name"], "table_name": s["table_name"]} for s in selections]
                    adapter = source_service.get_adapter(source["source_id"])
                    sync_engine.trigger_sync(source["source_id"], adapter, selections=sel_dicts)
                    logger.info("Config source '%s' registered and selectively synced", src_cfg.name)
                else:
                    logger.info("Config source '%s' registered (by_select, no selections yet)", src_cfg.name)
            else:
                adapter = source_service.get_adapter(source["source_id"])
                sync_engine.trigger_sync(source["source_id"], adapter)
                logger.info("Config source '%s' registered and synced", src_cfg.name)
        except Exception:
            logger.exception("Failed to register/sync config source '%s'", src_cfg.name)

    # ── Auto-register engines from config ─────────────────────
    engine_service = EngineService(metadata_store)
    for eng_cfg in config.engines:
        try:
            engine_service.ensure_engine(
                engine_type=eng_cfg.type,
                display_name=eng_cfg.name,
                connection=eng_cfg.connection,
            )
            logger.info("Config engine '%s' registered", eng_cfg.name)
        except Exception:
            logger.exception("Failed to register config engine '%s'", eng_cfg.name)

    # ── Auto-register bindings from config ───────────────────
    binding_service = BindingService(metadata_store)
    for bind_cfg in config.bindings:
        try:
            src_row = metadata_store.query_one(
                "SELECT source_id FROM sources WHERE display_name = ?",
                [bind_cfg.source],
            )
            eng_row = metadata_store.query_one(
                "SELECT engine_id FROM engines WHERE display_name = ?",
                [bind_cfg.engine],
            )
            if src_row and eng_row:
                binding_service.ensure_binding(
                    src_row["source_id"], eng_row["engine_id"], bind_cfg.priority,
                    namespace=bind_cfg.namespace,
                )
                logger.info("Config binding '%s' -> '%s' registered", bind_cfg.source, bind_cfg.engine)
            else:
                if not src_row:
                    logger.warning("Config binding: source '%s' not found", bind_cfg.source)
                if not eng_row:
                    logger.warning("Config binding: engine '%s' not found", bind_cfg.engine)
        except Exception:
            logger.exception("Failed to register config binding '%s' -> '%s'", bind_cfg.source, bind_cfg.engine)

    query_router = QueryRouter(metadata_store, engine_service)
    service.query_router = query_router

    app = FastAPI(title="OmniDB Semantic Layer", version="0.2.0")

    # ── Optional Web UI ─────────────────────────────────────────
    admin_enabled = config.ui.admin_enabled if config.ui.admin_enabled is not None else config.ui.enabled
    user_enabled = config.ui.user_enabled if config.ui.user_enabled is not None else config.ui.enabled

    if admin_enabled or user_enabled:
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles

        static_dir = Path(__file__).parent / "static"

        if admin_enabled:
            @app.get("/admin")
            def admin_index():
                return FileResponse(static_dir / "admin.html")

        if user_enabled:
            @app.get("/ui")
            def ui_index():
                return FileResponse(static_dir / "user.html")

        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # ── Auto-register governance policies from config ──────────
    if governance_service:
        for pol_cfg in config.governance.policies:
            try:
                existing = metadata_store.query_one(
                    "SELECT policy_id FROM policies WHERE name = ?", [pol_cfg.name]
                )
                if not existing:
                    governance_service.create_policy(
                        name=pol_cfg.name,
                        policy_type=pol_cfg.type,
                        definition=pol_cfg.definition,
                        scope=pol_cfg.scope,
                    )
                    logger.info("Config governance policy '%s' registered", pol_cfg.name)
            except Exception:
                logger.exception("Failed to register config governance policy '%s'", pol_cfg.name)
        for qr_cfg in config.governance.quality_rules:
            try:
                existing = metadata_store.query_one(
                    "SELECT rule_id FROM quality_rules WHERE name = ?", [qr_cfg.name]
                )
                if not existing:
                    governance_service.create_quality_rule(
                        name=qr_cfg.name,
                        rule_type=qr_cfg.type,
                        table_name=qr_cfg.table,
                        threshold=qr_cfg.threshold,
                        severity=qr_cfg.severity,
                    )
                    logger.info("Config quality rule '%s' registered", qr_cfg.name)
            except Exception:
                logger.exception("Failed to register config quality rule '%s'", qr_cfg.name)

    app.state.service = service
    app.state.source_service = source_service
    app.state.sync_engine = sync_engine
    app.state.engine_service = engine_service
    app.state.binding_service = binding_service
    app.state.query_router = query_router
    app.state.metadata_store = metadata_store
    app.state.analytics_engine = analytics_engine
    planning_service = PlanningService(metadata_store)
    app.state.planning_service = planning_service
    app.state.governance_service = governance_service
    app.state.approval_service = approval_service
    app.state.metrics = metrics_collector
    job_service = JobService(metadata_store, service, planning_service=planning_service)
    app.state.job_service = job_service

    # ── Middleware ────────────────────────────────────────────────
    app.add_middleware(TimingMiddleware)

    # ── Health & catalog ─────────────────────────────────────────

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "db_path": str(resolved_path)}

    @app.post("/sessions")
    def create_session(request: SessionCreateRequest) -> dict[str, object]:
        return service.create_session(
            goal=request.goal,
            constraints=request.constraints,
            budget=request.budget,
            policy=request.policy,
        )

    @app.get("/sessions")
    def list_sessions(status: str | None = Query(default=None)) -> list[dict[str, object]]:
        return service.list_sessions(status=status)

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, object]:
        try:
            return service.get_session(session_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/catalog")
    def catalog() -> dict[str, object]:
        return service.discover_catalog()

    @app.post("/sessions/{session_id}/steps/{step_type}")
    def run_step(session_id: str, step_type: str, body: dict[str, Any] | None = None) -> dict[str, object]:
        try:
            return service.run_step(session_id, step_type, params=body)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/sessions/{session_id}/workflow/watch-time-drop")
    def run_watch_time_drop(session_id: str) -> dict[str, object]:
        try:
            return service.run_watch_time_drop_workflow(session_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/sessions/{session_id}/evidence")
    def evidence_graph(session_id: str) -> dict[str, object]:
        try:
            return service.get_evidence_graph(session_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    # ── Planning ──────────────────────────────────────────────────

    @app.post("/sessions/{session_id}/plans")
    def draft_plan(session_id: str, body: dict[str, Any]) -> dict[str, object]:
        try:
            service._assert_session_exists(session_id)
            return planning_service.draft_plan(session_id, body.get("steps", []))
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/sessions/{session_id}/plans")
    def list_plans(session_id: str) -> list[dict[str, object]]:
        return planning_service.list_plans(session_id)

    @app.get("/sessions/{session_id}/plans/{plan_id}")
    def get_plan(session_id: str, plan_id: str) -> dict[str, object]:
        try:
            return planning_service.get_plan(plan_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.patch("/sessions/{session_id}/plans/{plan_id}")
    def patch_plan(session_id: str, plan_id: str, body: dict[str, Any]) -> dict[str, object]:
        try:
            return planning_service.patch_plan(plan_id, steps=body.get("steps"))
        except (KeyError, ValueError) as error:
            status = 404 if isinstance(error, KeyError) else 400
            raise HTTPException(status_code=status, detail=str(error)) from error

    @app.post("/sessions/{session_id}/plans/{plan_id}/validate")
    def validate_plan(session_id: str, plan_id: str) -> dict[str, object]:
        try:
            return planning_service.validate_plan(plan_id)
        except (KeyError, ValueError) as error:
            status = 404 if isinstance(error, KeyError) else 400
            raise HTTPException(status_code=status, detail=str(error)) from error

    @app.post("/sessions/{session_id}/plans/{plan_id}/approve")
    def approve_plan(session_id: str, plan_id: str) -> dict[str, object]:
        try:
            return planning_service.approve_plan(plan_id)
        except (KeyError, ValueError) as error:
            status = 404 if isinstance(error, KeyError) else 400
            raise HTTPException(status_code=status, detail=str(error)) from error

    @app.post("/sessions/{session_id}/plans/{plan_id}/execute")
    def execute_plan(session_id: str, plan_id: str) -> dict[str, object]:
        try:
            return planning_service.execute_plan(plan_id, service)
        except (KeyError, ValueError) as error:
            status = 404 if isinstance(error, KeyError) else 400
            raise HTTPException(status_code=status, detail=str(error)) from error

    @app.get("/sessions/{session_id}/plans/{plan_id}/explain")
    def explain_plan(session_id: str, plan_id: str) -> dict[str, object]:
        try:
            return planning_service.explain_plan(plan_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/sessions/{session_id}/plans/{plan_id}/estimate-costs")
    def estimate_plan_costs(session_id: str, plan_id: str) -> dict[str, object]:
        try:
            return planning_service.estimate_costs(plan_id, analytics_engine)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/sessions/{session_id}/plans/{plan_id}/budget-check")
    def check_plan_budget(session_id: str, plan_id: str) -> dict[str, object]:
        try:
            return planning_service.check_budget(plan_id, session_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    # ── Source registry (Phase 1) ────────────────────────────────

    @app.post("/sources")
    def register_source(request: SourceRegisterRequest) -> dict[str, object]:
        return source_service.register_source(
            source_type=request.source_type,
            display_name=request.display_name,
            connection=request.connection,
            capabilities=request.capabilities,
        )

    @app.get("/sources")
    def list_sources() -> list[dict[str, object]]:
        return source_service.list_sources()

    @app.get("/sources/{source_id}")
    def get_source(source_id: str) -> dict[str, object]:
        try:
            return source_service.get_source(source_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/sources/{source_id}/sync")
    def trigger_sync(source_id: str) -> dict[str, object]:
        try:
            sync_mode = source_service.get_sync_mode(source_id)
            if sync_mode == "none":
                raise HTTPException(status_code=400, detail="Sync disabled for this source (mode=none)")
            adapter = source_service.get_adapter(source_id)
            if sync_mode == "by_select":
                selections = source_service.list_sync_selections(source_id)
                if not selections:
                    raise HTTPException(status_code=400, detail="No sync selections configured for this source (mode=by_select)")
                sel_dicts = [{"schema_name": s["schema_name"], "table_name": s["table_name"]} for s in selections]
                job_id = sync_engine.trigger_sync(source_id, adapter, selections=sel_dicts)
            else:
                job_id = sync_engine.trigger_sync(source_id, adapter)
            return {"job_id": job_id, "source_id": source_id, "status": "succeeded"}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    # ── Sync selection management ────────────────────────────────
    # NOTE: These must be registered BEFORE /sync/{job_id} to avoid
    # the path parameter capturing "selections" as a job_id.

    @app.get("/sources/{source_id}/sync/selections")
    def list_sync_selections(source_id: str) -> list[dict[str, object]]:
        try:
            source_service.get_source(source_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return source_service.list_sync_selections(source_id)

    @app.post("/sources/{source_id}/sync/selections")
    def add_sync_selections(source_id: str, request: SyncSelectionRequest) -> list[dict[str, object]]:
        try:
            return source_service.set_sync_selections(
                source_id,
                [{"schema_name": s.schema_name, "table_name": s.table_name} for s in request.selections],
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.delete("/sources/{source_id}/sync/selections")
    def clear_sync_selections(source_id: str) -> dict[str, str]:
        try:
            source_service.clear_sync_selections(source_id)
            return {"status": "cleared", "source_id": source_id}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.delete("/sources/{source_id}/sync/selections/{selection_id}")
    def remove_sync_selection(source_id: str, selection_id: str) -> dict[str, str]:
        try:
            source_service.get_source(source_id)
            source_service.remove_sync_selection(selection_id)
            return {"status": "deleted", "selection_id": selection_id}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/sources/{source_id}/sync/{job_id}")
    def get_sync_status(source_id: str, job_id: str) -> dict[str, object]:
        try:
            return sync_engine.get_sync_status(job_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    # ── Live catalog browsing ────────────────────────────────────

    @app.get("/sources/{source_id}/catalog/schemas")
    def browse_catalog_schemas(source_id: str) -> list[dict[str, object]]:
        try:
            return source_service.browse_catalog_schemas(source_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/sources/{source_id}/catalog/tables")
    def browse_catalog_tables(
        source_id: str,
        schema: str = Query(...),
    ) -> list[dict[str, object]]:
        try:
            return source_service.browse_catalog_tables(source_id, schema)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/sources/{source_id}/objects")
    def list_source_objects(
        source_id: str,
        type: str | None = Query(default=None),
        schema: str | None = Query(default=None, alias="schema"),
    ) -> list[dict[str, object]]:
        try:
            source_service.get_source(source_id)  # verify source exists
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return source_service.list_objects(source_id, object_type=type, schema_name=schema)

    # ── Engine registry ──────────────────────────────────────────

    @app.post("/engines")
    def register_engine(request: EngineRegisterRequest) -> dict[str, object]:
        return engine_service.register_engine(
            engine_type=request.engine_type,
            display_name=request.display_name,
            connection=request.connection,
            capabilities=request.capabilities,
        )

    @app.get("/engines")
    def list_engines() -> list[dict[str, object]]:
        return engine_service.list_engines()

    @app.get("/engines/{engine_id}")
    def get_engine(engine_id: str) -> dict[str, object]:
        try:
            return engine_service.get_engine(engine_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    # ── Source-engine bindings ────────────────────────────────────

    @app.post("/bindings")
    def create_binding(request: BindingCreateRequest) -> dict[str, object]:
        try:
            return binding_service.create_binding(
                source_id=request.source_id,
                engine_id=request.engine_id,
                priority=request.priority,
                namespace=request.namespace,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/bindings")
    def list_bindings(
        source_id: str | None = Query(default=None),
        engine_id: str | None = Query(default=None),
    ) -> list[dict[str, object]]:
        return binding_service.list_bindings(source_id=source_id, engine_id=engine_id)

    @app.get("/bindings/{binding_id}")
    def get_binding(binding_id: str) -> dict[str, object]:
        try:
            return binding_service.get_binding(binding_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.delete("/bindings/{binding_id}")
    def delete_binding(binding_id: str) -> dict[str, str]:
        try:
            binding_service.delete_binding(binding_id)
            return {"status": "deleted", "binding_id": binding_id}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/sources/{source_id}/engines")
    def list_source_engines(source_id: str) -> list[dict[str, object]]:
        try:
            source_service.get_source(source_id)  # verify source exists
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return binding_service.get_engines_for_source(source_id)

    # ── Query routing ────────────────────────────────────────────

    @app.post("/routing/resolve")
    def routing_resolve(request: RouteResolveRequest) -> dict[str, object]:
        try:
            route = query_router.resolve_tables(request.table_names)

            # Get engine info for the resolved engine
            engine_row = metadata_store.query_one(
                "SELECT engine_id, engine_type, display_name FROM engines WHERE engine_id = ?",
                [route.engine_id],
            )
            engine_info = {
                "engine_id": engine_row["engine_id"],
                "engine_type": engine_row["engine_type"],
                "display_name": engine_row["display_name"],
            } if engine_row else None

            return {
                "resolved": True,
                "table_names": request.table_names,
                "engine": engine_info,
                "qualified_names": route.qualified_names,
            }
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    # ── Semantic CRUD (Phase 2) ──────────────────────────────────
    # Imports deferred to avoid circular imports at module level.

    @app.post("/semantic/entities")
    def create_entity(request: EntityCreateRequest) -> dict[str, object]:
        from app.semantic import SemanticService
        svc = SemanticService(metadata_store)
        return svc.create_entity(
            name=request.name,
            display_name=request.display_name,
            description=request.description,
            keys=request.keys,
            properties=request.properties,
        )

    @app.get("/semantic/entities")
    def list_entities(status: str | None = Query(default=None)) -> list[dict[str, object]]:
        from app.semantic import SemanticService
        return SemanticService(metadata_store).list_entities(status=status)

    @app.get("/semantic/entities/{entity_id}")
    def get_entity(entity_id: str) -> dict[str, object]:
        from app.semantic import SemanticService
        try:
            return SemanticService(metadata_store).get_entity(entity_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.put("/semantic/entities/{entity_id}")
    def update_entity(entity_id: str, request: EntityUpdateRequest) -> dict[str, object]:
        from app.semantic import SemanticService
        try:
            return SemanticService(metadata_store).update_entity(entity_id, **request.model_dump(exclude_none=True))
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/semantic/entities/{entity_id}/publish")
    def publish_entity(entity_id: str) -> dict[str, object]:
        from app.semantic import SemanticService
        try:
            return SemanticService(metadata_store).publish_entity(entity_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/semantic/metrics")
    def create_metric(request: MetricCreateRequest) -> dict[str, object]:
        from app.semantic import SemanticService
        return SemanticService(metadata_store).create_metric(
            name=request.name,
            display_name=request.display_name,
            description=request.description,
            definition_sql=request.definition_sql,
            dimensions=request.dimensions,
            entity_id=request.entity_id,
            properties=request.properties,
        )

    @app.get("/semantic/metrics")
    def list_metrics(status: str | None = Query(default=None)) -> list[dict[str, object]]:
        from app.semantic import SemanticService
        return SemanticService(metadata_store).list_metrics(status=status)

    @app.get("/semantic/metrics/{metric_id}")
    def get_metric(metric_id: str) -> dict[str, object]:
        from app.semantic import SemanticService
        try:
            return SemanticService(metadata_store).get_metric(metric_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.put("/semantic/metrics/{metric_id}")
    def update_metric(metric_id: str, request: MetricUpdateRequest) -> dict[str, object]:
        from app.semantic import SemanticService
        try:
            return SemanticService(metadata_store).update_metric(metric_id, **request.model_dump(exclude_none=True))
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/semantic/metrics/{metric_id}/publish")
    def publish_metric(metric_id: str) -> dict[str, object]:
        from app.semantic import SemanticService
        try:
            return SemanticService(metadata_store).publish_metric(metric_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/semantic/mappings")
    def create_mapping(request: MappingCreateRequest) -> dict[str, object]:
        from app.semantic import SemanticService
        return SemanticService(metadata_store).create_mapping(
            semantic_type=request.semantic_type,
            semantic_id=request.semantic_id,
            object_id=request.object_id,
            mapping_type=request.mapping_type,
            mapping_json=request.mapping_json,
        )

    @app.get("/semantic/mappings")
    def list_mappings(
        semantic_type: str | None = Query(default=None),
        semantic_id: str | None = Query(default=None),
    ) -> list[dict[str, object]]:
        from app.semantic import SemanticService
        return SemanticService(metadata_store).list_mappings(
            semantic_type=semantic_type,
            semantic_id=semantic_id,
        )

    @app.delete("/semantic/mappings/{mapping_id}")
    def delete_mapping(mapping_id: str) -> dict[str, str]:
        from app.semantic import SemanticService
        try:
            SemanticService(metadata_store).delete_mapping(mapping_id)
            return {"status": "deleted", "mapping_id": mapping_id}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    # ── Catalog query (Phase 3) ──────────────────────────────────

    @app.get("/catalog/search")
    def catalog_search(
        q: str = Query(..., min_length=1),
        type: str | None = Query(default=None),
    ) -> list[dict[str, object]]:
        from app.catalog_query import CatalogQueryService
        return CatalogQueryService(metadata_store).search(q, object_type=type)

    @app.get("/semantic/resolve/{name}")
    def resolve_term(name: str) -> dict[str, object]:
        from app.catalog_query import CatalogQueryService
        try:
            return CatalogQueryService(metadata_store, binding_service).resolve(name)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/sessions/{session_id}/planner-context")
    def planner_context(session_id: str) -> dict[str, object]:
        from app.catalog_query import CatalogQueryService
        try:
            service._assert_session_exists(session_id)
            return CatalogQueryService(metadata_store).planner_context(session_id, service)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/catalog/graph")
    def catalog_graph(
        root: str = Query(...),
        depth: int = Query(default=2, ge=1, le=5),
    ) -> dict[str, object]:
        from app.catalog_query import CatalogQueryService
        try:
            return CatalogQueryService(metadata_store).graph(root, depth)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    # ── Governance endpoints ─────────────────────────────────────

    @app.post("/policies")
    def create_policy(request: PolicyCreateRequest) -> dict[str, object]:
        if not governance_service:
            raise HTTPException(status_code=400, detail="Governance is disabled")
        try:
            return governance_service.create_policy(
                name=request.name,
                policy_type=request.policy_type,
                definition=request.definition,
                scope=request.scope,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/policies")
    def list_policies() -> list[dict[str, object]]:
        if not governance_service:
            return []
        return governance_service.list_policies(enabled_only=False)

    @app.get("/policies/{policy_id}")
    def get_policy(policy_id: str) -> dict[str, object]:
        if not governance_service:
            raise HTTPException(status_code=400, detail="Governance is disabled")
        try:
            return governance_service.get_policy(policy_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.put("/policies/{policy_id}")
    def update_policy(policy_id: str, request: PolicyUpdateRequest) -> dict[str, object]:
        if not governance_service:
            raise HTTPException(status_code=400, detail="Governance is disabled")
        try:
            return governance_service.update_policy(
                policy_id,
                enabled=request.enabled,
                definition=request.definition,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.delete("/policies/{policy_id}")
    def delete_policy(policy_id: str) -> dict[str, object]:
        if not governance_service:
            raise HTTPException(status_code=400, detail="Governance is disabled")
        try:
            return governance_service.delete_policy(policy_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/quality-rules")
    def create_quality_rule(request: QualityRuleCreateRequest) -> dict[str, object]:
        if not governance_service:
            raise HTTPException(status_code=400, detail="Governance is disabled")
        try:
            return governance_service.create_quality_rule(
                name=request.name,
                rule_type=request.rule_type,
                table_name=request.table_name,
                threshold=request.threshold,
                severity=request.severity,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/quality-rules")
    def list_quality_rules(table: str | None = Query(default=None)) -> list[dict[str, object]]:
        if not governance_service:
            return []
        return governance_service.list_quality_rules(table_name=table)

    @app.delete("/quality-rules/{rule_id}")
    def delete_quality_rule(rule_id: str) -> dict[str, object]:
        if not governance_service:
            raise HTTPException(status_code=400, detail="Governance is disabled")
        try:
            return governance_service.delete_quality_rule(rule_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/governance/check")
    def governance_check(request: GovernanceCheckRequest) -> dict[str, object]:
        if not governance_service:
            return {"passed": True, "violations": [], "warnings": []}
        return governance_service.check_step(
            session_id=request.session_id,
            step_type=request.step_type,
            params=request.params,
        )

    # ── Async job endpoints ──────────────────────────────────────

    @app.post("/jobs")
    def submit_job(request: JobSubmitRequest) -> dict[str, object]:
        try:
            return job_service.submit_job(
                session_id=request.session_id,
                job_type=request.job_type,
                payload=request.payload,
            )
        except (KeyError, ValueError) as error:
            status = 404 if isinstance(error, KeyError) else 400
            raise HTTPException(status_code=status, detail=str(error)) from error

    @app.get("/jobs")
    def list_jobs(
        session_id: str | None = Query(default=None),
        status: str | None = Query(default=None),
    ) -> list[dict[str, object]]:
        return job_service.list_jobs(session_id=session_id, status=status)

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, object]:
        try:
            return job_service.get_job(job_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, object]:
        try:
            return job_service.cancel_job(job_id)
        except (KeyError, ValueError) as error:
            status = 404 if isinstance(error, KeyError) else 400
            raise HTTPException(status_code=status, detail=str(error)) from error

    # ── Approval endpoints ───────────────────────────────────────

    @app.post("/approvals")
    def create_approval(request: ApprovalCreateRequest) -> dict[str, object]:
        try:
            return approval_service.request_approval(
                session_id=request.session_id,
                rec_id=request.rec_id,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/approvals")
    def list_approvals(
        session_id: str | None = Query(default=None),
        status: str | None = Query(default=None),
    ) -> list[dict[str, object]]:
        return approval_service.list_requests(session_id=session_id, status=status)

    @app.get("/approvals/{request_id}")
    def get_approval(request_id: str) -> dict[str, object]:
        try:
            return approval_service.get_request(request_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/approvals/{request_id}/approve")
    def approve_request(request_id: str, body: ApprovalDecisionRequest) -> dict[str, object]:
        try:
            return approval_service.approve(request_id, reviewer=body.reviewer, reason=body.reason)
        except (KeyError, ValueError) as error:
            status = 404 if isinstance(error, KeyError) else 400
            raise HTTPException(status_code=status, detail=str(error)) from error

    @app.post("/approvals/{request_id}/reject")
    def reject_request(request_id: str, body: ApprovalDecisionRequest) -> dict[str, object]:
        try:
            return approval_service.reject(request_id, reviewer=body.reviewer, reason=body.reason)
        except (KeyError, ValueError) as error:
            status = 404 if isinstance(error, KeyError) else 400
            raise HTTPException(status_code=status, detail=str(error)) from error

    @app.post("/sessions/{session_id}/approvals/auto-flag")
    def auto_flag_approvals(session_id: str, body: AutoFlagRequest | None = None) -> list[dict[str, object]]:
        try:
            service._assert_session_exists(session_id)
            threshold = body.risk_threshold if body else "P0"
            return approval_service.auto_flag_recommendations(session_id, risk_threshold=threshold)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    # ── Metrics endpoint ─────────────────────────────────────────

    @app.get("/metrics")
    def get_metrics(format: str | None = Query(default=None)) -> Any:
        if metrics_collector is None:
            return {"error": "Metrics collection is disabled"}
        if format == "prometheus":
            return PlainTextResponse(metrics_collector.prometheus(), media_type="text/plain")
        return metrics_collector.snapshot()

    return app


app = create_app()
