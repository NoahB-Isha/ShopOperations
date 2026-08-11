/* The half-built transfer request, following you around.

   A draft survives navigation (it lives in transferDraft.ts), but nothing
   said so — you could add three items from the restock list, walk to another
   page, and have no way back except remembering the route. This is that way
   back: a floating pill with the item count that opens the request page.

   It follows you everywhere a draft exists, and it does NOT hide: the way to
   get it out of your way is to DRAG it somewhere else (position sticks per
   device, in localStorage), and the way to make it go away is to place the
   request. It hides only on the request page itself — you're already there.

   Arrives with a bounce, leaves with a wind-up and a shrink; both honor
   prefers-reduced-motion through the global rule in tokens.css. */
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useDraftLines } from "../transferDraft";
import { fmtQty } from "../pages/shared/OpsBits";

const NEW_REQUEST_PATH = "/transfer-requests/new";
const POSITION_KEY = "ilops_transfer_bubble_pos";
const EDGE = 12; // keeps the pill off the very edge of the viewport
/** Below this, a pointer gesture is a tap (open) rather than a drag (move). */
const DRAG_SLOP_PX = 5;
const EXIT_MS = 300; // must match --animate-bubble-out

interface Pos {
  x: number;
  y: number;
}

function readStoredPos(): Pos | null {
  try {
    const raw = localStorage.getItem(POSITION_KEY);
    const p = raw ? (JSON.parse(raw) as Pos) : null;
    return p && Number.isFinite(p.x) && Number.isFinite(p.y) ? p : null;
  } catch {
    return null;
  }
}

/** Viewport size, or zeros while the page is hidden / not yet laid out —
 *  callers must not place the pill against a zero viewport, or every
 *  coordinate clamps into the top-left corner. */
function viewport(): { vw: number; vh: number } {
  return {
    vw: window.innerWidth || document.documentElement.clientWidth || 0,
    vh: window.innerHeight || document.documentElement.clientHeight || 0,
  };
}

function clamp(pos: Pos, w: number, h: number, vw: number, vh: number): Pos {
  return {
    x: Math.min(Math.max(pos.x, EDGE), Math.max(EDGE, vw - w - EDGE)),
    y: Math.min(Math.max(pos.y, EDGE), Math.max(EDGE, vh - h - EDGE)),
  };
}

/** Where it sits before anyone drags it: bottom-right, above the phone's
 *  bottom navigation and clear of the snackbar row. */
function defaultPos(w: number, h: number, vw: number, vh: number): Pos {
  const phone = vw < 768;
  return { x: vw - w - 16, y: vh - h - (phone ? 168 : 24) };
}

export function TransferDraftBubble() {
  const lines = useDraftLines();
  const location = useLocation();
  const navigate = useNavigate();
  const { roles } = useAuth();
  const canRequest = roles.has("shoppe_floor") || roles.has("admin");

  const show =
    canRequest && lines.length > 0 && location.pathname !== NEW_REQUEST_PATH;

  // rendered outlives `show` by one exit animation
  const [rendered, setRendered] = useState(show);
  const [leaving, setLeaving] = useState(false);
  useEffect(() => {
    if (show) {
      setLeaving(false);
      setRendered(true);
      return;
    }
    if (!rendered) return;
    setLeaving(true);
    const t = window.setTimeout(() => setRendered(false), EXIT_MS);
    return () => window.clearTimeout(t);
  }, [show, rendered]);

  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<Pos | null>(readStoredPos);
  // mirrored: the pointerup handler saves the spot, and reading it from the
  // ref can't catch a stale closure mid-gesture
  const posRef = useRef<Pos | null>(pos);
  posRef.current = pos;
  const drag = useRef<{ dx: number; dy: number; moved: boolean } | null>(null);
  const [dragging, setDragging] = useState(false);
  // the entrance animation has fill-mode `both`, so its final transform would
  // outrank the hover scale — drop the class once it has played
  const [entering, setEntering] = useState(true);

  // measure once mounted: the default position and every clamp need the
  // pill's real size, which depends on the item count text
  const retry = useRef(0);
  const place = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const { width, height } = el.getBoundingClientRect();
    const { vw, vh } = viewport();
    // hidden tab / pre-layout: measuring now would park the pill in the
    // top-left corner forever, so wait for a frame with real numbers
    if (!vw || !vh || !width || !height) {
      retry.current = requestAnimationFrame(place);
      return;
    }
    setPos((prev) => clamp(prev ?? defaultPos(width, height, vw, vh), width, height, vw, vh));
  }, []);

  // re-measure on mount, on resize, and whenever the count changes the width
  useEffect(() => {
    if (!rendered) return;
    place();
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("resize", place);
      cancelAnimationFrame(retry.current);
    };
  }, [rendered, lines.length, place]);

  useEffect(() => {
    if (show) setEntering(true);
  }, [show]);

  const onPointerDown = (e: React.PointerEvent) => {
    const el = ref.current;
    if (!el || leaving) return;
    const r = el.getBoundingClientRect();
    drag.current = { dx: e.clientX - r.left, dy: e.clientY - r.top, moved: false };
    el.setPointerCapture(e.pointerId);
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    const el = ref.current;
    if (!d || !el) return;
    const r = el.getBoundingClientRect();
    const { vw, vh } = viewport();
    const next = clamp({ x: e.clientX - d.dx, y: e.clientY - d.dy }, r.width, r.height, vw, vh);
    // ignore the jitter of a tap: only a real move becomes a drag
    if (!d.moved && Math.abs(next.x - r.left) + Math.abs(next.y - r.top) < DRAG_SLOP_PX) return;
    if (!d.moved) {
      d.moved = true;
      setDragging(true);
    }
    setPos(next);
  };

  const endDrag = (e: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    setDragging(false);
    ref.current?.releasePointerCapture?.(e.pointerId);
    if (!d) return;
    if (!d.moved) {
      navigate(NEW_REQUEST_PATH); // a tap is "take me there"
      return;
    }
    try {
      const p = posRef.current;
      if (p) localStorage.setItem(POSITION_KEY, JSON.stringify(p));
    } catch {
      /* private browsing — the spot just won't stick */
    }
  };

  if (!rendered) return null;

  const units = lines.reduce((sum, l) => sum + l.qty, 0);

  return (
    <div
      ref={ref}
      role="button"
      tabIndex={0}
      aria-label={`Transfer request in progress — ${lines.length} item${
        lines.length === 1 ? "" : "s"
      }. Open it, or drag to move.`}
      title="Open the request you're building — drag to move it out of the way"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onAnimationEnd={() => setEntering(false)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          navigate(NEW_REQUEST_PATH);
        }
      }}
      style={{
        left: pos?.x ?? -9999, // off-screen for the one frame before measuring
        top: pos?.y ?? -9999,
        touchAction: "none", // the pill owns its gestures; the page still scrolls elsewhere
        cursor: dragging ? "grabbing" : "grab",
      }}
      className={`state-layer fixed z-40 flex touch-none items-center gap-2.5 rounded-full
        bg-primary-container py-2.5 pr-4 pl-3.5 text-on-primary-container select-none
        shadow-(--shadow-e2) ${
          leaving
            ? "animate-bubble-out pointer-events-none"
            : entering
              ? "animate-bubble-in"
              : dragging
                ? "scale-105 shadow-(--shadow-e3)"
                : "transition-transform duration-200 ease-(--ease-spring) hover:scale-[1.04]"
        }`}
    >
      <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden>
        <path
          d="M2.5 4.5h2l1.7 7.2a1 1 0 0 0 1 .8h5.4a1 1 0 0 0 1-.77l1.1-4.23H5.2"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="7.5" cy="15" r="1" fill="currentColor" />
        <circle cx="12.5" cy="15" r="1" fill="currentColor" />
      </svg>
      <span className="text-left text-[13px] leading-tight font-semibold">
        Transfer request
        {/* keyed on the count so every added item re-pops the line */}
        <span key={lines.length} className="animate-pop block text-[11.5px] font-medium opacity-80">
          {lines.length} item{lines.length === 1 ? "" : "s"} · {fmtQty(units)} units
        </span>
      </span>
    </div>
  );
}
