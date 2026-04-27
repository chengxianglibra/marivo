import { Empty, Typography } from "antd";

const copy: Record<string, string> = {
  source: "No sources yet. Register a metadata authority through the HTTP API, then inspect readiness and sync selections here.",
  sourceObject: "No synced source objects yet. Configure sync selections and trigger source sync first.",
  engine: "No engines yet. Register an execution authority through the HTTP API; v1 UI does not edit marivo.yaml inventory.",
  mapping: "No mappings yet. Sources and engines need an explicit projection through /mappings.",
  semantic: "No semantic objects yet. Create typed semantic contracts first, then fix dependencies in the Readiness Queue.",
  session: "No analysis sessions yet. Sessions created by agents or users through typed intents will appear here.",
  evidence: "This proposition has no evidence closure yet. Check session state, artifact materialization, or blocking gaps.",
  jobs: "No matching Jobs. The Jobs page is read-only and does not expose submit, cancel, or retry controls.",
};

export function TaskEmpty({ kind }: { kind: keyof typeof copy }) {
  return (
    <Empty description={<Typography.Text type="secondary">{copy[kind]}</Typography.Text>} />
  );
}
