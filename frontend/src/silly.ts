/* Silly mode — a purely cosmetic rename layer for menus and page titles.
   The choice lives in localStorage (like the palette) and applies per device.
   Only EXACT canonical strings are mapped; anything operational — data,
   numbers, order names, buttons that commit things — stays serious. Toggled
   on /settings; components subscribe via useSillyLabel so a flip re-renders
   the chrome instantly. */

import { useSyncExternalStore } from "react";

const STORAGE_KEY = "ilops_silly";
const listeners = new Set<() => void>();

export function sillyEnabled(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function setSillyMode(on: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, on ? "1" : "0");
  } catch {
    /* private browsing — the mood just won't persist */
  }
  for (const fn of listeners) fn();
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function useSillyMode(): boolean {
  return useSyncExternalStore(subscribe, sillyEnabled, () => false);
}

/** Canonical label → street name. Keys must match nav labels / route titles
 *  EXACTLY — unknown strings pass through untouched, so dynamic titles
 *  (order names, SKUs) can never be renamed by accident. */
export const SILLY_LABELS: Record<string, string> = {
  // admin
  Status: "Vibe check",
  Reports: "🤑🤑🤑",
  Sales: "🤑🤑🤑", // the /reports route title
  "Time machine": "The rewind",
  "Out of stock": "Ghost town",
  "All SKUs": "The stash",
  Centers: "The crews",
  Users: "Peeps",
  Catalogs: "Mixtapes",
  Catalog: "Mixtape",
  Purchasing: "Get the goods",
  Vendors: "The plugs",
  "Purchase order": "The haul",
  "Audit log": "Paper trail",
  Styleguide: "Drip check",
  "Palette lab": "Paint booth",
  // warehouse
  Incoming: "Inbound loot",
  Transfers: "Big moves",
  "Staging 2": "Pallet party",
  "Coming soon": "OTW 👀",
  Adjustments: "Damage control",
  // floor
  Restock: "The re-up",
  "Transfer requests": "The wishlist",
  "Transfer request": "The wishlist",
  "Request stock": "Gimme stock",
  // coordinators + orderers
  "Pending orders": "The waitlist",
  "My centers": "My turf",
  "My departments": "My turf",
  "Place an order": "Snag stuff",
  "Order history": "Receipts 🧾",
  History: "Receipts 🧾",
  Order: "The receipt",
  // everywhere
  Settings: "Knobs & dials",
  "Sign out": "Peace out ✌️",
};

export function sillyLabel(label: string, on: boolean): string {
  return on ? (SILLY_LABELS[label] ?? label) : label;
}

/** Subscribe + map in one hook: `const s = useSillyLabel(); s("Purchasing")`. */
export function useSillyLabel(): (label: string) => string {
  const on = useSillyMode();
  return (label: string) => sillyLabel(label, on);
}
