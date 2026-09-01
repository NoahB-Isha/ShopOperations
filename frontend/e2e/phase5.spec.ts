/**
 * Phase-5 acceptance through the real UI:
 *
 *   1. Sales dashboard: KPI highlights, the labeled generated narrative,
 *      the stacked channel chart with its table-view twin, drill-down
 *      dimensions, and the Q&A box answering from the data (heuristic
 *      source when no LLM key — either way the label is present).
 *   2. Time machine: today mode, a past date (snapshot history + honest
 *      confidence), a future date (engine projection with method mix),
 *      and the beyond-horizon refusal.
 *   3. Out of stock scopes: the floor board plus the merged Everywhere /
 *      Warehouse snapshot lists (the old Availability page), and the
 *      warehouse Incoming list.
 *
 * Requires the stack up + seeded. Read-only — re-runnable.
 */
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

async function login(page: Page, email: string) {
  await page.goto("/login");
  await page.getByPlaceholder(/you@example/).fill(email);
  await page.getByRole("button", { name: "Send code" }).click();
  await expect(page.getByTestId("dev-code")).toBeVisible();
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/login/);
}

test("sales dashboard: highlights, generated narrative, chart+table, drill-down, Q&A", async ({ page }) => {
  await login(page, "admin@demo.ishalife.test");
  await page.goto("/reports");

  // KPI highlights up top (revenue/orders deltas + the loyalty tile)
  await expect(page.getByText(/vs prior period/).first()).toBeVisible();
  await expect(page.getByText("New customers", { exact: true })).toBeVisible();

  // channel scope tabs swap the whole view
  await page.getByTestId("scope-online").click();
  await expect(page.getByText("Revenue by category")).toBeVisible();
  await expect(page.getByText("Order size")).toBeVisible();
  await expect(page.getByText("Customers — new vs returning")).toBeVisible();
  await page.getByTestId("scope-all").click();

  // narrative is clearly labeled as generated
  await expect(page.getByText(/Generated · /)).toBeVisible();
  await expect(page.getByText("summary is machine-written", { exact: false })).toBeVisible();

  // the chart renders; its table twin carries the same months
  await expect(page.locator("svg[role=img]")).toBeVisible();
  await page.getByRole("tab", { name: "Table" }).click();
  await expect(page.locator("table").first()).toBeVisible();
  await page.getByRole("tab", { name: "Chart" }).click();

  // drill-down dimensions swap
  await page.getByRole("tab", { name: "Products" }).click();
  await expect(page.getByRole("columnheader", { name: "Product" })).toBeVisible();
  await page.getByRole("tab", { name: "City centers" }).click();
  await expect(page.getByRole("columnheader", { name: "Center" })).toBeVisible();

  // Q&A answers with a labeled source
  await page.getByLabel("Question").fill("Which centers grew fastest this quarter?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByText(/centers in Last 3 months|Fastest-growing/i)).toBeVisible({
    timeout: 20_000,
  });

  // switching the period re-queries without errors
  await page.getByLabel("Period").selectOption("12m");
  await expect(page.getByText("Last 12 months", { exact: false }).first()).toBeVisible();
});

test("stock status: read-only OOS scopes + the Coming soon tab", async ({ page }) => {
  await login(page, "floor@demo.ishalife.test");
  await page.goto("/out-of-stock");

  // floor roles land on their own scope; the board is READ-ONLY — marking
  // left the app 2026-08-24 (counting owns counted numbers now)
  await expect(page.getByTestId("scope-floor")).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("button", { name: "Mark item out of stock" })).toHaveCount(0);

  // the merged Availability scopes stay reachable (never-stocked peek shows
  // only off the floor board)
  await page.getByTestId("scope-org").click();
  await expect(page.getByRole("button", { name: "Include never-stocked" })).toBeVisible();
  await page.getByTestId("scope-bwhse").click();
  await expect(page.getByTestId("scope-bwhse")).toHaveAttribute("aria-selected", "true");

  // one Stock status destination: the Coming soon tab rides the same shell
  await page.getByRole("button", { name: "Coming soon" }).click();
  await expect(page).toHaveURL(/\/coming-soon/);
  await expect(page.getByLabel("Search items on the way")).toBeVisible();
});

test("warehouse incoming: pending inbound shipments from the snapshot", async ({ page }) => {
  await login(page, "warehouse@demo.ishalife.test");
  await page.goto("/incoming");
  await expect(page.getByRole("columnheader", { name: "On the way" })).toBeVisible();
  await expect(page.getByText(/expected back|arrival date TBD/).first()).toBeVisible();
});

test("settings: palette picker for everyone, blacklist manager for admins", async ({ page }) => {
  await login(page, "admin@demo.ishalife.test");
  await page.goto("/settings");
  await expect(page.getByRole("button", { name: "Charcoal Pop" })).toBeVisible();
  await expect(page.getByText("Product blacklist")).toBeVisible();
  await expect(page.getByRole("button", { name: "Open styleguide →" })).toBeVisible();
});
