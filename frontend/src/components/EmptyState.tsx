import { Empty, Typography } from "antd";

const copy: Record<string, string> = {
  datasource: "No datasources yet. Register a datasource through the HTTP API, then inspect readiness and browse catalogs here.",
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
