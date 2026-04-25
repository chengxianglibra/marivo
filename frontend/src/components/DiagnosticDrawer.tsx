import { Drawer, Space, Typography } from "antd";
import { CopyJsonButton, JsonPreview } from "./StatusBadge";

export function DiagnosticDrawer({
  open,
  onClose,
  title,
  payload,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  payload: unknown;
}) {
  return (
    <Drawer title={title} open={open} onClose={onClose} width={560}>
      <Space direction="vertical" size="middle" className="full-width">
        <Typography.Paragraph type="secondary">
          The diagnostic drawer shows API summaries, request ids, failure codes, blockers, and runtime details. The main page keeps the task-oriented summary.
        </Typography.Paragraph>
        <CopyJsonButton payload={payload} />
        <JsonPreview payload={payload} />
      </Space>
    </Drawer>
  );
}
