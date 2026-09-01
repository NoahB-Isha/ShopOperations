/* Sound effects + haptics for the floor's checklist gestures — synthesized
   with Web Audio so there are no asset files, no fetches, and nothing for the
   CSP to think about. Decoration only: every entry point is try/caught and a
   failure plays silence.

   The setting lives in localStorage per device (like silly mode), default ON —
   the sounds only fire on restock/draft gestures, so roles that never touch
   those lists never hear anything. iOS unlocks audio on a user gesture; every
   play call here happens inside a tap handler, and the shared AudioContext is
   created (and resumed) lazily on the first one. */
import { useSyncExternalStore } from "react";

const STORAGE_KEY = "ilops_sounds";
const listeners = new Set<() => void>();

export function soundsEnabled(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) !== "0"; // unset = on
  } catch {
    return false;
  }
}

export function setSoundsEnabled(on: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, on ? "1" : "0");
  } catch {
    /* private mode: the toggle just won't stick */
  }
  for (const fn of listeners) fn();
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function useSounds(): boolean {
  return useSyncExternalStore(subscribe, soundsEnabled, () => false);
}

// ------------------------------------------------------------- the synth
let ctx: AudioContext | null = null;

function context(): AudioContext | null {
  try {
    ctx ??= new AudioContext();
    if (ctx.state === "suspended") void ctx.resume(); // the iOS gesture unlock
    return ctx;
  } catch {
    return null;
  }
}

/** Master volume — quiet on purpose; this is a shop, not an arcade. */
const LEVEL = 0.12;

function ready(): AudioContext | null {
  if (!soundsEnabled() || document.hidden) return null;
  return context();
}

function buzz(pattern: number | number[]): void {
  try {
    navigator.vibrate?.(pattern); // Android only; iOS Safari has no vibrate API
  } catch {
    /* decoration */
  }
}

/** One enveloped oscillator note. `glide` slides the pitch over the note. */
function tone(
  c: AudioContext,
  at: number,
  freq: number,
  ms: number,
  { type = "triangle" as OscillatorType, gain = LEVEL, glide = 1 } = {},
): void {
  const osc = c.createOscillator();
  const amp = c.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, at);
  if (glide !== 1) osc.frequency.exponentialRampToValueAtTime(freq * glide, at + ms / 1000);
  amp.gain.setValueAtTime(gain, at);
  amp.gain.exponentialRampToValueAtTime(0.001, at + ms / 1000);
  osc.connect(amp).connect(c.destination);
  osc.start(at);
  osc.stop(at + ms / 1000 + 0.02);
}

/** Check-off: a bright little bing — a bell strike, not a beep. Instant
 *  attack, ~C6 ringing out, plus one quiet INHARMONIC partial (~2.5×, the
 *  bit that reads as "bell"). A whisper of random detune keeps a run of
 *  ticks organic without sounding out of tune. */
export function playCheck(): void {
  try {
    const c = ready();
    if (!c) return;
    const t = c.currentTime;
    const f = 1046.5 * (1 + (Math.random() - 0.5) * 0.03); // ~C6
    tone(c, t, f, 420, { type: "sine" });
    tone(c, t, f * 2.52, 260, { type: "sine", gain: LEVEL * 0.25 });
    buzz(12);
  } catch {
    /* silence */
  }
}

/** Un-check: the same shape, lower and quieter — clearly "undo". */
export function playUncheck(): void {
  try {
    const c = ready();
    if (!c) return;
    tone(c, c.currentTime, 340, 90, { type: "sine", glide: 0.75, gain: LEVEL * 0.6 });
  } catch {
    /* silence */
  }
}

/** Added to a list/draft: a rising noise whoosh matching the fly-to-bubble
 *  slingshot. Band-passed noise, filter sweeping up. */
export function playWhoosh(): void {
  try {
    const c = ready();
    if (!c) return;
    const dur = 0.24;
    const buf = c.createBuffer(1, Math.ceil(c.sampleRate * dur), c.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
    const src = c.createBufferSource();
    src.buffer = buf;
    const filter = c.createBiquadFilter();
    filter.type = "bandpass";
    filter.Q.value = 1.2;
    const t = c.currentTime;
    filter.frequency.setValueAtTime(420, t);
    filter.frequency.exponentialRampToValueAtTime(2600, t + dur);
    const amp = c.createGain();
    amp.gain.setValueAtTime(0.001, t);
    amp.gain.exponentialRampToValueAtTime(LEVEL * 0.9, t + dur * 0.4);
    amp.gain.exponentialRampToValueAtTime(0.001, t + dur);
    src.connect(filter).connect(amp).connect(c.destination);
    src.start(t);
    buzz(8);
  } catch {
    /* silence */
  }
}

/** The transfer bubble bursting into the page: a proper bubble pop — a fast
 *  downward blip with a tiny noise tick for the skin of the bubble. */
export function playPop(): void {
  try {
    const c = ready();
    if (!c) return;
    const t = c.currentTime;
    tone(c, t, 520, 110, { type: "sine", glide: 0.35 });
    // the "skin": 20ms of high, quiet noise right at the start
    const buf = c.createBuffer(1, Math.ceil(c.sampleRate * 0.02), c.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
    const src = c.createBufferSource();
    src.buffer = buf;
    const hp = c.createBiquadFilter();
    hp.type = "highpass";
    hp.frequency.value = 2500;
    const amp = c.createGain();
    amp.gain.setValueAtTime(LEVEL * 0.5, t);
    amp.gain.exponentialRampToValueAtTime(0.001, t + 0.02);
    src.connect(hp).connect(amp).connect(c.destination);
    src.start(t);
    buzz(15);
  } catch {
    /* silence */
  }
}

/** "Not today" (snooze): a pencil scribbling the line out — three quick
 *  strokes of high, hissy noise, each at a slightly different color. */
export function playScribble(): void {
  try {
    const c = ready();
    if (!c) return;
    const dur = 0.34;
    const buf = c.createBuffer(1, Math.ceil(c.sampleRate * dur), c.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
    const src = c.createBufferSource();
    src.buffer = buf;
    const filter = c.createBiquadFilter();
    filter.type = "bandpass";
    filter.Q.value = 0.9;
    const amp = c.createGain();
    const t = c.currentTime;
    amp.gain.setValueAtTime(0.001, t);
    // three strokes: up fast, mostly down, again — paper texture comes from
    // re-centering the filter per stroke
    const strokes: Array<[number, number]> = [
      [0.0, 3400],
      [0.11, 4400],
      [0.21, 2900],
    ];
    for (const [at, freq] of strokes) {
      filter.frequency.setValueAtTime(freq, t + at);
      amp.gain.linearRampToValueAtTime(LEVEL * 0.8, t + at + 0.035);
      amp.gain.linearRampToValueAtTime(LEVEL * 0.12, t + at + 0.09);
    }
    amp.gain.exponentialRampToValueAtTime(0.001, t + dur);
    src.connect(filter).connect(amp).connect(c.destination);
    src.start(t);
    buzz([10, 30, 10, 30, 10]);
  } catch {
    /* silence */
  }
}

/** An aisle finished: two quick bright notes. */
export function playChime(): void {
  try {
    const c = ready();
    if (!c) return;
    const t = c.currentTime;
    tone(c, t, 659.3, 140); // E5
    tone(c, t + 0.11, 880, 220); // A5
    buzz([15, 40, 15]);
  } catch {
    /* silence */
  }
}

/** The big one — list cleared or a milestone: a little pentatonic fanfare. */
export function playFanfare(): void {
  try {
    const c = ready();
    if (!c) return;
    const t = c.currentTime;
    const notes = [523.3, 587.3, 784, 1046.5]; // C5 D5 G5 C6
    notes.forEach((f, i) => tone(c, t + i * 0.09, f, 260, { gain: LEVEL * 0.9 }));
    tone(c, t + 0.36, 1046.5, 480, { type: "sine", gain: LEVEL * 0.5 });
    buzz([20, 60, 20, 60, 40]);
  } catch {
    /* silence */
  }
}
