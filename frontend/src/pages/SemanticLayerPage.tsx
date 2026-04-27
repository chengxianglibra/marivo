import { Button, Card, Descriptions, Drawer, Form, Input, List, Select, Space, Table, Tabs, Typography } from "antd";
import { useMemo, useState } from "react";
import { semanticKinds, useSemanticAction, useSemanticList, useSources } from "../api/hooks";
import type { EntityRow, JsonRecord } from "../api/types";
import { TaskEmpty } from "../components/EmptyState";
import { BlockerPanel, InlineState, JsonPreview, SectionHeader, StatusBadge } from "../components/StatusBadge";

function firstText(...values: unknown[]): string | undefined {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value;
  }
  return undefined;
}

function semanticHeader(row: EntityRow): JsonRecord {
  return row.header && typeof row.header === "object" ? (row.header as JsonRecord) : {};
}

function semanticId(row: EntityRow): string {
  const header = semanticHeader(row);
  return (
    firstText(
      row.entity_id,
      header.entity_ref,
      row.entity_contract_id,
      row.metric_id,
      header.metric_ref,
      row.metric_contract_id,
      row.process_contract_id,
      header.process_ref,
      row.dimension_contract_id,
      header.dimension_ref,
      row.time_contract_id,
      header.time_ref,
      row.enum_set_contract_id,
      header.enum_set_ref,
      row.predicate_contract_id,
      header.predicate_ref,
      row.binding_id,
      row.binding_contract_id,
      header.binding_ref,
      row.profile_id,
      header.profile_ref,
      row.id,
      row.name,
    ) ?? JSON.stringify(row)
  );
}

function semanticName(row: EntityRow): string {
  const header = semanticHeader(row);
  return firstText(row.name, row.display_name, header.display_name, header.name) ?? "-";
}

function formatSemanticValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(formatSemanticValue).filter(Boolean).join(", ");
  if (typeof value === "object") {
    return Object.entries(value as JsonRecord)
      .map(([key, entry]) => {
        const formatted = formatSemanticValue(entry);
        return formatted ? `${key}: ${formatted}` : key;
      })
      .join(", ");
  }
  return String(value);
}

function formatSemanticList(value: unknown, separator = ", ", fallback = "-"): string {
  const formatted = Array.isArray(value)
    ? value.map(formatSemanticValue).filter(Boolean).join(separator)
    : formatSemanticValue(value);
  return formatted || fallback;
}

function formatCapabilities(value: unknown): string {
  if (Array.isArray(value)) return formatSemanticList(value);
  if (value && typeof value === "object") {
    const formatted = Object.entries(value as JsonRecord)
      .flatMap(([key, entry]) => {
        if (entry === true) return [key];
        if (entry === false || entry === null || entry === undefined || typeof entry === "object") return [];
        return [`${key}: ${formatSemanticValue(entry)}`];
      })
      .join(", ");
    return formatted || "-";
  }
  return formatSemanticList(value);
}

function semanticCollectionCount(value: unknown): number {
  if (Array.isArray(value)) return value.length;
  if (value && typeof value === "object") return Object.keys(value).length;
  return value ? 1 : 0;
}

function InventoryTable({
  kind,
  onInspect,
}: {
  kind: (typeof semanticKinds)[number];
  onInspect: (kind: (typeof semanticKinds)[number], row: EntityRow) => void;
}) {
  const query = useSemanticList(kind);
  return (
    <Table
      rowKey={semanticId}
      size="small"
      dataSource={query.data}
      locale={{ emptyText: <TaskEmpty kind="semantic" /> }}
      columns={[
        { title: "Ref", render: (_, row) => semanticId(row) },
        { title: "Name", render: (_, row) => semanticName(row) },
        { title: "Lifecycle", render: (_, row) => <StatusBadge value={row.lifecycle_status ?? row.status} /> },
        { title: "Readiness", render: (_, row) => <InlineState readiness={row.readiness_status} failure={row.failure_code} /> },
        {
          title: "Dependencies",
          render: (_, row) => semanticCollectionCount(row.dependency_refs),
        },
        {
          title: "Capabilities",
          render: (_, row) => formatCapabilities(row.capabilities),
        },
        { title: "Inspect", render: (_, row) => <Button onClick={() => onInspect(kind, row)}>Details</Button> },
      ]}
    />
  );
}

function SemanticInventory({ onInspect }: { onInspect: (kind: (typeof semanticKinds)[number], row: EntityRow) => void }) {
  return (
    <Tabs
      items={semanticKinds.map((kind) => ({
        key: kind.key,
        label: kind.label,
        children: <InventoryTable kind={kind} onInspect={onInspect} />,
      }))}
    />
  );
}

function ReadinessQueue({ onInspect }: { onInspect: (kind: (typeof semanticKinds)[number], row: EntityRow) => void }) {
  return (
    <Space direction="vertical" size="middle" className="full-width">
      <Typography.Paragraph type="secondary">
        The queue answers why an object is not_ready or stale and what to fix next, prioritizing blocking requirements, dependency refs, and capability gaps.
      </Typography.Paragraph>
      {semanticKinds.map((kind) => (
        <ReadinessQueueSection key={kind.key} kind={kind} onInspect={onInspect} />
      ))}
    </Space>
  );
}

function ReadinessQueueSection({
  kind,
  onInspect,
}: {
  kind: (typeof semanticKinds)[number];
  onInspect: (kind: (typeof semanticKinds)[number], row: EntityRow) => void;
}) {
  const query = useSemanticList(kind);
  const rows = (query.data ?? []).filter((row) => row.readiness_status && row.readiness_status !== "ready");
  if (!rows.length) return null;
  return (
    <Card size="small" title={kind.label}>
      <List
        dataSource={rows}
        locale={{ emptyText: <TaskEmpty kind="semantic" /> }}
        renderItem={(row) => (
          <List.Item actions={[<Button onClick={() => onInspect(kind, row)}>Open</Button>]}>
            <List.Item.Meta
              title={
                <Space wrap>
                  <Typography.Text strong>{semanticId(row)}</Typography.Text>
                  <InlineState readiness={row.readiness_status} failure={row.failure_code} />
                </Space>
              }
              description={formatSemanticList(
                row.blocking_requirements,
                " / ",
                "The server did not return blocking requirements.",
              )}
            />
          </List.Item>
        )}
      />
    </Card>
  );
}

function SourceObjectBrowser() {
  const sources = useSources();
  return (
    <Space direction="vertical" size="middle" className="full-width">
      <Typography.Paragraph type="secondary">
        Source Object Browser starts from synced source summaries. Real column and table metadata depends on the source object detail API.
      </Typography.Paragraph>
      <Table
        rowKey={(row) => row.source_id}
        size="small"
        dataSource={sources.data}
        columns={[
          { title: "Source", render: (_, row) => row.display_name ?? row.source_id },
          { title: "Type", dataIndex: "source_type" },
          { title: "Readiness", render: (_, row) => <InlineState readiness={row.readiness_status} failure={row.failure_code} /> },
          { title: "Synced Objects", dataIndex: "synced_object_count" },
          { title: "Modeling entry", render: () => "Create binding from source object" },
        ]}
      />
    </Space>
  );
}

function BindingWizard() {
  const [preview, setPreview] = useState<JsonRecord | null>(null);
  return (
    <Space direction="vertical" size="middle" className="full-width">
      <Typography.Paragraph type="secondary">
        Binding Wizard v1 only builds a typed contract preview. Submission and persistence must go through the HTTP typed semantic API and must not fake backend state.
      </Typography.Paragraph>
      <Form layout="vertical" onFinish={(values) => setPreview(values)}>
        <Form.Item label="Carrier source object" name="carrier_source_object_ref" rules={[{ required: true }]}>
          <Input placeholder="source_object.sales.orders" />
        </Form.Item>
        <Form.Item label="Metric refs" name="metric_refs">
          <Select mode="tags" placeholder="metric.gmv" />
        </Form.Item>
        <Form.Item label="Key refs" name="key_refs">
          <Select mode="tags" placeholder="entity.customer" />
        </Form.Item>
        <Form.Item label="Time bindings" name="time_bindings">
          <Select mode="tags" placeholder="time.order_created_at" />
        </Form.Item>
        <Button type="primary" htmlType="submit">
          Preview Contract
        </Button>
      </Form>
      {preview ? <JsonPreview payload={preview} /> : null}
    </Space>
  );
}

function DetailDrawer({
  selected,
  onClose,
}: {
  selected: { kind: (typeof semanticKinds)[number]; row: EntityRow } | null;
  onClose: () => void;
}) {
  const action = useSemanticAction(selected?.kind.path ?? "", selected?.row ? semanticId(selected.row) : undefined);
  return (
    <Drawer title="Semantic Object Detail" open={Boolean(selected)} onClose={onClose} width={680}>
      {selected ? (
        <Space direction="vertical" size="middle" className="full-width">
          <Descriptions size="small" column={1} bordered>
            <Descriptions.Item label="kind">{selected.kind.label}</Descriptions.Item>
            <Descriptions.Item label="ref">{semanticId(selected.row)}</Descriptions.Item>
            <Descriptions.Item label="lifecycle">
              <StatusBadge value={selected.row.lifecycle_status ?? selected.row.status} />
            </Descriptions.Item>
            <Descriptions.Item label="readiness">
              <InlineState readiness={selected.row.readiness_status} failure={selected.row.failure_code} />
            </Descriptions.Item>
          </Descriptions>
          <BlockerPanel record={selected.row} />
          <Card size="small" title="Lifecycle Actions">
            <Space wrap>
              <Button onClick={() => action.mutate("validate")} loading={action.isPending}>
                Validate check-only
              </Button>
              <Button onClick={() => action.mutate("activate")} loading={action.isPending}>
                Activate
              </Button>
              <Button danger onClick={() => action.mutate("deprecate")} loading={action.isPending}>
                Deprecate
              </Button>
            </Space>
            <Typography.Paragraph type="secondary" className="top-gap">
              Validate is check-only and does not persist lifecycle changes. Activate still keeps readiness_status visible.
            </Typography.Paragraph>
          </Card>
          {action.data ? <JsonPreview payload={action.data} /> : null}
          <JsonPreview payload={selected.row} />
        </Space>
      ) : null}
    </Drawer>
  );
}

export function SemanticLayerPage() {
  const [selected, setSelected] = useState<{ kind: (typeof semanticKinds)[number]; row: EntityRow } | null>(null);
  const tabItems = useMemo(
    () => [
      { key: "inventory", label: "Object Inventory", children: <SemanticInventory onInspect={(kind, row) => setSelected({ kind, row })} /> },
      { key: "readiness", label: "Readiness Queue", children: <ReadinessQueue onInspect={(kind, row) => setSelected({ kind, row })} /> },
      { key: "source-objects", label: "Source Object Browser", children: <SourceObjectBrowser /> },
      { key: "binding-wizard", label: "Binding Wizard", children: <BindingWizard /> },
    ],
    [],
  );
  return (
    <Space direction="vertical" size="large" className="page">
      <SectionHeader
        title="Semantic Layer"
        description="Modeling workflows for lifecycle, readiness, blocking requirements, dependency refs, and capabilities."
      />
      <Tabs items={tabItems} />
      <DetailDrawer selected={selected} onClose={() => setSelected(null)} />
    </Space>
  );
}
