import {
  datasources,
  health,
  jobs,
  metrics,
  openapiIndex,
  policies,
  propositionContext,
  qualityRules,
  runtimeStatus,
  semantic,
  sessionState,
  sessions,
} from "./mockData";
import type { JsonRecord } from "../api/types";

function stripQuery(path: string): string {
  return path.split("?")[0] ?? path;
}

function idFor(kind: "datasource"): string {
  return `ds_${Math.random().toString(16).slice(2, 10)}`;
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
  if (clean === "/datasources") return datasources;
  const schemaMatch = clean.match(/^\/datasources\/([^/]+)\/browse\/schemas$/);
  if (schemaMatch) {
    return [{ schema_name: "analytics" }, { schema_name: "iceberg_inf" }];
  }
  const tableMatch = clean.match(/^\/datasources\/([^/]+)\/browse\/tables$/);
  if (tableMatch) {
    const schema = String(query?.schema_name ?? query?.schema ?? "");
    return schema === "iceberg_inf"
      ? [{ table_name: "public_holiday_events" }, { table_name: "v_ott_user_profile_wide" }]
      : [{ table_name: "orders" }, { table_name: "watch_events" }];
  }
  const columnsMatch = clean.match(/^\/datasources\/([^/]+)\/browse\/columns$/);
  if (columnsMatch) {
    return [
      { name: "order_id", schema_name: query?.schema_name ?? "analytics", table_name: query?.table_name ?? "orders", data_type: "string", properties: {} },
      { name: "amount", schema_name: query?.schema_name ?? "analytics", table_name: query?.table_name ?? "orders", data_type: "number", properties: {} },
    ];
  }
  const previewMatch = clean.match(/^\/datasources\/([^/]+)\/catalog\/preview$/);
  if (previewMatch) {
    return { columns: ["order_id", "amount"], rows: [["ord_001", 42.5]], total_rows: 1 };
  }
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

  const semanticMatch = clean.match(/^\/semantic\/([^/]+)$/);
  if (semanticMatch) return { items: semantic[semanticMatch[1]] ?? [] };
  if (clean === "/compiler/compatibility-profiles") return { items: semantic["compatibility-profiles"] };

  return {};
}

export function mockPost(path: string, body: unknown): unknown {
  if (path === "/datasources") {
    const payload = body as Record<string, unknown>;
    const datasource = readiness({
      datasource_id: idFor("datasource"),
      datasource_type: payload.datasource_type,
      display_name: payload.display_name,
      connection: payload.connection ?? {},
      capabilities: ["browse", "execute"],
      created_at: now(),
    });
    (datasources as JsonRecord[]).push(datasource);
    return datasource;
  }
  if (path === "/routing/resolve") {
    const tableNames = ((body as { table_names?: string[] })?.table_names ?? []).filter(Boolean);
    const unresolved = tableNames.some((tableName) => tableName.includes("missing") || tableName.includes("lake"));
    if (unresolved) {
      return {
        resolved: false,
        failure_code: "datasource_unreachable",
        table_names: tableNames,
        datasource: null,
        qualified_names: {},
        selection_reason: "No ready datasource covers every requested table.",
        routing_detail: {
          candidates: ["ds_lake_trino"],
          readiness_blockers: ["Fix connection.host", "Confirm live browse access"],
        },
      };
    }
    return {
      resolved: true,
      failure_code: null,
      table_names: tableNames,
      datasource: { datasource_id: "ds_sales_duckdb", datasource_type: "duckdb", display_name: "Sales DuckDB Datasource" },
      qualified_names: Object.fromEntries(tableNames.map((name) => [name, `analytics.${name.split(".").pop()}`])),
      selection_reason: "Selected ready datasource covering all requested tables.",
      routing_detail: { selected_datasource: "ds_sales_duckdb", candidates: ["ds_sales_duckdb"] },
    };
  }
  if (path.endsWith("/validate")) return { valid: false, readiness_status: "not_ready", blockers: ["source_object_not_ready"] };
  if (path.endsWith("/activate")) return { status: "active", readiness_status: "not_ready" };
  if (path.endsWith("/deprecate")) return { status: "deprecated", readiness_status: "not_ready" };
  return { status: "ok" };
}

export function mockPut(path: string, body: unknown): unknown {
  const payload = body as Record<string, unknown>;
  const dsMatch = path.match(/^\/datasources\/([^/]+)$/);
  if (dsMatch) {
    const index = datasources.findIndex((ds) => ds.datasource_id === dsMatch[1]);
    if (index >= 0) {
      (datasources as JsonRecord[])[index] = readiness({ ...datasources[index], ...payload });
      return datasources[index];
    }
  }
  return { status: "not_found" };
}

export function mockDelete(path: string): unknown {
  const dsMatch = path.match(/^\/datasources\/([^/]+)$/);
  if (dsMatch) {
    const index = datasources.findIndex((ds) => ds.datasource_id === dsMatch[1]);
    if (index >= 0) datasources.splice(index, 1);
    return { datasource_id: dsMatch[1], deleted: true };
  }
  return { status: "not_found" };
}
