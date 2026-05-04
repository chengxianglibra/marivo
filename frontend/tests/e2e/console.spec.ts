import { expect, test } from "@playwright/test";

test("administrator can inspect route resolution", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Marivo Console")).toBeVisible();
  await page.getByRole("menuitem", { name: "Operations" }).click();
  await expect(page.getByRole("tab", { name: "Datasources" })).toBeVisible();
  await page.getByRole("tab", { name: "Routing Debugger" }).click();
  await page.getByRole("button", { name: "Resolve Route" }).click();
  await expect(page.getByText("Selection reason")).toBeVisible({ timeout: 10000 });
});

test("administrator can create a datasource", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("menuitem", { name: "Operations" }).click();
  await expect(page.getByRole("tab", { name: "Datasources" })).toBeVisible();

  // Create a new DuckDB datasource
  await page.getByRole("button", { name: "New Datasource" }).click();
  const drawer = page.locator(".ant-drawer-open");
  await expect(drawer).toBeVisible();

  // Type defaults to duckdb — verify via the selection item label
  await expect(drawer.locator(".ant-select-selection-item")).toHaveText("duckdb");

  // Fill display name
  await drawer.locator("#display_name").fill("E2E Test DuckDB");

  // Fill connection JSON
  const connectionJson = JSON.stringify(
    { datasource_type: "duckdb", path: null, database: null, db_path: "/tmp/e2e_test.duckdb" },
    null,
    2,
  );
  await drawer.locator("textarea").fill(connectionJson);

  // Submit
  await drawer.getByRole("button", { name: "Save Datasource" }).click();

  // Wait for the drawer to close (animation: open class removed)
  await expect(page.locator(".ant-drawer-open")).toBeHidden({ timeout: 10000 });

  // Verify new datasource appears in the table
  const createdRow = page.locator("tr", { hasText: "E2E Test DuckDB" }).first();
  await expect(createdRow).toBeVisible({ timeout: 5000 });
});

test("administrator can edit a datasource", async ({ page }) => {
  // Create a datasource via API to ensure one exists
  const response = await page.request.post("http://127.0.0.1:8000/datasources", {
    data: {
      datasource_type: "duckdb",
      display_name: "Pre-seeded DuckDB",
      connection: { datasource_type: "duckdb", db_path: "/tmp/e2e_edit_test.duckdb" },
      policy: { allow_live_browse: true },
    },
  });
  expect(response.ok()).toBeTruthy();

  await page.goto("/");
  await page.getByRole("menuitem", { name: "Operations" }).click();
  await expect(page.getByRole("tab", { name: "Datasources" })).toBeVisible();

  // Find the pre-seeded row and click Edit
  const row = page.locator("tr", { hasText: "Pre-seeded DuckDB" }).first();
  await expect(row).toBeVisible({ timeout: 5000 });
  await row.getByRole("button", { name: "Edit" }).click();

  const drawer = page.locator(".ant-drawer-open");
  await expect(drawer).toBeVisible();
  await drawer.locator("#display_name").fill("Pre-seeded DuckDB Edited");
  await drawer.getByRole("button", { name: "Save Datasource" }).click();
  await expect(page.locator(".ant-drawer-open")).toBeHidden({ timeout: 10000 });

  // Verify updated name
  await expect(page.locator("text=Pre-seeded DuckDB Edited")).toBeVisible({ timeout: 5000 });
});

test("administrator can delete a datasource", async ({ page }) => {
  // Create a datasource via API
  const response = await page.request.post("http://127.0.0.1:8000/datasources", {
    data: {
      datasource_type: "duckdb",
      display_name: "To Be Deleted",
      connection: { datasource_type: "duckdb", db_path: "/tmp/e2e_delete_test.duckdb" },
      policy: { allow_live_browse: true },
    },
  });
  expect(response.ok()).toBeTruthy();

  await page.goto("/");
  await page.getByRole("menuitem", { name: "Operations" }).click();
  await expect(page.getByRole("tab", { name: "Datasources" })).toBeVisible();

  const row = page.locator("tr", { hasText: "To Be Deleted" }).first();
  await expect(row).toBeVisible({ timeout: 5000 });
  await row.getByRole("button", { name: "Delete" }).click();
  await page.locator(".ant-popconfirm").getByRole("button", { name: "Delete" }).click();
  await expect(row).toBeHidden({ timeout: 5000 });
});

test("can navigate to analysis proposition detail", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("menuitem", { name: "Analysis" }).click();
  await page.getByRole("tab", { name: "Proposition Detail" }).click();
  await expect(page.getByLabel("Proposition Detail").getByText("session_id")).toBeVisible();
});
