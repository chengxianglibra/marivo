import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ConfigProvider } from "antd";
import { describe, expect, it } from "vitest";
import { OperationsPage } from "./OperationsPage";

function renderOperations() {
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
  it("shows create actions for sources, engines, and mappings", async () => {
    renderOperations();

    expect(await screen.findByRole("button", { name: /New Source/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Engines" }));
    expect(await screen.findByRole("button", { name: /New Engine/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Mappings" }));
    expect(await screen.findByRole("button", { name: /New Mapping/i })).toBeInTheDocument();
  });

  it("opens the source create drawer with structured and JSON fields", async () => {
    renderOperations();

    fireEvent.click(await screen.findByRole("button", { name: /New Source/i }));
    await screen.findAllByText("New Source");
    const dialog = document.querySelector(".ant-drawer") as HTMLElement;

    expect(within(dialog).getByLabelText("Display name")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("DuckDB connection JSON")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Save Source" })).toBeInTheDocument();
  });

  it("shows source sync controls", async () => {
    renderOperations();

    expect(await screen.findByRole("button", { name: /Manage Selections/i })).toBeInTheDocument();
  });

  it("opens mapping drawer with catalog row controls", async () => {
    renderOperations();

    fireEvent.click(screen.getByRole("tab", { name: "Mappings" }));
    const newMappingButton = await screen.findByRole("button", { name: /New Mapping/i });
    fireEvent.click(newMappingButton);

    expect(await screen.findAllByText("New Mapping")).toHaveLength(2);
    expect(screen.getByRole("button", { name: /Add Catalog Mapping/i })).toBeInTheDocument();
  });
});
