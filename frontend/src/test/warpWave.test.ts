import { WAVE, WAVE_TOTAL_MAX_MS, waveState } from "../shell/warpWave";

const { EXPAND_MS, RELEASE_MS, MAX_HOLD_MS } = WAVE;

test("expands from the origin and never finishes early", () => {
  const start = waveState(0, null);
  expect(start.r).toBeLessThan(0.05);
  expect(start.a).toBeGreaterThan(0.9);
  const mid = waveState(EXPAND_MS / 2, null);
  expect(mid.r).toBeGreaterThan(start.r);
  expect(mid.done).toBe(false);
});

test("holds as a shimmer while the destination is still rendering", () => {
  // 2s in, no settle: front parked near the rim, amplitude alive, not done
  const held = waveState(EXPAND_MS + 700, null);
  expect(held.r).toBeGreaterThan(0.85);
  expect(held.r).toBeLessThan(1);
  expect(held.a).toBeGreaterThan(0.3);
  expect(held.done).toBe(false);
});

test("releases after settle and only then finishes", () => {
  const settleT = EXPAND_MS + 400; // page painted 400ms into the hold
  const beforeRelease = waveState(settleT - 50, settleT);
  expect(beforeRelease.done).toBe(false);
  const after = waveState(settleT + RELEASE_MS + 20, settleT);
  expect(after.done).toBe(true);
  expect(after.a).toBeCloseTo(0, 5);
  expect(after.r).toBeGreaterThan(1); // front has left the screen
});

test("an instant settle still plays the full expansion arc", () => {
  // settle at t=0 (cached page): release must not start before EXPAND_MS
  const duringExpand = waveState(EXPAND_MS - 100, 0);
  expect(duringExpand.done).toBe(false);
  expect(duringExpand.r).toBeLessThan(0.9);
  const done = waveState(EXPAND_MS + RELEASE_MS + 20, 0);
  expect(done.done).toBe(true);
});

test("a destination that never settles hits the cap, not infinity", () => {
  const capped = waveState(EXPAND_MS + MAX_HOLD_MS + RELEASE_MS + 20, null);
  expect(capped.done).toBe(true);
  expect(WAVE_TOTAL_MAX_MS).toBe(EXPAND_MS + MAX_HOLD_MS + RELEASE_MS);
});
