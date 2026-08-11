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

/** Add one item from somewhere else in the app (today: the restock list).
 *  An item already on the draft gains the quantity rather than appearing
 *  twice — the API rejects duplicate products on one request. */
export function addToDraft(line: PickedLine): "added" | "merged" {
  const existing = lines.find((l) => l.product_id === line.product_id);
  setDraftLines((prev) =>
    existing
      ? prev.map((l) =>
          l.product_id === line.product_id ? { ...l, qty: l.qty + line.qty } : l,
        )
      : [...prev, line],
  );
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
