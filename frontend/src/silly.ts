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

/** Canonical label → street name. Keys must match the rendered strings
 *  EXACTLY — unknown strings pass through untouched, so dynamic titles
 *  (order names, SKUs, counts) can never be renamed by accident. Beyond nav
 *  labels and page titles this also covers the sanctioned quirk zones:
 *  empty states, search placeholders, section headings, and chrome (brand,
 *  health chip, inbox). Failure/warning states and data stay serious —
 *  never add entries for error text, confirm buttons, or table content. */
export const SILLY_LABELS: Record<string, string> = {
  // admin
  Status: "Vibe check",
  "Dev Tools": "The engine room",
  Reports: "🤑🤑🤑",
  Sales: "🤑🤑🤑", // the /reports route title
  "Out of stock": "Ghost town",
  "Search Inventory": "Find the stash",
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
  "Request items": "Gimme gimme",
  "Suggested items": "The hit list",
  "Transfer request": "The wishlist",
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
  // chrome: brand, health chip (healthy states ONLY — failures stay serious), inbox
  "Shop Ops": "Da Shop",
  "Synced · live Odoo": "Vibin' with Odoo",
  "Synced · fixture data": "Vibin' on fixtures",
  Inbox: "The goss 📬",
  "Nothing here yet — post the first notice below.": "Crickets 🦗 — drop the first notice below.",
  "Nothing here yet.": "Crickets 🦗",
  // section headings
  Appearance: "The drip",
  Account: "Who dis",
  "Product blacklist": "The naughty list",
  "Design pages": "Art department",
  "Cleanup sweep": "Take out the trash",
  "Quick order": "Speed run",
  "Domestic orders": "Local hauls",
  "Forecast analogies": "Demand doppelgängers",
  // static page subtitles
  "Appearance for you; the blacklist and design pages for the whole app.":
    "Your drip here; the naughty list and the art department for everyone.",
  "How the app looks on this device.": "How the drip hits on this device.",
  "India imports quarterly by the engine; domestic vendors weekly by email — both tracked to arrival on the same timelines.":
    "Big-boat hauls every quarter, local plugs every week — all tracked till the goods pull up.",
  // search placeholders
  "Search products…": "Snoop the stash…",
  "Search name or SKU…": "Sniff out a SKU…",
  "Search items to blacklist…": "Who's getting benched…",
  "Search by name, SKU, category…": "Snoop what's inbound…",
  "Search the item that's actually out…": "Which one ghosted?…",
  "Filter by name, email, phone…": "Find your peeps…",
  "Filter centers…": "Find the crew…",
  // empty states — the design language's sanctioned quirk zone
  "Nothing here": "Crickets 🦗",
  "No rows match.": "Zilch. Zip. Nada.",
  "No lines match": "No hits, chief",
  "Loosen the filters above.": "Loosen up them filters.",
  "Nothing blacklisted": "Naughty list's empty",
  "No import orders yet": "No big hauls yet",
  "No domestic orders yet": "Local scene's quiet",
  "No vendors with products yet": "No plugs on deck",
  "No vendors yet": "Zero plugs",
  "Pick a vendor": "Pick a plug",
  "Nothing on the way": "Nothing rollin' in",
  "Nothing picked yet": "Cart's a desert",
  "Board's clear": "Board's squeaky clean",
  "All caught up": "Inbox zero, legend",
  "Shelves are happy": "Shelves are thriving 💅",
  "Back stock looks covered": "Backstock's chillin'",
  "Nothing's out": "Everything's in the building",
  "No orders yet": "No receipts yet",
  "No writes yet": "Paper trail's blank",
  "No lists granted yet": "No mixtapes dropped yet",
  "Nothing in this catalog yet": "This mixtape has no tracks",
};

export function sillyLabel(label: string, on: boolean): string {
  return on ? (SILLY_LABELS[label] ?? label) : label;
}

/** Subscribe + map in one hook: `const s = useSillyLabel(); s("Purchasing")`. */
export function useSillyLabel(): (label: string) => string {
  const on = useSillyMode();
  return (label: string) => sillyLabel(label, on);
}
