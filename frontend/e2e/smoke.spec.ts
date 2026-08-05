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
  await login(page, "admin@demo.ishalife.test");
  await expect(page.getByRole("navigation")).toContainText("Users");
  await expect(page.getByRole("navigation")).toContainText("Styleguide");
  await page.getByRole("button", { name: /sign out/i }).click();

  await login(page, "warehouse@demo.ishalife.test");
  await expect(page.getByRole("navigation")).toContainText("Incoming");
  await expect(page.getByRole("navigation")).not.toContainText("Users");
  await page.getByRole("button", { name: /sign out/i }).click();

  await login(page, "orderer@demo.ishalife.test");
  await expect(page.getByRole("navigation")).toContainText("Place an order");
  await expect(page.getByRole("navigation")).not.toContainText("All SKUs");
  await expect(page.getByRole("navigation")).not.toContainText("Catalogs");
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
