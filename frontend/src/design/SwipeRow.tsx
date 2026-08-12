/* Swipe-to-act rows — the restock list's gesture, extracted so the catalog
   and the out-of-stock board can wear it too.

   TOUCH ONLY, deliberately: a mouse gets the row's context menu instead
   (swiping with a pointer device is a guess; right-clicking isn't).

   Left and right carry different weights. LEFT is the "take it off my list"
   direction, so its commit slides the row away (`leftExits`). RIGHT is
   additive — the row stays exactly where it was, because adding an item to a
   transfer doesn't mean the shelf work is done. */
import { useRef, useState } from "react";

/** How far the row must travel before a swipe commits. */
export const SWIPE_COMMIT_PX = 96;
/** Exit animation length — callers time optimistic mutations against it. */
export const SWIPE_EXIT_MS = 220;

export interface SwipeOptions {
  /** commit on a left swipe; the row slides out unless leftExits is false */
  onLeft?: () => void;
  /** commit on a right swipe; the row springs back */
  onRight?: () => void;
  leftExits?: boolean;
  disabled?: boolean;
}

export interface SwipeRow {
  /** current travel in px: negative = left, positive = right */
  dx: number;
  dragging: boolean;
  leaving: boolean;
  canLeft: boolean;
  canRight: boolean;
  /** spread onto the element that moves */
  handlers: {
    onPointerDown: (e: React.PointerEvent) => void;
    onPointerMove: (e: React.PointerEvent) => void;
    onPointerUp: () => void;
    onPointerCancel: () => void;
  };
  /** true once, right after a committed swipe — swallow the synthetic click */
  swallowClick: () => boolean;
  /** style for the moving element (transform + touch-action) */
  motionStyle: React.CSSProperties;
}

export function useSwipeRow({
  onLeft,
  onRight,
  leftExits = true,
  disabled = false,
}: SwipeOptions): SwipeRow {
  const [dx, setDx] = useState(0);
  // The commit test reads this ref, never the state: on a fast flick the last
  // pointermove and the pointerup land in the same frame, so the state in
  // endDrag's closure is still the previous value and the swipe would be
  // silently dropped. The state exists only to drive the paint.
  const dxRef = useRef(0);
  // Whether the finger is down has to be state, not a ref: the transition is
  // decided at render, and a ref read there is stale, so the row kept its
  // 200ms ease WHILE being dragged and lagged behind the finger.
  const [dragging, setDragging] = useState(false);
  // Slide-and-collapse on the way out. Without it an optimistic update pulls
  // the row from the array the instant the swipe commits, so it vanishes
  // mid-gesture instead of leaving.
  const [leaving, setLeaving] = useState(false);
  const drag = useRef<{ x0: number; y0: number; axis: "" | "x" | "y" } | null>(null);
  // A swipe ends with a synthetic click on some browsers; this swallows it so
  // a gesture can never also fire the row's tap action.
  const swallow = useRef(false);

  const canLeft = Boolean(onLeft) && !disabled;
  const canRight = Boolean(onRight) && !disabled;

  const onPointerDown = (e: React.PointerEvent) => {
    if ((!canLeft && !canRight) || e.pointerType === "mouse" || leaving) return;
    drag.current = { x0: e.clientX, y0: e.clientY, axis: "" };
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const mx = e.clientX - d.x0;
    const my = e.clientY - d.y0;
    if (d.axis === "") {
      if (Math.abs(mx) < 8 && Math.abs(my) < 8) return;
      // Commit to an axis once: a vertical intent stays a scroll, forever.
      d.axis = Math.abs(mx) > Math.abs(my) ? "x" : "y";
    }
    if (d.axis !== "x") return;
    // each direction only travels if it has an action behind it
    const next = mx < 0 ? (canLeft ? mx : 0) : canRight ? mx : 0;
    dxRef.current = next;
    if (!dragging) setDragging(true);
    setDx(next);
  };

  const endDrag = () => {
    const d = drag.current;
    drag.current = null;
    setDragging(false);
    if (d?.axis === "x" && canLeft && dxRef.current <= -SWIPE_COMMIT_PX) {
      swallow.current = true;
      const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      if (!leftExits || reduced) {
        onLeft?.();
      } else {
        // Let it finish leaving, THEN drop it from the list — an optimistic
        // mutation would delete the row mid-animation.
        setLeaving(true);
        window.setTimeout(() => onLeft?.(), SWIPE_EXIT_MS);
        return;
      }
    } else if (d?.axis === "x" && canRight && dxRef.current >= SWIPE_COMMIT_PX) {
      swallow.current = true;
      onRight?.();
    }
    dxRef.current = 0;
    setDx(0);
  };

  return {
    dx,
    dragging,
    leaving,
    canLeft,
    canRight,
    handlers: { onPointerDown, onPointerMove, onPointerUp: endDrag, onPointerCancel: endDrag },
    swallowClick: () => {
      if (!swallow.current) return false;
      swallow.current = false;
      return true;
    },
    motionStyle: {
      transform: leaving
        ? "translate3d(-100%,0,0)"
        : dx
          ? `translate3d(${dx}px,0,0)`
          : undefined,
      // No transition while the finger is down, or the row lags behind it.
      transition: dragging ? "none" : `transform ${SWIPE_EXIT_MS}ms var(--ease-spring)`,
      willChange: dragging || leaving ? "transform" : undefined,
      touchAction: canLeft || canRight ? "pan-y" : undefined,
    },
  };
}

/** The colored panel revealed under a swiping row. */
export function SwipeBackdrop({
  side,
  label,
  dx,
  tone = "primary",
}: {
  side: "left" | "right";
  label: string;
  dx: number;
  tone?: "primary" | "tertiary";
}) {
  const travel = side === "left" ? dx : -dx;
  if (travel <= 0) return null;
  return (
    <span
      aria-hidden
      style={{ opacity: Math.min(1, travel / SWIPE_COMMIT_PX) }}
      className={`absolute inset-y-0 ${side === "left" ? "left-0" : "right-0"} flex items-center
        gap-1.5 rounded-(--radius-lg) px-4 text-[13px] font-medium ${
          tone === "primary"
            ? "bg-primary-container text-on-primary-container"
            : "bg-tertiary-container text-on-tertiary-container"
        }`}
    >
      {label}
    </span>
  );
}

/** "That one just joined the transfer" — the row bounces first, and the
 *  floating bubble bumps a beat later (see transferDraft's pulse), so the eye
 *  follows the item from the list into the pill. */
export function useAddedBounce(ms = 450) {
  const [id, setId] = useState<number | null>(null);
  const timer = useRef(0);
  const bounce = (next: number) => {
    window.clearTimeout(timer.current);
    setId(next);
    timer.current = window.setTimeout(() => setId(null), ms);
  };
  return { bouncing: id, bounce };
}

/** Collapse styles for a row on its way out (pairs with `leaving`). */
export function leavingStyle(leaving: boolean): React.CSSProperties {
  return leaving
    ? {
        maxHeight: 0,
        opacity: 0,
        marginBottom: "-0.5rem", // cancels the list's gap as it closes
        transition: `max-height ${SWIPE_EXIT_MS}ms ease, opacity ${SWIPE_EXIT_MS}ms ease,
          margin-bottom ${SWIPE_EXIT_MS}ms ease`,
      }
    : { maxHeight: 200, transition: `max-height ${SWIPE_EXIT_MS}ms ease` };
}
