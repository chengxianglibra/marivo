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
  sources,
} from "./mockData";

function stripQuery(path: string): string {
  return path.split("?")[0] ?? path;
}

export function mockGet(path: string, query?: Record<string, unknown>): unknown {
  const clean = stripQuery(path);
  if (clean === "/health") return health;
  if (clean === "/metrics") return metrics;
  if (clean === "/openapi/index") return openapiIndex;
  if (clean === "/sources") return sources;
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
