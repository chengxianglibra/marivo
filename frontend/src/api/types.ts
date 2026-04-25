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
