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
};

export interface NavItem {
  path: string;
  label: string;
  icon: ReactNode;
}

const byRole: Record<string, NavItem[]> = {
  admin: [
    { path: "/status", label: "Status", icon: Icons.pulse },
    { path: "/catalog", label: "Catalog", icon: Icons.box },
    { path: "/centers", label: "Centers", icon: Icons.mapPin },
    { path: "/users", label: "Users", icon: Icons.users },
    { path: "/audit", label: "Audit log", icon: Icons.scroll },
    { path: "/orders", label: "Orders", icon: Icons.clipboard },
    { path: "/styleguide", label: "Styleguide", icon: Icons.palette },
    { path: "/palette-lab", label: "Themes", icon: Icons.palette },
  ],
  warehouse: [
    { path: "/incoming", label: "Incoming", icon: Icons.truck },
    { path: "/transfers", label: "Transfers", icon: Icons.swap },
    { path: "/catalog", label: "Catalog", icon: Icons.box },
  ],
  shoppe_floor: [
    { path: "/restock", label: "Restock", icon: Icons.clipboard },
    { path: "/transfer-requests", label: "Transfer requests", icon: Icons.swap },
    { path: "/catalog", label: "Catalog", icon: Icons.box },
  ],
  zone_coordinator: [
    { path: "/my-centers", label: "My centers", icon: Icons.mapPin },
    { path: "/pending-orders", label: "Pending orders", icon: Icons.clipboard },
    { path: "/order-history", label: "History", icon: Icons.history },
  ],
  center_orderer: [
    { path: "/place-order", label: "Place an order", icon: Icons.bag },
    { path: "/order-history", label: "Order history", icon: Icons.history },
  ],
  dept_liaison: [
    { path: "/my-centers", label: "My departments", icon: Icons.mapPin },
    { path: "/pending-orders", label: "Pending orders", icon: Icons.clipboard },
    { path: "/order-history", label: "History", icon: Icons.history },
  ],
  dept_orderer: [
    { path: "/place-order", label: "Place an order", icon: Icons.bag },
    { path: "/order-history", label: "Order history", icon: Icons.history },
  ],
};

/** Union of the user's roles' nav items, deduped by path, role order preserved. */
export function navForRoles(roles: Set<string>): NavItem[] {
  const seen = new Set<string>();
  const items: NavItem[] = [];
  for (const role of Object.keys(byRole)) {
    if (!roles.has(role)) continue;
    for (const item of byRole[role]) {
      if (!seen.has(item.path)) {
        seen.add(item.path);
        items.push(item);
      }
    }
  }
  return items;
}

export function homeForRoles(roles: Set<string>): string {
  return navForRoles(roles)[0]?.path ?? "/login";
}
