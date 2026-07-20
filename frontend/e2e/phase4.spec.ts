/**
 * Phase-4 acceptance, end to end through the real UI:
 *
 *   1. Admin generates a FRESH draft from the current snapshot: the review
 *      table renders suggestions with the flag rail, an override sticks,
 *      placing dry-runs the order email (ordering_email_live OFF ⇒ SIMULATED,
 *      rendered verbatim) and attaches the CSV+XLSX exports. Pasting the
 *      acceptance-style reply ("we can only send N of the M …, and … is
 *      discontinued") yields two correctly-parsed proposals with verbatim
 *      quotes; confirming one updates the line + timeline append-only,
 *      rejecting the other changes nothing.
 *   2. The seeded placed order renders its timeline (email thread ingested).
 *   3. The seeded Botanie vendor shows MOQ suggestions.
 *
 * The flow test owns its data (creates its own order) so the suite is
 * re-runnable. Requires the stack up + seeded and ordering_email_live OFF
 * (its shipped state).
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

test("draft → review/override → place (dry-run email) → reply → 2 proposals → confirm/reject", async ({ page }) => {
  test.setTimeout(300_000);
  await login(page, "admin@demo.ishalife.test");
  await page.goto("/purchasing");

  // ---- generate a fresh draft from the current snapshot
  await page.getByTestId("new-order-draft").click();
  const name = `E2E ${Date.now().toString().slice(-6)}`;
  await page.getByLabel("Order name").fill(name);
  await page.getByRole("button", { name: "Generate draft" }).click();
  await expect(page.getByText(/candidates\./)).toBeVisible({ timeout: 120_000 });

  // review table: totals + flag rail render
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await expect(page.getByText(/sea units ·/)).toBeVisible();

  // narrow to ordering lines; override the first line's sea quantity
  await page.getByRole("button", { name: /Ordering only/ }).click();
  const firstSea = page.getByRole("spinbutton", { name: /sea quantity/ }).first();
  await expect(firstSea).toBeVisible();
  const aria = (await firstSea.getAttribute("aria-label")) ?? "";
  const sku = aria.replace(/^sea quantity for /, "").trim();
  expect(sku.length).toBeGreaterThan(3);
  const suggested = Number(await firstSea.inputValue());
  const overridden = Math.max(2, suggested - 1);
  await firstSea.fill(String(overridden));
  await firstSea.blur();
  await expect(firstSea).toHaveValue(String(overridden));
  // zero the row's air leg so the reply's quantity cut resolves to sea
  const firstAir = page.getByRole("spinbutton", { name: `air quantity for ${sku}` });
  await firstAir.fill("0");
  await firstAir.blur();
  await page.waitForTimeout(800); // the PATCHes land before we place

  // ---- place: dry-run email + exports attached
  await page.getByTestId("place-order").click();
  await expect(page.getByText(/Dry-run: the email will be rendered/)).toBeVisible();
  await page.getByTestId("confirm-place").click();
  await expect(page.getByText("Order placed — exports attached")).toBeVisible({
    timeout: 120_000,
  });

  // tracking view: timeline, both exports, the simulated email
  await expect(page.getByRole("heading", { name: "Timeline" })).toBeVisible();
  await expect(page.getByText(`${name} ORDER LIST.csv`)).toBeVisible();
  await expect(page.getByText(`${name} ORDER LIST.xlsx`)).toBeVisible();
  await page.getByText("→ sent", { exact: false }).first().click();
  await expect(page.getByText("DRY-RUN").first()).toBeVisible();

  // ---- paste the acceptance reply (SKU-addressed so it parses on any catalog)
  await page.getByTestId("ingest-reply").click();
  await page
    .getByTestId("ingest-body")
    .fill(
      `Namaskaram, we can only send 1 of the ${overridden} ${sku}, and ${sku} is discontinued. Pranam.`,
    );
  await page.getByTestId("ingest-submit").click();
  await expect(page.getByText(/2 proposal\(s\) parsed for review/)).toBeVisible();
  const cards = page.getByTestId("proposal-card");
  await expect(cards).toHaveCount(2);
  await expect(cards.filter({ hasText: "can only send" }).first()).toBeVisible();
  await expect(cards.filter({ hasText: "discontinued" }).first()).toBeVisible();

  // ---- confirm the quantity cut → the line + timeline update
  const qtyCard = cards.filter({ hasText: "Quantity change" }).first();
  await qtyCard.getByTestId("confirm-proposal").click();
  await expect(page.getByText("Confirmed — the timeline and quantities updated.")).toBeVisible();

  // ---- reject the discontinuation → recorded, nothing changes
  await expect(page.getByTestId("proposal-card")).toHaveCount(1, { timeout: 10_000 });
  const discCard = page.getByTestId("proposal-card").filter({ hasText: "Discontinued" }).first();
  await discCard.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByText("Rejected — nothing changed.")).toBeVisible();
  await expect(page.getByTestId("proposal-card")).toHaveCount(0);

  // the confirmed event sits on the timeline with its verbatim quote,
  // and the lines summary shows the revision (2 sea) against the origin
  await expect(page.getByText(`sea ${overridden} → 1`).first()).toBeVisible();
  await expect(page.getByText("can only send 1 of").first()).toBeVisible();
  const summaryRow = page.locator("table tbody tr").filter({ hasText: sku }).first();
  await expect(summaryRow.getByText("revised")).toBeVisible();
});

test("the seeded placed order renders its thread and timeline", async ({ page }) => {
  await login(page, "admin@demo.ishalife.test");
  await page.goto("/purchasing");
  // the seed places one quarterly order and ingests the vendor's reply
  const seeded = page
    .getByRole("row")
    .filter({ hasText: "India import" })
    .filter({ hasText: "Placed" })
    .first();
  await expect(seeded).toBeVisible();
  await seeded.click();
  await expect(page.getByRole("heading", { name: "Timeline" })).toBeVisible();
  await expect(page.getByText("Email thread")).toBeVisible();
  await expect(page.getByText("← received", { exact: false }).first()).toBeVisible();
});

test("domestic: quick order shows Botanie suggestions; vendors page manages the roster", async ({ page }) => {
  await login(page, "admin@demo.ishalife.test");

  // Purchasing → Domestic: the quick-order composer with suggested quantities
  await page.goto("/purchasing");
  await page.getByTestId("tab-domestic").click();
  await expect(page.getByRole("heading", { name: "Quick order" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Suggested" })).toBeVisible();
  await expect(page.getByRole("spinbutton").first()).toBeVisible();
  await expect(page.getByTestId("email-vendor-order")).toBeVisible();
  await expect(page.getByTestId("email-vendor-order")).toContainText("Email order to");

  // Vendors page: search-and-add roster management
  await page.goto("/purchasing/vendors");
  await page.getByText("Botanie Soap Co.").first().click();
  await expect(page.getByRole("heading", { name: "Add products" })).toBeVisible();
  await expect(page.getByPlaceholder(/Search products to add/)).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "MOQ" })).toBeVisible();
});
