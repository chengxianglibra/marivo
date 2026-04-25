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

test("analyst can read proposition context", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("role-view-select").click();
  await page.getByTitle("分析人员").click();
  await page.getByRole("tab", { name: "Proposition Detail" }).click();
  await expect(page.getByText("Latest Assessment")).toBeVisible();
  await expect(page.getByText("Relevant Findings")).toBeVisible();
});
