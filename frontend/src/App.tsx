import { Button, Layout, Menu, Space, Tag, Tooltip, Typography } from "antd";
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
import type { PageKey } from "./api/types";
import { AnalysisPage } from "./pages/AnalysisPage";
import { ApiContractPage } from "./pages/ApiContractPage";
import { OperationsPage } from "./pages/OperationsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { SemanticLayerPage } from "./pages/SemanticLayerPage";
import { apiConfig } from "./api/config";

const { Header, Sider, Content } = Layout;

const pageMeta: Record<PageKey, { label: string; icon: ReactNode }> = {
  overview: { label: "Overview", icon: <LayoutDashboard size={18} /> },
  operations: { label: "Operations", icon: <DatabaseZap size={18} /> },
  semantic: { label: "Semantic Layer", icon: <BookOpenCheck size={18} /> },
  analysis: { label: "Analysis", icon: <Activity size={18} /> },
  "api-contract": { label: "API Contract", icon: <FileJson size={18} /> },
};

const pageOrder: PageKey[] = ["overview", "operations", "semantic", "analysis", "api-contract"];

function useMenuItems(): MenuProps["items"] {
  return useMemo(() => {
    return pageOrder.map((key) => ({
      key,
      icon: pageMeta[key].icon,
      label: pageMeta[key].label,
    }));
  }, []);
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
  const [page, setPage] = useState<PageKey>("overview");
  const [collapsed, setCollapsed] = useState(false);
  const menuItems = useMenuItems();

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
