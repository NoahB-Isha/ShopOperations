/* The "Inventory counting" nav destination = the counting page and the
   review queue under one tab bar (merged 2026-08-24). Tabs follow the role:
   warehouse only counts, an inventory_wrangler add-on only reviews, the
   Inventory Flow Manager (shoppe_floor) and admin do both — a lone tab
   renders no bar at all. Data-only module (Fast Refresh). */
import type { SectionTab } from "../shared/SectionTabs";

const COUNTER_ROLES = ["shoppe_floor", "floor_rotating", "warehouse", "admin"];
const REVIEWER_ROLES = ["shoppe_floor", "inventory_wrangler", "admin"];

export function countingTabsFor(roles: Set<string>): SectionTab[] {
  const tabs: SectionTab[] = [];
  if (COUNTER_ROLES.some((r) => roles.has(r))) {
    tabs.push({ path: "/inventory-count", label: "Count" });
  }
  if (REVIEWER_ROLES.some((r) => roles.has(r))) {
    tabs.push({ path: "/count-review", label: "Review" });
  }
  return tabs;
}
