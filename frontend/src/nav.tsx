import type { ReactNode } from "react";

/* Minimal 16px inline icon set — hand-drawn, stroke-based, no dependency. */
const stroke = { stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, fill: "none" };
export const Icons = {
  pulse: (
    <svg width="16" height="16" viewBox="0 0 16 16"><path {...stroke} d="M1.5 8h3l2-5 3 10 2-5h3" /></svg>
  ),
  box: (
    <svg width="16" height="16" viewBox="0 0 16 16"><path {...stroke} d="M2.5 5 8 2l5.5 3v6L8 14l-5.5-3v-6ZM8 8l5.5-3M8 8 2.5 5M8 8v6" /></svg>
  ),
  mapPin: (
    <svg width="16" height="16" viewBox="0 0 16 16"><path {...stroke} d="M13 6.5c0 3.5-5 7.5-5 7.5S3 10 3 6.5a5 5 0 0 1 10 0Z" /><circle {...stroke} cx="8" cy="6.5" r="1.8" /></svg>
  ),
  users: (
    <svg width="16" height="16" viewBox="0 0 16 16"><circle {...stroke} cx="5.5" cy="5.5" r="2.5" /><path {...stroke} d="M1.5 13.5c0-2.2 1.8-4 4-4s4 1.8 4 4M11 3.3a2.5 2.5 0 0 1 0 4.4M14.5 13.5c0-2-1.2-3.4-3-3.9" /></svg>
  ),
  clipboard: (
    <svg width="16" height="16" viewBox="0 0 16 16"><rect {...stroke} x="3" y="2.5" width="10" height="11.5" rx="1.5" /><path {...stroke} d="M6 2.5h4M5.8 7h4.4M5.8 10h3" /></svg>
  ),
  truck: (
    <svg width="16" height="16" viewBox="0 0 16 16"><path {...stroke} d="M1.5 3.5h8v7h-8zM9.5 6h3l2 2.5V10.5h-5" /><circle {...stroke} cx="4.5" cy="12" r="1.5" /><circle {...stroke} cx="11.5" cy="12" r="1.5" /></svg>
  ),
  swap: (
    <svg width="16" height="16" viewBox="0 0 16 16"><path {...stroke} d="M11 2.5 13.5 5 11 7.5M13.5 5h-11M5 8.5 2.5 11 5 13.5M2.5 11h11" /></svg>
  ),
  history: (
    <svg width="16" height="16" viewBox="0 0 16 16"><path {...stroke} d="M2.5 8a5.5 5.5 0 1 1 1.6 3.9M2.5 8V5M2.5 8h3M8 5.5V8l2 1.5" /></svg>
  ),
  bag: (
    <svg width="16" height="16" viewBox="0 0 16 16"><path {...stroke} d="M3 5.5h10l-.8 8H3.8l-.8-8ZM5.5 5.5V4a2.5 2.5 0 0 1 5 0v1.5" /></svg>
  ),
  palette: (
    <svg width="16" height="16" viewBox="0 0 16 16"><path {...stroke} d="M8 1.5a6.5 6.5 0 1 0 0 13c1 0 1.3-.7.9-1.4-.5-.9 0-2.1 1.3-2.1h1.4c1 0 1.9-.8 1.9-1.9A6.7 6.7 0 0 0 8 1.5Z" /><circle cx="5" cy="6" r="0.9" fill="currentColor" /><circle cx="8" cy="4.5" r="0.9" fill="currentColor" /><circle cx="11" cy="6" r="0.9" fill="currentColor" /></svg>
  ),
  scroll: (
    <svg width="16" height="16" viewBox="0 0 16 16"><path {...stroke} d="M4 2.5h8.5v9a2 2 0 0 1-2 2H4a1.5 1.5 0 0 1-1.5-1.5v-1H10" /><path {...stroke} d="M6 5.5h4M6 8h3" /></svg>
  ),
  scale: (
    <svg width="16" height="16" viewBox="0 0 16 16"><path {...stroke} d="M8 2.5v11M4.5 13.5h7M3.5 4.5h9M3.5 4.5 1.8 9a2 2 0 0 0 3.4 0L3.5 4.5ZM12.5 4.5 10.8 9a2 2 0 0 0 3.4 0l-1.7-4.5Z" /></svg>
  ),
  eta: (
    <svg width="16" height="16" viewBox="0 0 16 16"><circle {...stroke} cx="8" cy="8" r="5.5" /><path {...stroke} d="M8 5v3l2.2 1.3M13.5 2.5 15 4M2.5 2.5 1 4" /></svg>
  ),
  ship: (
    <svg width="16" height="16" viewBox="0 0 16 16"><path {...stroke} d="M2 10.5 3 13c.4.9 1.2 1 2 .4.9-.7 2.1-.7 3 0 .9.6 2.1.6 3 0 .8-.6 1.6-.5 2-.4l1-2.5-6-2-6 2Z" /><path {...stroke} d="M4 10V5.5h8V10M8 5.5V3M6.5 3h3" /></svg>
  ),
  chart: (
    <svg width="16" height="16" viewBox="0 0 16 16"><path {...stroke} d="M2.5 2.5v11h11" /><path {...stroke} d="M5.5 10.5V7M8.5 10.5V4.5M11.5 10.5V6" /></svg>
  ),
  sparkle: (
    <svg width="16" height="16" viewBox="0 0 16 16"><path {...stroke} d="M8 1.8l1.5 3.9L13.4 7l-3.9 1.4L8 12.3 6.5 8.4 2.6 7l3.9-1.3L8 1.8Z" /><path {...stroke} d="M12.6 11.2l.6 1.5 1.5.6-1.5.6-.6 1.5-.6-1.5-1.5-.6 1.5-.6.6-1.5Z" /></svg>
  ),
  radar: (
    <svg width="16" height="16" viewBox="0 0 16 16"><path {...stroke} d="M8 8 12.2 4.2M8 1.5A6.5 6.5 0 1 1 1.5 8" /><path {...stroke} d="M8 4.5A3.5 3.5 0 1 0 11.5 8" /><circle cx="8" cy="8" r="1" fill="currentColor" /></svg>
  ),
  // a real cog: eight square teeth on a ring, not a lumpy blob
  gear: (
    <svg width="16" height="16" viewBox="0 0 16 16">
      <circle {...stroke} cx="8" cy="8" r="2.4" />
      <path
        {...stroke}
        d="M12.9 9.7a1.1 1.1 0 0 0 .22 1.21l.04.04a1.33 1.33 0 1 1-1.88 1.88l-.04-.04a1.1 1.1 0 0 0-1.21-.22 1.1 1.1 0 0 0-.67 1v.11a1.33 1.33 0 0 1-2.66 0v-.06a1.1 1.1 0 0 0-.72-1 1.1 1.1 0 0 0-1.21.22l-.04.04a1.33 1.33 0 1 1-1.88-1.88l.04-.04a1.1 1.1 0 0 0 .22-1.21 1.1 1.1 0 0 0-1-.67h-.11a1.33 1.33 0 0 1 0-2.66h.06a1.1 1.1 0 0 0 1-.72 1.1 1.1 0 0 0-.22-1.21l-.04-.04a1.33 1.33 0 1 1 1.88-1.88l.04.04a1.1 1.1 0 0 0 1.21.22h.05a1.1 1.1 0 0 0 .67-1v-.11a1.33 1.33 0 0 1 2.66 0v.06a1.1 1.1 0 0 0 .67 1 1.1 1.1 0 0 0 1.21-.22l.04-.04a1.33 1.33 0 1 1 1.88 1.88l-.04.04a1.1 1.1 0 0 0-.22 1.21v.05a1.1 1.1 0 0 0 1 .67h.11a1.33 1.33 0 0 1 0 2.66h-.06a1.1 1.1 0 0 0-1 .67Z"
      />
    </svg>
  ),
  card: (
    <svg width="16" height="16" viewBox="0 0 16 16"><rect {...stroke} x="1.5" y="3.5" width="13" height="9" rx="1.5" /><path {...stroke} d="M1.5 6.5h13M4 10h2.5" /></svg>
  ),
  search: (
    <svg width="16" height="16" viewBox="0 0 16 16"><circle {...stroke} cx="7" cy="7" r="4.5" /><path {...stroke} d="M10.4 10.4 14 14" /></svg>
  ),
  scan: (
    <svg width="16" height="16" viewBox="0 0 16 16"><path {...stroke} d="M2 5.5v-2A1.5 1.5 0 0 1 3.5 2h2M10.5 2h2A1.5 1.5 0 0 1 14 3.5v2M14 10.5v2a1.5 1.5 0 0 1-1.5 1.5h-2M5.5 14h-2A1.5 1.5 0 0 1 2 12.5v-2M5 5.5v5M7.5 5.5v5M10 5.5v5" /></svg>
  ),
  bell: (
    <svg width="16" height="16" viewBox="0 0 16 16"><path {...stroke} d="M8 2a4 4 0 0 1 4 4c0 3 .8 4 1.5 4.7H2.5C3.2 10 4 9 4 6a4 4 0 0 1 4-4ZM6.4 13a1.7 1.7 0 0 0 3.2 0" /></svg>
  ),
  download: (
    <svg width="16" height="16" viewBox="0 0 16 16"><path {...stroke} d="M8 2.5V10M5 7.5 8 10.5 11 7.5M2.5 13.5h11" /></svg>
  ),
  more: (
    <svg width="16" height="16" viewBox="0 0 16 16"><circle cx="3.2" cy="8" r="1.35" fill="currentColor" /><circle cx="8" cy="8" r="1.35" fill="currentColor" /><circle cx="12.8" cy="8" r="1.35" fill="currentColor" /></svg>
  ),
  upload: (
    <svg width="16" height="16" viewBox="0 0 16 16"><path {...stroke} d="M8 10.5V3M5 6 8 3l3 3M2.5 13.5h11" /></svg>
  ),
};

export interface NavItem {
  path: string;
  label: string;
  icon: ReactNode;
  /** Bottom-bar label. A phone slot is ~70px; anything longer than about ten
   *  characters truncates to "Search Inv…". Only long labels need one. */
  short?: string;
}

const byRole: Record<string, NavItem[]> = {
  admin: [
    { path: "/reports", label: "Reports", icon: Icons.chart },
    { path: "/coming-soon", label: "Coming soon", short: "Incoming", icon: Icons.eta },
    { path: "/catalog", label: "Search Inventory", short: "Search", icon: Icons.search },
    { path: "/centers", label: "Centers", icon: Icons.mapPin },
    { path: "/orders", label: "Catalogs", icon: Icons.clipboard },
    { path: "/purchasing", label: "Purchasing", icon: Icons.ship },
  ],
  // Warehouse Team: deliberately down to two destinations (Noah, 2026-08-17).
  // They live in Odoo — the app's job for them is the delivery form and
  // looking a product up; the scanner, inbox and settings are in the top bar
  // (and Scan is pinned in the phone bottom bar). Incoming, Transfers, Coming
  // soon and Out of stock left the MENU, not the app: their routes and role
  // access are untouched, so a link still opens them and nothing has to be
  // rebuilt to bring one back. (Adjustments left the app entirely, 2026-08-24.)
  warehouse: [
    { path: "/staging2", label: "Send to floor", short: "To floor", icon: Icons.box },
    { path: "/inventory-count", label: "Inventory counting", short: "Counting", icon: Icons.scale },
    { path: "/catalog", label: "Search Inventory", short: "Search", icon: Icons.search },
  ],
  // Order matters twice over: the first item is the landing page
  // (homeForRoles), and the phone bar keeps only the first TWO destinations
  // before Scan and More. Transfers sits second (Noah, 2026-08-18) so raising
  // one is a tap on any screen; Suggested items moved behind More, since the
  // transfer form now carries its own suggestions strip. Count review rides
  // the Inventory counting destination (tab bar on both pages, 2026-08-24).
  // The out-of-stock page is GONE (2026-09-01, redundant with restock's
  // computed list) — Coming soon stands alone.
  shoppe_floor: [
    { path: "/restock", label: "Restock", icon: Icons.clipboard },
    { path: "/transfer-requests", label: "Transfers", icon: Icons.swap },
    { path: "/suggested-items", label: "Suggested items", short: "Suggested", icon: Icons.sparkle },
    { path: "/inventory-count", label: "Inventory counting", short: "Counting", icon: Icons.scale },
    { path: "/coming-soon", label: "Coming soon", short: "Incoming", icon: Icons.eta },
    { path: "/catalog", label: "Search Inventory", short: "Search", icon: Icons.search },
  ],
  // Floor Team: the floor toolkit minus creating transfers (the pages hide
  // the "new request" entry points; the API refuses too). What they CAN do is
  // ask — /request-items feeds the manager's Suggested items page.
  floor_rotating: [
    { path: "/restock", label: "Restock", icon: Icons.clipboard },
    { path: "/request-items", label: "Request items", short: "Request", icon: Icons.bag },
    { path: "/transfer-requests", label: "Transfers", icon: Icons.swap },
    { path: "/inventory-count", label: "Inventory counting", short: "Counting", icon: Icons.scale },
    { path: "/coming-soon", label: "Coming soon", short: "Incoming", icon: Icons.eta },
    { path: "/catalog", label: "Search Inventory", short: "Search", icon: Icons.search },
  ],
  // Order Reviewer — one nav whether the review zone is a field zone or III
  // Departments; the pages themselves say "center" or "department" based on
  // the zone's kind (the dept-specific roles merged 2026-08-13).
  zone_coordinator: [
    // pending orders first: it's the job — and the landing page (homeForRoles)
    { path: "/pending-orders", label: "Pending orders", short: "Pending", icon: Icons.clipboard },
    { path: "/my-centers", label: "My centers", short: "Centers", icon: Icons.mapPin },
    { path: "/my-order-lists", label: "Catalogs", icon: Icons.scroll },
    { path: "/order-history", label: "History", icon: Icons.history },
  ],
  // Approve dept orders — an ADD-ON held by a shop team member, so somebody
  // behind the counter can review what a department is taking. Same one
  // destination as the Order Reviewer's, and navForRoles dedupes by path if
  // they happen to be both.
  dept_order_approver: [
    { path: "/pending-orders", label: "Pending orders", short: "Pending", icon: Icons.clipboard },
  ],
  // Inventory Wrangler — an ADD-ON, not a user type. It contributes exactly
  // one destination, unioned onto whatever the person already is.
  inventory_wrangler: [
    { path: "/count-review", label: "Count review", short: "Review", icon: Icons.clipboard },
  ],
  // Order Requester
  center_orderer: [
    { path: "/place-order", label: "Place an order", short: "Order", icon: Icons.bag },
    { path: "/order-history", label: "Order history", short: "History", icon: Icons.history },
  ],
};

export interface NavOptions {
  /** the user's review zone is III Departments — the one label that differs
   *  between an Order Reviewer's two flavours */
  departments?: boolean;
}

/** Union of the user's roles' nav items, deduped by path, role order preserved. */
export function navForRoles(roles: Set<string>, opts: NavOptions = {}): NavItem[] {
  const seen = new Set<string>();
  const items: NavItem[] = [];
  for (const role of Object.keys(byRole)) {
    if (!roles.has(role)) continue;
    for (const item of byRole[role]) {
      if (seen.has(item.path)) continue;
      seen.add(item.path);
      items.push(
        opts.departments && item.path === "/my-centers"
          ? { ...item, label: "My departments" }
          : item,
      );
    }
  }
  return items;
}

export function homeForRoles(roles: Set<string>): string {
  return navForRoles(roles)[0]?.path ?? "/login";
}
