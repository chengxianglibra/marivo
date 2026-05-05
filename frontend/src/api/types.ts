export type JsonRecord = Record<string, any>;

export type RoleKey = "admin" | "semantic" | "analyst";
export type PageKey = "overview" | "operations" | "semantic" | "analysis" | "api-contract";

export interface EntityRow extends JsonRecord {
  id?: string;
  name?: string;
  display_name?: string;
  status?: string;
  lifecycle_status?: string;
  readiness_status?: string;
  failure_code?: string | null;
  updated_at?: string;
}

export interface DuckDbConnection {
  datasource_type: "duckdb";
  path?: string | null;
  database?: string | null;
  db_path?: string;
}

export interface TrinoConnection {
  datasource_type: "trino";
  host: string;
  port?: number;
  user?: string;
  catalog?: string;
  http_scheme?: "http" | "https";
  session_properties?: Record<string, string>;
}

export type DatasourceConnection = DuckDbConnection | TrinoConnection;

export interface DatasourceRow {
  datasource_id: string;
  datasource_type: "duckdb" | "trino";
  display_name: string;
  connection: DatasourceConnection;
  status?: string;
  readiness_status?: string;
  failure_code?: string | null;
  capabilities?: string[];
  blocking_requirements?: string[];
  created_at?: string;
  updated_at?: string;
}

export interface ApiErrorShape {
  kind:
    | "network"
    | "timeout"
    | "http"
    | "validation"
    | "not_ready"
    | "runtime_blocked"
    | "permission_placeholder"
    | "unknown";
  status?: number;
  message: string;
  detail?: unknown;
  requestId?: string;
}

export interface RuntimeStatus extends JsonRecord {
  status?: string;
  stage?: string;
  schema_version?: string;
  blocked_reason?: string;
  error_message?: string;
}

export interface QueryEnvelope<T> {
  data: T;
  fromMock: boolean;
}
