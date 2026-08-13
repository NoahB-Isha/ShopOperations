/* The half-built request, following you around.

   Whose request depends on the role: the Inventory Flow Manager builds a
   TRANSFER, the Floor Team builds an ASK (they can't raise transfers). One
   draft, one bubble, two destinations.

   A draft survives navigation (it lives in transferDraft.ts), but nothing
   said so — you could add three items from the restock list, walk to another
   page, and have no way back except remembering the route. This is that way
   back: a floating pill with the item count that opens the transfer page.

   It follows you everywhere a draft exists, and it does NOT hide: the way to
   get it out of your way is to FLING it somewhere else (it carries momentum
   and settles against the edges; the spot sticks per device), and the way to
   make it go away is to place the request.

   Three motions, all in the same liquid vocabulary (tokens.css):
     · arrive  — squash, stretch, settle
     · bump    — an item joined the draft (a beat after the row's own bounce)
     · burst   — you reached the transfer page; it blows into it

   All of them honor prefers-reduced-motion through the global rule. */
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { markBurst, useDraftLines } from "../transferDraft";
import { clearBubbleAnchor, setBubbleAnchor, useArrival } from "./flyToBubble";
import { fmtQty } from "../pages/shared/OpsBits";

/** Where the draft lives — the pill has nothing to say once you're there.
 *  The Inventory Flow Manager's draft becomes a transfer; the Floor Team's
 *  becomes an ask on /request-items (they can't raise transfers). Same
 *  draft, same bubble, different destination. */
const TRANSFER_PATHS = ["/transfer-requests", "/transfer-requests/new"];
const REQUEST_PATH = "/request-items";
const POSITION_KEY = "ilops_transfer_bubble_pos";
const EDGE = 12; // keeps the pill off the very edge of the viewport
/** Below this, a pointer gesture is a tap (open) rather than a drag (move). */
const DRAG_SLOP_PX = 5;
const EXIT_MS = 300; // must match --animate-bubble-out
const BURST_MS = 420; // must match --animate-bubble-burst
const BUMP_MS = 420; // must match --animate-bubble-bump
const ENTER_MS = 550; // must match --animate-bubble-in
const COUNT_MS = 1100; // must match --animate-count-pop

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

/** Where it sits before anyone moves it: inset from the corner, well clear of
 *  the phone's bottom navigation and the snackbar row — it should read as
 *  floating over the page, not stuck to its edge. */
function defaultPos(w: number, h: number, vw: number, vh: number): Pos {
  const phone = vw < 768;
  return {
    x: vw - w - (phone ? 20 : 40),
    y: vh - h - (phone ? 200 : 96),
  };
}

export function TransferDraftBubble() {
  const lines = useDraftLines();
  // a ball thrown by a swipe (or a menu) just landed — see shell/flyToBubble
  const arrival = useArrival();
  const location = useLocation();
  const navigate = useNavigate();
  const { roles } = useAuth();
  const canTransfer = roles.has("shoppe_floor") || roles.has("admin");
  const canAsk = roles.has("floor_rotating");
  const home = canTransfer ? TRANSFER_PATHS[1] : REQUEST_PATH;

  const atDraftPage = canTransfer
    ? TRANSFER_PATHS.includes(location.pathname)
    : location.pathname === REQUEST_PATH;
  const show = (canTransfer || canAsk) && lines.length > 0 && !atDraftPage;

  // rendered outlives `show` by one exit animation. Which exit depends on WHY
  // it's going: reaching the transfer page is a burst (the draft is about to
  // fill that page), an emptied draft is a plain shrink.
  const [rendered, setRendered] = useState(show);
  const [exit, setExit] = useState<"" | "out" | "burst">("");
  useEffect(() => {
    if (show) {
      setExit("");
      setRendered(true);
      return;
    }
    if (!rendered) return;
    const bursting = lines.length > 0 && atDraftPage;
    setExit(bursting ? "burst" : "out");
    if (bursting) markBurst();
    const t = window.setTimeout(() => setRendered(false), bursting ? BURST_MS : EXIT_MS);
    return () => window.clearTimeout(t);
  }, [show, rendered, lines.length, atDraftPage]);

  // the ball lands: catch it (bump) and float the quantity that joined
  const [bumping, setBumping] = useState(false);
  const [gained, setGained] = useState<{ qty: number; seq: number } | null>(null);
  const firstArrival = useRef(true);
  useEffect(() => {
    if (firstArrival.current) {
      firstArrival.current = false;
      return;
    }
    setBumping(true);
    setGained({ qty: arrival.qty, seq: arrival.seq });
    const stopBump = window.setTimeout(() => setBumping(false), BUMP_MS);
    const clearGain = window.setTimeout(
      () => setGained((g) => (g?.seq === arrival.seq ? null : g)),
      COUNT_MS,
    );
    return () => {
      window.clearTimeout(stopBump);
      window.clearTimeout(clearGain);
    };
  }, [arrival]);

  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<Pos | null>(readStoredPos);
  // mirrored: the pointerup handler and the momentum loop both need the live
  // position, and reading it from a closure can be a frame stale
  const posRef = useRef<Pos | null>(pos);
  posRef.current = pos;
  const drag = useRef<{
    dx: number;
    dy: number;
    moved: boolean;
    vx: number;
    vy: number;
    lastX: number;
    lastY: number;
    lastT: number;
  } | null>(null);
  const [dragging, setDragging] = useState(false);
  const glide = useRef(0);
  // the entrance animation has fill-mode `both`, so its final transform would
  // outrank the hover scale — drop the class once it has played
  const [entering, setEntering] = useState(true);

  const savePos = useCallback(() => {
    try {
      const p = posRef.current;
      if (p) localStorage.setItem(POSITION_KEY, JSON.stringify(p));
    } catch {
      /* private browsing — the spot just won't stick */
    }
  }, []);

  // measure once mounted: the default position and every clamp need the
  // pill's real size, which depends on the item count text
  const retry = useRef(0);
  const place = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    // offsetWidth/Height, never getBoundingClientRect: the entrance animation
    // scales the pill, and a transformed rect makes it measure a third of its
    // real size — which parks it half off the right edge of a phone
    const width = el.offsetWidth;
    const height = el.offsetHeight;
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
      cancelAnimationFrame(glide.current);
    };
  }, [rendered, lines.length, place]);

  // The entrance class must come off on a TIMER, not on animationend: a tab
  // that was backgrounded mid-animation never delivers the event, and the
  // class then outranks every later motion (the bump would never show).
  // the flight target is the pill's centre, wherever it currently sits
  useEffect(() => {
    const el = ref.current;
    if (!rendered || !pos || !el) return;
    setBubbleAnchor(pos.x + el.offsetWidth / 2, pos.y + el.offsetHeight / 2);
  }, [rendered, pos, lines.length]);
  useEffect(() => () => clearBubbleAnchor(), []);

  useEffect(() => {
    if (!show) return;
    setEntering(true);
    const t = window.setTimeout(() => setEntering(false), ENTER_MS);
    return () => window.clearTimeout(t);
  }, [show]);

  /** Fling physics: keep the last pointer velocity, then coast with friction
   *  and stop dead at the edges. It should feel thrown, not dragged-and-set. */
  const coast = (vx: number, vy: number) => {
    cancelAnimationFrame(glide.current);
    const el = ref.current;
    if (!el) return;
    const width = el.offsetWidth;
    const height = el.offsetHeight;
    let speedX = vx;
    let speedY = vy;
    let last = performance.now();
    const step = (now: number) => {
      const dt = Math.min(32, now - last); // a backgrounded tab must not teleport it
      last = now;
      const { vw, vh } = viewport();
      const current = posRef.current;
      if (!vw || !vh || !current) return;
      const moved = { x: current.x + speedX * dt, y: current.y + speedY * dt };
      const next = clamp(moved, width, height, vw, vh);
      // hitting an edge kills that axis rather than bouncing — a pill that
      // ricochets around the screen reads as a bug, not as personality
      if (next.x !== moved.x) speedX = 0;
      if (next.y !== moved.y) speedY = 0;
      setPos(next);
      const friction = Math.pow(0.9, dt / 16);
      speedX *= friction;
      speedY *= friction;
      if (Math.abs(speedX) > 0.02 || Math.abs(speedY) > 0.02) {
        glide.current = requestAnimationFrame(step);
      } else {
        savePos();
      }
    };
    glide.current = requestAnimationFrame(step);
  };

  const onPointerDown = (e: React.PointerEvent) => {
    const el = ref.current;
    if (!el || exit) return;
    cancelAnimationFrame(glide.current);
    // grab offset against the LOGICAL position, so a running bump animation
    // can't make the pill jump under the finger
    const origin = posRef.current ?? { x: el.offsetLeft, y: el.offsetTop };
    drag.current = {
      dx: e.clientX - origin.x,
      dy: e.clientY - origin.y,
      moved: false,
      vx: 0,
      vy: 0,
      lastX: e.clientX,
      lastY: e.clientY,
      lastT: performance.now(),
    };
    // capture keeps the drag alive if the finger leaves the pill. It can
    // legitimately fail (a pointer already released), and that must never
    // cost us the gesture — a throw here would swallow the tap.
    try {
      el.setPointerCapture(e.pointerId);
    } catch {
      /* no capture — the drag still works while the pointer stays on the pill */
    }
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    const el = ref.current;
    if (!d || !el) return;
    const { vw, vh } = viewport();
    const next = clamp(
      { x: e.clientX - d.dx, y: e.clientY - d.dy },
      el.offsetWidth,
      el.offsetHeight,
      vw,
      vh,
    );
    const from = posRef.current ?? next;
    // ignore the jitter of a tap: only a real move becomes a drag
    if (!d.moved && Math.abs(next.x - from.x) + Math.abs(next.y - from.y) < DRAG_SLOP_PX) return;
    if (!d.moved) {
      d.moved = true;
      setDragging(true);
    }
    const now = performance.now();
    const dt = now - d.lastT;
    if (dt > 0) {
      // px per ms, smoothed — a single jittery frame shouldn't decide the throw
      d.vx = 0.6 * ((e.clientX - d.lastX) / dt) + 0.4 * d.vx;
      d.vy = 0.6 * ((e.clientY - d.lastY) / dt) + 0.4 * d.vy;
      d.lastX = e.clientX;
      d.lastY = e.clientY;
      d.lastT = now;
    }
    setPos(next);
  };

  const endDrag = (e: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    setDragging(false);
    try {
      ref.current?.releasePointerCapture?.(e.pointerId);
    } catch {
      /* never captured — nothing to release */
    }
    if (!d) return;
    if (!d.moved) {
      navigate(home); // a tap is "take me there"
      return;
    }
    // a slow set-down keeps its spot; a flick keeps flying
    if (Math.abs(d.vx) > 0.05 || Math.abs(d.vy) > 0.05) coast(d.vx, d.vy);
    else savePos();
  };

  if (!rendered) return null;

  const units = lines.reduce((sum, l) => sum + l.qty, 0);
  const motion = exit
    ? exit === "burst"
      ? "animate-bubble-burst pointer-events-none"
      : "animate-bubble-out pointer-events-none"
    : entering
      ? "animate-bubble-in"
      : bumping
        ? "animate-bubble-bump"
        : dragging
          ? "scale-105 shadow-(--shadow-e3)"
          : "transition-transform duration-200 ease-(--ease-spring) hover:scale-[1.04]";

  return (
    <>
      {exit === "burst" && <BurstSparks pos={pos} />}
      {gained && pos && (
        <span
          key={gained.seq}
          aria-hidden
          className="animate-count-pop pointer-events-none fixed z-50 text-[15px] font-bold
            text-primary drop-shadow-sm"
          style={{ left: pos.x + 12, top: pos.y - 18 }}
        >
          +{fmtQty(gained.qty)}
        </span>
      )}
      <div
        ref={ref}
        role="button"
        tabIndex={0}
        aria-label={`${canTransfer ? "Transfer request" : "Item request"} in progress — ${
          lines.length
        } item${lines.length === 1 ? "" : "s"}. Open it, or drag to move.`}
        title={`Open the ${
          canTransfer ? "transfer" : "request"
        } you're building — drag to move it out of the way`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            navigate(home);
          }
        }}
        style={{
          left: pos?.x ?? -9999, // off-screen for the one frame before measuring
          top: pos?.y ?? -9999,
          touchAction: "none", // the pill owns its gestures; the page still scrolls elsewhere
          cursor: dragging ? "grabbing" : "grab",
        }}
        className={`state-layer fixed z-40 flex touch-none items-center gap-2.5 rounded-full
          bg-primary-container py-2.5 pr-4 pl-3.5 text-on-primary-container shadow-(--shadow-e2)
          select-none ${motion}`}
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
        {/* nowrap: a narrow phone would otherwise wrap this into a paragraph */}
        <span className="text-left text-[13px] leading-tight font-semibold whitespace-nowrap">
          {canTransfer ? "Transfer request" : "Item request"}
          <span className="block text-[11.5px] font-medium opacity-80">
            {lines.length} item{lines.length === 1 ? "" : "s"} · {fmtQty(units)} units
          </span>
        </span>
      </div>
    </>
  );
}

/** The confetti of the burst — eight sparks thrown outward from the pill.
 *  Purely decorative, and gone with the animation. */
function BurstSparks({ pos }: { pos: Pos | null }) {
  if (!pos) return null;
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed z-40"
      style={{ left: pos.x + 24, top: pos.y + 22 }}
    >
      {Array.from({ length: 8 }).map((_, i) => {
        const angle = (i / 8) * Math.PI * 2;
        return (
          <span
            key={i}
            className="animate-burst-spark absolute h-2 w-2 rounded-full bg-primary"
            style={
              {
                "--spark-x": `${Math.cos(angle) * 90}px`,
                "--spark-y": `${Math.sin(angle) * 90}px`,
                animationDelay: `${i * 8}ms`,
              } as React.CSSProperties
            }
          />
        );
      })}
    </div>
  );
}
