/**
 * Smoke tests for the critical foundation flows. Requires the full stack up
 * and seeded:  make dev && make seed && make e2e
 */
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

async function login(page: Page, email: string) {
  await page.goto("/login");
  await page.getByPlaceholder(/you@example/).fill(email);
  await page.getByRole("button", { name: "Send code" }).click();
  // dev mode surfaces the code and pre-fills it
  await expect(page.getByTestId("dev-code")).toBeVisible();
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/login/);
}

test("admin, warehouse, and orderer see different navs", async ({ page }) => {
  // The nav is the role boundary made visible, so it is asserted on what each
  // role SHOULD reach. Admin-only pages that left the menu for Settings (Users,
  // Dev Tools) and the design pages (Styleguide, Palette lab) are deliberately
  // not here — see the settings test below for those.
  await login(page, "admin@demo.ishalife.test");
  await expect(page.getByRole("navigation").first()).toContainText("Purchasing");
  await expect(page.getByRole("navigation").first()).toContainText("Centers");
  await page.getByRole("button", { name: /sign out/i }).click();

  // the warehouse menu is deliberately slim since 2026-08-17 — they work
  // in Odoo, so the app gives them the delivery form and product lookup. The
  // pages that left the MENU (Incoming, Transfers, Coming soon) kept their
  // routes. (Adjustments left the app 2026-08-24; Out of stock 2026-09-01.)
  await login(page, "warehouse@demo.ishalife.test");
  await expect(page.getByRole("navigation").first()).toContainText("Send to floor");
  await expect(page.getByRole("navigation").first()).toContainText("Search Inventory");
  await expect(page.getByRole("navigation").first()).not.toContainText("Purchasing");
  await expect(page.getByRole("navigation").first()).not.toContainText("Incoming");
  // …and the route still opens for them
  await page.goto("/incoming");
  await expect(page.getByRole("heading", { name: "Incoming" })).toBeVisible();
  await page.getByRole("button", { name: /sign out/i }).click();

  await login(page, "orderer@demo.ishalife.test");
  await expect(page.getByRole("navigation").first()).toContainText("Place an order");
  await expect(page.getByRole("navigation").first()).not.toContainText("Search Inventory");
  await expect(page.getByRole("navigation").first()).not.toContainText("Catalogs");
});

test("settings holds the admin pages that left the menu", async ({ page }) => {
  await login(page, "admin@demo.ishalife.test");
  await page.goto("/settings");
  // account first, then the pages that moved here
  await expect(page.getByText("Signed in as")).toBeVisible();
  await expect(page.getByRole("link", { name: /Users/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /Dev Tools/ })).toBeVisible();

  // a non-admin gets settings too, without the admin pages
  await page.getByRole("button", { name: /sign out/i }).click();
  await login(page, "orderer@demo.ishalife.test");
  await page.goto("/settings");
  await expect(page.getByText("Signed in as")).toBeVisible();
  await expect(page.getByRole("link", { name: /Dev Tools/ })).toHaveCount(0);
});

test("catalog live search stays smooth at 1,200 products", async ({ page }) => {
  await login(page, "admin@demo.ishalife.test");
  await page.goto("/catalog");
  await expect(page.getByText(/active products/)).toBeVisible();

  const search = page.getByPlaceholder("Search products…");
  await search.fill("copper");
  await expect(page.locator("tbody tr").first()).toContainText(/copper/i, { timeout: 5000 });

  await search.fill("zzzznope");
  await expect(page.getByText(/No products match/)).toBeVisible();
});

test("product drawer shows stock and the tag editor", async ({ page }) => {
  await login(page, "admin@demo.ishalife.test");
  await page.goto("/catalog");
  // similar names collapse into group rows (click = expand); open a product row
  await page.locator("tbody tr").filter({ hasNotText: "variants" }).first().click();
  await expect(page.getByText("On hand")).toBeVisible();
  await expect(page.getByText("App tags")).toBeVisible();
});

test("styleguide renders every section", async ({ page }) => {
  await login(page, "admin@demo.ishalife.test");
  await page.goto("/styleguide");
  for (const section of ["Color roles", "Type", "Buttons", "Chips & status", "Forms", "Data table", "Empty state"]) {
    await expect(page.getByRole("heading", { name: section })).toBeVisible();
  }
});

test("public health is a thin liveness probe and leaks no posture", async ({ request }) => {
  // The detailed payload used to be anonymous, which told any caller whether
  // this stack was on live Odoo with writes enabled. It moved to /health/detail.
  const health = await (await request.get("/api/v1/health")).json();
  expect(health.status).toBe("ok");
  expect(health.db).toBe(true);
  expect(health.odoo_mode).toBeUndefined();
  expect(health.writes_enabled).toBeUndefined();
  expect(health.sync).toBeUndefined();
});

test("detailed health needs a session and reports mode and staleness honestly", async ({
  request,
}) => {
  expect((await request.get("/api/v1/health/detail")).status()).toBe(401);

  const { dev_code } = await (
    await request.post("/api/v1/auth/request-code", {
      data: { identifier: "admin@demo.ishalife.test" },
    })
  ).json();
  const { token } = await (
    await request.post("/api/v1/auth/verify", {
      data: { identifier: "admin@demo.ishalife.test", code: dev_code },
    })
  ).json();

  const health = await (
    await request.get("/api/v1/health/detail", {
      headers: { Authorization: `Bearer ${token}` },
    })
  ).json();
  // Works against fixture-mode demos AND live-credential dev stacks: what we
  // assert is the honest-reporting contract, not which mode we're in.
  expect(["fixture", "live"]).toContain(health.odoo_mode);
  expect(typeof health.writes_enabled).toBe("boolean");
  for (const domain of ["products", "stock", "sales", "incoming"]) {
    expect(health.sync[domain]).toBeDefined();
    expect(typeof health.sync[domain].stale).toBe("boolean");
  }
});
