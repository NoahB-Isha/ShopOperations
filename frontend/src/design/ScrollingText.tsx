/* Long-press a truncated name to scroll the rest of it past.

   Noah, 2026-08-18: shelf names run long ("Shikakai & Jatamansi Certified
   Organic Strengthening Shampoo - 30ml (Bloom)") and a phone row shows the
   first half. Press and hold; the name travels out and back, once, then stops.
   No layout shift, no dialog, nothing to dismiss.

   The structure matters and cost a rewrite to get right. IDLE is a single
   `truncate` span (overflow hidden + ellipsis + nowrap) — that's what draws the
   "…". RUNNING swaps to a clipping outer with an `inline-block w-max` inner:
   the inner is allowed to be wider than the row, so translating it actually
   reveals text. The first version clipped the overflow on the moving element
   itself, which slides the ellipsis along and reveals nothing.

   Three other things this is careful about:
     * it only reacts when the text ACTUALLY overflows, measured on press (a
       rotation, a font swap or a longer sibling changes the answer);
     * a small finger drift while reading is not a cancel, but a real scroll is;
     * `prefers-reduced-motion` gets nothing — the title tooltip still works. */
import { useEffect, useLayoutEffect, useRef, useState } from "react";

const HOLD_MS = 350;
/** finger drift allowed before we call it a scroll rather than a hold */
const DRIFT = 8;
/** travel speed in px/sec — readable, not a ticker tape. Measured against the
 *  worst real name in the catalog ("Shikakai & Jatamansi Certified Organic
 *  Strengthening Shampoo - 30ml (Bloom)", 359px hidden on a 375px screen):
 *  at 70px/s that was a ten-second round trip nobody would wait out. */
const SPEED = 130;
/** and a hard ceiling, so no name can hold the row hostage */
const MAX_MS = 6000;

export function ScrollingText({ text, className = "" }: { text: string; className?: string }) {
  const box = useRef<HTMLSpanElement>(null);
  const inner = useRef<HTMLSpanElement>(null);
  const timer = useRef<number | null>(null);
  const press = useRef<{ x: number; y: number } | null>(null);
  const [overflow, setOverflow] = useState(0);

  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);

  const cancel = () => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = null;
    press.current = null;
  };

  // once we've switched to the scrolling layout, the inner span exists and can
  // be animated — hence layout effect, not a callback on the press
  useLayoutEffect(() => {
    const el = inner.current;
    if (!overflow || !el) return;
    const oneWay = Math.min(MAX_MS / 2, Math.max(600, (overflow / SPEED) * 1000));
    const anim = el.animate(
      [
        { transform: "translateX(0)" },
        { transform: `translateX(-${overflow}px)`, offset: 0.45 },
        { transform: `translateX(-${overflow}px)`, offset: 0.55 },
        { transform: "translateX(0)" },
      ],
      { duration: oneWay * 2, easing: "ease-in-out" },
    );
    const done = () => setOverflow(0); // back to the plain truncated row
    anim.addEventListener("finish", done);
    anim.addEventListener("cancel", done);
    return () => anim.cancel();
  }, [overflow]);

  /* These rows are usually INSIDE something tappable — the restock row is a
     `role="checkbox"` button — so the click that follows a long press would
     tick the item off. Swallow exactly one click in the capture phase, and
     disarm on a timer in case no click ever arrives (a cancelled touch), so
     the trap can't eat someone's next real tap. Same discipline as
     SwipeRow.swallowClick. */
  const swallowNextClick = () => {
    const kill = (e: MouseEvent) => {
      e.stopPropagation();
      e.preventDefault();
    };
    document.addEventListener("click", kill, { capture: true, once: true });
    window.setTimeout(
      () => document.removeEventListener("click", kill, { capture: true }),
      700,
    );
  };

  const start = () => {
    const el = box.current;
    if (!el) return;
    const hidden = el.scrollWidth - el.clientWidth;
    if (hidden <= 1) return; // nothing hidden, nothing to reveal
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    swallowNextClick();
    setOverflow(hidden);
  };

  const handlers = {
    onPointerDown: (e: React.PointerEvent) => {
      press.current = { x: e.clientX, y: e.clientY };
      timer.current = window.setTimeout(() => {
        timer.current = null;
        start();
      }, HOLD_MS);
    },
    onPointerMove: (e: React.PointerEvent) => {
      if (!press.current) return;
      if (
        Math.abs(e.clientX - press.current.x) > DRIFT ||
        Math.abs(e.clientY - press.current.y) > DRIFT
      )
        cancel();
    },
    onPointerUp: cancel,
    onPointerCancel: cancel,
    // a long press otherwise raises the text-selection menu over the thing we
    // just started scrolling
    onContextMenu: (e: React.MouseEvent) => {
      if (overflow) e.preventDefault();
    },
  };

  if (overflow) {
    return (
      <span
        ref={box}
        title={text}
        className={`scrolling-name block overflow-hidden whitespace-nowrap ${className}`}
        {...handlers}
      >
        <span ref={inner} className="inline-block w-max whitespace-nowrap">
          {text}
        </span>
      </span>
    );
  }
  return (
    <span ref={box} title={text} className={`scrolling-name block truncate ${className}`} {...handlers}>
      {text}
    </span>
  );
}
