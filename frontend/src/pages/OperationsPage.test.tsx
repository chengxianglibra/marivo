import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ConfigProvider } from "antd";
import { describe, expect, it } from "vitest";
import { apiConfig } from "../api/config";
import { OperationsPage } from "./OperationsPage";

function renderOperations() {
  apiConfig.useMocks = true;
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <ConfigProvider>
      <QueryClientProvider client={client}>
        <OperationsPage />
      </QueryClientProvider>
    </ConfigProvider>,
  );
}

describe("operations inventory CRUD", () => {
  it("shows create action for datasources", async () => {
    renderOperations();

    expect(await screen.findByRole("button", { name: /New Datasource/i })).toBeInTheDocument();
  });

  it("does not show engine or mapping tabs", async () => {
    renderOperations();
    await screen.findByRole("tab", { name: "Datasources" });
    expect(screen.queryByRole("tab", { name: "Engines" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Mappings" })).not.toBeInTheDocument();
  });

  it("opens the datasource create drawer with structured and JSON fields", async () => {
    renderOperations();

    fireEvent.click(await screen.findByRole("button", { name: /New Datasource/i }));
    await screen.findAllByText("New Datasource");
    const dialog = document.querySelector(".ant-drawer") as HTMLElement;

    expect(within(dialog).getByLabelText("Display name")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("DuckDB connection JSON")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Save Datasource" })).toBeInTheDocument();
  });

  it("does not show removed source sync controls", async () => {
    renderOperations();

    await screen.findByRole("button", { name: /New Datasource/i });
    expect(screen.queryByText(/Sync mode/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Synced Objects/i })).not.toBeInTheDocument();
  });
});
