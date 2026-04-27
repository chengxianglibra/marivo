import {
  approvals,
  engines,
  health,
  jobs,
  mappings,
  metrics,
  openapiIndex,
  policies,
  propositionContext,
  qualityRules,
  runtimeStatus,
  semantic,
  sessionState,
  sessions,
  sourceObjects,
  sources,
} from "./mockData";
import type { JsonRecord } from "../api/types";

function stripQuery(path: string): string {
  return path.split("?")[0] ?? path;
}

function idFor(kind: "source" | "engine" | "mapping"): string {
  return `${kind.slice(0, 3)}_${Math.random().toString(16).slice(2, 10)}`;
}

function now(): string {
  return new Date().toISOString();
}

function readiness(payload: JsonRecord): JsonRecord {
  return {
    status: "active",
    readiness_status: "ready",
    failure_code: null,
    updated_at: now(),
    ...payload,
  };
}

export function mockGet(path: string, query?: Record<string, unknown>): unknown {
  const clean = stripQuery(path);
  if (clean === "/health") return health;
  if (clean === "/metrics") return metrics;
  if (clean === "/openapi/index") return openapiIndex;
  if (clean === "/sources") return sources;
  const objectsMatch = clean.match(/^\/sources\/([^/]+)\/objects$/);
  if (objectsMatch) return sourceObjects[objectsMatch[1]] ?? [];
  const selectionMatch = clean.match(/^\/sources\/([^/]+)\/sync\/selections$/);
  if (selectionMatch) {
    if (selectionMatch[1] === "src_sales_duckdb") {
      return [
        {
          selection_id: "sel_sales_orders",
          source_id: selectionMatch[1],
          schema_name: "analytics",
          table_name: "orders",
        },
      ];
    }
    return [];
  }
  const schemaMatch = clean.match(/^\/sources\/([^/]+)\/catalog\/schemas$/);
  if (schemaMatch) {
    return [{ schema_name: "analytics" }, { schema_name: "iceberg_inf" }];
  }
  const tableMatch = clean.match(/^\/sources\/([^/]+)\/catalog\/tables$/);
  if (tableMatch) {
    const schema = String(query?.schema ?? "");
    return schema === "iceberg_inf"
      ? [{ table_name: "public_holiday_events" }, { table_name: "v_ott_user_profile_wide" }]
      : [{ table_name: "orders" }, { table_name: "watch_events" }];
  }
  if (clean === "/engines") return engines;
  if (clean === "/mappings") return mappings;
  if (clean === "/jobs") {
    return jobs.filter((job) => {
      return (!query?.session_id || job.session_id === query.session_id) && (!query?.status || job.status === query.status);
    });
  }
  if (clean === "/policies") return policies;
  if (clean === "/quality-rules") return qualityRules;
  if (clean === "/sessions") {
    return { items: sessions.filter((session) => !query?.status || session.status === query.status) };
  }
  if (clean.endsWith("/state")) return sessionState;
  if (clean.endsWith("/runtime-status")) return runtimeStatus;
  if (clean.endsWith("/context")) return propositionContext;
  if (clean === "/approvals") return approvals;

  const semanticMatch = clean.match(/^\/semantic\/([^/]+)$/);
  if (semanticMatch) return { items: semantic[semanticMatch[1]] ?? [] };
  if (clean === "/compiler/compatibility-profiles") return { items: semantic["compatibility-profiles"] };

  return {};
}

export function mockPost(path: string, body: unknown): unknown {
  const sourceSyncMatch = path.match(/^\/sources\/([^/]+)\/sync$/);
  if (sourceSyncMatch) {
    const source = sources.find((item) => item.source_id === sourceSyncMatch[1]);
    if (source) {
      source.synced_object_count = Math.max(Number(source.synced_object_count ?? 0), 2);
      source.readiness_status = "ready";
      source.failure_code = null;
      source.updated_at = now();
      if (!sourceObjects[sourceSyncMatch[1]]?.length) {
        sourceObjects[sourceSyncMatch[1]] = [
          {
            object_id: `obj_${sourceSyncMatch[1]}_analytics_orders`,
            source_id: sourceSyncMatch[1],
            object_type: "table",
            name: "orders",
            fqn: "main.analytics.orders",
            authority_locator: { catalog: "main", schema: "analytics", table: "orders" },
            properties: { column_count: 8 },
            sync_version: `v_${Math.random().toString(16).slice(2, 8)}`,
            synced_at: now(),
          },
        ];
      }
    }
    return { job_id: `job_sync_${Math.random().toString(16).slice(2, 8)}`, source_id: sourceSyncMatch[1], status: "succeeded" };
  }
  const selectionMatch = path.match(/^\/sources\/([^/]+)\/sync\/selections$/);
  if (selectionMatch) {
    return ((body as { selections?: JsonRecord[] })?.selections ?? []).map((selection, index) => ({
      selection_id: `sel_${index + 1}`,
      source_id: selectionMatch[1],
      ...selection,
    }));
  }
  if (path === "/sources") {
    const payload = body as Record<string, unknown>;
    const source = readiness({
      source_id: idFor("source"),
      display_name: payload.display_name,
      source_type: payload.source_type,
      authority: payload.authority,
      sync: payload.sync ?? { mode: "selected" },
      policy: payload.policy ?? { allow_live_browse: true, allow_sync: true },
      mappings: [],
      created_at: now(),
    });
    (sources as JsonRecord[]).push(source);
    return source;
  }
  if (path === "/engines") {
    const payload = body as Record<string, unknown>;
    const engine = readiness({
      engine_id: idFor("engine"),
      display_name: payload.display_name,
      engine_type: payload.engine_type,
      connection: payload.connection ?? {},
      auth: payload.auth ?? { mode: "none" },
      default_namespace: payload.default_namespace ?? { catalog: null, schema: null },
      deployment_capabilities: payload.deployment_capabilities ?? {},
      policy: payload.policy ?? { allowed_step_types: [], required_policy_support: [] },
      mappings: [],
      created_at: now(),
    });
    (engines as JsonRecord[]).push(engine);
    return engine;
  }
  if (path === "/mappings") {
    const payload = body as Record<string, unknown>;
    const mapping = readiness({
      mapping_id: idFor("mapping"),
      source_id: payload.source_id,
      engine_id: payload.engine_id,
      priority: payload.priority ?? 0,
      catalog_mappings: payload.catalog_mappings ?? [],
      status: payload.status ?? "active",
      created_at: now(),
    });
    (mappings as JsonRecord[]).push(mapping);
    return mapping;
  }
  if (path === "/routing/resolve") {
    const tableNames = ((body as { table_names?: string[] })?.table_names ?? []).filter(Boolean);
    const unresolved = tableNames.some((tableName) => tableName.includes("missing") || tableName.includes("lake"));
    if (unresolved) {
      return {
        resolved: false,
        failure_code: "mapping_incomplete",
        table_names: tableNames,
        engine: null,
        qualified_names: {},
        selection_reason: "No ready mapping covers every requested authority table.",
        routing_detail: {
          candidates: ["map_lake_trino"],
          readiness_blockers: ["source not ready", "engine not ready", "catalog mapping is empty"],
        },
      };
    }
    return {
      resolved: true,
      failure_code: null,
      table_names: tableNames,
      engine: { engine_id: "eng_duckdb_runtime", engine_type: "duckdb", display_name: "DuckDB Runtime" },
      qualified_names: Object.fromEntries(tableNames.map((name) => [name, `duckdb_runtime.analytics.${name.split(".").pop()}`])),
      selection_reason: "Selected highest-priority ready mapping covering all requested tables.",
      routing_detail: { selected_mapping: "map_sales_duckdb", candidates: ["map_sales_duckdb"] },
    };
  }
  if (path.endsWith("/validate")) return { valid: false, readiness_status: "not_ready", blockers: ["source_object_not_ready"] };
  if (path.endsWith("/activate")) return { status: "active", readiness_status: "not_ready" };
  if (path.endsWith("/deprecate")) return { status: "deprecated", readiness_status: "not_ready" };
  return { status: "ok" };
}

export function mockPut(path: string, body: unknown): unknown {
  const payload = body as Record<string, unknown>;
  const sourceMatch = path.match(/^\/sources\/([^/]+)$/);
  if (sourceMatch) {
    const index = sources.findIndex((source) => source.source_id === sourceMatch[1]);
    if (index >= 0) {
      (sources as JsonRecord[])[index] = readiness({ ...sources[index], ...payload });
      return sources[index];
    }
  }
  const engineMatch = path.match(/^\/engines\/([^/]+)$/);
  if (engineMatch) {
    const index = engines.findIndex((engine) => engine.engine_id === engineMatch[1]);
    if (index >= 0) {
      (engines as JsonRecord[])[index] = readiness({ ...engines[index], ...payload });
      return engines[index];
    }
  }
  const mappingMatch = path.match(/^\/mappings\/([^/]+)$/);
  if (mappingMatch) {
    const index = mappings.findIndex((mapping) => mapping.mapping_id === mappingMatch[1]);
    if (index >= 0) {
      (mappings as JsonRecord[])[index] = readiness({ ...mappings[index], ...payload });
      return mappings[index];
    }
  }
  return { status: "not_found" };
}

export function mockDelete(path: string): unknown {
  const sourceMatch = path.match(/^\/sources\/([^/]+)$/);
  if (sourceMatch) {
    const index = sources.findIndex((source) => source.source_id === sourceMatch[1]);
    if (index >= 0) sources.splice(index, 1);
    return { status: "deleted", source_id: sourceMatch[1] };
  }
  const engineMatch = path.match(/^\/engines\/([^/]+)$/);
  if (engineMatch) {
    const index = engines.findIndex((engine) => engine.engine_id === engineMatch[1]);
    if (index >= 0) engines.splice(index, 1);
    return { status: "deleted", engine_id: engineMatch[1] };
  }
  const mappingMatch = path.match(/^\/mappings\/([^/]+)$/);
  if (mappingMatch) {
    const index = mappings.findIndex((mapping) => mapping.mapping_id === mappingMatch[1]);
    if (index >= 0) mappings.splice(index, 1);
    return { status: "deleted", mapping_id: mappingMatch[1] };
  }
  return { status: "not_found" };
}
