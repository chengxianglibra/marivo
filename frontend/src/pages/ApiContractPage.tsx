import { Alert, Card, List, Space, Table, Tag, Typography } from "antd";
import { useOpenApiIndex } from "../api/hooks";
import { SectionHeader } from "../components/StatusBadge";

const dependencies = [
  {
    area: "Operations",
    need: "source / engine / mapping list fields should keep readiness_status and failure_code stable",
    severity: "blocker",
  },
  {
    area: "Semantic Layer",
    need: "semantic list endpoints should expose dependency_refs, dependent_refs, capabilities and blocking_requirements consistently",
    severity: "enhancement",
  },
  {
    area: "Analysis",
    need: "session list benefits from server-side filters for session_user and created_at range",
    severity: "enhancement",
  },
  {
    area: "Evidence",
    need: "artifact inspector needs artifact identity and extraction detail surfaces beyond runtime status",
    severity: "enhancement",
  },
];

export function ApiContractPage() {
  const openapi = useOpenApiIndex();
  const paths = (openapi.data?.paths ?? []) as Array<{ path: string; operations?: string[] }>;
  return (
    <Space direction="vertical" size="large" className="page">
      <SectionHeader
        title="API Contract"
        description="The frontend only consumes capabilities exposed by the Marivo HTTP API and OpenAPI. Missing capabilities go to the backlog instead of being faked in frontend state."
      />
      <Alert
        type="info"
        showIcon
        message="OpenAPI constrained client"
        description="`npm run openapi:types` generates TypeScript types from MARIVO_OPENAPI_URL. Runtime requests must go through the shared apiClient and hooks."
      />
      <Card size="small" title="OpenAPI Index">
        <Table
          rowKey={(row) => row.path}
          size="small"
          dataSource={paths}
          columns={[
            { title: "Path", dataIndex: "path" },
            {
              title: "Operations",
              render: (_, row) => (
                <Space wrap>
                  {(row.operations ?? []).map((op) => (
                    <Tag key={op}>{op}</Tag>
                  ))}
                </Space>
              ),
            },
          ]}
        />
      </Card>
      <Card size="small" title="API Dependency Backlog">
        <List
          dataSource={dependencies}
          renderItem={(item) => (
            <List.Item>
              <List.Item.Meta
                title={
                  <Space>
                    <Typography.Text strong>{item.area}</Typography.Text>
                    <Tag color={item.severity === "blocker" ? "error" : "processing"}>{item.severity}</Tag>
                  </Space>
                }
                description={item.need}
              />
            </List.Item>
          )}
        />
      </Card>
    </Space>
  );
}
