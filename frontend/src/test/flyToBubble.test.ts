/* The slingshot's shape. The DOM plumbing (append, animate, remove) isn't
   worth a test; the throw is — it has to start where the swipe action was,
   wind up AWAY from the target, arc above the straight line, and land dead on
   the bubble. */
import { buildFlightFrames, hitOffset } from "../shell/flyToBubble";

const FROM = { x: 100, y: 600 };
const TARGET = { x: 340, y: 200 };
const ORIGIN = { width: 150, height: 36 };

/** frames carry `transform: translate(Xpx, Ypx) …` relative to the start */
function offsetOf(frame: Keyframe): { x: number; y: number } {
  const m = /translate\(([-\d.]+)px, ([-\d.]+)px\)/.exec(String(frame.transform));
  if (!m) throw new Error(`no translate in ${frame.transform}`);
  return { x: Number(m[1]), y: Number(m[2]) };
}

test("starts at the action's own box and lands on the bubble", () => {
  const frames = buildFlightFrames(FROM, TARGET, ORIGIN);
  const first = offsetOf(frames[0]);
  expect(first).toEqual({ x: 0, y: 0 });
  // the pill shape at rest: wider than the ball, so it reads as a morph
  expect(String(frames[0].transform)).toMatch(/scale\(5\.000, 1\.200\)/);

  const last = offsetOf(frames[frames.length - 1]);
  expect(last.x).toBeCloseTo(TARGET.x - FROM.x, 0);
  expect(last.y).toBeCloseTo(TARGET.y - FROM.y, 0);
  // solid all the way in: it disappears INTO the pill, and the pill's bump
  // carries the last beat
  expect(frames[frames.length - 1].opacity).toBe(1);
});

test("winds up backwards before launching", () => {
  const frames = buildFlightFrames(FROM, TARGET, ORIGIN);
  const early = offsetOf(frames[2]);
  // the target is up and to the right, so the wind-up must go down and left
  expect(early.x).toBeLessThan(0);
  expect(early.y).toBeGreaterThan(0);
});

test("arcs above the straight line between the two points", () => {
  const frames = buildFlightFrames(FROM, TARGET, ORIGIN);
  const mid = offsetOf(frames[Math.floor(frames.length / 2)]);
  const straightY = ((TARGET.y - FROM.y) / (TARGET.x - FROM.x)) * mid.x;
  // smaller y = higher on screen
  expect(mid.y).toBeLessThan(straightY);
});

test("offsets are ordered and cover the whole timeline", () => {
  const frames = buildFlightFrames(FROM, TARGET, ORIGIN);
  expect(frames[0].offset).toBe(0);
  expect(frames[frames.length - 1].offset).toBe(1);
  for (let i = 1; i < frames.length; i++) {
    expect(frames[i].offset as number).toBeGreaterThan(frames[i - 1].offset as number);
  }
});

test("a bubble already under the finger still gets a valid throw", () => {
  // zero distance would divide by zero in the direction vector
  const frames = buildFlightFrames(FROM, FROM, ORIGIN);
  expect(frames).toHaveLength(35);
  for (const f of frames) expect(String(f.transform)).not.toContain("NaN");
});

test("the hit fires when the ball reaches the pill, not when the timeline ends", () => {
  const frames = buildFlightFrames(FROM, TARGET, ORIGIN);
  const hit = hitOffset(frames, FROM, TARGET);
  // it must land early enough that the bump reads as instant…
  expect(hit).toBeLessThan(1);
  // …but only once the ball is actually there
  expect(hit).toBeGreaterThan(0.5);
});

test("a throw that never gets close still lands at the end", () => {
  // one frame, nowhere near the target: the fallback keeps the beat
  expect(hitOffset([{ offset: 0, transform: "translate(0px, 0px)" }], FROM, TARGET)).toBe(1);
});
