import { Button, Card, Descriptions, Drawer, Form, Input, Select, Space, Table, Tabs, Typography } from "antd";
import { useMemo, useState } from "react";
import {
  useEngines,
  useJobs,
  useMappings,
  usePolicies,
  useQualityRules,
  useRoutingResolve,
  useSources,
} from "../api/hooks";
import type { EntityRow, JsonRecord } from "../api/types";
import { DiagnosticDrawer } from "../components/DiagnosticDrawer";
import { TaskEmpty } from "../components/EmptyState";
import {
  BlockerPanel,
  InlineState,
  JsonPreview,
  ReadOnlyNotice,
  SectionHeader,
  StatusBadge,
} from "../components/StatusBadge";

function ObjectTable({
  rows,
  kind,
  onInspect,
}: {
  rows?: EntityRow[];
  kind: "source" | "engine" | "mapping";
  onInspect: (row: EntityRow) => void;
}) {
  return (
    <Table
      rowKey={(row) => row.source_id ?? row.engine_id ?? row.mapping_id ?? row.id ?? JSON.stringify(row)}
      size="small"
      dataSource={rows}
      locale={{ emptyText: <TaskEmpty kind={kind} /> }}
      columns={[
        {
          title: "ID",
          render: (_, row) => row.source_id ?? row.engine_id ?? row.mapping_id ?? row.id,
        },
        { title: "Name", render: (_, row) => row.display_name ?? row.name ?? "-" },
        { title: "Type", render: (_, row) => row.source_type ?? row.engine_type ?? "mapping" },
        { title: "State", render: (_, row) => <InlineState readiness={row.readiness_status} failure={row.failure_code} /> },
        { title: "Updated", dataIndex: "updated_at" },
        { title: "Inspect", render: (_, row) => <Button onClick={() => onInspect(row)}>Details</Button> },
      ]}
    />
  );
}

function SourcesTab({ onInspect }: { onInspect: (row: EntityRow) => void }) {
  const sources = useSources();
  return (
    <Space direction="vertical" size="middle" className="full-width">
      <Typography.Paragraph type="secondary">
        Source inventory comes from the HTTP API. Sync selections are represented as structured tables instead of handwritten JSON.
      </Typography.Paragraph>
      <ObjectTable rows={sources.data} kind="source" onInspect={onInspect} />
    </Space>
  );
}

function EnginesTab({ onInspect }: { onInspect: (row: EntityRow) => void }) {
  const engines = useEngines();
  return (
    <Space direction="vertical" size="middle" className="full-width">
      <Typography.Paragraph type="secondary">
        Engine readiness is configuration validation. It does not imply that the UI ran an online SELECT 1 probe. DuckDB ignores session execution identity; Trino can use username_only.
      </Typography.Paragraph>
      <ObjectTable rows={engines.data} kind="engine" onInspect={onInspect} />
    </Space>
  );
}

function MappingsTab({ onInspect }: { onInspect: (row: EntityRow) => void }) {
  const mappings = useMappings();
  return (
    <Space direction="vertical" size="middle" className="full-width">
      <Typography.Paragraph type="secondary">
        Mapping gaps are shown through coverage summaries and failure codes. `mapping_incomplete` and `mapping_inactive_dependency` point to actionable blockers first.
      </Typography.Paragraph>
      <ObjectTable rows={mappings.data} kind="mapping" onInspect={onInspect} />
    </Space>
  );
}

function RoutingDebugger() {
  const routing = useRoutingResolve();
  const [form] = Form.useForm();
  return (
    <Space direction="vertical" size="middle" className="full-width">
      <Form
        form={form}
        layout="vertical"
        initialValues={{ table_names: "sales.orders\nsales.revenue_daily" }}
        onFinish={(values) => {
          routing.mutate({
            table_names: String(values.table_names)
              .split(/\n|,/)
              .map((item) => item.trim())
              .filter(Boolean),
          });
        }}
      >
        <Form.Item label="Table names" name="table_names">
          <Input.TextArea rows={4} placeholder="schema.table, one per line" />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={routing.isPending}>
          Resolve Route
        </Button>
      </Form>
      {routing.data ? (
        <Card size="small" title="Routing Result">
          <Space direction="vertical" className="full-width">
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="resolved">
                <StatusBadge value={routing.data.resolved ? "ready" : "not_ready"} />
              </Descriptions.Item>
              <Descriptions.Item label="failure_code">{routing.data.failure_code ?? "n/a"}</Descriptions.Item>
              <Descriptions.Item label="selection reason">{routing.data.selection_reason ?? "n/a"}</Descriptions.Item>
            </Descriptions>
            <JsonPreview payload={routing.data} />
          </Space>
        </Card>
      ) : null}
    </Space>
  );
}

function GovernanceTab() {
  const policies = usePolicies();
  const rules = useQualityRules();
  const groupedRules = useMemo(() => rules.data ?? [], [rules.data]);
  return (
    <Space direction="vertical" size="middle" className="full-width">
      <Typography.Paragraph type="secondary">Governance is a constraints and troubleshooting page, not an analysis conclusion page.</Typography.Paragraph>
      <Card size="small" title="Policies">
        <Table
          rowKey={(row) => row.policy_id ?? row.name}
          size="small"
          dataSource={policies.data}
          columns={[
            { title: "Name", dataIndex: "name" },
            { title: "Type", dataIndex: "policy_type" },
            { title: "Enabled", render: (_, row) => <StatusBadge value={row.enabled ? "active" : "deprecated"} /> },
          ]}
        />
      </Card>
      <Card size="small" title="Quality Rules">
        <Table
          rowKey={(row) => row.rule_id ?? row.name}
          size="small"
          dataSource={groupedRules}
          columns={[
            { title: "Table", dataIndex: "table_name" },
            { title: "Name", dataIndex: "name" },
            { title: "Severity", dataIndex: "severity" },
            { title: "Type", dataIndex: "rule_type" },
          ]}
        />
      </Card>
    </Space>
  );
}

function JobsRuntimeTab() {
  const [filters, setFilters] = useState<{ session_id?: string; status?: string }>({});
  const jobs = useJobs(filters);
  return (
    <Space direction="vertical" size="middle" className="full-width">
      <ReadOnlyNotice />
      <Space wrap>
        <Input.Search placeholder="session_id" allowClear onSearch={(value) => setFilters((old) => ({ ...old, session_id: value || undefined }))} />
        <Select
          allowClear
          placeholder="status"
          style={{ width: 160 }}
          options={["pending", "running", "failed", "succeeded", "cancelled"].map((value) => ({ value }))}
          onChange={(value) => setFilters((old) => ({ ...old, status: value }))}
        />
      </Space>
      <Table
        rowKey={(row) => row.job_id}
        size="small"
        dataSource={jobs.data}
        locale={{ emptyText: <TaskEmpty kind="jobs" /> }}
        columns={[
          { title: "job_id", dataIndex: "job_id" },
          { title: "session_id", dataIndex: "session_id" },
          { title: "job_type", dataIndex: "job_type" },
          { title: "status", render: (_, row) => <StatusBadge value={row.status} /> },
          { title: "updated_at", dataIndex: "updated_at" },
          { title: "error", dataIndex: "error_message" },
        ]}
      />
    </Space>
  );
}

export function OperationsPage() {
  const [selected, setSelected] = useState<EntityRow | null>(null);
  const [diagnostic, setDiagnostic] = useState<JsonRecord | null>(null);
  return (
    <Space direction="vertical" size="large" className="page">
      <SectionHeader
        title="Operations"
        description="Operations workflows for sources, engines, mappings, routing, governance, jobs, and runtime diagnostics."
      />
      <Tabs
        items={[
          { key: "sources", label: "Sources", children: <SourcesTab onInspect={setSelected} /> },
          { key: "engines", label: "Engines", children: <EnginesTab onInspect={setSelected} /> },
          { key: "mappings", label: "Mappings", children: <MappingsTab onInspect={setSelected} /> },
          { key: "routing", label: "Routing Debugger", children: <RoutingDebugger /> },
          { key: "governance", label: "Governance", children: <GovernanceTab /> },
          { key: "jobs", label: "Jobs / Runtime", children: <JobsRuntimeTab /> },
        ]}
      />
      <Drawer title="Object Detail" open={Boolean(selected)} onClose={() => setSelected(null)} width={640}>
        {selected ? (
          <Space direction="vertical" size="middle" className="full-width">
            <BlockerPanel record={selected} />
            <Button onClick={() => setDiagnostic(selected)}>Open Diagnostic Payload</Button>
            <JsonPreview payload={selected} />
          </Space>
        ) : null}
      </Drawer>
      <DiagnosticDrawer open={Boolean(diagnostic)} onClose={() => setDiagnostic(null)} title="Operations Diagnostic" payload={diagnostic} />
    </Space>
  );
}
