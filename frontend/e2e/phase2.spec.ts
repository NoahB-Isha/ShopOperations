/**
 * Phase-2 acceptance flows, end to end through the real UI:
 *
 *   1. floor places a request (the Odoo draft renders immediately — honestly
 *      simulated while writes are gated) → warehouse marks it seen, then
 *      staged → floor closes it by hand (with writes gated there is no Odoo
 *      picking to pull from, no pallet to declare and nothing to scan)
 *   2. admin curates a catalog (no quantities), grants it to Zone 1 → the
 *      coordinator opens it to one of their centers
 *   3. the restock checklists render for the floor role
 *
 * Requires the stack up and seeded (make dev && make seed). The write
 * feature flags must be OFF (their shipped state) — these tests never
 * create real Odoo records; they assert the honest "simulated" labels.
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

async function signOut(page: Page) {
  await page.getByRole("button", { name: /sign out/i }).click();
  await expect(page).toHaveURL(/login/);
}

test("transfer flow: place → seen by warehouse → staged → received", async ({ page }) => {
  test.setTimeout(90_000);

  // ---- floor: place the request
  await login(page, "floor@demo.ishalife.test");
  await page.goto("/transfer-requests/new");
  await page.getByLabel("Search products").fill("co");
  const firstResult = page.locator("ul li button").first();
  await expect(firstResult).toBeVisible();
  await firstResult.click();
  await page.getByLabel(/^Quantity for/).fill("10");
  await page.getByRole("button", { name: /Send request/ }).click();
  await expect(page).toHaveURL(/transfer-requests\/\d+/);
  const requestId = page.url().split("/").pop()!;

  // the Odoo linkage is front and center — honestly simulated with flags off
  await expect(page.getByLabel("Status: Requested")).toBeVisible();
  await expect(page.getByText(/simulated/).first()).toBeVisible();
  await signOut(page);

  // ---- warehouse: seen → staged (the pallet is what gets counted now, and
  // with writes gated there's no pallet to declare)
  await login(page, "warehouse@demo.ishalife.test");
  await page.goto(`/transfer-requests/${requestId}`);
  await page.getByRole("button", { name: "I've seen it" }).click();
  await expect(page.getByLabel("Status: Seen by warehouse")).toBeVisible();
  await page.getByRole("button", { name: "Mark staged" }).click();
  await expect(page.getByLabel("Status: Staged")).toBeVisible();
  await signOut(page);

  // ---- floor: nothing live in Odoo (writes gated) -> manual close
  await login(page, "floor@demo.ishalife.test");
  await page.goto(`/transfer-requests/${requestId}`);
  await page.getByRole("button", { name: "Mark done" }).click();
  await expect(page.getByLabel("Status: Received")).toBeVisible();
  // counted taken as sent -> no invented discrepancies
  await expect(page.getByRole("columnheader", { name: "Counted" })).toBeVisible();
});

test("live board updates without a refresh", async ({ page, browser }) => {
  test.setTimeout(90_000);
  // warehouse watches the board…
  await login(page, "warehouse@demo.ishalife.test");
  await page.goto("/transfer-requests");
  await expect(page.getByText("live")).toBeVisible();

  // …while the floor places a request in another session
  const floorCtx = await browser.newContext();
  const floorPage = await floorCtx.newPage();
  await login(floorPage, "floor@demo.ishalife.test");
  await floorPage.goto("/transfer-requests/new");
  await floorPage.getByLabel("Search products").fill("in");
  const firstResult = floorPage.locator("ul li button").first();
  await expect(firstResult).toBeVisible();
  await firstResult.click();
  await floorPage.getByRole("button", { name: /Send request/ }).click();
  await expect(floorPage).toHaveURL(/transfer-requests\/\d+/);
  const requestId = floorPage.url().split("/").pop()!;
  await floorCtx.close();

  // the warehouse board picks it up on its own (poll interval is 4s)
  await expect(
    page.locator("tbody tr", { hasText: `#${requestId}` }).first(),
  ).toBeVisible({ timeout: 15_000 });

  // tidy up: cancel it so the demo board stays clean
  await page.goto(`/transfer-requests/${requestId}`);
  await page.getByRole("button", { name: "Cancel request" }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Cancel request" })
    .click();
  await expect(page.getByText("Cancelled").first()).toBeVisible();
});

test("catalog: admin grants a list to Zone 1, coordinator opens it to a center", async ({
  page,
}) => {
  test.setTimeout(90_000);
  const listName = `E2E catalog ${Date.now()}`;

  // ---- admin: create the catalog (products only — no quantities anywhere)
  await login(page, "admin@demo.ishalife.test");
  await page.goto("/orders");
  await page.getByRole("button", { name: "New Catalog" }).first().click();
  await page.getByLabel("Name").fill(listName);
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await expect(page).toHaveURL(/orders\/\d+/);

  await page.getByLabel("Search products").fill("co");
  const firstResult = page.locator("ul li button").first();
  await expect(firstResult).toBeVisible();
  await firstResult.click();
  await page.getByRole("button", { name: "Save products" }).click();
  await expect(page.getByRole("button", { name: "Save products" })).toHaveCount(0);
  await expect(page.getByLabel(/^Quantity for/)).toHaveCount(0); // it's a menu

  // grant to Zone 1 via the pill toggle
  const zonePill = page.getByRole("button", { name: /Zone 1/ });
  const granted = page.waitForResponse(
    (r) => r.url().includes("/zones") && r.request().method() === "PUT",
  );
  await zonePill.click();
  expect((await granted).ok()).toBeTruthy();
  await expect(page.getByRole("button", { name: /✓ Zone 1/ })).toBeVisible();
  await signOut(page);

  // ---- coordinator: the list is there; open it to a center
  await login(page, "coordinator@demo.ishalife.test");
  await page.goto("/my-order-lists");
  const card = page
    .locator("div", { hasText: listName })
    .getByRole("button", { name: "Choose centers" })
    .first();
  await card.click();
  const dialog = page.getByRole("dialog");
  const centerPill = dialog.locator('button[aria-pressed="false"]').first();
  await expect(centerPill).toBeVisible();
  const centerName = (await centerPill.textContent())?.trim() ?? "";
  const saved = page.waitForResponse(
    (r) => r.url().includes("/centers") && r.request().method() === "PUT",
  );
  await centerPill.click();
  expect((await saved).ok()).toBeTruthy();
  await expect(dialog.getByRole("button", { name: `✓ ${centerName}` })).toBeVisible();
});

test("restock checklists render for the floor role", async ({ page }) => {
  await login(page, "floor@demo.ishalife.test");
  await page.goto("/restock");
  await expect(page.getByRole("button", { name: /From warehouse/ })).toBeVisible();
  const anyRow = page.locator('[role="checkbox"]').first();
  const empty = page.getByText("Shelves are happy");
  await expect(anyRow.or(empty).first()).toBeVisible();
});
