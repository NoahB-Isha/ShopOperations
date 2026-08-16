/* The centers map: where every city center is, which review zone it belongs
   to, and — on a click — who runs it and what's on its shelf.

   Desktop only, by design. This is a "stand back and look at the whole
   country" view; on a phone the list underneath is the better tool, and the
   page renders it there instead.

   Colour rules (see the --zone-* block in tokens.css for the validation): a
   dot map is an all-pairs form, which caps the palette at FOUR safe hues, so
   hue carries the four field zones and nothing else. Canada needs no hue —
   it is the only thing above the border. III Departments is one campus glyph,
   because five departments at one address are not five places. Centers with no
   zone are grey: an absence, not another identity. Every zone also carries a
   direct label on its territory and a legend row, so identity is never colour
   alone. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useCenterDetail } from "../../api/hooks";
import type { CenterOut } from "../../api/types";
import { Badge, Spinner } from "../../design";
import {
  NO_ZONE_COLOR,
  R_MAX,
  R_MIN,
  isCampusZone,
  monthName,
  radiusFor,
  trendOf,
  trendPct,
  zoneColors,
} from "./centerSignals";
import type { Trend } from "./centerSignals";
import { MAP_HEIGHT, MAP_SHAPES, MAP_WIDTH, project } from "./mapGeo";

/** Map units covered by the floating legend row at the top of the frame. */
const LEGEND_BAND = 62;

/** ▲ / ▼ as a path, so the trend survives colour-blindness, greyscale printing
 *  and forced-colours — the arrow is the encoding, the colour only seconds it. */
function TrendMark({ trend, x, y, size }: { trend: Trend; x: number; y: number; size: number }) {
  if (trend === "none" || trend === "flat") return null;
  const up = trend === "up" || trend === "first";
  const h = size * (up ? -1 : 1);
  return (
    <path
      d={`M${x - size} ${y}L${x + size} ${y}L${x} ${y + h}Z`}
      className={up ? "fill-success" : "fill-error"}
      stroke="var(--color-surface)"
      strokeWidth={size * 0.35}
      paintOrder="stroke"
    />
  );
}

export interface MapCenter extends CenterOut {
  x: number;
  y: number;
}

/** Departments live at one address; the map says so once. */
const isCampus = isCampusZone;

function colorFor(zoneName: string | null, slots: Map<string, string>): string {
  if (!zoneName) return NO_ZONE_COLOR;
  return slots.get(zoneName) ?? NO_ZONE_COLOR;
}

/** Convex hull (Andrew's monotone chain). A zone's territory is the smallest
 *  shape containing its centers; stroking it round and thick turns that into
 *  something soft enough to sit behind the pins. */
function hull(points: { x: number; y: number }[]): { x: number; y: number }[] {
  if (points.length < 3) return points;
  const pts = [...points].sort((a, b) => a.x - b.x || a.y - b.y);
  const cross = (o: typeof pts[0], a: typeof pts[0], b: typeof pts[0]) =>
    (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const build = (source: typeof pts) => {
    const out: typeof pts = [];
    for (const p of source) {
      while (out.length >= 2 && cross(out[out.length - 2], out[out.length - 1], p) <= 0) out.pop();
      out.push(p);
    }
    out.pop();
    return out;
  };
  return [...build(pts), ...build([...pts].reverse())];
}

function hullPath(points: { x: number; y: number }[]): string {
  if (points.length === 0) return "";
  if (points.length === 1) return `M${points[0].x} ${points[0].y}L${points[0].x} ${points[0].y}`;
  const ring = hull(points);
  const shape = ring.length >= 3 ? ring : points;
  return (
    shape.map((p, i) => `${i === 0 ? "M" : "L"}${p.x} ${p.y}`).join("") +
    (shape.length >= 3 ? "Z" : "")
  );
}

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}
const overlaps = (a: Box, b: Box) =>
  a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;

export function CentersMap({
  centers,
  selectedId,
  onSelect,
}: {
  centers: CenterOut[];
  selectedId: number | null;
  onSelect: (id: number | null) => void;
}) {
  const [view, setView] = useState({ scale: 1, x: 0, y: 0 });
  const [hovered, setHovered] = useState<number | null>(null);
  const [dimZone, setDimZone] = useState<string | null>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  /* A press is a click until it moves. `moved` is what tells them apart, and
     pointer CAPTURE is taken only once it has — capturing on pointerdown
     retargets the following click to the frame, so the dot's own onClick never
     fires and nothing opens. That was the bug: clicks did nothing. */
  const drag = useRef<
    { x: number; y: number; vx: number; vy: number; moved: boolean } | null
  >(null);
  const DRAG_SLOP = 4;

  // Hue per zone, fixed by zone name — never by position in a filtered list.
  const zoneSlots = useMemo(() => zoneColors(centers.map((c) => c.zone_name)), [centers]);

  const placed = useMemo<MapCenter[]>(
    () =>
      centers
        .filter((c) => c.latitude != null && c.longitude != null)
        .map((c) => ({ ...c, ...project(c.latitude as number, c.longitude as number) })),
    [centers],
  );

  // Departments collapse onto one campus pin — five names, one address.
  const campus = placed.filter((c) => isCampus(c.zone_name));
  const pins = placed.filter((c) => !isCampus(c.zone_name));

  /** Zone titles ride ABOVE the territory, not through it: at the centroid
   *  they sat on top of the very cities they were naming (Boston and New York
   *  both lost their labels to "ZONE 1 (LILI)"). */
  const zoneTitlePos = useCallback(
    (list: MapCenter[]) => ({
      x: list.reduce((sum, c) => sum + c.x, 0) / list.length,
      // never above LEGEND_BAND: a northern zone (Seattle) would otherwise
      // hang its title behind the legend row floating over the frame
      y: Math.max(LEGEND_BAND, Math.min(...list.map((c) => c.y)) - 30),
    }),
    [],
  );

  const zones = useMemo(() => {
    const byZone = new Map<string, MapCenter[]>();
    for (const c of pins) {
      if (!c.zone_name) continue;
      const list = byZone.get(c.zone_name) ?? [];
      list.push(c);
      byZone.set(c.zone_name, list);
    }
    return [...byZone.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [pins]);

  const maxUnits = useMemo(() => Math.max(0, ...pins.map((c) => c.sales_units ?? 0)), [pins]);

  /* Labels: as many as fit without colliding, biggest claim first (active
     centers, then alphabetical so the choice is stable frame to frame). Zoom
     in and more appear, because the boxes shrink in map units as the scale
     grows — which is exactly the "zoom to read the northeast" gesture. */
  const labels = useMemo(() => {
    const fontPx = 11 / view.scale;
    // The zone titles claim their space FIRST — they name the territory, and a
    // city label crossing one makes both unreadable.
    const taken: Box[] = zones.map(([name, list]) => {
      const at = zoneTitlePos(list);
      const w = name.length * (13 / view.scale) * 0.62;
      return { x: at.x - w / 2, y: at.y - 13 / view.scale, w, h: (13 / view.scale) * 1.4 };
    });
    const shown = new Set<number>();
    const ordered = [...pins].sort(
      (a, b) => Number(b.is_active) - Number(a.is_active) || a.name.localeCompare(b.name),
    );
    for (const c of ordered) {
      const w = c.name.length * fontPx * 0.55 + 8 / view.scale;
      const h = fontPx * 1.25;
      // clear the dot, whose radius now depends on last month's sales
      const gap = (radiusFor(c.sales_units, maxUnits) + 3) / view.scale;
      const box = { x: c.x + gap, y: c.y - h / 2, w, h };
      if (taken.some((t) => overlaps(box, t))) continue;
      taken.push(box);
      shown.add(c.id);
    }
    return shown;
  }, [pins, zones, zoneTitlePos, maxUnits, view.scale]);

  /* Zoom is a TARGET the view eases toward, not a jump.

     A wheel notch used to multiply the scale by 1.15 and paint that frame, so
     one flick of a trackpad — which fires a dozen events — crossed half the
     zoom range instantly. Now each event nudges a target by a gentle factor
     derived from its own delta (trackpads send many small ones, a mouse wheel
     a few large ones, and both land in the same place), and a rAF loop walks
     the drawn view toward it. The eased tail is what makes it read as smooth.

     Panning writes the target directly: a drag is already continuous, and
     easing it would feel like the map was lagging the finger. */
  const target = useRef(view);
  const raf = useRef(0);

  const settle = useCallback(() => {
    if (raf.current) return;
    const step = () => {
      raf.current = 0;
      setView((v) => {
        const t = target.current;
        const ds = t.scale - v.scale;
        const dx = t.x - v.x;
        const dy = t.y - v.y;
        // close enough that another frame would be invisible
        if (Math.abs(ds) < 0.0015 && Math.abs(dx) < 0.15 && Math.abs(dy) < 0.15) return t;
        raf.current = requestAnimationFrame(step);
        const ease = 0.22;
        return { scale: v.scale + ds * ease, x: v.x + dx * ease, y: v.y + dy * ease };
      });
    };
    raf.current = requestAnimationFrame(step);
  }, []);

  useEffect(() => () => cancelAnimationFrame(raf.current), []);

  const zoomToward = useCallback(
    (deltaY: number, ox: number, oy: number) => {
      const t = target.current;
      // ~1.03 per notch on a trackpad, ~1.08 on a mouse wheel: sensitivity
      // follows the device instead of the event count
      const step = Math.exp(-deltaY * 0.0016);
      const scale = Math.min(9, Math.max(1, t.scale * step));
      const k = scale / t.scale;
      // keep the point under the cursor fixed while the scale changes
      const next = { scale, x: ox - (ox - t.x) * k, y: oy - (oy - t.y) * k };
      target.current = next;
      // Commit a slice of the move on THIS event and ease the rest: the wheel
      // always answers immediately, even where rAF is throttled (a background
      // tab, some embedded webviews), where an ease-only zoom does nothing at
      // all until frames resume.
      setView((v) => ({
        scale: v.scale + (next.scale - v.scale) * 0.35,
        x: v.x + (next.x - v.x) * 0.35,
        y: v.y + (next.y - v.y) * 0.35,
      }));
      settle();
    },
    [settle],
  );

  // Non-passive wheel listener: React's onWheel is passive, so preventDefault
  // there is ignored and the page scrolls out from under the zoom.
  useEffect(() => {
    const el = frameRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      // a wheel line/page delta is not pixels — normalise or a Firefox notch
      // zooms a hundred times harder than a Chrome one
      const px = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaMode === 2 ? e.deltaY * 400 : e.deltaY;
      zoomToward(px, e.clientX - rect.left, e.clientY - rect.top);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomToward]);

  const detail = useCenterDetail(selectedId);

  const active = placed.filter((c) => c.is_active).length;
  const monthLabel = monthName(centers[0]?.sales_month);
  const prevLabel = monthName(centers[0]?.sales_prev_month);
  const sizeHelp =
    `Dot area is units sold in ${monthLabel} — the last complete month. ` +
    "City centers set up about once a month, so that is one pop-up's takings.";

  return (
    <div className="relative mb-10 overflow-hidden rounded-(--radius-xl) border border-outline-variant bg-surface-container-low">
      {/* legend — always present, so a colour never has to carry identity alone */}
      <div className="absolute inset-x-0 top-0 z-10 flex flex-wrap items-center gap-x-4 gap-y-2 bg-gradient-to-b from-surface-container-low to-transparent px-5 py-4 text-[12px]">
        {zones.map(([name]) => {
          const canada = name.toLowerCase() === "canada";
          return (
            <button
              key={name}
              onMouseEnter={() => setDimZone(name)}
              onMouseLeave={() => setDimZone(null)}
              className="flex items-center gap-1.5 rounded-full px-1 text-on-surface-variant hover:text-on-surface"
            >
              <span
                className="h-2.5 w-2.5 rounded-full"
                style={{
                  background: canada ? "transparent" : colorFor(name, zoneSlots),
                  boxShadow: canada ? "inset 0 0 0 1.5px var(--color-outline)" : undefined,
                }}
              />
              {name}
            </button>
          );
        })}
        {campus.length > 0 && (
          <span className="flex items-center gap-1.5 text-on-surface-variant">
            <span className="h-2.5 w-2.5 rounded-[3px] bg-on-surface-variant" />
            III Departments ({campus.length})
          </span>
        )}
        <span className="flex items-center gap-1.5 text-on-surface-variant">
          <span className="h-2.5 w-2.5 rounded-full bg-outline opacity-60" />
          No review zone
        </span>
        {maxUnits > 0 && (
          <span className="flex items-center gap-1.5 text-on-surface-variant" title={sizeHelp}>
            <svg width="34" height="16" aria-hidden className="overflow-visible">
              <circle cx="5" cy="8" r={R_MIN} className="fill-outline" />
              <circle cx="22" cy="8" r={R_MAX / 2} className="fill-outline" />
            </svg>
            units sold, {monthLabel}
          </span>
        )}
        <span className="flex items-center gap-1.5 text-on-surface-variant">
          <svg width="20" height="14" aria-hidden>
            <path d="M2 9L8 9L5 4Z" className="fill-success" />
            <path d="M12 5L18 5L15 10Z" className="fill-error" />
          </svg>
          vs {prevLabel}
        </span>
        <span className="ml-auto text-outline">
          {active} active of {placed.length} placed · scroll to zoom, drag to pan
        </span>
      </div>

      <div
        ref={frameRef}
        className="h-[62vh] max-h-[46rem] min-h-[26rem] cursor-grab touch-none active:cursor-grabbing"
        onPointerDown={(e) => {
          const t = target.current;
          drag.current = { x: e.clientX, y: e.clientY, vx: t.x, vy: t.y, moved: false };
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d) return;
          const dx = e.clientX - d.x;
          const dy = e.clientY - d.y;
          if (!d.moved) {
            if (Math.hypot(dx, dy) < DRAG_SLOP) return; // still a click
            d.moved = true;
            // capture NOW, so the pointer can leave the frame mid-drag. Can
            // throw on an id the browser doesn't know — the drag survives
            // without it (same lesson as the draft bubble).
            try {
              e.currentTarget.setPointerCapture(e.pointerId);
            } catch {
              /* dragging still works without capture */
            }
          }
          target.current = { ...target.current, x: d.vx + dx, y: d.vy + dy };
          setView((v) => ({ ...v, x: d.vx + dx, y: d.vy + dy }));
        }}
        onPointerUp={(e) => {
          if (drag.current?.moved) {
            try {
              e.currentTarget.releasePointerCapture(e.pointerId);
            } catch {
              /* nothing captured */
            }
          }
          drag.current = null;
        }}
        onPointerCancel={() => (drag.current = null)}
      >
        <svg
          viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`}
          className="h-full w-full"
          role="img"
          aria-label={`Map of ${placed.length} city centers across the US and Canada`}
        >
          <g transform={`translate(${view.x} ${view.y}) scale(${view.scale})`}>
            {/* land: scenery first, then the states and provinces we operate in */}
            {MAP_SHAPES.filter((s) => s.kind === "context").map((s) => (
              <path
                key={s.name}
                d={s.d}
                className="fill-surface-container stroke-outline-variant"
                strokeWidth={0.6 / view.scale}
                opacity={0.45}
              />
            ))}
            {MAP_SHAPES.filter((s) => s.kind !== "context").map((s) => (
              <path
                key={`${s.kind}-${s.name}`}
                d={s.d}
                className="fill-surface-container-high stroke-outline-variant"
                strokeWidth={0.7 / view.scale}
              />
            ))}

            {/* zone territories: a hull, stroked round and fat until it reads
                as a soft blob. Degenerate hulls (one or two centers) inflate
                into a dot or a capsule, which is the honest shape for them. */}
            {zones.map(([name, list]) => {
              const canada = name.toLowerCase() === "canada";
              const color = canada ? "var(--color-outline)" : colorFor(name, zoneSlots);
              const dim = dimZone !== null && dimZone !== name;
              return (
                <g key={name} opacity={dim ? 0.15 : 1} className="transition-opacity duration-300">
                  <path
                    d={hullPath(list)}
                    fill={color}
                    stroke={color}
                    strokeWidth={38}
                    strokeLinejoin="round"
                    strokeLinecap="round"
                    opacity={0.14}
                  />
                </g>
              );
            })}

            {/* the campus: one glyph for every III department */}
            {campus.length > 0 && (
              <g
                onClick={() => onSelect(campus[0].id)}
                className="cursor-pointer"
                onMouseEnter={() => setHovered(campus[0].id)}
                onMouseLeave={() => setHovered(null)}
              >
                <rect
                  x={campus[0].x - 5.5 / view.scale}
                  y={campus[0].y - 5.5 / view.scale}
                  width={11 / view.scale}
                  height={11 / view.scale}
                  rx={2.5 / view.scale}
                  className="fill-on-surface-variant stroke-surface"
                  strokeWidth={2 / view.scale}
                />
                <text
                  x={campus[0].x}
                  y={campus[0].y + 15 / view.scale}
                  textAnchor="middle"
                  className="fill-on-surface-variant"
                  style={{ fontSize: 10 / view.scale, fontWeight: 600 }}
                >
                  III Campus ({campus.length})
                </text>
              </g>
            )}

            {/* centers */}
            {pins.map((c) => {
              const color = colorFor(c.zone_name, zoneSlots);
              const selected = c.id === selectedId;
              const isHovered = c.id === hovered;
              const dim = dimZone !== null && dimZone !== c.zone_name;
              const base = radiusFor(c.sales_units, maxUnits);
              const r = (base * (selected ? 1.35 : isHovered ? 1.2 : 1)) / view.scale;
              const trend = trendOf(c);
              return (
                <g
                  key={c.id}
                  opacity={dim ? 0.2 : 1}
                  className="cursor-pointer transition-opacity duration-300"
                  onMouseEnter={() => setHovered(c.id)}
                  onMouseLeave={() => setHovered(null)}
                  onClick={(e) => {
                    e.stopPropagation();
                    onSelect(selected ? null : c.id);
                  }}
                >
                  {selected && (
                    <circle cx={c.x} cy={c.y} r={r * 2.4} fill={color} opacity={0.22} />
                  )}
                  {/* Dormant centers are rings, not discs — on the map, plainly
                      not trading. A solid surface fill turned a big dormant
                      center (Richmond, Houston: marked inactive but selling)
                      into what looked like a hole punched in the map, so the
                      ring keeps a wash of its zone colour inside. */}
                  <circle
                    cx={c.x}
                    cy={c.y}
                    r={r}
                    fill={color}
                    fillOpacity={c.is_active ? 1 : 0.22}
                    stroke={c.is_active ? "var(--color-surface)" : color}
                    strokeWidth={(c.is_active ? 2 : 1.6) / view.scale}
                  />
                  {/* last month against the one before, on the dot itself */}
                  {c.is_active && (
                    <TrendMark
                      trend={trend}
                      x={c.x}
                      y={c.y - (base + 4.5) / view.scale}
                      size={3.2 / view.scale}
                    />
                  )}
                  {(labels.has(c.id) || selected || isHovered) && (
                    <text
                      x={c.x + (base + 3.5) / view.scale}
                      y={c.y + 3.5 / view.scale}
                      className={
                        c.is_active ? "fill-on-surface" : "fill-on-surface-variant"
                      }
                      style={{
                        fontSize: 11 / view.scale,
                        fontWeight: selected || isHovered ? 700 : 500,
                        paintOrder: "stroke",
                        stroke: "var(--color-surface-container-high)",
                        strokeWidth: 3 / view.scale,
                        strokeLinejoin: "round",
                      }}
                    >
                      {c.name}
                    </text>
                  )}
                </g>
              );
            })}

            {/* zone names sit on their territory, not in a tooltip */}
            {zones.map(([name, list]) => {
              const at = zoneTitlePos(list);
              const dim = dimZone !== null && dimZone !== name;
              return (
                <text
                  key={`label-${name}`}
                  x={at.x}
                  y={at.y}
                  textAnchor="middle"
                  opacity={dim ? 0.15 : 0.75}
                  className="pointer-events-none fill-on-surface-variant transition-opacity duration-300"
                  style={{
                    fontSize: 13 / view.scale,
                    fontWeight: 700,
                    letterSpacing: "0.04em",
                    textTransform: "uppercase",
                  }}
                >
                  {name}
                </text>
              );
            })}
          </g>
        </svg>
      </div>

      {view.scale !== 1 && (
        <button
          onClick={() => setView({ scale: 1, x: 0, y: 0 })}
          className="state-layer absolute bottom-4 left-4 rounded-full border border-outline-variant
            bg-surface px-3 py-1.5 text-[12px] font-medium text-on-surface-variant"
        >
          Reset view
        </button>
      )}

      {selectedId !== null && (
        <CenterPanel
          detail={detail.data}
          center={placed.find((c) => c.id === selectedId) ?? null}
          loading={detail.isLoading}
          onClose={() => onSelect(null)}
        />
      )}
    </div>
  );
}

function People({ title, people }: { title: string; people: { name: string; email: string; phone?: string; note?: string }[] }) {
  return (
    <div>
      <div className="text-[11px] font-semibold tracking-wide text-on-surface-variant uppercase">
        {title}
      </div>
      {people.length === 0 ? (
        <div className="mt-1 text-[13px] text-outline">Nobody assigned</div>
      ) : (
        <ul className="mt-1.5 space-y-1.5">
          {people.map((p) => (
            <li key={`${p.name}-${p.email}`} className="text-[13px] leading-tight">
              <div className="font-medium text-on-surface">{p.name || "—"}</div>
              <div className="text-on-surface-variant">
                {p.email}
                {p.phone ? ` · ${p.phone}` : ""}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The sales line in the panel: the number the dot's size came from, the one
 *  it is being compared against, and the direction — spelled out, because an
 *  arrow on a map is a hint and this is the place for the actual figures. */
function SalesLine({ center }: { center: MapCenter }) {
  const trend = trendOf(center);
  const pct = trendPct(center);
  const month = monthName(center.sales_month);
  const prev = monthName(center.sales_prev_month);
  if (center.sales_units == null) {
    return (
      <div className="text-[13px] text-on-surface-variant">
        No city-center sales recorded for this center yet.
      </div>
    );
  }
  const word =
    trend === "first"
      ? `first sales since ${prev}`
      : trend === "flat"
        ? `about level with ${prev}`
        : pct == null
          ? ""
          : `${pct > 0 ? "up" : "down"} ${Math.abs(pct)}% on ${prev}`;
  const tone =
    trend === "up" || trend === "first"
      ? "text-success"
      : trend === "down"
        ? "text-error"
        : "text-on-surface-variant";
  return (
    <div>
      <div className="flex items-baseline gap-1.5">
        <span className="display text-2xl text-on-surface">{Math.round(center.sales_units)}</span>
        <span className="text-[13px] text-on-surface-variant">units in {month}</span>
      </div>
      <div className={`mt-0.5 flex items-center gap-1 text-[13px] ${tone}`}>
        {(trend === "up" || trend === "first" || trend === "down") && (
          <svg width="9" height="9" viewBox="0 0 10 10" aria-hidden>
            <path
              d={trend === "down" ? "M1 3L9 3L5 9Z" : "M1 7L9 7L5 1Z"}
              fill="currentColor"
            />
          </svg>
        )}
        {word}
      </div>
      {center.sales_prev_units != null && (
        <div className="mt-0.5 text-[12px] text-outline">
          {Math.round(center.sales_prev_units)} units in {prev}
        </div>
      )}
    </div>
  );
}

function CenterPanel({
  detail,
  center,
  loading,
  onClose,
}: {
  detail: ReturnType<typeof useCenterDetail>["data"];
  center: MapCenter | null;
  loading: boolean;
  onClose: () => void;
}) {
  return (
    <div className="animate-rise-in absolute top-16 right-4 bottom-4 flex w-80 flex-col overflow-hidden
      rounded-(--radius-lg) border border-outline-variant bg-surface shadow-(--shadow-e3)">
      <div className="flex items-start justify-between gap-2 border-b border-outline-variant px-4 py-3">
        <div className="min-w-0">
          <div className="truncate font-semibold text-on-surface">{detail?.name ?? "…"}</div>
          {detail?.zone_name && (
            <div className="text-[12px] text-on-surface-variant">{detail.zone_name}</div>
          )}
        </div>
        <button
          onClick={onClose}
          aria-label="Close"
          className="state-layer grid h-8 w-8 shrink-0 place-items-center rounded-full text-on-surface-variant"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
            <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
        </button>
      </div>

      {loading || !detail ? (
        <div className="grid flex-1 place-items-center">
          <Spinner size={20} />
        </div>
      ) : (
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4">
          {center && <SalesLine center={center} />}
          <People title="Order Reviewer" people={detail.reviewers} />
          <People title="Order Requester" people={detail.requesters} />
          {detail.contacts.length > 0 && (
            <People title="Also on the roster" people={detail.contacts} />
          )}

          <div>
            <div className="flex items-baseline justify-between">
              <div className="text-[11px] font-semibold tracking-wide text-on-surface-variant uppercase">
                In stock now
              </div>
              {detail.stock_status === "ok" && (
                <Badge tone="outline">{detail.stock.length} SKUs</Badge>
              )}
            </div>
            {detail.stock_status !== "ok" ? (
              <div className="mt-1.5 text-[13px] leading-snug text-on-surface-variant">
                {detail.stock_note}
              </div>
            ) : detail.stock.length === 0 ? (
              <div className="mt-1.5 text-[13px] text-on-surface-variant">
                Nothing on the shelf in Odoo right now.
              </div>
            ) : (
              <table className="mt-2 w-full text-[13px]">
                <tbody>
                  {detail.stock.map((line) => (
                    <tr key={line.sku} className="border-b border-outline-variant/60 last:border-0">
                      <td className="py-1 pr-2 leading-tight text-on-surface">{line.name}</td>
                      <td className="py-1 text-right font-semibold tabular-nums text-on-surface">
                        {line.qty}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
