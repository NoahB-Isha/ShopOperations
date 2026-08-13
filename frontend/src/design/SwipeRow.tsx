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
/** Past four fifths of its own width, an additive row stops being a row and
 *  starts becoming the ball it's about to throw. */
export const MORPH_START = 0.8;
/** The ball's size — matched to shell/flyToBubble so the hand-off on release
 *  is invisible: the flight starts at exactly the size the row ended at. */
export const MORPH_BALL_PX = 30;

/** The box the revealed action label occupies — handed to the commit callback
 *  so it can launch something from exactly where the label was. */
export interface ActionBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface SwipeOptions {
  /** commit on a left swipe; the row slides out unless leftExits is false */
  onLeft?: (from: ActionBox) => void;
  /** commit on a right swipe; the row springs back */
  onRight?: (from: ActionBox) => void;
  leftExits?: boolean;
  disabled?: boolean;
  /** right-swipe rows that throw something: past MORPH_START the row itself
   *  shrinks into a ball, tracking the finger, and the ball is what flies. */
  morphOnRight?: boolean;
}

export interface SwipeRow {
  /** current travel in px: negative = left, positive = right */
  dx: number;
  dragging: boolean;
  leaving: boolean;
  canLeft: boolean;
  canRight: boolean;
  /** 0 → 1 as the row collapses into a ball (morphOnRight only) */
  morph: number;
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
  morphOnRight = false,
}: SwipeOptions): SwipeRow {
  const [dx, setDx] = useState(0);
  // the row's own box, measured when the finger lands: the morph is a
  // fraction of the row's width, so a wide desk row and a phone row both
  // start balling up at the same point in the gesture
  const [box, setBox] = useState<{ w: number; h: number }>({ w: 0, h: 0 });
  // after a morph commits, the row must NOT slide back — the ball left it, so
  // it just reappears where it belongs
  const [snap, setSnap] = useState(false);
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
  // the row element, captured on pointerdown: the commit callbacks want to
  // know where the action label sat on screen
  const rowEl = useRef<Element | null>(null);

  const canLeft = Boolean(onLeft) && !disabled;
  const canRight = Boolean(onRight) && !disabled;

  const onPointerDown = (e: React.PointerEvent) => {
    if ((!canLeft && !canRight) || e.pointerType === "mouse" || leaving) return;
    rowEl.current = e.currentTarget;
    const r = e.currentTarget.getBoundingClientRect();
    setBox({ w: r.width, h: r.height });
    drag.current = { x0: e.clientX, y0: e.clientY, axis: "" };
  };

  // how far into "becoming a ball" the row is: nothing until MORPH_START of
  // its own width, then 0 → 1 over the remaining fifth
  const morph =
    morphOnRight && canRight && box.w > 0 && dx > 0
      ? Math.min(1, Math.max(0, (dx / box.w - MORPH_START) / (1 - MORPH_START)))
      : 0;

  /** Travel, capped so a fully formed ball still sits inside the row's own
   *  width — past that the finger keeps going but the ball only shrinks,
   *  instead of sliding out of the list and getting clipped. */
  const travelFor = (raw: number) =>
    morph > 0 ? Math.min(raw, Math.max(0, box.w - MORPH_BALL_PX - 8)) : raw;

  /** Where the ball currently sits — its left edge tracks the finger, because
   *  the row scales from its own left edge. */
  const ballBox = (): ActionBox => {
    const r = rowEl.current?.getBoundingClientRect();
    if (!r) return { left: 0, top: 0, width: MORPH_BALL_PX, height: MORPH_BALL_PX };
    return {
      left: r.left + travelFor(dxRef.current),
      top: r.top + box.h / 2 - MORPH_BALL_PX / 2,
      width: MORPH_BALL_PX,
      height: MORPH_BALL_PX,
    };
  };

  /** Where the backdrop label sits: pinned to one edge, vertically centred. */
  const actionBox = (side: "left" | "right"): ActionBox => {
    const r = rowEl.current?.getBoundingClientRect();
    if (!r) return { left: 0, top: 0, width: 140, height: 36 };
    const width = Math.min(150, Math.max(90, r.width * 0.42));
    return {
      left: side === "left" ? r.left + 8 : r.right - width - 8,
      top: r.top + r.height / 2 - 18,
      width,
      height: 36,
    };
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
      const labelBox = actionBox("right"); // shadowing the row box would confuse
      if (!leftExits || reduced) {
        onLeft?.(labelBox);
      } else {
        // Let it finish leaving, THEN drop it from the list — an optimistic
        // mutation would delete the row mid-animation.
        setLeaving(true);
        window.setTimeout(() => onLeft?.(labelBox), SWIPE_EXIT_MS);
        return;
      }
    } else if (d?.axis === "x" && canRight && dxRef.current >= SWIPE_COMMIT_PX) {
      swallow.current = true;
      // a fully morphed row hands the flight its exact ball; a shorter swipe
      // throws from the action label instead
      onRight?.(morph > 0.15 ? ballBox() : actionBox("left"));
      if (morph > 0) {
        // no spring-back: the ball is gone, the row simply is where it was
        setSnap(true);
        window.setTimeout(() => setSnap(false), 60);
      }
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
    morph,
    handlers: { onPointerDown, onPointerMove, onPointerUp: endDrag, onPointerCancel: endDrag },
    swallowClick: () => {
      if (!swallow.current) return false;
      swallow.current = false;
      return true;
    },
    motionStyle: {
      // scaling from the left edge keeps the shrinking row pinned to the
      // finger, so the ball forms exactly where the hand is
      transformOrigin: morph > 0 ? "0% 50%" : undefined,
      transform: leaving
        ? "translate3d(-100%,0,0)"
        : morph > 0
          ? `translate3d(${travelFor(dx)}px,0,0) scale(${(
              1 - morph * (1 - MORPH_BALL_PX / Math.max(box.w, 1))
            ).toFixed(4)}, ${(1 - morph * (1 - MORPH_BALL_PX / Math.max(box.h, 1))).toFixed(4)})`
          : dx
            ? `translate3d(${dx}px,0,0)`
            : undefined,
      // rounding follows the shape: a card at 0, a ball at 1. PERCENT, not
      // px — the box is scaled non-uniformly, and only a per-axis radius
      // still reads as a circle at the end of it.
      borderRadius: morph > 0 ? `${Math.min(50, 4 + morph * 60)}%` : undefined,
      // No transition while the finger is down (the row would lag behind it),
      // and none right after a morph committed (the row must not fly back).
      transition: dragging || snap ? "none" : `transform ${SWIPE_EXIT_MS}ms var(--ease-spring)`,
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
  morph = 0,
}: {
  side: "left" | "right";
  label: string;
  dx: number;
  tone?: "primary" | "tertiary";
  /** hand off to the ball: the panel dissolves as the row becomes it */
  morph?: number;
}) {
  const travel = side === "left" ? dx : -dx;
  if (travel <= 0 || morph >= 1) return null;
  return (
    <span
      aria-hidden
      style={{ opacity: Math.min(1, travel / SWIPE_COMMIT_PX) * (1 - morph) }}
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

/** The orange that swallows the row as it becomes the ball — the same
 *  primary the flying ball is painted in, so the hand-off is invisible. */
export function MorphBall({ progress }: { progress: number }) {
  if (progress <= 0) return null;
  return (
    <span
      aria-hidden
      className="pointer-events-none absolute inset-0 rounded-full bg-primary"
      // squared-off early, fully round by the time it leaves
      style={{
        opacity: Math.min(1, progress * 1.6),
        borderRadius: `${Math.min(50, 4 + progress * 60)}%`,
      }}
    />
  );
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
