/** The time-machine warp — a GPU shockwave that bends the page from
 * wherever the user clicked (nav button, slider thumb…).
 *
 * Latency story (v4): three things fire in order, each covering the next.
 *   t=0    a compositor-driven pop + rings leave the cursor the moment the
 *          click lands (transform/opacity only — they keep playing even
 *          while React blocks the main thread mounting the big table);
 *   t≈2f   the GPU wave joins: a pre-captured viewport snapshot (taken
 *          during idle — nav-link hover, settled renders) is shipped to a
 *          WORKER driving an OffscreenCanvas, so the distortion renders
 *          smoothly no matter what the main thread is doing;
 *   t=end  the overlay fades; the real page beneath never moved.
 * The nav warp fires on the CLICK itself (capture phase), before the route
 * mounts — the wave masks the mount instead of waiting behind it.
 *
 * Honest fallbacks: no OffscreenCanvas → main-thread WebGL; no fresh
 * snapshot / no WebGL → rings only; reduced-motion → nothing.
 */
import { useEffect, useRef } from "react";
import html2canvas from "html2canvas-pro";
import { WAVE, WAVE_TOTAL_MAX_MS, waveState } from "./warpWave";

// ---------------------------------------------------------- pointer memory
// "Where did the user last press?" — warp origins follow the mouse/finger.
const lastPointer = { x: 0, y: 0, t: 0 };
if (typeof window !== "undefined") {
  window.addEventListener(
    "pointerdown",
    (e) => {
      lastPointer.x = e.clientX;
      lastPointer.y = e.clientY;
      lastPointer.t = Date.now();
    },
    { capture: true, passive: true },
  );
}

export function recentPointer(maxAgeMs = 2000): { x: number; y: number } {
  if (Date.now() - lastPointer.t <= maxAgeMs && (lastPointer.x || lastPointer.y)) {
    return { x: lastPointer.x, y: lastPointer.y };
  }
  return { x: window.innerWidth / 2, y: window.innerHeight / 2 };
}

function reducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// ------------------------------------------------------------ the snapshot
interface Snapshot {
  canvas: HTMLCanvasElement;
  /** pre-decoded GPU-ready frame — created at capture time so firing a wave
   * posts it to the worker SYNCHRONOUSLY, before a route mount can block the
   * main thread. One-shot: transferring it to the worker consumes it. */
  bitmap: ImageBitmap | null;
  preflipped: boolean;
  scrollX: number;
  scrollY: number;
  width: number;
  height: number;
  at: number;
}

let snapshot: Snapshot | null = null;
let capturing = false;
let captureQueued = false;
let captureTimer = 0;

/** Queue a viewport snapshot during idle time. Call it BEFORE a warp could
 * fire (nav-link hover, a settled render) — capture costs ~200-600ms of
 * background work, which is exactly why it never happens at fire time. */
export function requestWarpCapture(delayMs = 120): void {
  if (typeof window === "undefined" || reducedMotion()) return;
  window.clearTimeout(captureTimer);
  captureTimer = window.setTimeout(() => {
    if (capturing) {
      captureQueued = true;
      return;
    }
    capturing = true;
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    const width = window.innerWidth;
    const height = window.innerHeight;
    html2canvas(document.body, {
      x: scrollX,
      y: scrollY,
      width,
      height,
      scale: Math.min(window.devicePixelRatio || 1, 1.5),
      logging: false,
      backgroundColor: null,
      ignoreElements: (el) =>
        el.classList?.contains("tm-ripple-overlay") || el.id === "tm-warp-canvas",
    })
      .then(async (canvas) => {
        const { bmp, preflipped } = await toBitmap(canvas);
        snapshot = {
          canvas,
          bitmap: bmp,
          preflipped,
          scrollX,
          scrollY,
          width,
          height,
          at: Date.now(),
        };
      })
      .catch(() => {
        snapshot = null; // rings-only until a later capture succeeds
      })
      .finally(() => {
        capturing = false;
        if (captureQueued) {
          captureQueued = false;
          requestWarpCapture(0);
        }
      });
  }, delayMs);
}

function snapshotUsable(): Snapshot | null {
  const s = snapshot;
  if (!s) return null;
  const fresh = Date.now() - s.at < 20_000;
  const aligned =
    Math.abs(s.scrollX - window.scrollX) < 16 &&
    Math.abs(s.scrollY - window.scrollY) < 16 &&
    s.width === window.innerWidth &&
    s.height === window.innerHeight;
  return fresh && aligned ? s : null;
}

// ------------------------------------------------------------- warp events
export interface WarpOptions {
  x?: number;
  y?: number;
  /** 0..1 — slider nudges use less, the grand entrance uses full power */
  power?: number;
}

type WarpListener = (opts: Required<WarpOptions>) => void;
const listeners = new Set<WarpListener>();
let lastFiredAt = 0;

export function fireWarp(opts: WarpOptions = {}): void {
  const now = Date.now();
  if (now - lastFiredAt < 450) return; // scrubbing shouldn't strobe
  lastFiredAt = now;
  const origin =
    opts.x !== undefined && opts.y !== undefined ? { x: opts.x, y: opts.y } : recentPointer();
  const full = { x: origin.x, y: origin.y, power: Math.min(Math.max(opts.power ?? 1, 0.2), 1) };
  listeners.forEach((fn) => fn(full));
}

/** For entry-effect deduping: the nav CLICK usually fired already. */
export function msSinceLastWarp(): number {
  return Date.now() - lastFiredAt;
}

// -------------------------------------------------------------- settling
// The wave holds until the destination reports itself rendered, so the
// animation never ends on a half-loaded page (warpWave.ts caps the hold).
let waveActive = false;
let waveUsedWorker = false;
let mainWaveStart = 0;
let mainSettleT: number | null = null;
let lastOrigin = { x: 0, y: 0 };

/** Call AFTER the destination view has painted (double-rAF from an effect).
 * Releases the held wave; a release-pop ring marks the arrival. */
export function settleWarp(): void {
  if (!waveActive) return;
  if (waveUsedWorker) worker?.postMessage({ type: "settle" });
  else if (mainSettleT === null) mainSettleT = performance.now() - mainWaveStart;
  spawnSettlePop(lastOrigin.x, lastOrigin.y);
}

if (typeof window !== "undefined") {
  // scroll/resize move the viewport away from what we captured
  window.addEventListener("scroll", () => requestWarpCapture(350), { passive: true });
  window.addEventListener("resize", () => requestWarpCapture(350));
  // the grand entrance, part 1: capture while the pointer is still on its
  // way to the Time-machine link — the literal "render it before the event"
  window.addEventListener(
    "pointerover",
    (e) => {
      const link = (e.target as Element | null)?.closest?.('a[href="/time-machine"]');
      if (link) requestWarpCapture(0);
    },
    { capture: true, passive: true },
  );
  // part 2: fire on the click itself, BEFORE the router mounts the (heavy)
  // page — the wave covers the mount instead of queueing behind it
  window.addEventListener(
    "click",
    (e) => {
      const link = (e.target as Element | null)?.closest?.('a[href="/time-machine"]');
      if (!link || window.location.pathname === "/time-machine") return;
      const rect = link.getBoundingClientRect();
      fireWarp({
        x: e.clientX || rect.left + rect.width / 2,
        y: e.clientY || rect.top + rect.height / 2,
        power: 1,
      });
    },
    { capture: true },
  );
}

// dev-only handle for poking the pipeline from the console / e2e
declare global {
  interface Window {
    __warpFx?: {
      fireWarp: typeof fireWarp;
      requestWarpCapture: typeof requestWarpCapture;
      hasSnapshot: () => boolean;
      waveActive: () => boolean;
    };
  }
}
if (typeof window !== "undefined" && import.meta.env.DEV) {
  window.__warpFx = {
    fireWarp,
    requestWarpCapture,
    hasSnapshot: () => !!snapshot,
    waveActive: () => waveActive,
  };
}

// --------------------------------------------------- worker / GL plumbing
let worker: Worker | null = null;

function ensureWorker(canvas: HTMLCanvasElement, onDone: () => void): boolean {
  if (typeof OffscreenCanvas === "undefined" || !canvas.transferControlToOffscreen) {
    return false;
  }
  try {
    if (!worker) {
      worker = new Worker(new URL("./warpWorker.ts", import.meta.url), { type: "module" });
    }
    worker.onmessage = (e) => {
      if (e.data?.type === "done") onDone();
    };
    // StrictMode remounts hand us a fresh canvas element; transfer it anew
    if (!canvas.dataset.transferred) {
      const off = canvas.transferControlToOffscreen();
      canvas.dataset.transferred = "1";
      worker.postMessage({ type: "init", canvas: off }, [off]);
    }
    return true;
  } catch {
    return false;
  }
}

async function toBitmap(
  src: HTMLCanvasElement,
): Promise<{ bmp: ImageBitmap; preflipped: boolean }> {
  try {
    // bake the GL vertical flip into the bitmap where supported
    return { bmp: await createImageBitmap(src, { imageOrientation: "flipY" }), preflipped: true };
  } catch {
    return { bmp: await createImageBitmap(src), preflipped: false };
  }
}

// ---- main-thread fallback (no OffscreenCanvas): same shader, local loop
const VERT = `
attribute vec2 aPos;
varying vec2 vUv;
void main() {
  vUv = aPos * 0.5 + 0.5;
  gl_Position = vec4(aPos, 0.0, 1.0);
}`;

// keep in lockstep with warpWorker.ts — same wave, different thread
const FRAG = `
precision mediump float;
varying vec2 vUv;
uniform sampler2D uTex;
uniform vec2 uRes;
uniform vec2 uCenter;
uniform float uRadius;
uniform float uAmp;
void main() {
  vec2 px = vec2(vUv.x, 1.0 - vUv.y) * uRes;
  vec2 toC = px - uCenter;
  float d = length(toC);
  float band = exp(-pow((d - uRadius) / (uRes.x * 0.11), 2.0));
  float inside = 1.0 - smoothstep(0.0, max(uRadius, 1.0), d);
  vec2 dir = d > 0.001 ? toC / d : vec2(0.0);
  vec2 offsetPx = dir * (band * uAmp + inside * uAmp * 0.35);
  vec2 offsetUv = vec2(offsetPx.x, -offsetPx.y) / uRes;
  float fringe = 1.0 + band * 0.45;
  float r = texture2D(uTex, vUv - offsetUv * fringe).r;
  float g = texture2D(uTex, vUv - offsetUv).g;
  float b = texture2D(uTex, vUv - offsetUv / fringe).b;
  vec3 color = vec3(r, g, b) + band * 0.10;
  gl_FragColor = vec4(color, 1.0);
}`;

interface Gl {
  gl: WebGLRenderingContext;
  uCenter: WebGLUniformLocation;
  uRadius: WebGLUniformLocation;
  uAmp: WebGLUniformLocation;
  uRes: WebGLUniformLocation;
}

function initGl(canvas: HTMLCanvasElement): Gl | null {
  const gl = canvas.getContext("webgl", { alpha: false, antialias: false });
  if (!gl) return null;
  const compile = (type: number, src: string) => {
    const sh = gl.createShader(type)!;
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    return sh;
  };
  const prog = gl.createProgram()!;
  gl.attachShader(prog, compile(gl.VERTEX_SHADER, VERT));
  gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FRAG));
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return null;
  gl.useProgram(prog);
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const aPos = gl.getAttribLocation(prog, "aPos");
  gl.enableVertexAttribArray(aPos);
  gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);
  const tex = gl.createTexture();
  gl.activeTexture(gl.TEXTURE0);
  gl.bindTexture(gl.TEXTURE_2D, tex);
  // canvas pixels arrive top-row-first; GL samples bottom-up — flip or the
  // whole page renders mirrored during the wave
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.uniform1i(gl.getUniformLocation(prog, "uTex"), 0);
  return {
    gl,
    uCenter: gl.getUniformLocation(prog, "uCenter")!,
    uRadius: gl.getUniformLocation(prog, "uRadius")!,
    uAmp: gl.getUniformLocation(prog, "uAmp")!,
    uRes: gl.getUniformLocation(prog, "uRes")!,
  };
}

/** Mount once (AppShell). Renders the overlay canvas + rings. */
export function WarpFX(_props: { targetRef?: React.RefObject<HTMLElement | null> }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const glRef = useRef<Gl | null>(null);
  const raf = useRef(0);
  const hideTimer = useRef(0);
  const failsafe = useRef(0);

  useEffect(() => {
    // the cleanup must hide the same canvas this effect animated
    const mountedCanvas = canvasRef.current;

    const endWave = () => {
      waveActive = false;
      const canvas = canvasRef.current;
      if (!canvas) return;
      window.clearTimeout(failsafe.current);
      canvas.style.transition = `opacity ${WAVE.FADE_MS}ms ease-out`;
      canvas.style.opacity = "0";
      hideTimer.current = window.setTimeout(() => {
        canvas.style.display = "none";
      }, WAVE.FADE_MS + 30);
    };

    const onWarp = ({ x, y, power }: Required<WarpOptions>) => {
      if (reducedMotion()) return;
      // t=0: the cheap, unjankable part — pop + rings from the cursor
      spawnRings(overlayRef.current, x, y, power);
      lastOrigin = { x, y };
      // whatever happens next, make sure the NEXT warp has a fresh page
      requestWarpCapture(WAVE.EXPAND_MS + 300);

      const canvas = canvasRef.current;
      const shot = snapshotUsable();
      if (!canvas || !shot) return; // rings-only — still fun, never broken

      const useWorker = ensureWorker(canvas, endWave);
      canvas.style.width = `${shot.width}px`;
      canvas.style.height = `${shot.height}px`;
      window.clearTimeout(hideTimer.current);
      canvas.style.transition = "";
      canvas.style.opacity = "1";
      canvas.style.display = "block";
      waveActive = true;
      waveUsedWorker = Boolean(useWorker && worker);
      mainSettleT = null;
      // insurance: never leave the overlay up (hidden tabs, worker hiccups)
      window.clearTimeout(failsafe.current);
      failsafe.current = window.setTimeout(() => {
        waveActive = false;
        canvas.style.display = "none";
      }, WAVE_TOTAL_MAX_MS + 900);

      const dpr = shot.canvas.width / shot.width;
      if (useWorker && worker && shot.bitmap) {
        // synchronous handoff — the worker starts drawing on its own thread
        // even while React blocks this one mounting the page
        const bmp = shot.bitmap;
        shot.bitmap = null; // transferring consumes it; next capture makes a new one
        worker.postMessage(
          {
            type: "wave",
            bitmap: bmp,
            cssW: shot.width,
            cssH: shot.height,
            dpr,
            x,
            y,
            power,
            preflipped: shot.preflipped,
          },
          [bmp],
        );
        return;
      }
      if (useWorker && worker) {
        // bitmap already spent (rapid re-fire) — decode a fresh one async
        toBitmap(shot.canvas).then(({ bmp, preflipped }) => {
          worker!.postMessage(
            {
              type: "wave",
              bitmap: bmp,
              cssW: shot.width,
              cssH: shot.height,
              dpr,
              x,
              y,
              power,
              preflipped,
            },
            [bmp],
          );
        });
        return;
      }

      // ---- fallback: main-thread loop (old Safari and friends)
      canvas.width = shot.canvas.width;
      canvas.height = shot.canvas.height;
      if (!glRef.current) glRef.current = initGl(canvas);
      const g = glRef.current;
      if (!g) {
        waveActive = false;
        canvas.style.display = "none";
        return; // rings carried it
      }
      const { gl } = g;
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, shot.canvas);
      gl.uniform2f(g.uRes, canvas.width, canvas.height);
      gl.uniform2f(g.uCenter, x * dpr, y * dpr);
      const maxR =
        Math.hypot(Math.max(x, shot.width - x), Math.max(y, shot.height - y)) * 1.2 * dpr;
      mainWaveStart = performance.now();
      cancelAnimationFrame(raf.current);
      const frame = (now: number) => {
        const s = waveState(now - mainWaveStart, mainSettleT);
        gl.uniform1f(g.uRadius, 20 + s.r * maxR);
        gl.uniform1f(g.uAmp, WAVE.AMP_PX * dpr * power * s.a);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
        if (!s.done) raf.current = requestAnimationFrame(frame);
        else endWave();
      };
      raf.current = requestAnimationFrame(frame);
    };

    listeners.add(onWarp);
    return () => {
      listeners.delete(onWarp);
      cancelAnimationFrame(raf.current);
      window.clearTimeout(hideTimer.current);
      window.clearTimeout(failsafe.current);
      if (mountedCanvas) mountedCanvas.style.display = "none";
    };
  }, []);

  return (
    <>
      <canvas
        ref={canvasRef}
        id="tm-warp-canvas"
        aria-hidden
        className="pointer-events-none fixed inset-0"
        style={{ display: "none", zIndex: 80 }}
      />
      <div ref={overlayRef} aria-hidden className="tm-ripple-overlay" />
    </>
  );
}

/** The instant feedback layer: a pop at the cursor plus rings riding the
 * wavefront. Compositor-driven (transform/opacity ONLY — width/height would
 * relayout on the main thread and hitch exactly when the page mounts). */
function spawnRings(overlay: HTMLDivElement | null, x: number, y: number, power: number) {
  if (!overlay) return;

  const dot = document.createElement("span");
  dot.className = "tm-dot";
  dot.style.left = `${x}px`;
  dot.style.top = `${y}px`;
  overlay.appendChild(dot);
  const dotAnim = dot.animate(
    [
      { transform: "translate(-50%, -50%) scale(0.4)", opacity: 0.95 },
      { transform: "translate(-50%, -50%) scale(4)", opacity: 0 },
    ],
    { duration: 320, easing: "cubic-bezier(0.05, 0.7, 0.1, 1)", fill: "forwards" },
  );
  dotAnim.onfinish = () => dot.remove();

  const D = Math.max(window.innerWidth, window.innerHeight) * 2.3 * power;
  const colors = ["var(--color-primary)", "var(--color-tertiary)", "var(--color-secondary)"];
  colors.forEach((color, i) => {
    const ring = document.createElement("span");
    ring.className = "tm-ring";
    ring.style.borderColor = color;
    ring.style.left = `${x}px`;
    ring.style.top = `${y}px`;
    ring.style.width = `${D}px`;
    ring.style.height = `${D}px`;
    overlay.appendChild(ring);
    const anim = ring.animate(
      [
        { transform: "translate(-50%, -50%) scale(0.012)", opacity: 0.9 },
        { transform: "translate(-50%, -50%) scale(1)", opacity: 0 },
      ],
      {
        duration: WAVE.EXPAND_MS + 260 - i * 100,
        delay: i * 100,
        easing: "cubic-bezier(0.05, 0.7, 0.1, 1)",
        fill: "forwards",
      },
    );
    anim.onfinish = () => ring.remove();
  });
}

/** One bright, quick ring from the original origin when the destination
 * settles — the "we've arrived" beat that syncs with the wave's release. */
function spawnSettlePop(x: number, y: number) {
  const overlay = document.querySelector<HTMLDivElement>(".tm-ripple-overlay");
  if (!overlay || reducedMotion()) return;
  const ring = document.createElement("span");
  ring.className = "tm-ring";
  const D = Math.max(window.innerWidth, window.innerHeight) * 1.6;
  ring.style.borderColor = "var(--color-primary)";
  ring.style.left = `${x}px`;
  ring.style.top = `${y}px`;
  ring.style.width = `${D}px`;
  ring.style.height = `${D}px`;
  overlay.appendChild(ring);
  const anim = ring.animate(
    [
      { transform: "translate(-50%, -50%) scale(0.02)", opacity: 0.95 },
      { transform: "translate(-50%, -50%) scale(1)", opacity: 0 },
    ],
    { duration: WAVE.RELEASE_MS + 160, easing: "cubic-bezier(0.05, 0.7, 0.1, 1)", fill: "forwards" },
  );
  anim.onfinish = () => ring.remove();
}
