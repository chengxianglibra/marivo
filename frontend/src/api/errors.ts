import type { ApiErrorShape } from "./types";

export class ApiError extends Error {
  readonly shape: ApiErrorShape;

  constructor(shape: ApiErrorShape) {
    super(shape.message);
    this.name = "ApiError";
    this.shape = shape;
  }
}

export function classifyError(status: number | undefined, detail: unknown): ApiErrorShape["kind"] {
  const text = typeof detail === "string" ? detail : JSON.stringify(detail ?? "");
  if (status === 422 || text.includes("validation")) return "validation";
  if (status === 401 || status === 403) return "permission_placeholder";
  if (status === 409 && (text.includes("not ready") || text.includes("not_ready"))) return "not_ready";
  if (status === 409 && (text.includes("blocked") || text.includes("runtime"))) {
    return "runtime_blocked";
  }
  if (status && status >= 400) return "http";
  return "unknown";
}

export function normalizeThrownError(error: unknown): ApiErrorShape {
  if (error instanceof ApiError) return error.shape;
  if (error instanceof DOMException && error.name === "AbortError") {
    return { kind: "timeout", message: "The request timed out before the Marivo HTTP API responded." };
  }
  if (error instanceof TypeError) {
    return {
      kind: "network",
      message: "Unable to connect to the Marivo HTTP API. Check the API base URL, service status, or dev proxy.",
      detail: error.message,
    };
  }
  if (error instanceof Error) {
    return { kind: "unknown", message: error.message };
  }
  return { kind: "unknown", message: "Unknown error", detail: error };
}

export function errorActionText(kind: ApiErrorShape["kind"]): string {
  switch (kind) {
    case "network":
      return "Check VITE_MARIVO_API_BASE_URL or the backend service.";
    case "timeout":
      return "Refresh the page or check whether the backend is processing a long request.";
    case "validation":
      return "Check the form input and API contract.";
    case "not_ready":
      return "Open the readiness blocker panel to inspect missing dependencies.";
    case "runtime_blocked":
      return "Review runtime status and the read-only Jobs diagnostics.";
    case "permission_placeholder":
      return "v1 has no real RBAC; permission-like server errors are shown as diagnostics only.";
    case "http":
      return "Review HTTP status and detail in the diagnostic drawer.";
    default:
      return "Open the diagnostic drawer to inspect the raw error.";
  }
}
