import { Empty, Typography } from "antd";

const copy: Record<string, string> = {
  source: "还没有 source。请通过 HTTP API 注册 metadata authority，然后在 UI 中检查 readiness 与 sync selections。",
  engine: "还没有 engine。请通过 HTTP API 注册 execution authority；v1 UI 不编辑 marivo.yaml inventory。",
  mapping: "还没有 mapping。source 与 engine 需要通过 /mappings 建立投影关系。",
  semantic: "还没有 semantic object。请先创建 typed semantic contract，再在 Readiness Queue 中修复依赖。",
  session: "还没有 analysis session。agent 或用户通过 typed intent 创建 session 后会出现在这里。",
  evidence: "当前 proposition 没有 evidence closure。请检查 session state、artifact materialization 或 blocking gaps。",
  jobs: "当前没有匹配 Jobs。Jobs 页面只读展示，不提供 submit、cancel、retry 控制。",
};

export function TaskEmpty({ kind }: { kind: keyof typeof copy }) {
  return (
    <Empty description={<Typography.Text type="secondary">{copy[kind]}</Typography.Text>} />
  );
}
