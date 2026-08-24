/* The half-built transfer request — one shared store instead of page state.

   The request page used to own its draft privately (usePersistedState), which
   was fine while it was the only writer. It isn't any more: the restock list
   appends to it from another page, and the floating bubble has to know it
   exists from anywhere in the app. Same sessionStorage key as before, so a
   draft in progress survives this change; the store just gives every screen
   the same live copy of it.

   sessionStorage semantics are deliberate and unchanged (see persist.ts): the
   draft lives for this tab / app session, a fresh launch starts clean. */

import { useSyncExternalStore } from "react";
import { clearPersisted } from "./persist";
import type { PickedLine } from "./pages/shared/OpsBits";

/* Two signals ride alongside the lines, both purely for motion:

   - `pulse` counts additions, so the floating bubble can bump when an item
     joins it. The page bounces the row FIRST and the bubble follows a beat
     later — the eye tracks the item into the bubble.
   - `burst` records the moment the bubble blew itself into the transfer page,
     so that page knows to make its list arrive rather than just appear. */

export const DRAFT_LINES_KEY = "transfer.new.lines";
export const DRAFT_NOTES_KEY = "transfer.new.notes";

const listeners = new Set<() => void>();
const EMPTY: PickedLine[] = [];

function readStored(): PickedLine[] {
  try {
    const raw = sessionStorage.getItem(DRAFT_LINES_KEY);
    const parsed = raw === null ? null : JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as PickedLine[]) : EMPTY;
  } catch {
    return EMPTY; // corrupt entry or storage unavailable — start empty
  }
}

// The snapshot must be referentially stable between changes or
// useSyncExternalStore re-renders forever — so the array is cached here and
// only ever replaced by a write.
let lines: PickedLine[] = readStored();

export function getDraftLines(): PickedLine[] {
  return lines;
}

export function setDraftLines(
  next: PickedLine[] | ((prev: PickedLine[]) => PickedLine[]),
): void {
  lines = typeof next === "function" ? next(lines) : next;
  try {
    sessionStorage.setItem(DRAFT_LINES_KEY, JSON.stringify(lines));
  } catch {
    /* storage full/unavailable — the draft still works, just won't persist */
  }
  for (const fn of listeners) fn();
}

/** The one ordering rule for a draft: the item you just added is at the TOP
 *  (Noah, 2026-08-22). Pure, so both the page's own adds and everyone else's
 *  go through the same behaviour.
 *
 *  An item already on the draft gains the quantity rather than appearing
 *  twice — the API rejects duplicate products on one request — and it moves
 *  to the top too, because a merge you can't see reads as a tap that did
 *  nothing. */
export function withNewestFirst(prev: PickedLine[], line: PickedLine): PickedLine[] {
  const existing = prev.find((l) => l.product_id === line.product_id);
  if (!existing) return [line, ...prev];
  return [
    { ...existing, qty: existing.qty + line.qty },
    ...prev.filter((l) => l.product_id !== line.product_id),
  ];
}

/** Add one item from somewhere else in the app (the restock list, the OOS
 *  board, the suggested strip). */
export function addToDraft(line: PickedLine): "added" | "merged" {
  const existing = lines.some((l) => l.product_id === line.product_id);
  pulse += 1;
  setDraftLines((prev) => withNewestFirst(prev, line));
  return existing ? "merged" : "added";
}

/** After the request is placed — the cart must not resurrect on the next
 *  visit. Clears the note too; they're one draft. */
export function clearDraft(): void {
  lines = EMPTY;
  clearPersisted(DRAFT_LINES_KEY, DRAFT_NOTES_KEY);
  for (const fn of listeners) fn();
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function useDraftLines(): PickedLine[] {
  return useSyncExternalStore(subscribe, getDraftLines, () => EMPTY);
}

/* ---------------------------------------------------------- motion signals */

let pulse = 0;
let burstAt = 0;

/** Counts additions — the bubble bumps when this changes. */
export function useDraftPulse(): number {
  return useSyncExternalStore(subscribe, () => pulse, () => 0);
}

/** The bubble blew itself into the transfer page just now. */
export function markBurst(): void {
  burstAt = performance.now();
}

/** True for a short window after a burst — the destination's cue to make its
 *  list arrive. Reads once (on mount); it deliberately doesn't subscribe. */
export function burstedRecently(withinMs = 900): boolean {
  return burstAt > 0 && performance.now() - burstAt < withinMs;
}
