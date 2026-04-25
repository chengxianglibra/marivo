import { Card, List, Space, Tag, Typography } from "antd";
import type { JsonRecord } from "../api/types";
import { StatusBadge } from "./StatusBadge";
import { TaskEmpty } from "./EmptyState";

function findingColor(stance?: string) {
  if (stance === "support") return "success";
  if (stance === "oppose") return "error";
  return "default";
}

export function EvidenceClosure({ context }: { context?: JsonRecord }) {
  const findings = (context?.relevant_findings ?? []) as JsonRecord[];
  const blockingGaps = (context?.blocking_gaps ?? []) as JsonRecord[];
  const inferenceRecords = (context?.inference_records ?? []) as JsonRecord[];
  if (!context) return <TaskEmpty kind="evidence" />;

  return (
    <Space direction="vertical" size="middle" className="full-width">
      <Card size="small" title="Latest Assessment">
        <Space direction="vertical">
          <StatusBadge value={context.latest_assessment?.status} />
          <Typography.Text>{context.latest_assessment?.summary ?? "No assessment summary"}</Typography.Text>
        </Space>
      </Card>
      <Card size="small" title="Relevant Findings">
        <List
          dataSource={findings}
          locale={{ emptyText: "No findings" }}
          renderItem={(finding) => (
            <List.Item>
              <List.Item.Meta
                title={
                  <Space wrap>
                    <Typography.Text strong>{finding.finding_id}</Typography.Text>
                    <Tag color={findingColor(finding.stance)}>{finding.stance ?? "neutral"}</Tag>
                  </Space>
                }
                description={finding.summary}
              />
            </List.Item>
          )}
        />
      </Card>
      <Card size="small" title="Blocking Gaps">
        <List
          dataSource={blockingGaps}
          locale={{ emptyText: "No blocking gaps" }}
          renderItem={(gap) => (
            <List.Item>
              <List.Item.Meta
                title={`${gap.gap_id ?? "gap"} - ${gap.severity ?? "severity unknown"}`}
                description={gap.requirement_summary ?? gap.gap_type}
              />
            </List.Item>
          )}
        />
      </Card>
      <Card size="small" title="Inference Records">
        <List
          dataSource={inferenceRecords}
          locale={{ emptyText: "No inference records" }}
          renderItem={(record) => (
            <List.Item>
              <List.Item.Meta title={record.inference_id ?? record.method} description={record.summary} />
            </List.Item>
          )}
        />
      </Card>
    </Space>
  );
}
