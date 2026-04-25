import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { ConfigProvider } from "antd";
import { describe, expect, it } from "vitest";
import App from "./App";

function renderApp() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <ConfigProvider>
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>
    </ConfigProvider>,
  );
}

describe("app shell", () => {
  it("renders the console shell and overview data", async () => {
    renderApp();
    expect(screen.getByText("Marivo Console")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Operations Overview")).toBeInTheDocument());
    expect(screen.getByText("Readiness Blockers")).toBeInTheDocument();
  });
});
