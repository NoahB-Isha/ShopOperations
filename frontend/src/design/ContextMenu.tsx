/* Right-click menu + Premiere-style row selection.

   `useRowSelection` gives lists shift-click ranges and cmd/ctrl toggles over
   whatever key order the caller currently renders (sort/filter aware).
   `useContextMenu` + <ContextMenu> render the actions available to the
   selection at the cursor. Menus never fire actions themselves — they call
   back into the page, which owns the mutation. */
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

export interface MenuAction {
  label: string;
  icon?: ReactNode;
  danger?: boolean;
  disabled?: boolean;
  onSelect: () => void;
}

export interface MenuState {
  x: number;
  y: number;
  actions: MenuAction[];
}

export function useContextMenu() {
  const [menu, setMenu] = useState<MenuState | null>(null);
  const open = (e: { preventDefault: () => void; clientX: number; clientY: number }, actions: MenuAction[]) => {
    e.preventDefault();
    if (actions.length) setMenu({ x: e.clientX, y: e.clientY, actions });
  };
  return { menu, open, close: () => setMenu(null) };
}

export function ContextMenu({ menu, onClose }: { menu: MenuState | null; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menu) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    const onScroll = () => onClose();
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
    };
  }, [menu, onClose]);

  if (!menu) return null;
  // keep the menu on screen near the edges
  const left = Math.min(menu.x, window.innerWidth - 232);
  const top = Math.min(menu.y, window.innerHeight - (menu.actions.length * 38 + 16));
  return (
    <>
      <div
        className="fixed inset-0 z-40"
        onMouseDown={onClose}
        onContextMenu={(e) => {
          e.preventDefault();
          onClose();
        }}
        aria-hidden
      />
      <div
        ref={ref}
        role="menu"
        className="animate-pop-in fixed z-50 min-w-52 rounded-(--radius-md) bg-surface-container-high
          py-1.5 shadow-(--shadow-e2)"
        style={{ left, top: Math.max(top, 8) }}
      >
        {menu.actions.map((a, i) => (
          <button
            key={i}
            role="menuitem"
            disabled={a.disabled}
            onClick={() => {
              onClose();
              a.onSelect();
            }}
            className={`flex w-full items-center gap-2.5 px-3.5 py-2 text-left text-[13.5px] font-medium
              transition-colors hover:bg-on-surface/8 disabled:opacity-40
              ${a.danger ? "text-error" : "text-on-surface"}`}
          >
            {a.icon}
            {a.label}
          </button>
        ))}
      </div>
    </>
  );
}

/* ------------------------------------------------------------- selection */

export interface SelectionClickEvent {
  shiftKey: boolean;
  metaKey: boolean;
  ctrlKey: boolean;
}

/** True when the click landed on a control inside the row — those clicks
 *  belong to the control, not to selection. */
export function isInteractiveTarget(e: { target: EventTarget | null }): boolean {
  return e.target instanceof Element && e.target.closest("button, input, textarea, select, a") !== null;
}

export function useRowSelection<K extends string | number>(visible: readonly K[]) {
  const [selected, setSelected] = useState<Set<K>>(new Set());
  const anchor = useRef<K | null>(null);

  /** plain click = only this row · cmd/ctrl toggles · shift extends from the anchor */
  const click = (key: K, e: SelectionClickEvent) => {
    setSelected((prev) => {
      if (e.shiftKey && anchor.current !== null) {
        const a = visible.indexOf(anchor.current);
        const b = visible.indexOf(key);
        if (a !== -1 && b !== -1) {
          const [lo, hi] = a < b ? [a, b] : [b, a];
          const next = e.metaKey || e.ctrlKey ? new Set(prev) : new Set<K>();
          for (const k of visible.slice(lo, hi + 1)) next.add(k);
          return next;
        }
      }
      if (e.metaKey || e.ctrlKey) {
        const next = new Set(prev);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        anchor.current = key;
        return next;
      }
      anchor.current = key;
      // clicking the only selected row again deselects — an exit that
      // doesn't require knowing about cmd-click
      return prev.size === 1 && prev.has(key) ? new Set() : new Set([key]);
    });
  };

  /** right-click: keep an existing multi-selection; otherwise select the row.
   *  Returns the effective set so the caller can build actions synchronously. */
  const forContext = (key: K): Set<K> => {
    if (selected.has(key)) return selected;
    const next = new Set([key]);
    anchor.current = key;
    setSelected(next);
    return next;
  };

  const clear = () => {
    anchor.current = null;
    setSelected(new Set());
  };

  return { selected, click, forContext, clear, setSelected };
}
