export const apiConfig = {
  baseUrl: import.meta.env.VITE_MARIVO_API_BASE_URL || "/api",
  useMocks: import.meta.env.VITE_MARIVO_USE_MOCKS !== "false",
  timeoutMs: Number(import.meta.env.VITE_MARIVO_REQUEST_TIMEOUT_MS || 15_000),
};

export const queryKeys = {
  health: ["health"] as const,
  metrics: ["metrics"] as const,
  openapiIndex: ["openapi", "index"] as const,
  sources: ["sources"] as const,
  engines: ["engines"] as const,
  mappings: ["mappings"] as const,
  jobs: (filters?: Record<string, string | undefined>) => ["jobs", filters ?? {}] as const,
  policies: ["policies"] as const,
  qualityRules: ["quality-rules"] as const,
  semanticList: (kind: string) => ["semantic", kind] as const,
  sessions: (filters?: Record<string, string | undefined>) => ["sessions", filters ?? {}] as const,
  sessionState: (sessionId?: string) => ["sessions", sessionId, "state"] as const,
  sessionRuntime: (sessionId?: string) => ["sessions", sessionId, "runtime-status"] as const,
  propositionContext: (sessionId?: string, propositionId?: string) =>
    ["sessions", sessionId, "propositions", propositionId, "context"] as const,
  propositionRuntime: (sessionId?: string, propositionId?: string) =>
    ["sessions", sessionId, "propositions", propositionId, "runtime-status"] as const,
  approvals: (filters?: Record<string, string | undefined>) => ["approvals", filters ?? {}] as const,
};
