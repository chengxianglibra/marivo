import { apiConfig } from "./config";
import { ApiError, classifyError } from "./errors";
import { mockDelete, mockGet, mockPost, mockPut } from "../fixtures/mockApi";
import type { JsonRecord } from "./types";

interface RequestOptions {
  query?: Record<string, string | number | boolean | undefined>;
  body?: unknown;
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const normalizedPath = path.replace(/^\/+/, "");
  const basePath = apiConfig.baseUrl.startsWith("/") ? apiConfig.baseUrl : `/${apiConfig.baseUrl}`;
  const baseUrl = apiConfig.baseUrl.startsWith("http")
    ? apiConfig.baseUrl
    : new URL(basePath, window.location.origin).toString();
  const url = new URL(normalizedPath, baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== "") url.searchParams.set(key, String(value));
  }
  return url.toString();
}

async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  if (apiConfig.useMocks) {
    if (method === "GET") return mockGet(path, options.query) as T;
    if (method === "POST") return mockPost(path, options.body) as T;
    if (method === "PUT") return mockPut(path, options.body) as T;
    if (method === "DELETE") return mockDelete(path) as T;
  }

  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), apiConfig.timeoutMs);
  const signal = options.signal ?? controller.signal;
  try {
    const response = await fetch(buildUrl(path, options.query), {
      method,
      headers: {
        "Content-Type": "application/json",
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal,
    });
    const requestId =
      response.headers.get("x-request-id") ??
      response.headers.get("x-trace-id") ??
      response.headers.get("x-openapi-revision") ??
      undefined;
    const text = await response.text();
    const payload = text ? JSON.parse(text) : null;
    if (!response.ok) {
      const detail = payload?.detail ?? payload;
      throw new ApiError({
        kind: classifyError(response.status, detail),
        status: response.status,
        message: typeof detail === "string" ? detail : response.statusText,
        detail,
        requestId,
      });
    }
    return payload as T;
  } finally {
    window.clearTimeout(timeout);
  }
}

export const apiClient = {
  get: <T = JsonRecord>(path: string, query?: RequestOptions["query"]) =>
    request<T>("GET", path, { query }),
  post: <T = JsonRecord>(path: string, body?: unknown) => request<T>("POST", path, { body }),
  put: <T = JsonRecord>(path: string, body?: unknown) => request<T>("PUT", path, { body }),
  delete: <T = JsonRecord>(path: string) => request<T>("DELETE", path),
};
