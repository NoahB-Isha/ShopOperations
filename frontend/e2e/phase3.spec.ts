/**
 * Phase-3 acceptance, end to end through the real UI:
 *
 *   1. PHONE-SIZED viewport: the Austin orderer places an order in well
 *      under a minute — search, add, review (reasonability check), place.
 *      The coordinator approves it; the draft transfer chip appears
 *      (honestly simulated while the write flag is off) and the orderer's
 *      WhatsApp "approved" notification shows on the timeline (simulated —
 *      notify flags ship off).
 *   2. The Kitchen department orders water (a non-Odoo item) and the liaison
 *      approves it — the order flows through with NO Odoo transfer at all.
 *   3. The seeded deliberately-absurd order carries visible reasonability
 *      warnings on the coordinator's board and detail page.
 *
 * Requires the stack up and seeded (make dev && make seed). Write flags and
 * notify flags must be OFF (their shipped state).
 */
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const PHONE = { width: 390, height: 844 }; // iPhone-ish

async function login(page: Page, email: string) {
  await page.goto("/login");
  await page.getByPlaceholder(/you@example/).fill(email);
  await page.getByRole("button", { name: "Send code" }).click();
  await expect(page.getByTestId("dev-code")).toBeVisible();
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/login/);
}

test("a city orderer places on a phone in under a minute; coordinator approves; WhatsApp ping recorded", async ({ browser }) => {
  test.setTimeout(120_000);

  // ---- the orderer, on a phone
  const phone = await browser.newContext({ viewport: PHONE });
  const page = await phone.newPage();
  await login(page, "orderer@demo.ishalife.test");

  const placeStarted = Date.now();
  await page.goto("/place-order");
  // autofilled who/where — no typing needed
  await expect(page.getByText(/Ordering as/)).toBeVisible();

  // find something orderable and add it twice (stepper appears after Add)
  await page.getByLabel("Search your catalog").fill("");
  const addButtons = page.getByRole("button", { name: /^Add / });
  await expect(addButtons.first()).toBeVisible();
  await addButtons.first().click();
  await addButtons.first().click(); // the next visible "Add" — second item

  // review → the sticky cart bar
  await page.getByTestId("review-order").click();
  await expect(page.getByRole("button", { name: "Place order" })).toBeVisible();
  await page.getByLabel("Order notes").fill("e2e phone order");
  await page.getByRole("button", { name: "Place order" }).click();

  await expect(page.getByText("Order placed!")).toBeVisible();
  const placeSeconds = (Date.now() - placeStarted) / 1000;
  expect(placeSeconds).toBeLessThan(60);

  // grab the order id from the success screen's "View order"
  await page.getByRole("button", { name: "View order" }).click();
  await expect(page).toHaveURL(/order\/\d+/);
  const orderId = page.url().split("/").pop()!;
  await expect(page.getByText("Pending approval").first()).toBeVisible();
  await phone.close();

  // ---- the coordinator approves from the board
  const desktop = await browser.newContext();
  const cpage = await desktop.newPage();
  await login(cpage, "coordinator@demo.ishalife.test");
  await cpage.goto(`/order/${orderId}`);
  await cpage.getByTestId("approve-order").click();
  await cpage.getByTestId("confirm-action").click();

  // approved + the draft transfer chip — honestly simulated (flag off)
  await expect(cpage.getByText("Approved", { exact: true }).first()).toBeVisible();
  await expect(cpage.getByText(/simulated/).first()).toBeVisible();

  // the orderer's WhatsApp ping is on the shared timeline — simulated, honestly
  await expect(
    cpage.getByText(/order approved notification .* SIMULATED/i).first(),
  ).toBeVisible();
  await desktop.close();
});

test("a department orders water (non-Odoo) and it never touches Odoo", async ({ browser }) => {
  test.setTimeout(120_000);

  const phone = await browser.newContext({ viewport: PHONE });
  const page = await phone.newPage();
  await login(page, "kitchen@demo.ishalife.test");
  await page.goto("/place-order");
  await expect(page.getByText("fulfilled from the Shoppe floor")).toBeVisible();

  await page.getByLabel("Search your catalog").fill("water");
  await page.getByRole("button", { name: /^Add / }).first().click();
  await page.getByTestId("review-order").click();
  await page.getByRole("button", { name: "Place order" }).click();
  await expect(page.getByText("Order placed!")).toBeVisible();
  await page.getByRole("button", { name: "View order" }).click();
  const orderId = page.url().split("/").pop()!;
  await phone.close();

  const desktop = await browser.newContext();
  const lpage = await desktop.newPage();
  await login(lpage, "liaison@demo.ishalife.test");
  await lpage.goto(`/order/${orderId}`);
  await lpage.getByTestId("approve-order").click();
  await lpage.getByTestId("confirm-action").click();

  await expect(lpage.getByText("Approved", { exact: true }).first()).toBeVisible();
  // the honest no-Odoo path: fulfilled from the floor, no picking, no link
  await expect(lpage.getByText(/No Odoo transfer/i).first()).toBeVisible();
  await expect(lpage.getByText(/Open .* in Odoo/)).toHaveCount(0);
  await desktop.close();
});

test("the absurd seed order wears its warnings on the board and the detail", async ({ page }) => {
  await login(page, "coordinator@demo.ishalife.test");
  await page.goto("/pending-orders");

  // the seeded absurd order (80× usual + more than the warehouse has)
  const flagged = page.getByTestId(/pending-order-/).filter({ hasText: "worth a look" });
  await expect(flagged.first()).toBeVisible();
  await flagged.first().click();

  await expect(page.getByTestId("order-reasonability")).toBeVisible();
  // at least one concrete rule badge is visible on the lines
  await expect(page.getByText(/× your usual volume|warehouse has/).first()).toBeVisible();
});
