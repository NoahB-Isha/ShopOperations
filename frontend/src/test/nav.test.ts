import { homeForRoles, navForRoles } from "../nav";

test("each role sees its own nav", () => {
  expect(navForRoles(new Set(["center_orderer"])).map((i) => i.label)).toEqual([
    "Place an order",
    "Order history",
  ]);
  expect(navForRoles(new Set(["warehouse"])).map((i) => i.label)).toEqual([
    "Incoming",
    "Transfers",
    "Adjustments",
    "Catalog",
  ]);
  expect(navForRoles(new Set(["admin"])).map((i) => i.path)).toContain("/styleguide");
});

test("multi-role users get a deduped union", () => {
  const items = navForRoles(new Set(["zone_coordinator", "center_orderer"]));
  const paths = items.map((i) => i.path);
  expect(paths).toEqual([
    "/my-centers",
    "/pending-orders",
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
