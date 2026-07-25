/** The warp's shared timeline — imported by both the worker and the
 * main-thread fallback so the two renderers can never drift apart.
 *
 * Three phases:
 *   EXPAND  — the front rushes from the cursor to ~88% of the screen;
 *   HOLD    — if the destination hasn't rendered yet, the front creeps and
 *             the distortion shimmers in place (bullet-time while the page
 *             loads beneath the snapshot), capped by MAX_HOLD_MS;
 *   RELEASE — on settle (or the cap), the front rushes off-screen and the
 *             amplitude decays to nothing; the overlay then fades.
 * `settle` arriving during EXPAND still plays the full expansion — the wave
 * never feels clipped on fast pages.
 */

export const WAVE = {
  EXPAND_MS: 1300,
  RELEASE_MS: 520,
  MAX_HOLD_MS: 3200, // wait at most this long for the destination to render
  FADE_MS: 220,
  /** peak displacement in CSS px at power=1 (scaled by dpr at render) */
  AMP_PX: 88,
} as const;

/** Longest a wave can possibly run — failsafe timers key off this. */
export const WAVE_TOTAL_MAX_MS = WAVE.EXPAND_MS + WAVE.MAX_HOLD_MS + WAVE.RELEASE_MS;

const easeOutCubic = (t: number) => 1 - (1 - t) ** 3;

export interface WaveSample {
  /** wavefront radius as a fraction of maxR (can exceed 1 while releasing) */
  r: number;
  /** amplitude as a fraction of peak */
  a: number;
  done: boolean;
}

/** Sample the wave at `t` ms since fire; `settleT` is when (ms since fire)
 * the destination reported itself rendered, or null while still waiting. */
export function waveState(t: number, settleT: number | null): WaveSample {
  const { EXPAND_MS, RELEASE_MS, MAX_HOLD_MS } = WAVE;
  const releaseStart = Math.max(
    Math.min(settleT ?? Infinity, EXPAND_MS + MAX_HOLD_MS),
    EXPAND_MS,
  );

  if (t < EXPAND_MS) {
    const p = easeOutCubic(t / EXPAND_MS);
    return { r: 0.02 + p * 0.86, a: 0.45 + 0.55 * (1 - t / EXPAND_MS), done: false };
  }
  const hold = (at: number) => ({
    r: 0.88 + 0.06 * (1 - Math.exp(-at / 1100)),
    a: 0.5 + 0.12 * Math.sin(at / 130),
  });
  if (t < releaseStart) {
    const h = hold(t - EXPAND_MS);
    return { r: h.r, a: h.a, done: false };
  }
  const from = hold(releaseStart - EXPAND_MS);
  const q = Math.min((t - releaseStart) / RELEASE_MS, 1);
  const e = easeOutCubic(q);
  return {
    r: from.r + (1.25 - from.r) * e,
    a: from.a * (1 - e),
    done: q >= 1,
  };
}
