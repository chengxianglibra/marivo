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
    return { kind: "timeout", message: "请求超时，Marivo HTTP API 没有在预期时间内响应。" };
  }
  if (error instanceof TypeError) {
    return {
      kind: "network",
      message: "无法连接 Marivo HTTP API。请检查 API base URL、服务状态或 dev proxy。",
      detail: error.message,
    };
  }
  if (error instanceof Error) {
    return { kind: "unknown", message: error.message };
  }
  return { kind: "unknown", message: "未知错误", detail: error };
}

export function errorActionText(kind: ApiErrorShape["kind"]): string {
  switch (kind) {
    case "network":
      return "检查 VITE_MARIVO_API_BASE_URL 或后端服务。";
    case "timeout":
      return "刷新页面，或检查后端是否正在处理长请求。";
    case "validation":
      return "检查表单输入和 API contract。";
    case "not_ready":
      return "进入 readiness blocker 面板查看缺失依赖。";
    case "runtime_blocked":
      return "查看 runtime status 和 Jobs 只读诊断。";
    case "permission_placeholder":
      return "v1 无真实 RBAC；服务端返回权限类错误时仅展示诊断。";
    case "http":
      return "查看诊断抽屉中的 HTTP 状态和 detail。";
    default:
      return "打开诊断抽屉查看原始错误。";
  }
}
