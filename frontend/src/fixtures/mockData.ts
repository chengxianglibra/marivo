import type { JsonRecord } from "../api/types";

export const health = {
  status: "ok",
  service: "marivo",
  mode: "mock",
  checked_at: "2026-04-25T12:00:00+08:00",
};

export const metrics = {
  active_sessions: 3,
  pending_jobs: 1,
  steps_failed: 2,
  routing_failures: 2,
};

export const datasources = [
  {
    datasource_id: "ds_sales_duckdb",
    display_name: "Sales DuckDB Datasource",
    datasource_type: "duckdb",
    connection: { datasource_type: "duckdb", path: null, database: null, db_path: "./sales.duckdb" },
    status: "active",
    readiness_status: "ready",
    failure_code: null,
    capabilities: ["browse", "execute"],
    created_at: "2026-04-25T10:12:00+08:00",
    updated_at: "2026-04-25T10:12:00+08:00",
  },
  {
    datasource_id: "ds_lake_trino",
    display_name: "Lakehouse Trino Datasource",
    datasource_type: "trino",
    connection: {
      datasource_type: "trino",
      host: "trino.example.com",
      port: 8080,
      catalog: "iceberg",
      http_scheme: "https",
    },
    status: "active",
    readiness_status: "not_ready",
    failure_code: "datasource_invalid_connection",
    capabilities: ["browse", "execute"],
    created_at: "2026-04-25T09:00:00+08:00",
    updated_at: "2026-04-25T09:00:00+08:00",
    blocking_requirements: ["Fix connection.host", "Confirm live browse access"],
  },
];

export const jobs = [
  {
    job_id: "job_routing_001",
    session_id: null,
    job_type: "routing_check",
    status: "failed",
    created_at: "2026-04-25T09:48:00+08:00",
    updated_at: "2026-04-25T09:50:00+08:00",
    error_message: "Datasource connection invalid",
  },
  {
    job_id: "job_runtime_031",
    session_id: "sess_growth_review",
    job_type: "intent_observe",
    status: "pending",
    created_at: "2026-04-25T11:11:00+08:00",
    updated_at: "2026-04-25T11:11:00+08:00",
  },
];

export const semantic: Record<string, JsonRecord[]> = {
  entities: [
    {
      entity_id: "entity.customer",
      name: "customer",
      lifecycle_status: "active",
      readiness_status: "ready",
      capabilities: ["lookup", "aggregate"],
      dependency_refs: ["dataset.customer_profile"],
    },
  ],
  metrics: [
    {
      metric_id: "metric.gmv",
      name: "gmv",
      lifecycle_status: "active",
      readiness_status: "not_ready",
      failure_code: "dataset_time_missing",
      blocking_requirements: ["Configure primary time field", "Confirm datasource readiness"],
      dependency_refs: ["dataset.sales_orders"],
      capabilities: ["observe", "compare"],
    },
  ],
  "process-objects": [
    {
      process_contract_id: "process.checkout",
      name: "checkout",
      lifecycle_status: "draft",
      readiness_status: "stale",
      blocking_requirements: ["validate typed contract"],
    },
  ],
  dimensions: [],
  time: [],
  "enum-sets": [],
  predicates: [
    {
      predicate_contract_id: "predicate.cn_public_holiday",
      name: "cn_public_holiday",
      lifecycle_status: "active",
      readiness_status: "ready",
    },
  ],
  "compatibility-profiles": [
    {
      profile_id: "compiler.duckdb_default",
      name: "duckdb_default",
      lifecycle_status: "active",
      readiness_status: "ready",
    },
  ],
};

export const sessions = [
  {
    session_id: "sess_growth_review",
    goal: "Explain why GMV declined year over year",
    lifecycle_status: "open",
    status: "open",
    execution_identity: { session_user: "analyst.li" },
    active_proposition_count: 2,
    blocking_gap_count: 1,
    runtime_overall_status: "blocked",
    created_at: "2026-04-25T10:00:00+08:00",
  },
  {
    session_id: "sess_capacity_check",
    goal: "Investigate abnormal Trino query latency",
    lifecycle_status: "terminated",
    status: "closed",
    active_proposition_count: 1,
    blocking_gap_count: 0,
    runtime_overall_status: "succeeded",
    created_at: "2026-04-24T16:00:00+08:00",
  },
];

export const sessionState = {
  schema_version: "session_state_view.v1",
  session_id: "sess_growth_review",
  propositions: [
    {
      proposition_id: "prop_gmv_decline",
      statement: "GMV decline is mostly explained by lower paid order conversion.",
      proposition_type: "explanation",
      latest_assessment_status: "partially_supported",
      has_blocking_gaps: true,
      origin_kind: "typed_intent",
    },
  ],
  gaps: [
    {
      gap_id: "gap_conversion_by_channel",
      gap_type: "missing_dimension_breakdown",
      severity: "P1",
      blocking: true,
      related_proposition_id: "prop_gmv_decline",
      requirement_summary: "Channel-level conversion rate comparison evidence is required.",
      satisfiable_by: "observe metric.conversion_rate by dimension.channel",
      status: "open",
    },
  ],
};

export const propositionContext = {
  schema_version: "proposition_context_view.v1",
  proposition: sessionState.propositions[0],
  seed_entries: [{ seed_ref: "artifact.obs_001", reason: "primary observed delta" }],
  relevant_findings: [
    {
      finding_id: "finding_orders_conversion_drop",
      stance: "support",
      summary: "Paid order conversion dropped 8.4% in the aligned comparison window.",
      artifact_ref: "artifact.obs_001",
    },
    {
      finding_id: "finding_aov_stable",
      stance: "oppose",
      summary: "Average order value stayed within normal range.",
      artifact_ref: "artifact.obs_002",
    },
  ],
  latest_assessment: {
    status: "partially_supported",
    confidence: "medium",
    summary: "The evidence supports a conversion-rate decline, but the channel breakdown is still missing.",
  },
  blocking_gaps: sessionState.gaps,
  non_blocking_gaps: [],
  inference_records: [
    {
      inference_id: "infer_001",
      method: "support_oppose_resolution",
      summary: "support finding outweighs non-causal AOV stability check.",
    },
  ],
  artifact_refs: ["artifact.obs_001", "artifact.obs_002"],
};

export const runtimeStatus = {
  schema_version: "session_runtime_status.v1",
  status: "blocked",
  stage: "evidence_materialization",
  blocked_reason: "waiting for semantic object readiness",
  error_message: null,
};

export const openapiIndex = {
  revision: "mock-revision",
  paths: [
    { path: "/datasources", operations: ["get", "post"] },
    { path: "/datasources/{datasource_id}", operations: ["get", "put", "delete"] },
    { path: "/sessions/{session_id}/state", operations: ["get", "post"] },
    { path: "/sessions/{session_id}/propositions/{proposition_id}/context", operations: ["get"] },
  ],
  schemas: ["DatasourceResponse", "SessionStateView"],
};
