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
    ? `当前 blocker: ${String(record.failure_code)}`
    : record.readiness_status && record.readiness_status !== "ready"
      ? `当前 readiness: ${String(record.readiness_status)}`
      : "当前对象 ready。";

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
          "没有服务端返回的 blocking requirements。"
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
      message="只读诊断面"
      description="此页面解释运行状态、Jobs 和 readiness blocker，不提供 raw SQL 主入口，也不暴露 Jobs submit / cancel / retry。"
    />
  );
}

export function SecurityBoundaryNotice() {
  return (
    <Alert
      type="warning"
      showIcon
      message="v1 角色不是安全边界"
      description="角色切换只影响导航优先级和信息架构。当前 Marivo API 未提供真实认证 / RBAC，服务端权限隔离需要等待后续契约。"
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
