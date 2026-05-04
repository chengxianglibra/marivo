import { Button, Card, Descriptions, Input, List, Select, Space, Table, Tabs, Typography } from "antd";
import { useState } from "react";
import {
  useJobs,
  usePropositionContext,
  usePropositionRuntime,
  useSessionRuntime,
  useSessions,
  useSessionState,
} from "../api/hooks";
import type { EntityRow, JsonRecord } from "../api/types";
import { EvidenceClosure } from "../components/EvidenceClosure";
import { TaskEmpty } from "../components/EmptyState";
import { RuntimeStatusWidget } from "../components/RuntimeStatusWidget";
import { JsonPreview, ReadOnlyNotice, SectionHeader, StatusBadge } from "../components/StatusBadge";
import { sessionGoalText, sessionLifecycleStatus } from "./analysisRows";

function SessionInbox({ onOpen }: { onOpen: (sessionId: string) => void }) {
  const [filters, setFilters] = useState<{ status?: string; session_id?: string }>({});
  const sessions = useSessions(filters);
  return (
    <Space direction="vertical" size="middle" className="full-width">
      <Space wrap>
        <Input.Search placeholder="session_id" allowClear onSearch={(value) => setFilters((old) => ({ ...old, session_id: value || undefined }))} />
        <Select
          allowClear
          placeholder="status"
          style={{ width: 150 }}
          options={["open", "closed", "terminated"].map((value) => ({ value }))}
          onChange={(value) => setFilters((old) => ({ ...old, status: value }))}
        />
      </Space>
      <Table
        rowKey={(row) => row.session_id}
        size="small"
        dataSource={sessions.data}
        locale={{ emptyText: <TaskEmpty kind="session" /> }}
        columns={[
          { title: "session_id", dataIndex: "session_id" },
          { title: "Goal", render: (_, row) => sessionGoalText(row) },
          { title: "Lifecycle", render: (_, row) => <StatusBadge value={sessionLifecycleStatus(row)} /> },
          { title: "User", render: (_, row) => row.execution_identity?.session_user ?? "-" },
          { title: "Propositions", dataIndex: "active_proposition_count" },
          { title: "Blocking Gaps", dataIndex: "blocking_gap_count" },
          { title: "Runtime", render: (_, row) => <StatusBadge value={row.runtime_overall_status} /> },
          { title: "Open", render: (_, row) => <Button onClick={() => onOpen(row.session_id)}>Detail</Button> },
        ]}
      />
    </Space>
  );
}

function SessionDetail({ sessionId, onProposition }: { sessionId?: string; onProposition: (id: string) => void }) {
  const state = useSessionState(sessionId);
  const runtime = useSessionRuntime(sessionId);
  const jobs = useJobs({ session_id: sessionId });
  const propositions = (state.data?.propositions ?? []) as JsonRecord[];
  return (
    <Space direction="vertical" size="middle" className="full-width">
      <ReadOnlyNotice />
      <Card size="small" title="Session State">
        <Table
          rowKey={(row) => row.proposition_id}
          size="small"
          dataSource={propositions}
          locale={{ emptyText: <TaskEmpty kind="evidence" /> }}
          columns={[
            { title: "proposition_id", dataIndex: "proposition_id" },
            { title: "Statement", dataIndex: "statement" },
            { title: "Assessment", render: (_, row) => <StatusBadge value={row.latest_assessment_status} /> },
            { title: "Blocking gaps", render: (_, row) => <StatusBadge value={row.has_blocking_gaps ? "blocked" : "ready"} /> },
            { title: "Open", render: (_, row) => <Button onClick={() => onProposition(row.proposition_id)}>Context</Button> },
          ]}
        />
      </Card>
      <RuntimeStatusWidget status={runtime.data} />
      <Card size="small" title="Related Jobs">
        <Table
          rowKey={(row) => row.job_id}
          size="small"
          dataSource={jobs.data}
          columns={[
            { title: "job_id", dataIndex: "job_id" },
            { title: "type", dataIndex: "job_type" },
            { title: "status", render: (_, row) => <StatusBadge value={row.status} /> },
            { title: "error", dataIndex: "error_message" },
          ]}
        />
      </Card>
    </Space>
  );
}

function PropositionDetail({ sessionId, propositionId }: { sessionId?: string; propositionId?: string }) {
  const context = usePropositionContext(sessionId, propositionId);
  const runtime = usePropositionRuntime(sessionId, propositionId);
  return (
    <Space direction="vertical" size="middle" className="full-width">
      <Descriptions size="small" column={1} bordered>
        <Descriptions.Item label="session_id">{sessionId ?? "n/a"}</Descriptions.Item>
        <Descriptions.Item label="proposition_id">{propositionId ?? "n/a"}</Descriptions.Item>
      </Descriptions>
      <EvidenceClosure context={context.data} />
      <RuntimeStatusWidget status={runtime.data} />
    </Space>
  );
}

function EvidenceTimeline({ sessionId }: { sessionId?: string }) {
  const state = useSessionState(sessionId);
  const propositions = (state.data?.propositions ?? []) as JsonRecord[];
  const timeline = [
    { title: "session created", description: sessionId ?? "select a session" },
    { title: "typed intent submitted", description: "observe / compare / diagnose intent" },
    { title: "artifact materialized", description: "artifact refs are provenance, not conclusions" },
    ...propositions.map((prop) => ({ title: "proposition seeded", description: prop.statement })),
    { title: "assessment committed", description: "support / oppose / gaps resolved into latest assessment" },
  ];
  return (
    <List
      dataSource={timeline}
      renderItem={(item) => (
        <List.Item>
          <List.Item.Meta title={item.title} description={item.description} />
        </List.Item>
      )}
    />
  );
}

function GapView({ sessionId }: { sessionId?: string }) {
  const state = useSessionState(sessionId);
  const gaps = ((state.data?.gaps ?? []) as EntityRow[]).sort((a, b) => Number(Boolean(b.blocking)) - Number(Boolean(a.blocking)));
  return (
    <Table
      rowKey={(row) => row.gap_id}
      size="small"
      dataSource={gaps}
      columns={[
        { title: "gap_id", dataIndex: "gap_id" },
        { title: "type", dataIndex: "gap_type" },
        { title: "severity", dataIndex: "severity" },
        { title: "blocking", render: (_, row) => <StatusBadge value={row.blocking ? "blocked" : "ready"} /> },
        { title: "requirement", dataIndex: "requirement_summary" },
        { title: "satisfiable by", dataIndex: "satisfiable_by" },
      ]}
    />
  );
}

function EvidenceInspector({ payload }: { payload?: unknown }) {
  return (
    <Space direction="vertical" size="middle" className="full-width">
      <Typography.Paragraph type="secondary">
        Artifacts and findings are distinct. SQL, when present, is folded provenance detail rather than an evidence conclusion.
      </Typography.Paragraph>
      <JsonPreview payload={payload ?? { artifact_identity: "select proposition context first" }} />
    </Space>
  );
}

export function AnalysisPage() {
  const [sessionId, setSessionId] = useState("sess_growth_review");
  const [propositionId, setPropositionId] = useState("prop_gmv_decline");
  const context = usePropositionContext(sessionId, propositionId);
  return (
    <Space direction="vertical" size="large" className="page">
      <SectionHeader
        title="Analysis"
        description="Read evidence closures through session state and proposition context. Runtime status remains diagnostic context only."
        action={
          <Space wrap>
            <Input
              aria-label="session id"
              value={sessionId}
              onChange={(event) => setSessionId(event.target.value)}
              style={{ width: 220 }}
            />
            <Input
              aria-label="proposition id"
              value={propositionId}
              onChange={(event) => setPropositionId(event.target.value)}
              style={{ width: 220 }}
            />
          </Space>
        }
      />
      <Tabs
        items={[
          { key: "inbox", label: "Session Inbox", children: <SessionInbox onOpen={setSessionId} /> },
          { key: "detail", label: "Session Detail", children: <SessionDetail sessionId={sessionId} onProposition={setPropositionId} /> },
          { key: "proposition", label: "Proposition Detail", children: <PropositionDetail sessionId={sessionId} propositionId={propositionId} /> },
          { key: "timeline", label: "Evidence Timeline", children: <EvidenceTimeline sessionId={sessionId} /> },
          { key: "inspector", label: "Evidence Inspector", children: <EvidenceInspector payload={context.data} /> },
          { key: "gaps", label: "Gap View", children: <GapView sessionId={sessionId} /> },
        ]}
      />
    </Space>
  );
}
