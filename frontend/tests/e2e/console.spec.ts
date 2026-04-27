import { expect, test } from "@playwright/test";

test("administrator can inspect a mapping blocker and route resolution", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Marivo Console")).toBeVisible();
  await page.getByRole("menuitem", { name: "Operations" }).click();
  await page.getByRole("tab", { name: "Mappings" }).click();
  await expect(page.getByText("mapping_inactive_dependency", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Routing Debugger" }).click();
  await page.getByRole("button", { name: "Resolve Route" }).click();
  await expect(
    page.getByText("Selected highest-priority ready mapping covering all requested tables.", { exact: true }),
  ).toBeVisible();
});

test("administrator can create edit and delete a mapping", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("menuitem", { name: "Operations" }).click();
  await page.getByRole("tab", { name: "Mappings" }).click();

  await page.getByRole("button", { name: "New Mapping" }).click();
  await page.getByLabel("Source").click();
  await page.getByText("Sales DuckDB Authority").click();
  await page.getByLabel("Engine").click();
  await page.getByText("DuckDB Runtime").click();
  await page.getByLabel("Priority").fill("30");
  await page.getByLabel("Authority catalog").fill("main");
  await page.getByLabel("Execution catalog").fill("main");
  await page.getByLabel("Default schema").fill("analytics");
  await page.getByRole("button", { name: "Save Mapping" }).click();

  const createdRow = page.locator("tr", { hasText: /map_[0-9a-f]{8}/ }).first();
  await expect(createdRow).toBeVisible();
  await createdRow.getByRole("button", { name: "Edit" }).click();
  await page.getByLabel("Priority").fill("31");
  await page.getByRole("button", { name: "Save Mapping" }).click();

  await expect(createdRow).toBeVisible();
  await createdRow.getByRole("button", { name: "Delete" }).click();
  await page.locator(".ant-popconfirm").getByRole("button", { name: "Delete" }).click();
  await expect(createdRow).toBeHidden();
});

test("can read proposition context", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("menuitem", { name: "Analysis" }).click();
  await page.getByRole("tab", { name: "Proposition Detail" }).click();
  await expect(page.getByText("Latest Assessment")).toBeVisible();
  await expect(page.getByText("Relevant Findings")).toBeVisible();
});
