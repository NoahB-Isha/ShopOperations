/* Long-press a row to scroll a name too long to fit.

   Noah, 2026-08-18: shelf names run long ("Shikakai & Jatamansi Certified
   Organic Strengthening Shampoo - 30ml (Bloom)") and a phone row shows the
   first half. Press and hold anywhere on the CARD; the name travels out and
   back, once, then stops. No layout shift, no dialog, nothing to dismiss.

   The press target is the card, not the text (Noah's follow-up: "can it be the
   whole card?"). The card marks itself with `data-name-press` and this
   component finds it with `closest()`, attaching NATIVE listeners to it —
   which is what lets the gesture coexist with the React handlers those rows
   already carry (the restock row is a `role="checkbox"` button wearing
   useSwipeRow's pointer handlers; spreading a second set would clobber the
   first). Without the attribute it falls back to the text itself, so a row
   that hasn't opted in still behaves.

   Three things that cost a rewrite each:
     * IDLE is one `truncate` span — that's what draws the "…". RUNNING swaps
       to a clipping outer plus an `inline-block w-max` inner, because clipping
       the overflow on the element you translate slides the ellipsis along and
       reveals nothing.
     * iOS Safari answers a long press on text by starting a SELECTION and
       taking the gesture, which fires pointercancel and kills the hold before
       it fires. The `.scrolling-name` / `[data-name-press]` rule in tokens.css
       suppresses that, on coarse pointers only.
     * the card is usually tappable, so the click after a press would tick the
       item off. One click is eaten in the capture phase.
*/
import { useEffect, useLayoutEffect, useRef, useState } from "react";

const HOLD_MS = 350;
/** finger drift allowed before we call it a scroll or a swipe, not a hold */
const DRIFT = 8;
/** travel speed in px/sec — readable, not a ticker tape. Measured against the
 *  worst real name in the catalog (359px hidden on a 375px screen): at 70px/s
 *  that was a ten-second round trip nobody would wait out. */
const SPEED = 130;
/** breathing room between the two ticker copies, so the name doesn't run
 *  straight into itself */
const TICKER_GAP = 48;
/** how many times the name comes round before the row goes quiet again — one
 *  full pass reads as a glitch, three is a fidget */
const TICKER_LOOPS = 2;

export function ScrollingText({ text, className = "" }: { text: string; className?: string }) {
  const box = useRef<HTMLSpanElement>(null);
  const inner = useRef<HTMLSpanElement>(null);
  const [overflow, setOverflow] = useState(0);

  /* The gesture, wired to the card. Native listeners, refs for the in-flight
     state — a re-render mid-press must not restart the timer. */
  useEffect(() => {
    const label = box.current;
    if (!label) return;
    const target: HTMLElement = label.closest<HTMLElement>("[data-name-press]") ?? label;

    let timer: number | null = null;
    let from: { x: number; y: number } | null = null;

    const clear = () => {
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
      from = null;
    };

    /* The card is usually tappable, so the click that follows a long press
       would toggle it. Swallow exactly one, in the capture phase, and disarm
       on a timer in case no click arrives (a cancelled touch) so the trap
       can't eat someone's next real tap. Same discipline as
       SwipeRow.swallowClick. */
    const swallowNextClick = () => {
      const kill = (e: MouseEvent) => {
        e.stopPropagation();
        e.preventDefault();
      };
      document.addEventListener("click", kill, { capture: true, once: true });
      window.setTimeout(() => document.removeEventListener("click", kill, { capture: true }), 700);
    };

    const fire = () => {
      timer = null;
      const el = box.current;
      if (!el) return;
      const hidden = el.scrollWidth - el.clientWidth;
      if (hidden <= 1) return; // nothing hidden, nothing to reveal
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
      swallowNextClick();
      setOverflow(hidden);
    };

    const down = (e: PointerEvent) => {
      from = { x: e.clientX, y: e.clientY };
      timer = window.setTimeout(fire, HOLD_MS);
    };
    const move = (e: PointerEvent) => {
      if (!from) return;
      if (Math.abs(e.clientX - from.x) > DRIFT || Math.abs(e.clientY - from.y) > DRIFT) clear();
    };
    // a long press on a phone otherwise raises the selection menu over the
    // thing we just started scrolling
    const menu = (e: Event) => {
      if (overflow) e.preventDefault();
    };

    target.addEventListener("pointerdown", down);
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", clear);
    target.addEventListener("pointercancel", clear);
    target.addEventListener("contextmenu", menu);
    return () => {
      clear();
      target.removeEventListener("pointerdown", down);
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", clear);
      target.removeEventListener("pointercancel", clear);
      target.removeEventListener("contextmenu", menu);
    };
  }, [overflow]);

  /* iPod ticker (Noah, 2026-08-19: "all the way across like how iPods used
     to"): the name travels fully off to the left while a second copy follows
     it in from the right, so the whole name passes through rather than easing
     out and back. Two copies + a gap + a LINEAR loop is what makes the seam
     invisible; the distance is one copy plus the gap, so when the animation
     wraps, copy two sits exactly where copy one started. */
  useLayoutEffect(() => {
    const el = inner.current;
    if (!overflow || !el) return;
    const first = el.firstElementChild as HTMLElement | null;
    const distance = first ? first.offsetWidth + TICKER_GAP : overflow;
    const anim = el.animate(
      [{ transform: "translateX(0)" }, { transform: `translateX(-${distance}px)` }],
      {
        duration: Math.max(900, (distance / SPEED) * 1000),
        easing: "linear",
        iterations: TICKER_LOOPS,
      },
    );
    const done = () => setOverflow(0); // back to the plain truncated row
    anim.addEventListener("finish", done);
    anim.addEventListener("cancel", done);
    return () => anim.cancel();
  }, [overflow]);

  if (overflow) {
    return (
      <span
        ref={box}
        title={text}
        className={`scrolling-name block overflow-hidden whitespace-nowrap ${className}`}
      >
        <span ref={inner} className="inline-flex w-max whitespace-nowrap">
          <span className="shrink-0">{text}</span>
          {/* the copy that follows it in, so there is no empty gap mid-cycle */}
          <span aria-hidden className="shrink-0" style={{ paddingLeft: TICKER_GAP }}>
            {text}
          </span>
        </span>
      </span>
    );
  }
  return (
    <span ref={box} title={text} className={`scrolling-name block truncate ${className}`}>
      {text}
    </span>
  );
}
