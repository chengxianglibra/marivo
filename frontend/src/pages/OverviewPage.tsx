import { Alert, Card, Col, List, Row, Space, Statistic, Table, Typography } from "antd";
import { Activity, Database, Route, ShieldAlert } from "lucide-react";
import { useDatasources, useHealth, useJobs, useMetrics } from "../api/hooks";
import type { EntityRow } from "../api/types";
import { BlockerPanel, InlineState, SectionHeader, StatusBadge } from "../components/StatusBadge";

function notReady(rows: EntityRow[] | undefined) {
  return (rows ?? []).filter((row) => row.readiness_status && row.readiness_status !== "ready");
}

export function OverviewPage() {
  const health = useHealth();
  const metrics = useMetrics();
  const datasources = useDatasources();
  const jobs = useJobs();
  const blockers = [...notReady(datasources.data)];

  return (
    <Space direction="vertical" size="large" className="page">
      <SectionHeader
        title="Operations Overview"
        description="Default operations home focused on readiness, failure codes, routing blockers, and recent failures."
      />
      <Alert
        showIcon
        type={health.data?.status === "ok" ? "success" : "warning"}
        message={
          <Space>
            <Activity size={16} />
            Marivo HTTP API: <StatusBadge value={health.data?.status ?? "unknown"} />
            <Typography.Text type="secondary">mock mode: {String(health.data?.mode === "mock")}</Typography.Text>
          </Space>
        }
      />
      <Row gutter={[12, 12]}>
        <Col xs={24} md={8} xl={6}>
          <Card size="small">
            <Statistic title="Active Sessions" value={metrics.data?.active_sessions ?? 0} prefix={<Activity size={16} />} />
          </Card>
        </Col>
        <Col xs={24} md={8} xl={6}>
          <Card size="small">
            <Statistic title="Pending Jobs" value={metrics.data?.pending_jobs ?? 0} prefix={<Route size={16} />} />
          </Card>
        </Col>
        <Col xs={24} md={8} xl={6}>
          <Card size="small">
            <Statistic title="Steps Failed" value={metrics.data?.steps_failed ?? 0} prefix={<ShieldAlert size={16} />} />
          </Card>
        </Col>
        <Col xs={24} md={8} xl={6}>
          <Card size="small">
            <Statistic title="Datasources" value={datasources.data?.length ?? 0} prefix={<Database size={16} />} />
          </Card>
        </Col>
      </Row>
      <Row gutter={[12, 12]}>
        <Col xs={24} xl={15}>
          <Card title="Readiness Blockers" size="small">
            <Table
              rowKey={(row) => row.datasource_id ?? row.name ?? JSON.stringify(row)}
              size="small"
              dataSource={blockers}
              pagination={false}
              columns={[
                {
                  title: "Object",
                  render: (_, row) => row.display_name ?? row.name ?? row.datasource_id,
                },
                { title: "Type", render: (_, row) => row.datasource_type ?? "-" },
                {
                  title: "State",
                  render: (_, row) => <InlineState readiness={row.readiness_status} failure={row.failure_code} />,
                },
                {
                  title: "Next blocker",
                  render: (_, row) => (row.blocking_requirements as string[] | undefined)?.[0] ?? "Inspect details",
                },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} xl={9}>
          <Card title="Routing & Jobs Signals" size="small">
            <List
              dataSource={(jobs.data ?? []).filter((job) => job.status !== "succeeded")}
              renderItem={(job) => (
                <List.Item>
                  <List.Item.Meta
                    avatar={<Route size={18} />}
                    title={
                      <Space>
                        {job.job_id}
                        <StatusBadge value={job.status} />
                      </Space>
                    }
                    description={job.error_message ?? job.job_type}
                  />
                </List.Item>
              )}
            />
          </Card>
        </Col>
      </Row>
      {blockers[0] ? <BlockerPanel record={blockers[0]} /> : null}
    </Space>
  );
}
