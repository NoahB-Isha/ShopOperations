/**
 * Phase-2 acceptance flows, end to end through the real UI:
 *
 *   1. floor requests 10 → warehouse fulfills 9 → staging count finds 8
 *      → the discrepancy appears in the warehouse adjustments queue
 *   2. admin builds an order list → assigns it to Zone 1 → the coordinator
 *      approves → the write outcome is shown honestly with its reference
 *   3. the restock checklists render for the floor role
 *
 * Requires the stack up and seeded (make dev && make seed). The
 * write_create_internal_transfer feature flag must be OFF (its shipped
 * state): approval then renders a clearly-labeled SIMULATED outcome — these
 * tests never create real Odoo records.
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

test("floor requests 10, warehouse sends 9, count finds 8, adjustment queued", async ({
  page,
}) => {
  test.setTimeout(90_000);

  // ---- floor: build the request
  await login(page, "floor@demo.ishalife.test");
  await page.goto("/transfer-requests/new");
  await page.getByLabel("Search products").fill("co");
  const firstResult = page.locator("ul li button").first();
  await expect(firstResult).toBeVisible();
  const productName = (await firstResult.locator(".truncate").first().textContent()) ?? "";
  await firstResult.click();

  const qty = page.getByLabel(/^Quantity for/);
  await qty.fill("10");
  await page.getByRole("button", { name: /Send request/ }).click();
  await expect(page).toHaveURL(/transfer-requests\/\d+/);
  const url = page.url();
  const requestId = url.split("/").pop()!;
  await expect(page.getByLabel("Status: Requested")).toBeVisible();
  await signOut(page);

  // ---- warehouse: fulfill 9 of 10
  await login(page, "warehouse@demo.ishalife.test");
  await page.goto(`/transfer-requests/${requestId}`);
  await page.getByLabel(/^Sent quantity for/).fill("9");
  await page.getByRole("button", { name: "Mark as picked" }).click();
  await expect(page.getByRole("button", { name: "It's in staging" })).toBeVisible();
  await page.getByRole("button", { name: "It's in staging" }).click();
  await expect(page.getByText("delivered to floor staging")).toBeVisible();
  await signOut(page);

  // ---- floor: count 8
  await login(page, "floor@demo.ishalife.test");
  await page.goto(`/transfer-requests/${requestId}`);
  await page.getByLabel(/^Counted quantity for/).fill("8");
  await page.getByRole("button", { name: "Submit count" }).click();
  await expect(page.getByText("Count didn't match")).toBeVisible();
  await expect(page.getByText("adjustments queue", { exact: false }).first()).toBeVisible();
  await signOut(page);

  // ---- warehouse: the discrepancy is in the queue; resolve it
  await login(page, "warehouse@demo.ishalife.test");
  await page.goto("/adjustments");
  const row = page.locator("tbody tr", { hasText: `request #${requestId}` });
  await expect(row).toContainText("9 → 8");
  await expect(row).toContainText("-1");
  await row.click();
  await page.getByLabel(/Note/).fill("Found it under the cart — e2e");
  await page.getByRole("dialog").getByRole("button", { name: "Resolved", exact: true }).click();
  await expect(page.locator("tbody tr", { hasText: `request #${requestId}` })).toHaveCount(0);

  // productName was on the request all along
  expect(productName.length).toBeGreaterThan(0);
});

test("order list: create → assign to Zone 1 → coordinator approves (honest outcome)", async ({
  page,
}) => {
  test.setTimeout(90_000);
  const listName = `E2E list ${Date.now()}`;

  // ---- admin: create, add an item, assign
  await login(page, "admin@demo.ishalife.test");
  await page.goto("/orders");
  await page.getByRole("button", { name: "New list" }).first().click();
  await page.getByLabel("Name").fill(listName);
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await expect(page).toHaveURL(/orders\/\d+/);

  await page.getByLabel("Search products").fill("co");
  const firstResult = page.locator("ul li button").first();
  await expect(firstResult).toBeVisible();
  await firstResult.click();
  await page.getByRole("button", { name: "Save lines" }).click();
  await expect(page.getByRole("button", { name: "Save lines" })).toHaveCount(0);

  await page.getByRole("button", { name: "Assign…" }).click();
  const zoneSelect = page.getByLabel("Zone");
  const zone1 = await zoneSelect
    .locator("option", { hasText: "Zone 1" })
    .first()
    .getAttribute("value");
  const centersLoaded = page.waitForResponse(
    (r) => r.url().includes("/centers") && r.url().includes("zone_id"),
  );
  await zoneSelect.selectOption(zone1!);
  await centersLoaded;
  const centerSelect = page.getByLabel("Destination center");
  // the option list re-renders after the zone pick — retry until a real
  // center sticks (the placeholder has an empty value)
  await expect(async () => {
    await centerSelect.selectOption({ index: 1 });
    expect(await centerSelect.inputValue()).not.toBe("");
  }).toPass();
  const assigned = page.waitForResponse(
    (r) => r.url().includes("/assign") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Assign", exact: true }).click();
  expect((await assigned).ok()).toBeTruthy();
  await expect(page.getByText("Pending approval")).toBeVisible();
  await signOut(page);

  // ---- coordinator: review + approve; the outcome is labeled honestly
  await login(page, "coordinator@demo.ishalife.test");
  await page.goto("/pending-orders");
  const card = page.locator("div", { hasText: listName }).getByRole("button", { name: "Review" }).first();
  await card.click();
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  // flag ships OFF -> simulated; the chip + reference prove the write path ran
  await expect(page.getByText(/simulated/).first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/ILAPP-OL-/).first()).toBeVisible();
});

test("restock checklists render for the floor role", async ({ page }) => {
  await login(page, "floor@demo.ishalife.test");
  await page.goto("/restock");
  await expect(page.getByRole("button", { name: /From warehouse/ })).toBeVisible();
  // either items flagged by the accumulator or an honest empty state
  const anyRow = page.locator('[role="checkbox"]').first();
  const empty = page.getByText("Shelves are happy");
  await expect(anyRow.or(empty).first()).toBeVisible();
});
