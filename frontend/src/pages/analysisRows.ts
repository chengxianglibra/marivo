import type { JsonRecord } from "../api/types";

export function sessionGoalText(row: JsonRecord): string {
  if (typeof row.goal === "string") return row.goal;
  if (row.goal && typeof row.goal === "object") {
    const goal = row.goal as JsonRecord;
    if (typeof goal.question === "string") return goal.question;
  }
  return "";
}

export function sessionLifecycleStatus(row: JsonRecord): string | undefined {
  if (typeof row.lifecycle_status === "string") return row.lifecycle_status;
  if (typeof row.status === "string") return row.status;
  if (row.lifecycle && typeof row.lifecycle === "object") {
    const lifecycle = row.lifecycle as JsonRecord;
    if (typeof lifecycle.status === "string") return lifecycle.status;
  }
  return undefined;
}
