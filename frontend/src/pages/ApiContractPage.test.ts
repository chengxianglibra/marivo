import { describe, expect, it } from "vitest";
import { operationLabel } from "./apiContractRows";

describe("API contract operation labels", () => {
  it("renders string operations directly", () => {
    expect(operationLabel("get")).toBe("get");
  });

  it("renders OpenAPI index operation objects by method", () => {
    expect(operationLabel({ method: "post", operation_id: "create_session", summary: "Create Session" })).toBe(
      "POST",
    );
  });
});
