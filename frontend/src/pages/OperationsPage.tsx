import {
  Button,
  Card,
  Checkbox,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Typography,
  message,
} from "antd";
import { Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  useCreateEngine,
  useCreateMapping,
  useCreateSource,
  useDeleteEngine,
  useDeleteMapping,
  useDeleteSource,
  useEngines,
  useJobs,
  useMappings,
  usePolicies,
  useQualityRules,
  useRoutingResolve,
  useSetSourceSyncSelections,
  useSources,
  useSourceCatalogSchemas,
  useSourceCatalogTables,
  useSourceSyncSelections,
  useSyncSource,
  useUpdateEngine,
  useUpdateMapping,
  useUpdateSource,
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

type InventoryKind = "source" | "engine" | "mapping";
type EditState = { kind: InventoryKind; row?: EntityRow } | null;

function objectId(row: EntityRow, kind: InventoryKind): string {
  if (kind === "source") return String(row.source_id ?? row.id ?? "");
  if (kind === "engine") return String(row.engine_id ?? row.id ?? "");
  return String(row.mapping_id ?? row.id ?? "");
}

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

function compactRecord(payload: JsonRecord): JsonRecord {
  return Object.fromEntries(
    Object.entries(payload).filter(([, value]) => value !== undefined && value !== ""),
  );
}

function splitCsv(value: string | undefined): string[] {
  return String(value ?? "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function apiErrorMessage(error: unknown): string {
  if (error && typeof error === "object" && "message" in error) {
    return String((error as { message?: unknown }).message);
  }
  return "Operation failed.";
}

function ObjectTable({
  rows,
  kind,
  onInspect,
  onEdit,
  onDelete,
  onSync,
  onSelections,
  deleting,
  syncing,
}: {
  rows?: EntityRow[];
  kind: InventoryKind;
  onInspect: (row: EntityRow) => void;
  onEdit: (row: EntityRow) => void;
  onDelete: (row: EntityRow) => void;
  onSync?: (row: EntityRow) => void;
  onSelections?: (row: EntityRow) => void;
  deleting?: boolean;
  syncing?: boolean;
}) {
  return (
    <Table
      rowKey={(row) => objectId(row, kind) || JSON.stringify(row)}
      size="small"
      dataSource={rows}
      locale={{ emptyText: <TaskEmpty kind={kind} /> }}
      columns={[
        {
          title: "ID",
          render: (_, row) => objectId(row, kind),
        },
        { title: "Name", render: (_, row) => row.display_name ?? row.name ?? "-" },
        { title: "Type", render: (_, row) => row.source_type ?? row.engine_type ?? "mapping" },
        { title: "State", render: (_, row) => <InlineState readiness={row.readiness_status} failure={row.failure_code} /> },
        { title: "Updated", dataIndex: "updated_at" },
        {
          title: "Actions",
          render: (_, row) => (
            <Space>
              {kind === "source" ? (
                <>
                  <Button loading={syncing} onClick={() => onSync?.(row)}>
                    Sync
                  </Button>
                  <Button onClick={() => onSelections?.(row)}>Selections</Button>
                </>
              ) : null}
              <Button onClick={() => onInspect(row)}>Details</Button>
              <Button onClick={() => onEdit(row)}>Edit</Button>
              <Popconfirm
                title={`Delete this ${kind}?`}
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

function SourceDrawer({
  row,
  open,
  onClose,
}: {
  row?: EntityRow;
  open: boolean;
  onClose: () => void;
}) {
  const [form] = Form.useForm();
  const createSource = useCreateSource();
  const updateSource = useUpdateSource();
  const sourceType = Form.useWatch("source_type", form);

  useEffect(() => {
    if (!open) return;
    form.setFieldsValue({
      source_type: row?.source_type ?? "duckdb",
      display_name: row?.display_name ?? "",
      sync_mode: row?.sync?.mode ?? "selected",
      allow_live_browse: row?.policy?.allow_live_browse ?? true,
      allow_sync: row?.policy?.allow_sync ?? true,
      connection_json: formatJson(row?.authority?.connection, {}),
    });
  }, [form, open, row]);

  async function submit(values: JsonRecord) {
    try {
      const type = String(values.source_type);
      const authority: JsonRecord = {
        catalog_system: type,
        connection: parseJsonObject(values.connection_json, "authority.connection"),
      };
      if (type === "duckdb") authority.synthetic_catalog = "main";
      const payload = {
        display_name: values.display_name,
        authority,
        sync: { mode: values.sync_mode },
        policy: {
          allow_live_browse: Boolean(values.allow_live_browse),
          allow_sync: Boolean(values.allow_sync),
        },
        ...(row ? {} : { source_type: type }),
      };
      if (row?.source_id) {
        await updateSource.mutateAsync({ sourceId: String(row.source_id), payload });
      } else {
        await createSource.mutateAsync(payload);
      }
      onClose();
    } catch (error) {
      message.error(apiErrorMessage(error));
    }
  }

  return (
    <Drawer title={row ? "Edit Source" : "New Source"} open={open} onClose={onClose} width={560}>
      <Form form={form} layout="vertical" onFinish={submit}>
        <Form.Item label="Type" name="source_type" rules={[{ required: true }]}>
          <Select disabled={Boolean(row)} options={["duckdb", "trino"].map((value) => ({ value }))} />
        </Form.Item>
        <Form.Item label="Display name" name="display_name" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item label="Sync mode" name="sync_mode">
          <Select options={["selected", "all", "none"].map((value) => ({ value }))} />
        </Form.Item>
        <Space>
          <Form.Item name="allow_live_browse" valuePropName="checked">
            <Checkbox>Allow live browse</Checkbox>
          </Form.Item>
          <Form.Item name="allow_sync" valuePropName="checked">
            <Checkbox>Allow sync</Checkbox>
          </Form.Item>
        </Space>
        <Form.Item
          label={`${sourceType === "trino" ? "Trino" : "DuckDB"} connection JSON`}
          name="connection_json"
          rules={[{ required: true }]}
        >
          <Input.TextArea rows={8} />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={createSource.isPending || updateSource.isPending}>
          Save Source
        </Button>
      </Form>
    </Drawer>
  );
}

function EngineDrawer({
  row,
  open,
  onClose,
}: {
  row?: EntityRow;
  open: boolean;
  onClose: () => void;
}) {
  const [form] = Form.useForm();
  const createEngine = useCreateEngine();
  const updateEngine = useUpdateEngine();
  const engineType = Form.useWatch("engine_type", form);
  const authMode = Form.useWatch("auth_mode", form);

  useEffect(() => {
    if (!open) return;
    form.setFieldsValue({
      engine_type: row?.engine_type ?? "duckdb",
      display_name: row?.display_name ?? "",
      connection_json: formatJson(row?.connection, {}),
      auth_mode: row?.auth?.mode ?? "none",
      username_source: row?.auth?.username_source,
      fallback_username: row?.auth?.fallback_username,
      default_catalog: row?.default_namespace?.catalog,
      default_schema: row?.default_namespace?.schema,
      deployment_capabilities_json: formatJson(row?.deployment_capabilities, {}),
      allowed_step_types: (row?.policy?.allowed_step_types ?? []).join(", "),
      required_policy_support: (row?.policy?.required_policy_support ?? []).join(", "),
    });
  }, [form, open, row]);

  async function submit(values: JsonRecord) {
    try {
      const auth =
        values.auth_mode === "none"
          ? { mode: "none" }
          : compactRecord({
              mode: "username_only",
              username_source: values.username_source,
              fallback_username: values.fallback_username,
            });
      const namespace = compactRecord({
        catalog: values.default_catalog,
        schema: values.default_schema,
      });
      const payload = compactRecord({
        display_name: values.display_name,
        connection: parseJsonObject(values.connection_json, "connection"),
        auth,
        default_namespace: Object.keys(namespace).length > 0 ? namespace : null,
        deployment_capabilities: parseJsonObject(
          values.deployment_capabilities_json,
          "deployment_capabilities",
        ),
        policy: {
          allowed_step_types: splitCsv(values.allowed_step_types),
          required_policy_support: splitCsv(values.required_policy_support),
        },
        ...(row ? {} : { engine_type: values.engine_type }),
      });
      if (row?.engine_id) {
        await updateEngine.mutateAsync({ engineId: String(row.engine_id), payload });
      } else {
        await createEngine.mutateAsync(payload);
      }
      onClose();
    } catch (error) {
      message.error(apiErrorMessage(error));
    }
  }

  return (
    <Drawer title={row ? "Edit Engine" : "New Engine"} open={open} onClose={onClose} width={600}>
      <Form form={form} layout="vertical" onFinish={submit}>
        <Form.Item label="Type" name="engine_type" rules={[{ required: true }]}>
          <Select disabled={Boolean(row)} options={["duckdb", "trino"].map((value) => ({ value }))} />
        </Form.Item>
        <Form.Item label="Display name" name="display_name" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item label={`${engineType === "trino" ? "Trino" : "DuckDB"} connection JSON`} name="connection_json">
          <Input.TextArea rows={7} />
        </Form.Item>
        <Form.Item label="Auth mode" name="auth_mode">
          <Select
            options={[
              { value: "none", label: "none" },
              { value: "username_only", label: "username_only" },
            ]}
          />
        </Form.Item>
        {authMode === "username_only" ? (
          <Space className="full-width" align="start">
            <Form.Item label="Username source" name="username_source" rules={[{ required: true }]}>
              <Select style={{ width: 180 }} options={["session_user", "fixed"].map((value) => ({ value }))} />
            </Form.Item>
            <Form.Item label="Fallback username" name="fallback_username">
              <Input />
            </Form.Item>
          </Space>
        ) : null}
        <Space className="full-width" align="start">
          <Form.Item label="Default catalog" name="default_catalog">
            <Input disabled={engineType === "duckdb"} />
          </Form.Item>
          <Form.Item label="Default schema" name="default_schema">
            <Input disabled={engineType === "duckdb"} />
          </Form.Item>
        </Space>
        <Form.Item label="Deployment capabilities JSON" name="deployment_capabilities_json">
          <Input.TextArea rows={5} />
        </Form.Item>
        <Form.Item label="Allowed step types" name="allowed_step_types">
          <Input placeholder="observe, compare" />
        </Form.Item>
        <Form.Item label="Required policy support" name="required_policy_support">
          <Input placeholder="row_filter" />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={createEngine.isPending || updateEngine.isPending}>
          Save Engine
        </Button>
      </Form>
    </Drawer>
  );
}

function MappingDrawer({
  row,
  open,
  onClose,
}: {
  row?: EntityRow;
  open: boolean;
  onClose: () => void;
}) {
  const [form] = Form.useForm();
  const sources = useSources();
  const engines = useEngines();
  const createMapping = useCreateMapping();
  const updateMapping = useUpdateMapping();

  useEffect(() => {
    if (!open) return;
    const catalogMappings = Array.isArray(row?.catalog_mappings) ? row.catalog_mappings : [];
    form.setFieldsValue({
      source_id: row?.source_id,
      engine_id: row?.engine_id,
      priority: row?.priority ?? 0,
      status: row?.status ?? "active",
      catalog_mappings:
        catalogMappings.length > 0
          ? catalogMappings
          : [{ authority_catalog: "", execution_catalog: "", default_schema: undefined }],
    });
  }, [form, open, row]);

  async function submit(values: JsonRecord) {
    try {
      const catalogMappings = (values.catalog_mappings ?? []).map((entry: JsonRecord) =>
        compactRecord({
          authority_catalog: entry.authority_catalog,
          execution_catalog: entry.execution_catalog,
          default_schema: entry.default_schema,
        }),
      );
      if (row?.mapping_id) {
        await updateMapping.mutateAsync({
          mappingId: String(row.mapping_id),
          payload: {
            priority: values.priority ?? 0,
            status: values.status,
            catalog_mappings: catalogMappings,
          },
        });
      } else {
        await createMapping.mutateAsync({
          source_id: values.source_id,
          engine_id: values.engine_id,
          priority: values.priority ?? 0,
          status: values.status,
          catalog_mappings: catalogMappings,
        });
      }
      onClose();
    } catch (error) {
      message.error(apiErrorMessage(error));
    }
  }

  return (
    <Drawer title={row ? "Edit Mapping" : "New Mapping"} open={open} onClose={onClose} width={680}>
      <Form form={form} layout="vertical" onFinish={submit}>
        <Space className="full-width" align="start">
          <Form.Item label="Source" name="source_id" rules={[{ required: true }]}>
            <Select
              disabled={Boolean(row)}
              style={{ width: 260 }}
              options={(sources.data ?? []).map((source) => ({
                value: source.source_id,
                label: `${source.display_name ?? source.source_id} (${source.source_id})`,
              }))}
            />
          </Form.Item>
          <Form.Item label="Engine" name="engine_id" rules={[{ required: true }]}>
            <Select
              disabled={Boolean(row)}
              style={{ width: 260 }}
              options={(engines.data ?? []).map((engine) => ({
                value: engine.engine_id,
                label: `${engine.display_name ?? engine.engine_id} (${engine.engine_id})`,
              }))}
            />
          </Form.Item>
        </Space>
        <Space className="full-width" align="start">
          <Form.Item label="Priority" name="priority">
            <InputNumber />
          </Form.Item>
          <Form.Item label="Status" name="status">
            <Select style={{ width: 180 }} options={["active", "inactive", "deprecated"].map((value) => ({ value }))} />
          </Form.Item>
        </Space>
        <Form.List name="catalog_mappings">
          {(fields, { add, remove }) => (
            <Space direction="vertical" className="full-width">
              {fields.map((field) => (
                <Space key={field.key} align="start">
                  <Form.Item label="Authority catalog" name={[field.name, "authority_catalog"]} rules={[{ required: true }]}>
                    <Input />
                  </Form.Item>
                  <Form.Item label="Execution catalog" name={[field.name, "execution_catalog"]} rules={[{ required: true }]}>
                    <Input />
                  </Form.Item>
                  <Form.Item label="Default schema" name={[field.name, "default_schema"]}>
                    <Input />
                  </Form.Item>
                  <Button aria-label="Remove catalog mapping" icon={<Trash2 size={16} />} onClick={() => remove(field.name)} />
                </Space>
              ))}
              <Button icon={<Plus size={16} />} onClick={() => add({ authority_catalog: "", execution_catalog: "" })}>
                Add Catalog Mapping
              </Button>
            </Space>
          )}
        </Form.List>
        <Button
          type="primary"
          htmlType="submit"
          loading={createMapping.isPending || updateMapping.isPending}
          style={{ marginTop: 16 }}
        >
          Save Mapping
        </Button>
      </Form>
    </Drawer>
  );
}

function SourceSelectionsDrawer({
  row,
  open,
  onClose,
}: {
  row?: EntityRow;
  open: boolean;
  onClose: () => void;
}) {
  const [form] = Form.useForm();
  const [schemaName, setSchemaName] = useState<string>();
  const [tableName, setTableName] = useState<string>();
  const sourceId = row?.source_id ? String(row.source_id) : undefined;
  const selections = useSourceSyncSelections(sourceId, open);
  const schemas = useSourceCatalogSchemas(sourceId, open);
  const tables = useSourceCatalogTables(sourceId, schemaName, open);
  const setSelections = useSetSourceSyncSelections();

  useEffect(() => {
    if (!open) return;
    form.setFieldsValue({
      selections:
        selections.data && selections.data.length > 0
          ? selections.data
          : [{ schema_name: "", table_name: "" }],
    });
  }, [form, open, selections.data]);

  useEffect(() => {
    if (open) return;
    setSchemaName(undefined);
    setTableName(undefined);
  }, [open]);

  async function submit(values: JsonRecord) {
    if (!sourceId) return;
    try {
      const nextSelections = ((values.selections ?? []) as JsonRecord[])
        .map((selection) =>
          compactRecord({
            schema_name: selection.schema_name,
            table_name: selection.table_name,
          }),
        )
        .filter((selection) => selection.schema_name && selection.table_name);
      await setSelections.mutateAsync({ sourceId, selections: nextSelections });
      onClose();
    } catch (error) {
      message.error(apiErrorMessage(error));
    }
  }

  function addBrowsedSelection() {
    if (!schemaName || !tableName) return;
    const current = (form.getFieldValue("selections") ?? []) as JsonRecord[];
    const exists = current.some(
      (selection) => selection.schema_name === schemaName && selection.table_name === tableName,
    );
    if (!exists) {
      form.setFieldValue("selections", [...current, { schema_name: schemaName, table_name: tableName }]);
    }
  }

  return (
    <Drawer title="Sync Selections" open={open} onClose={onClose} width={720}>
      <Space direction="vertical" size="middle" className="full-width">
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label="Source">{row?.display_name ?? row?.source_id}</Descriptions.Item>
          <Descriptions.Item label="Sync mode">{row?.sync?.mode ?? "selected"}</Descriptions.Item>
        </Descriptions>
        <Space wrap align="end">
          <Form.Item label="Schema" style={{ marginBottom: 0 }}>
            <Select
              showSearch
              allowClear
              loading={schemas.isFetching}
              style={{ width: 220 }}
              value={schemaName}
              onChange={(value) => {
                setSchemaName(value);
                setTableName(undefined);
              }}
              options={(schemas.data ?? []).map((schema) => ({
                value: schema.schema_name ?? schema.name,
                label: schema.schema_name ?? schema.name,
              }))}
            />
          </Form.Item>
          <Form.Item label="Table" style={{ marginBottom: 0 }}>
            <Select
              showSearch
              allowClear
              loading={tables.isFetching}
              disabled={!schemaName}
              style={{ width: 260 }}
              value={tableName}
              onChange={setTableName}
              options={(tables.data ?? []).map((table) => ({
                value: table.table_name ?? table.name,
                label: table.table_name ?? table.name,
              }))}
            />
          </Form.Item>
          <Button onClick={addBrowsedSelection} disabled={!schemaName || !tableName}>
            Add Selection
          </Button>
        </Space>
        <Form form={form} layout="vertical" onFinish={submit}>
          <Form.List name="selections">
            {(fields, { add, remove }) => (
              <Space direction="vertical" className="full-width">
                {fields.map((field) => (
                  <Space key={field.key} align="start">
                    <Form.Item label="Schema" name={[field.name, "schema_name"]} rules={[{ required: true }]}>
                      <Input />
                    </Form.Item>
                    <Form.Item label="Table" name={[field.name, "table_name"]} rules={[{ required: true }]}>
                      <Input />
                    </Form.Item>
                    <Button aria-label="Remove sync selection" icon={<Trash2 size={16} />} onClick={() => remove(field.name)} />
                  </Space>
                ))}
                <Button icon={<Plus size={16} />} onClick={() => add({ schema_name: "", table_name: "" })}>
                  Add Manual Selection
                </Button>
              </Space>
            )}
          </Form.List>
          <Button type="primary" htmlType="submit" loading={setSelections.isPending} style={{ marginTop: 16 }}>
            Save Selections
          </Button>
        </Form>
      </Space>
    </Drawer>
  );
}

function SourcesTab({ onInspect, onEdit }: { onInspect: (row: EntityRow) => void; onEdit: (row?: EntityRow) => void }) {
  const sources = useSources();
  const deleteSource = useDeleteSource();
  const syncSource = useSyncSource();
  const [selectionSource, setSelectionSource] = useState<EntityRow>();
  return (
    <Space direction="vertical" size="middle" className="full-width">
      <Typography.Paragraph type="secondary">
        Source inventory comes from the HTTP API. Sync selections are represented as structured tables instead of handwritten JSON.
      </Typography.Paragraph>
      <Space wrap>
        <Button type="primary" icon={<Plus size={16} />} onClick={() => onEdit()}>
          New Source
        </Button>
        <Button
          disabled={!sources.data?.length}
          onClick={() => setSelectionSource(sources.data?.[0])}
        >
          Manage Selections
        </Button>
      </Space>
      <ObjectTable
        rows={sources.data}
        kind="source"
        onInspect={onInspect}
        onEdit={(row) => onEdit(row)}
        onSync={(row) =>
          syncSource.mutate(String(row.source_id), {
            onSuccess: () => message.success("Source metadata sync completed."),
            onError: (error) => message.error(apiErrorMessage(error)),
          })
        }
        onSelections={setSelectionSource}
        onDelete={(row) => deleteSource.mutate(String(row.source_id), { onError: (error) => message.error(apiErrorMessage(error)) })}
        deleting={deleteSource.isPending}
        syncing={syncSource.isPending}
      />
      <SourceSelectionsDrawer
        row={selectionSource}
        open={Boolean(selectionSource)}
        onClose={() => setSelectionSource(undefined)}
      />
    </Space>
  );
}

function EnginesTab({ onInspect, onEdit }: { onInspect: (row: EntityRow) => void; onEdit: (row?: EntityRow) => void }) {
  const engines = useEngines();
  const deleteEngine = useDeleteEngine();
  return (
    <Space direction="vertical" size="middle" className="full-width">
      <Typography.Paragraph type="secondary">
        Engine readiness is configuration validation. It does not imply that the UI ran an online SELECT 1 probe. DuckDB ignores session execution identity; Trino can use username_only.
      </Typography.Paragraph>
      <Button type="primary" icon={<Plus size={16} />} onClick={() => onEdit()}>
        New Engine
      </Button>
      <ObjectTable
        rows={engines.data}
        kind="engine"
        onInspect={onInspect}
        onEdit={(row) => onEdit(row)}
        onDelete={(row) => deleteEngine.mutate(String(row.engine_id), { onError: (error) => message.error(apiErrorMessage(error)) })}
        deleting={deleteEngine.isPending}
      />
    </Space>
  );
}

function MappingsTab({ onInspect, onEdit }: { onInspect: (row: EntityRow) => void; onEdit: (row?: EntityRow) => void }) {
  const mappings = useMappings();
  const deleteMapping = useDeleteMapping();
  return (
    <Space direction="vertical" size="middle" className="full-width">
      <Typography.Paragraph type="secondary">
        Mapping gaps are shown through coverage summaries and failure codes. `mapping_incomplete` and `mapping_inactive_dependency` point to actionable blockers first.
      </Typography.Paragraph>
      <Button type="primary" icon={<Plus size={16} />} onClick={() => onEdit()}>
        New Mapping
      </Button>
      <ObjectTable
        rows={mappings.data}
        kind="mapping"
        onInspect={onInspect}
        onEdit={(row) => onEdit(row)}
        onDelete={(row) => deleteMapping.mutate(String(row.mapping_id), { onError: (error) => message.error(apiErrorMessage(error)) })}
        deleting={deleteMapping.isPending}
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
  const [editState, setEditState] = useState<EditState>(null);
  return (
    <Space direction="vertical" size="large" className="page">
      <SectionHeader
        title="Operations"
        description="Operations workflows for sources, engines, mappings, routing, governance, jobs, and runtime diagnostics."
      />
      <Tabs
        items={[
          { key: "sources", label: "Sources", children: <SourcesTab onInspect={setSelected} onEdit={(row) => setEditState({ kind: "source", row })} /> },
          { key: "engines", label: "Engines", children: <EnginesTab onInspect={setSelected} onEdit={(row) => setEditState({ kind: "engine", row })} /> },
          { key: "mappings", label: "Mappings", children: <MappingsTab onInspect={setSelected} onEdit={(row) => setEditState({ kind: "mapping", row })} /> },
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
      <SourceDrawer row={editState?.kind === "source" ? editState.row : undefined} open={editState?.kind === "source"} onClose={() => setEditState(null)} />
      <EngineDrawer row={editState?.kind === "engine" ? editState.row : undefined} open={editState?.kind === "engine"} onClose={() => setEditState(null)} />
      <MappingDrawer row={editState?.kind === "mapping" ? editState.row : undefined} open={editState?.kind === "mapping"} onClose={() => setEditState(null)} />
      <DiagnosticDrawer open={Boolean(diagnostic)} onClose={() => setDiagnostic(null)} title="Operations Diagnostic" payload={diagnostic} />
    </Space>
  );
}
