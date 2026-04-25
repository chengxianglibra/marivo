import { Button, Layout, Menu, Select, Space, Tag, Tooltip, Typography } from "antd";
import type { MenuProps } from "antd";
import type { ReactNode } from "react";
import { useMemo, useState } from "react";
import {
  Activity,
  BookOpenCheck,
  DatabaseZap,
  FileJson,
  LayoutDashboard,
  PanelLeftClose,
  PanelLeftOpen,
  ShieldCheck,
} from "lucide-react";
import type { PageKey, RoleKey } from "./api/types";
import { AnalysisPage } from "./pages/AnalysisPage";
import { ApiContractPage } from "./pages/ApiContractPage";
import { OperationsPage } from "./pages/OperationsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { SemanticLayerPage } from "./pages/SemanticLayerPage";
import { apiConfig } from "./api/config";

const { Header, Sider, Content } = Layout;

const roleOptions: Array<{ value: RoleKey; label: string; defaultPage: PageKey }> = [
  { value: "admin", label: "管理员", defaultPage: "overview" },
  { value: "semantic", label: "业务专家", defaultPage: "semantic" },
  { value: "analyst", label: "分析人员", defaultPage: "analysis" },
];

const pageMeta: Record<PageKey, { label: string; icon: ReactNode; role: RoleKey | "shared" }> = {
  overview: { label: "Overview", icon: <LayoutDashboard size={18} />, role: "admin" },
  operations: { label: "Operations", icon: <DatabaseZap size={18} />, role: "admin" },
  semantic: { label: "Semantic Layer", icon: <BookOpenCheck size={18} />, role: "semantic" },
  analysis: { label: "Analysis", icon: <Activity size={18} />, role: "analyst" },
  "api-contract": { label: "API Contract", icon: <FileJson size={18} />, role: "shared" },
};

function roleWeight(role: RoleKey, page: PageKey): number {
  const pageRole = pageMeta[page].role;
  if (pageRole === role) return 0;
  if (pageRole === "shared") return 1;
  return 2;
}

function useMenuItems(role: RoleKey): MenuProps["items"] {
  return useMemo(() => {
    return (Object.keys(pageMeta) as PageKey[])
      .sort((a, b) => roleWeight(role, a) - roleWeight(role, b))
      .map((key) => ({
        key,
        icon: pageMeta[key].icon,
        label: pageMeta[key].label,
      }));
  }, [role]);
}

function renderPage(page: PageKey) {
  switch (page) {
    case "overview":
      return <OverviewPage />;
    case "operations":
      return <OperationsPage />;
    case "semantic":
      return <SemanticLayerPage />;
    case "analysis":
      return <AnalysisPage />;
    case "api-contract":
      return <ApiContractPage />;
  }
}

export default function App() {
  const [role, setRole] = useState<RoleKey>("admin");
  const [page, setPage] = useState<PageKey>("overview");
  const [collapsed, setCollapsed] = useState(false);
  const menuItems = useMenuItems(role);

  return (
    <Layout className="app-frame">
      <Sider width={248} collapsed={collapsed} breakpoint="lg" collapsedWidth={72}>
        <div className="brand">
          <ShieldCheck size={24} />
          {!collapsed ? (
            <div>
              <Typography.Text className="brand-title">Marivo Console</Typography.Text>
              <Typography.Text className="brand-subtitle">HTTP-only UI v1</Typography.Text>
            </div>
          ) : null}
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[page]}
          items={menuItems}
          onClick={({ key }) => setPage(key as PageKey)}
        />
      </Sider>
      <Layout>
        <Header className="app-header">
          <Space wrap className="header-left">
            <Tooltip title={collapsed ? "Expand navigation" : "Collapse navigation"}>
              <Button
                aria-label="toggle navigation"
                icon={collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
                onClick={() => setCollapsed((value) => !value)}
              />
            </Tooltip>
            <Select
              aria-label="role view"
              data-testid="role-view-select"
              value={role}
              style={{ width: 150 }}
              options={roleOptions}
              onChange={(nextRole) => {
                setRole(nextRole);
                setPage(roleOptions.find((option) => option.value === nextRole)?.defaultPage ?? "overview");
              }}
            />
            <Tag color={apiConfig.useMocks ? "warning" : "success"}>
              {apiConfig.useMocks ? "mock fixtures" : "live HTTP API"}
            </Tag>
          </Space>
          <Typography.Text type="secondary" className="api-base">
            API: {apiConfig.baseUrl}
          </Typography.Text>
        </Header>
        <Content className="app-content">
          {renderPage(page)}
        </Content>
      </Layout>
    </Layout>
  );
}
