import { Card, Descriptions, Space } from "antd";
import type { RuntimeStatus } from "../api/types";
import { JsonPreview, ReadOnlyNotice, StatusBadge } from "./StatusBadge";

export function RuntimeStatusWidget({ status }: { status?: RuntimeStatus }) {
  return (
    <Card size="small" title="Runtime Status">
      <Space direction="vertical" size="middle" className="full-width">
        <ReadOnlyNotice />
        <Descriptions size="small" column={1} bordered>
          <Descriptions.Item label="schema">{status?.schema_version ?? "unknown"}</Descriptions.Item>
          <Descriptions.Item label="status">
            <StatusBadge value={status?.status ?? status?.stage} />
          </Descriptions.Item>
          <Descriptions.Item label="stage">{status?.stage ?? "n/a"}</Descriptions.Item>
          <Descriptions.Item label="blocked reason">{status?.blocked_reason ?? "n/a"}</Descriptions.Item>
          <Descriptions.Item label="error">{status?.error_message ?? "n/a"}</Descriptions.Item>
        </Descriptions>
        <JsonPreview payload={status ?? {}} />
      </Space>
    </Card>
  );
}
