/* Page-state persistence — filters, tabs, carts and search boxes survive
   menu navigation (and, in the home-screen app, being backgrounded) instead
   of resetting every time a page unmounts.

   sessionStorage on purpose: state sticks for the life of the tab / app
   session, but a fresh launch starts clean — yesterday's filters shouldn't
   greet today's shift. Selections and open menus stay ephemeral. */

import { useCallback, useEffect, useState } from "react";
import type { Dispatch, SetStateAction } from "react";

function read<T>(key: string): { value: T } | null {
  try {
    const raw = sessionStorage.getItem(key);
    if (raw === null) return null;
    return { value: JSON.parse(raw) as T };
  } catch {
    return null; // corrupt entry or storage unavailable — fall back to initial
  }
}

/** Drop-in useState that mirrors itself to sessionStorage under `key`.
 *  A key change (e.g. per-center cart keys) re-seeds from storage. */
export function usePersistedState<T>(
  key: string,
  initial: T,
): [T, Dispatch<SetStateAction<T>>] {
  const [state, setState] = useState<{ key: string; value: T }>(() => ({
    key,
    value: read<T>(key)?.value ?? initial,
  }));

  // key switched mid-life (per-entity keys): re-seed during render so the
  // old value never flashes — the sanctioned derived-state-from-props form
  if (state.key !== key) {
    setState({ key, value: read<T>(key)?.value ?? initial });
  }

  useEffect(() => {
    if (state.key !== key) return; // mid-switch render — skip the stale write
    try {
      sessionStorage.setItem(key, JSON.stringify(state.value));
    } catch {
      /* storage full/unavailable — the page still works, just won't persist */
    }
  }, [key, state]);

  // stable identity, like useState's setter — safe in effect deps
  const setValue: Dispatch<SetStateAction<T>> = useCallback(
    (action) =>
      setState((prev) => ({
        key: prev.key,
        value: typeof action === "function" ? (action as (p: T) => T)(prev.value) : action,
      })),
    [],
  );

  return [state.value, setValue];
}

/** Imperative cleanup for submit flows: a placed order/request must not
 *  resurrect its cart on the next visit. Safe to call during navigation
 *  (unlike a setState, which can miss its write effect on unmount). */
export function clearPersisted(...keys: string[]): void {
  for (const key of keys) {
    try {
      sessionStorage.removeItem(key);
    } catch {
      /* nothing to clear */
    }
  }
}
