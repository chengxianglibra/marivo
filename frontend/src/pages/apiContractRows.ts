export function operationLabel(operation: unknown): string {
  if (typeof operation === "string") return operation;
  if (operation && typeof operation === "object") {
    const record = operation as { method?: unknown; operation_id?: unknown; summary?: unknown };
    if (typeof record.method === "string") return record.method.toUpperCase();
    if (typeof record.operation_id === "string") return record.operation_id;
    if (typeof record.summary === "string") return record.summary;
  }
  return "unknown";
}
