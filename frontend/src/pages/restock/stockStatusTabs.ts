/* The "Stock status" nav destination = two sibling routes under one tab bar
   (merged 2026-08-24). Data-only module so both pages import one list without
   breaking Fast Refresh. */
import type { SectionTab } from "../shared/SectionTabs";

export const STOCK_STATUS_TABS: SectionTab[] = [
  { path: "/out-of-stock", label: "Out of stock" },
  { path: "/coming-soon", label: "Coming soon" },
];
