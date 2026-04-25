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
          诊断抽屉展示 API 摘要、request id、failure_code、blocker 和 runtime detail。主页面仍保留任务摘要。
        </Typography.Paragraph>
        <CopyJsonButton payload={payload} />
        <JsonPreview payload={payload} />
      </Space>
    </Drawer>
  );
}
