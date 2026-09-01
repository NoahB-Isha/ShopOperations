/* "It went into the bubble" — the swipe action morphs into a ball and gets
   slingshot across the screen into the floating transfer pill.

   Imperative on purpose: one absolutely-positioned <div> appended to <body>
   and driven by the Web Animations API with transform + opacity ONLY. No
   React state changes during the flight, nothing that can relayout, so it
   stays smooth while the list behind it re-renders.

   The arc is sampled into keyframes (a quadratic Bézier with a wind-up), and
   the animation runs LINEAR — the easing lives in how the samples are spaced,
   which is what makes a slingshot read as a slingshot: slow pull back, sharp
   release, long decelerating flight. */

import { useSyncExternalStore } from "react";

import { playWhoosh } from "../sound";

/** Where the pill currently is, in viewport coordinates. The bubble keeps
 *  this up to date as it's placed, dragged and flung. */
let anchor: { x: number; y: number } | null = null;

export function setBubbleAnchor(x: number, y: number): void {
  anchor = { x, y };
}

export function clearBubbleAnchor(): void {
  anchor = null;
}

/* --- arrivals: the bubble bumps and shows "+N" when a ball lands ---------- */

let arrival = { qty: 0, seq: 0 };
const listeners = new Set<() => void>();

function announceArrival(qty: number): void {
  arrival = { qty, seq: arrival.seq + 1 };
  for (const fn of listeners) fn();
}

export function useArrival(): { qty: number; seq: number } {
  return useSyncExternalStore(
    (fn) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    () => arrival,
    () => arrival,
  );
}

/* --- the flight ---------------------------------------------------------- */

/** A small launch box around a point — for menus, where the "action" is
 *  wherever the cursor was. */
export function boxAt(x: number, y: number): FlyOrigin {
  return { left: x - 18, top: y - 18, width: 36, height: 36 };
}

/** Last resort when nothing knows where the action happened. */
export function centerOf(): FlyOrigin {
  return boxAt(window.innerWidth / 2, window.innerHeight / 2);
}

export interface FlyOrigin {
  left: number;
  top: number;
  width: number;
  height: number;
}

const BALL = 30; // px — the ball's real size; the pill shape is a scale of it
const WIND_UP = 0.16; // fraction of the timeline spent pulling back
const DURATION = 560;
const SAMPLES = 34;
/** Close enough to the pill to call it a hit — the bump fires HERE, not at
 *  the formal end of the timeline. A hard-decelerating tail covers a couple
 *  of pixels over its last 60ms, and waiting for it read as lag. */
const HIT_PX = 10;

const easeOutQuad = (t: number) => 1 - (1 - t) * (1 - t);
/** Sharp release, then settle — the slingshot's signature. */
const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3);

function reducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

/** Wait a moment for the bubble to mount and place itself — on the FIRST
 *  item there is no pill yet when the ball launches.
 *
 *  On a TIMER, never rAF: a hidden tab pauses animation frames entirely, and
 *  the arrival (the "+N" and the bump) must land either way. */
function withAnchor(waitMs: number, run: (target: { x: number; y: number } | null) => void): void {
  if (anchor) {
    run(anchor);
    return;
  }
  if (waitMs <= 0) {
    run(null); // no pill to fly to — announce anyway, the count still changed
    return;
  }
  window.setTimeout(() => withAnchor(waitMs - 40, run), 40);
}

/** The sampled arc, as WAAPI keyframes. Pure — the shape of the throw is
 *  unit-tested; only the DOM plumbing below is not. */
export function buildFlightFrames(
  from: { x: number; y: number },
  target: { x: number; y: number },
  origin: { width: number; height: number },
): Keyframe[] {
  const dx = target.x - from.x;
  const dy = target.y - from.y;
  const distance = Math.hypot(dx, dy) || 1;

  // pull back along the reverse of the launch direction, like drawing a sling
  const back = Math.min(34, distance * 0.12);
  const pull = { x: from.x - (dx / distance) * back, y: from.y - (dy / distance) * back };

  // control point lifted above the flight, so it arcs over the page rather
  // than sliding across it
  const lift = Math.min(180, distance * 0.42) + 40;
  const control = { x: (pull.x + target.x) / 2, y: Math.min(pull.y, target.y) - lift };

  // the morph: start as the action label's box, collapse into the ball
  const startScaleX = Math.max(1, origin.width / BALL);
  const startScaleY = Math.max(1, origin.height / BALL);

  const frames: Keyframe[] = [];
  for (let i = 0; i <= SAMPLES; i++) {
    const t = i / SAMPLES;
    if (t <= WIND_UP) {
      const u = easeOutQuad(t / WIND_UP);
      const x = from.x + (pull.x - from.x) * u;
      const y = from.y + (pull.y - from.y) * u;
      frames.push({
        offset: t,
        transform: `translate(${(x - from.x).toFixed(2)}px, ${(y - from.y).toFixed(2)}px) scale(${(
          startScaleX + (1 - startScaleX) * u
        ).toFixed(3)}, ${(startScaleY + (1 - startScaleY) * u).toFixed(3)})`,
        opacity: 1,
      });
      continue;
    }
    // flight: quadratic Bézier pull → control → target, decelerating
    const u = easeOutCubic((t - WIND_UP) / (1 - WIND_UP));
    const inv = 1 - u;
    const x = inv * inv * pull.x + 2 * inv * u * control.x + u * u * target.x;
    const y = inv * inv * pull.y + 2 * inv * u * control.y + u * u * target.y;
    // stretches out of the sling, shrinks as it drops into the pill
    const scale = u < 0.25 ? 1 + u * 0.7 : 1.18 - (u - 0.25) * 0.9;
    frames.push({
      offset: t,
      transform: `translate(${(x - from.x).toFixed(2)}px, ${(y - from.y).toFixed(2)}px) scale(${scale.toFixed(
        3,
      )}) rotate(${(u * 40).toFixed(1)}deg)`,
      // stays solid all the way in — it disappears INTO the pill, and the
      // pill's bump is what carries the last beat
      opacity: 1,
    });
  }
  return frames;
}

/** The moment the ball is effectively on the pill, as a fraction of the
 *  timeline — the bump is scheduled here so the catch looks instant. */
export function hitOffset(
  frames: Keyframe[],
  from: { x: number; y: number },
  target: { x: number; y: number },
): number {
  const wantX = target.x - from.x;
  const wantY = target.y - from.y;
  for (const f of frames) {
    const m = /translate\(([-\d.]+)px, ([-\d.]+)px\)/.exec(String(f.transform));
    if (!m) continue;
    if (Math.hypot(Number(m[1]) - wantX, Number(m[2]) - wantY) <= HIT_PX) {
      return (f.offset as number) ?? 1;
    }
  }
  return 1;
}

/**
 * Launch a ball from `origin` (the swipe action's own box, so it reads as
 * that label morphing) into the transfer bubble. Calls back into the bubble
 * on arrival — which is when the count "+N" and the bump happen, not when the
 * data changed. The draft itself is updated by the caller, first and always:
 * an animation must never be load-bearing for data.
 */
export function flyToBubble(origin: FlyOrigin, qty: number): void {
  if (typeof document === "undefined" || document.hidden || reducedMotion()) {
    announceArrival(qty); // no flight worth watching — just land it
    return;
  }
  playWhoosh(); // the slingshot's sound — one call site covers every add gesture

  withAnchor(240, (target) => {
    if (!target) {
      announceArrival(qty);
      return;
    }
    const from = { x: origin.left + origin.width / 2, y: origin.top + origin.height / 2 };

    const ball = document.createElement("div");
    ball.setAttribute("aria-hidden", "true");
    ball.style.cssText = `position:fixed;left:${from.x - BALL / 2}px;top:${from.y - BALL / 2}px;
      width:${BALL}px;height:${BALL}px;border-radius:9999px;z-index:60;pointer-events:none;
      background:var(--color-primary);box-shadow:var(--shadow-e2);will-change:transform,opacity`;
    document.body.appendChild(ball);

    const frames = buildFlightFrames(from, target, origin);
    const animation = ball.animate(frames, {
      duration: DURATION,
      easing: "linear",
      fill: "forwards",
    });
    let landed = false;
    const land = () => {
      if (landed) return;
      landed = true;
      ball.remove();
      announceArrival(qty);
    };
    animation.oncancel = land;
    // fire on contact, not on completion — the pill bumps as the ball touches
    // it, and the ball is removed in the same beat
    window.setTimeout(land, hitOffset(frames, from, target) * DURATION);
    // belt and braces: a tab backgrounded mid-flight may never run the timer
    animation.onfinish = land;
  });
}
