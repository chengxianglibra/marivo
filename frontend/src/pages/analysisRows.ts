import type { JsonRecord } from "../api/types";

export function sessionGoalText(row: JsonRecord): string {
  if (row.goal && typeof row.goal === "object") {
    return (row.goal as JsonRecord).question ?? "";
  }
  return "";
}

export function sessionLifecycleStatus(row: JsonRecord): string | undefined {
  if (row.lifecycle && typeof row.lifecycle === "object") {
    return (row.lifecycle as JsonRecord).status as string | undefined;
  }
  return undefined;
}
