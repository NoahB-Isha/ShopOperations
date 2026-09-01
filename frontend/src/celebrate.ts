/* A confetti burst for the restock celebrations — dependency-free canvas,
   brand-palette colors, self-cleaning. Decoration with the usual manners:
   nothing on prefers-reduced-motion or a hidden tab, everything try/caught,
   and the overlay ignores pointer events so it can never eat a tap. */

const COLORS = ["#f36f21", "#d81b7f", "#00b8c4", "#e0a800", "#7c5cff"];
const COUNT = 90;
const LIFE_MS = 1600;

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  spin: number;
  angle: number;
  size: number;
  color: string;
}

let active: HTMLCanvasElement | null = null;

/** Burst from a point (viewport px); defaults to upper-center — "from the
 *  header", which is where the progress bar that earned it lives. */
export function celebrate(origin?: { x: number; y: number }): void {
  try {
    if (document.hidden) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    if (active) active.remove(); // a new burst replaces a running one

    const canvas = document.createElement("canvas");
    canvas.width = window.innerWidth * devicePixelRatio;
    canvas.height = window.innerHeight * devicePixelRatio;
    canvas.style.cssText =
      "position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:80";
    document.body.appendChild(canvas);
    active = canvas;
    const g = canvas.getContext("2d");
    if (!g) {
      canvas.remove();
      active = null;
      return;
    }
    g.scale(devicePixelRatio, devicePixelRatio);

    const ox = origin?.x ?? window.innerWidth / 2;
    const oy = origin?.y ?? Math.min(180, window.innerHeight * 0.25);
    const parts: Particle[] = Array.from({ length: COUNT }, () => {
      const a = Math.random() * Math.PI * 2;
      const speed = 4 + Math.random() * 7;
      return {
        x: ox,
        y: oy,
        vx: Math.cos(a) * speed,
        vy: Math.sin(a) * speed - 5, // bias upward, gravity brings them down
        spin: (Math.random() - 0.5) * 0.4,
        angle: Math.random() * Math.PI,
        size: 5 + Math.random() * 5,
        color: COLORS[Math.floor(Math.random() * COLORS.length)],
      };
    });

    const started = performance.now();
    const frame = (now: number) => {
      const t = now - started;
      if (t > LIFE_MS || canvas !== active) {
        canvas.remove();
        if (canvas === active) active = null;
        return;
      }
      g.clearRect(0, 0, window.innerWidth, window.innerHeight);
      const fade = 1 - Math.max(0, (t - LIFE_MS * 0.6) / (LIFE_MS * 0.4));
      for (const p of parts) {
        p.vy += 0.18; // gravity
        p.vx *= 0.99;
        p.x += p.vx;
        p.y += p.vy;
        p.angle += p.spin;
        g.save();
        g.translate(p.x, p.y);
        g.rotate(p.angle);
        g.globalAlpha = fade;
        g.fillStyle = p.color;
        g.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
        g.restore();
      }
      requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  } catch {
    /* decoration */
  }
}
