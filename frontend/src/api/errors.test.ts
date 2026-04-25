import { describe, expect, it } from "vitest";
import { classifyError, errorActionText } from "./errors";

describe("error taxonomy", () => {
  it("classifies validation, readiness and runtime errors", () => {
    expect(classifyError(422, "validation failed")).toBe("validation");
    expect(classifyError(409, "semantic object not_ready")).toBe("not_ready");
    expect(classifyError(409, "runtime blocked")).toBe("runtime_blocked");
  });

  it("maps every known kind to an action", () => {
    expect(errorActionText("network")).toContain("VITE_MARIVO_API_BASE_URL");
    expect(errorActionText("permission_placeholder")).toContain("RBAC");
  });
});
