import { describe, expect, it } from "vitest";
import { navForRoles } from "../nav";
import { SILLY_LABELS, sillyLabel } from "../silly";

// the six user types (the departments roles merged into reviewer/requester
// on 2026-08-13 — see models/users.py)
const ALL_ROLES = [
  "admin",
  "warehouse",
  "shoppe_floor",
  "floor_rotating",
  "zone_coordinator",
  "center_orderer",
];

describe("silly mode", () => {
  it("passes labels through untouched when off", () => {
    expect(sillyLabel("Purchasing", false)).toBe("Purchasing");
    expect(sillyLabel("Q3 2026", false)).toBe("Q3 2026");
  });

  it("maps the requested renames when on", () => {
    expect(sillyLabel("Purchasing", true)).toBe("Get the goods");
    expect(sillyLabel("Users", true)).toBe("Peeps");
    expect(sillyLabel("Reports", true)).toBe("🤑🤑🤑");
  });

  it("leaves unknown (dynamic) labels alone even when on", () => {
    expect(sillyLabel("Q3 2026", true)).toBe("Q3 2026");
    expect(sillyLabel("CA0023000009", true)).toBe("CA0023000009");
  });

  it("covers every nav label for every role — no half-silly menus", () => {
    for (const role of ALL_ROLES) {
      for (const item of navForRoles(new Set([role]))) {
        expect(
          SILLY_LABELS[item.label],
          `nav label "${item.label}" (role ${role}) has no silly name`,
        ).toBeTruthy();
      }
    }
  });

  it("covers the quirk zones: empty states, placeholders, chrome", () => {
    expect(sillyLabel("Nothing here", true)).toBe("Crickets 🦗");
    expect(sillyLabel("Search products…", true)).toBe("Snoop the stash…");
    expect(sillyLabel("Shop Ops", true)).toBe("Da Shop");
    expect(sillyLabel("Synced · live Odoo", true)).toBe("Vibin' with Odoo");
    // failure states must NEVER be renamed — no entries may exist for them
    expect(sillyLabel("Odoo auth failing!", true)).toBe("Odoo auth failing!");
    expect(sillyLabel("Sync stale", true)).toBe("Sync stale");
  });

  it("never maps an entry to itself", () => {
    for (const [canonical, street] of Object.entries(SILLY_LABELS)) {
      expect(street, `"${canonical}" maps to itself`).not.toBe(canonical);
    }
  });
});
