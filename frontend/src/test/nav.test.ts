import { homeForRoles, navForRoles } from "../nav";

test("each role sees its own nav", () => {
  expect(navForRoles(new Set(["center_orderer"])).map((i) => i.label)).toEqual([
    "Place an order",
    "Order history",
  ]);
  expect(navForRoles(new Set(["warehouse"])).map((i) => i.label)).toEqual([
    "Incoming",
    "Transfers",
    "Staging 2",
    "Coming soon",
    "Out of stock",
    "Adjustments",
    "All SKUs",
  ]);
  const adminPaths = navForRoles(new Set(["admin"])).map((i) => i.path);
  expect(adminPaths).toContain("/reports");
  expect(adminPaths).toContain("/out-of-stock");
  // moved off the menu: audit lives on Status, design pages in Settings,
  // the availability page merged into Out of stock
  expect(adminPaths).not.toContain("/styleguide");
  expect(adminPaths).not.toContain("/palette-lab");
  expect(adminPaths).not.toContain("/audit");
  expect(adminPaths).not.toContain("/availability");
  // the time-machine page was removed 2026-08-11 (its endpoints stayed)
  expect(adminPaths).not.toContain("/time-machine");
});

test("floor_rotating mirrors the floor nav (creation is gated in-page)", () => {
  const floor = navForRoles(new Set(["shoppe_floor"])).map((i) => i.path);
  const rotating = navForRoles(new Set(["floor_rotating"])).map((i) => i.path);
  expect(rotating).toEqual(floor);
});

test("multi-role users get a deduped union", () => {
  const items = navForRoles(new Set(["zone_coordinator", "center_orderer"]));
  const paths = items.map((i) => i.path);
  expect(paths).toEqual([
    "/pending-orders",
    "/my-centers",
    "/my-order-lists",
    "/order-history",
    "/place-order",
  ]);
});

test("home route follows the first nav item", () => {
  expect(homeForRoles(new Set(["admin"]))).toBe("/status");
  expect(homeForRoles(new Set(["center_orderer"]))).toBe("/place-order");
  expect(homeForRoles(new Set())).toBe("/login");
});
