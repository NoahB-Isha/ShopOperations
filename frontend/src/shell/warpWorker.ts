/// <reference lib="webworker" />
/** The warp's render loop, off the main thread.
 *
 * A dedicated worker drawing to an OffscreenCanvas keeps the shockwave
 * running no matter what React is doing on the main thread (the time
 * machine mounts a 1,277-row table right when the wave plays).
 *
 * Duration is ADAPTIVE (see warpWave.ts): the wave expands, holds as a
 * shimmering front while the destination renders, and releases when the
 * main thread posts {type:"settle"} — so the animation ends only once the
 * page beneath is ready, never before.
 *
 * Protocol: {type:"init", canvas} once per canvas element;
 * {type:"wave", bitmap, cssW, cssH, dpr, x, y, power, preflipped} per fire;
 * {type:"settle"} when the destination has painted. The worker answers
 * {type:"done"} so the main thread can fade the overlay out.
 */

import { WAVE, waveState } from "./warpWave";

const VERT = `
attribute vec2 aPos;
varying vec2 vUv;
void main() {
  vUv = aPos * 0.5 + 0.5;
  gl_Position = vec4(aPos, 0.0, 1.0);
}`;

// An expanding refraction band (traveling magnifier + chromatic fringe +
// bright rim) with a lens "suction" inside the bubble — the bullet-time
// look. Pixel-space math so circles stay circular.
const FRAG = `
precision mediump float;
varying vec2 vUv;
uniform sampler2D uTex;
uniform vec2 uRes;     // canvas resolution (px)
uniform vec2 uCenter;  // wave origin (px, y-down)
uniform float uRadius; // wavefront radius (px)
uniform float uAmp;    // displacement amplitude (px)
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

let canvas: OffscreenCanvas | null = null;
let glState: Gl | null = null;
let waveToken = 0;
let waveStart = 0;
let settleT: number | null = null;

function initGl(target: OffscreenCanvas): Gl | null {
  const gl = target.getContext("webgl", {
    alpha: false,
    antialias: false,
  }) as WebGLRenderingContext | null;
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

// worker rAF exists in Chromium/Firefox/modern Safari; shim just in case
const nextFrame: (cb: (t: number) => void) => void =
  typeof requestAnimationFrame === "function"
    ? (cb) => requestAnimationFrame(cb)
    : (cb) => setTimeout(() => cb(performance.now()), 16);

interface WaveMsg {
  type: "wave";
  bitmap: ImageBitmap;
  cssW: number;
  cssH: number;
  dpr: number;
  x: number;
  y: number;
  power: number;
  preflipped: boolean;
}

function runWave(m: WaveMsg): void {
  if (!canvas) return;
  const W = Math.round(m.cssW * m.dpr);
  const H = Math.round(m.cssH * m.dpr);
  if (canvas.width !== W) canvas.width = W;
  if (canvas.height !== H) canvas.height = H;
  if (!glState) glState = initGl(canvas);
  const g = glState;
  if (!g) {
    m.bitmap.close();
    postMessage({ type: "done" });
    return;
  }
  const { gl } = g;
  gl.viewport(0, 0, W, H);
  // bitmaps arrive pre-flipped when the browser supports imageOrientation;
  // otherwise flip at upload so the page never renders mirrored
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, !m.preflipped);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, m.bitmap);
  m.bitmap.close();
  gl.uniform2f(g.uRes, W, H);
  gl.uniform2f(g.uCenter, m.x * m.dpr, m.y * m.dpr);

  const maxR =
    Math.hypot(Math.max(m.x, m.cssW - m.x), Math.max(m.y, m.cssH - m.y)) * 1.2 * m.dpr;
  const token = ++waveToken;
  waveStart = performance.now();
  settleT = null;
  const frame = (now: number) => {
    if (token !== waveToken) return; // a newer wave took over
    const s = waveState(now - waveStart, settleT);
    gl.uniform1f(g.uRadius, 20 + s.r * maxR);
    gl.uniform1f(g.uAmp, WAVE.AMP_PX * m.dpr * m.power * s.a);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    if (!s.done) nextFrame(frame);
    else postMessage({ type: "done" });
  };
  nextFrame(frame);
}

self.onmessage = (e: MessageEvent) => {
  const msg = e.data;
  if (msg?.type === "init") {
    canvas = msg.canvas as OffscreenCanvas;
    glState = null; // fresh context for a fresh canvas
  } else if (msg?.type === "wave") {
    runWave(msg as WaveMsg);
  } else if (msg?.type === "settle") {
    if (settleT === null) settleT = performance.now() - waveStart;
  }
};
