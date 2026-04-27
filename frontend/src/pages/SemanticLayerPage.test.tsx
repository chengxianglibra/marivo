import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { ConfigProvider } from "antd";
import { describe, expect, it, vi } from "vitest";
import { SemanticLayerPage } from "./SemanticLayerPage";

vi.mock("../api/hooks", () => {
  const semanticKinds = [
    { key: "entities", label: "Entities", path: "/semantic/entities" },
    { key: "metrics", label: "Metrics", path: "/semantic/metrics" },
  ];

  return {
    semanticKinds,
    useSemanticAction: () => ({ mutate: vi.fn(), isPending: false, data: null }),
    useSemanticList: (kind: (typeof semanticKinds)[number]) => ({
      data:
        kind.key === "entities"
          ? [
              {
                entity_contract_id: "entc_customer",
                header: { entity_ref: "entity.customer", display_name: "Customer" },
                lifecycle_status: "active",
                readiness_status: "ready",
                capabilities: { lookup: true, aggregate: { supported: true }, dimension_policy: "all" },
                dependency_refs: { primary: "binding.customer_profile" },
              },
            ]
          : [],
    }),
    useSources: () => ({ data: [] }),
  };
});

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <ConfigProvider>
      <QueryClientProvider client={client}>
        <SemanticLayerPage />
      </QueryClientProvider>
    </ConfigProvider>,
  );
}

describe("SemanticLayerPage", () => {
  it("renders object-shaped capability and dependency payloads", async () => {
    renderPage();

    await waitFor(() => expect(screen.getByText("entity.customer")).toBeInTheDocument());
    expect(screen.getByText("Customer")).toBeInTheDocument();
    expect(screen.getByText("lookup, dimension_policy: all")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "1" })).toBeInTheDocument();
  });
});
