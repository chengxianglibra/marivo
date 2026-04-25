import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BlockerPanel, StatusBadge } from "./StatusBadge";

describe("status rendering", () => {
  it("renders readiness values deterministically", () => {
    render(<StatusBadge label="readiness" value="not_ready" />);
    expect(screen.getByText("readiness: not_ready")).toBeInTheDocument();
  });

  it("renders blocker requirements", () => {
    render(
      <BlockerPanel
        record={{
          readiness_status: "not_ready",
          failure_code: "mapping_incomplete",
          blocking_requirements: ["add catalog mapping"],
        }}
      />,
    );
    expect(screen.getByText("Current blocker: mapping_incomplete")).toBeInTheDocument();
    expect(screen.getByText("add catalog mapping")).toBeInTheDocument();
  });
});
