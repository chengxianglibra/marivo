import { Alert, Button, Space, Tag, Typography } from "antd";
import type { ReactNode } from "react";
import type { EntityRow, JsonRecord } from "../api/types";

const readinessColors: Record<string, string> = {
  ready: "success",
  active: "success",
  succeeded: "success",
  ok: "success",
  not_ready: "error",
  failed: "error",
  stale: "warning",
  blocked: "warning",
  pending: "processing",
  open: "processing",
  draft: "default",
  deprecated: "default",
};

export function StatusBadge({ value, label }: { value?: string | null; label?: string }) {
  const normalized = value || "unknown";
  return (
    <Tag color={readinessColors[normalized] ?? "default"}>
      {label ? `${label}: ` : ""}
      {normalized}
    </Tag>
  );
}

export function FailureTag({ code }: { code?: string | null }) {
  if (!code) return <Tag>no failure</Tag>;
  return <Tag color="error">{code}</Tag>;
}

export function BlockerPanel({ record }: { record?: EntityRow | JsonRecord | null }) {
  const blockers =
    (record?.blocking_requirements as string[] | undefined) ??
    (record?.readiness_blockers as string[] | undefined) ??
    (record?.blockers as string[] | undefined) ??
    [];
  if (!record) return null;

  const message = record.failure_code
    ? `Current blocker: ${String(record.failure_code)}`
    : record.readiness_status && record.readiness_status !== "ready"
      ? `Current readiness: ${String(record.readiness_status)}`
      : "This object is ready.";

  return (
    <Alert
      type={record.readiness_status === "ready" ? "success" : "warning"}
      showIcon
      message={message}
      description={
        blockers.length ? (
          <ul className="compact-list">
            {blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
        ) : (
          "The server did not return blocking requirements."
        )
      }
    />
  );
}

export function SectionHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="section-header">
      <div>
        <Typography.Title level={3}>{title}</Typography.Title>
        {description ? <Typography.Text type="secondary">{description}</Typography.Text> : null}
      </div>
      {action}
    </div>
  );
}

export function ReadOnlyNotice() {
  return (
    <Alert
      type="info"
      showIcon
      message="Read-only diagnostics"
      description="This page explains runtime status, Jobs, and readiness blockers. It does not provide a primary raw SQL entry point or expose Jobs submit, cancel, or retry controls."
    />
  );
}

export function SecurityBoundaryNotice() {
  return (
    <Alert
      type="warning"
      showIcon
      message="v1 roles are not a security boundary"
      description="Role views only affect navigation priority and information architecture. The current Marivo API does not provide real authentication or RBAC; server-side isolation requires a future contract."
    />
  );
}

export function CopyJsonButton({ payload }: { payload: unknown }) {
  return (
    <Button size="small" onClick={() => navigator.clipboard.writeText(JSON.stringify(payload, null, 2))}>
      Copy JSON
    </Button>
  );
}

export function JsonPreview({ payload }: { payload: unknown }) {
  return <pre className="json-preview">{JSON.stringify(payload, null, 2)}</pre>;
}

export function InlineState({
  readiness,
  failure,
}: {
  readiness?: string | null;
  failure?: string | null;
}) {
  return (
    <Space wrap>
      <StatusBadge label="readiness" value={readiness} />
      <FailureTag code={failure} />
    </Space>
  );
}
