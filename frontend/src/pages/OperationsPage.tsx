import {
  Button,
  Card,
  Descriptions,
  Drawer,
  Form,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Typography,
  message,
} from "antd";
import { Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  useCreateDatasource,
  useDeleteDatasource,
  useDatasources,
  useJobs,
  useRoutingResolve,
  useUpdateDatasource,
} from "../api/hooks";
import type { DatasourceRow, EntityRow, JsonRecord } from "../api/types";
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

type EditState = { kind: "datasource"; row?: DatasourceRow } | null;

function formatJson(value: unknown, fallback: unknown): string {
  return JSON.stringify(value ?? fallback, null, 2);
}

function parseJsonObject(value: string | undefined, fieldName: string): JsonRecord {
  if (!value?.trim()) return {};
  const parsed = JSON.parse(value) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${fieldName} must be a JSON object.`);
  }
  return parsed as JsonRecord;
}

function apiErrorMessage(error: unknown): string {
  if (error && typeof error === "object" && "message" in error) {
    return String((error as { message?: unknown }).message);
  }
  return "Operation failed.";
}

const DUCKDB_CONNECTION_DEFAULT = JSON.stringify({ datasource_type: "duckdb", path: null, database: null, db_path: "" }, null, 2);
const TRINO_CONNECTION_DEFAULT = JSON.stringify({ datasource_type: "trino", host: "", port: 8080, catalog: "", http_scheme: "http" }, null, 2);

function ObjectTable({
  rows,
  onInspect,
  onEdit,
  onDelete,
  deleting,
}: {
  rows?: DatasourceRow[];
  onInspect: (row: DatasourceRow) => void;
  onEdit: (row: DatasourceRow) => void;
  onDelete: (row: DatasourceRow) => void;
  deleting?: boolean;
}) {
  return (
    <Table
      rowKey={(row) => row.datasource_id}
      size="small"
      dataSource={rows}
      locale={{ emptyText: <TaskEmpty kind="datasource" /> }}
      columns={[
        {
          title: "ID",
          dataIndex: "datasource_id",
        },
        { title: "Name", render: (_, row) => row.display_name ?? "-" },
        { title: "Type", dataIndex: "datasource_type" },
        { title: "State", render: (_, row) => <InlineState readiness={row.readiness_status} failure={row.failure_code} /> },
        { title: "Updated", dataIndex: "updated_at" },
        {
          title: "Actions",
          render: (_, row) => (
            <Space>
              <Button onClick={() => onInspect(row)}>Details</Button>
              <Button onClick={() => onEdit(row)}>Edit</Button>
              <Popconfirm
                title="Delete this datasource?"
                description="The backend will reject the request if dependencies still exist."
                okText="Delete"
                okButtonProps={{ danger: true }}
                onConfirm={() => onDelete(row)}
              >
                <Button danger loading={deleting}>
                  Delete
                </Button>
              </Popconfirm>
            </Space>
          ),
        },
      ]}
    />
  );
}

function DatasourceDrawer({
  row,
  open,
  onClose,
}: {
  row?: DatasourceRow;
  open: boolean;
  onClose: () => void;
}) {
  const [form] = Form.useForm();
  const createDatasource = useCreateDatasource();
  const updateDatasource = useUpdateDatasource();
  const datasourceType = Form.useWatch("datasource_type", form);

  useEffect(() => {
    if (!open) return;
    const type = row?.datasource_type ?? "duckdb";
    form.setFieldsValue({
      datasource_type: type,
      display_name: row?.display_name ?? "",
      connection_json: row?.connection ? formatJson(row.connection, {}) : (type === "trino" ? TRINO_CONNECTION_DEFAULT : DUCKDB_CONNECTION_DEFAULT),
    });
  }, [form, open, row]);

  async function submit(values: JsonRecord) {
    try {
      const type = String(values.datasource_type);
      const connection = parseJsonObject(values.connection_json, "connection");
      if (!connection.datasource_type) connection.datasource_type = type;

      if (row?.datasource_id) {
        await updateDatasource.mutateAsync({
          datasourceId: String(row.datasource_id),
          payload: { display_name: values.display_name, connection },
        });
      } else {
        await createDatasource.mutateAsync({
          datasource_type: type,
          display_name: values.display_name,
          connection,
        });
      }
      onClose();
    } catch (error) {
      message.error(apiErrorMessage(error));
    }
  }

  return (
    <Drawer title={row ? "Edit Datasource" : "New Datasource"} open={open} onClose={onClose} width={560}>
      <Form form={form} layout="vertical" onFinish={submit}>
        <Form.Item label="Type" name="datasource_type" rules={[{ required: true }]}>
          <Select disabled={Boolean(row)} options={["duckdb", "trino"].map((value) => ({ value }))} />
        </Form.Item>
        <Form.Item label="Display name" name="display_name" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item
          label={`${datasourceType === "trino" ? "Trino" : "DuckDB"} connection JSON`}
          name="connection_json"
          rules={[{ required: true }]}
        >
          <Input.TextArea rows={8} />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={createDatasource.isPending || updateDatasource.isPending}>
          Save Datasource
        </Button>
      </Form>
    </Drawer>
  );
}

function DatasourcesTab({ onInspect, onEdit }: { onInspect: (row: DatasourceRow) => void; onEdit: (row?: DatasourceRow) => void }) {
  const datasources = useDatasources();
  const deleteDatasource = useDeleteDatasource();
  return (
    <Space direction="vertical" size="middle" className="full-width">
      <Typography.Paragraph type="secondary">
        Datasource inventory comes from the HTTP API. Live catalog browsing is used for table and column discovery.
      </Typography.Paragraph>
      <Space wrap>
        <Button type="primary" icon={<Plus size={16} />} onClick={() => onEdit()}>
          New Datasource
        </Button>
      </Space>
      <ObjectTable
        rows={datasources.data}
        onInspect={onInspect}
        onEdit={(row) => onEdit(row)}
        onDelete={(row) => deleteDatasource.mutate(String(row.datasource_id), { onError: (error) => message.error(apiErrorMessage(error)) })}
        deleting={deleteDatasource.isPending}
      />
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
  const [selected, setSelected] = useState<DatasourceRow | null>(null);
  const [diagnostic, setDiagnostic] = useState<JsonRecord | null>(null);
  const [editState, setEditState] = useState<EditState>(null);
  return (
    <Space direction="vertical" size="large" className="page">
      <SectionHeader
        title="Operations"
        description="Operations workflows for datasources, routing, jobs, and runtime diagnostics."
      />
      <Tabs
        items={[
          { key: "datasources", label: "Datasources", children: <DatasourcesTab onInspect={setSelected} onEdit={(row) => setEditState({ kind: "datasource", row })} /> },
          { key: "routing", label: "Routing Debugger", children: <RoutingDebugger /> },
          { key: "jobs", label: "Jobs / Runtime", children: <JobsRuntimeTab /> },
        ]}
      />
      <Drawer title="Object Detail" open={Boolean(selected)} onClose={() => setSelected(null)} width={640}>
        {selected ? (
          <Space direction="vertical" size="middle" className="full-width">
            <BlockerPanel record={selected as EntityRow} />
            <Button onClick={() => setDiagnostic(selected as EntityRow)}>Open Diagnostic Payload</Button>
            <JsonPreview payload={selected} />
          </Space>
        ) : null}
      </Drawer>
      <DatasourceDrawer
        row={editState?.kind === "datasource" ? editState.row : undefined}
        open={editState?.kind === "datasource"}
        onClose={() => setEditState(null)}
      />
      <DiagnosticDrawer open={Boolean(diagnostic)} onClose={() => setDiagnostic(null)} title="Operations Diagnostic" payload={diagnostic} />
    </Space>
  );
}
