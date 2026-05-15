import { describe, expect, it } from "vitest";
import { sessionGoalText, sessionLifecycleStatus } from "./analysisRows";

describe("analysis session row helpers", () => {
  it("reads canonical session goal and lifecycle fields", () => {
    const row = {
      goal: { question: "Analyze DAU" },
      lifecycle: { status: "closed" },
    };

    expect(sessionGoalText(row)).toBe("Analyze DAU");
    expect(sessionLifecycleStatus(row)).toBe("closed");
  });
});
