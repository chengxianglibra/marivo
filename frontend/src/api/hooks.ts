import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import { queryKeys } from "./config";
import type { EntityRow, JsonRecord, RuntimeStatus } from "./types";

export const semanticKinds = [
  { key: "entities", label: "Entities", path: "/semantic/entities" },
  { key: "metrics", label: "Metrics", path: "/semantic/metrics" },
  { key: "process-objects", label: "Processes", path: "/semantic/process-objects" },
  { key: "dimensions", label: "Dimensions", path: "/semantic/dimensions" },
  { key: "time", label: "Time", path: "/semantic/time" },
  { key: "enum-sets", label: "Enum Sets", path: "/semantic/enum-sets" },
  { key: "predicates", label: "Predicates", path: "/semantic/predicates" },
  { key: "bindings", label: "Bindings", path: "/semantic/bindings" },
  { key: "compatibility-profiles", label: "Compiler Profiles", path: "/compiler/compatibility-profiles" },
] as const;

export function unwrapList(payload: unknown): EntityRow[] {
  if (Array.isArray(payload)) return payload as EntityRow[];
  if (payload && typeof payload === "object") {
    const record = payload as JsonRecord;
    for (const key of ["items", "data", "results", "sessions", "objects"]) {
      if (Array.isArray(record[key])) return record[key] as EntityRow[];
    }
  }
  return [];
}

export function useHealth() {
  return useQuery({ queryKey: queryKeys.health, queryFn: () => apiClient.get<JsonRecord>("/health") });
}

export function useMetrics() {
  return useQuery({ queryKey: queryKeys.metrics, queryFn: () => apiClient.get<JsonRecord>("/metrics") });
}

export function useOpenApiIndex() {
  return useQuery({
    queryKey: queryKeys.openapiIndex,
    queryFn: () => apiClient.get<JsonRecord>("/openapi/index"),
  });
}

export function useSources() {
  return useQuery({
    queryKey: queryKeys.sources,
    queryFn: async () => unwrapList(await apiClient.get("/sources")),
  });
}

export function useEngines() {
  return useQuery({
    queryKey: queryKeys.engines,
    queryFn: async () => unwrapList(await apiClient.get("/engines")),
  });
}

export function useMappings() {
  return useQuery({
    queryKey: queryKeys.mappings,
    queryFn: async () => unwrapList(await apiClient.get("/mappings")),
  });
}

export function useJobs(filters?: { session_id?: string; status?: string }) {
  return useQuery({
    queryKey: queryKeys.jobs(filters),
    queryFn: async () => unwrapList(await apiClient.get("/jobs", filters)),
  });
}

export function usePolicies() {
  return useQuery({
    queryKey: queryKeys.policies,
    queryFn: async () => unwrapList(await apiClient.get("/policies")),
  });
}

export function useQualityRules() {
  return useQuery({
    queryKey: queryKeys.qualityRules,
    queryFn: async () => unwrapList(await apiClient.get("/quality-rules")),
  });
}

export function useSemanticList(kind: (typeof semanticKinds)[number]) {
  return useQuery({
    queryKey: queryKeys.semanticList(kind.key),
    queryFn: async () => unwrapList(await apiClient.get(kind.path, { detail: true })),
  });
}

export function useSessions(filters?: { status?: string; session_id?: string }) {
  return useQuery({
    queryKey: queryKeys.sessions(filters),
    queryFn: async () => unwrapList(await apiClient.get("/sessions", filters)),
  });
}

export function useSessionState(sessionId?: string) {
  return useQuery({
    queryKey: queryKeys.sessionState(sessionId),
    enabled: Boolean(sessionId),
    queryFn: () => apiClient.get<JsonRecord>(`/sessions/${sessionId}/state`),
  });
}

export function useSessionRuntime(sessionId?: string) {
  return useQuery({
    queryKey: queryKeys.sessionRuntime(sessionId),
    enabled: Boolean(sessionId),
    queryFn: () => apiClient.get<RuntimeStatus>(`/sessions/${sessionId}/runtime-status`),
  });
}

export function usePropositionContext(sessionId?: string, propositionId?: string) {
  return useQuery({
    queryKey: queryKeys.propositionContext(sessionId, propositionId),
    enabled: Boolean(sessionId && propositionId),
    queryFn: () => apiClient.get<JsonRecord>(`/sessions/${sessionId}/propositions/${propositionId}/context`),
  });
}

export function usePropositionRuntime(sessionId?: string, propositionId?: string) {
  return useQuery({
    queryKey: queryKeys.propositionRuntime(sessionId, propositionId),
    enabled: Boolean(sessionId && propositionId),
    queryFn: () =>
      apiClient.get<RuntimeStatus>(`/sessions/${sessionId}/propositions/${propositionId}/runtime-status`),
  });
}

export function useApprovals(filters?: { session_id?: string; status?: string }) {
  return useQuery({
    queryKey: queryKeys.approvals(filters),
    queryFn: async () => unwrapList(await apiClient.get("/approvals", filters)),
  });
}

export function useRoutingResolve() {
  return useMutation({
    mutationFn: (payload: { table_names: string[]; routing_intent?: JsonRecord }) =>
      apiClient.post<JsonRecord>("/routing/resolve", payload),
  });
}

export function useSemanticAction(kindPath: string, id: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (action: "validate" | "activate" | "deprecate") =>
      apiClient.post<JsonRecord>(`${kindPath}/${id}/${action}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["semantic"] }),
  });
}
