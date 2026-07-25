/** Dashboard chart pieces — hand-rolled SVG in the dataviz-skill idiom:
 * thin marks (≤24px bars, 4px rounded data-end, square baseline), 2px
 * surface gaps between stacked segments, hairline solid gridlines, a legend
 * for ≥2 series, whole-column hover tooltips (hit target ≥ the mark), and a
 * table-view twin the caller renders as the relief channel. Series colors
 * are the validated --chart-N tokens; identity is fixed per channel. */
import { useMemo, useRef, useState } from "react";

export const CHANNEL_ORDER = ["shoppe", "online", "city_center", "campus_other"] as const;
export const CHANNEL_COLOR: Record<string, string> = {
  shoppe: "var(--chart-1)",
  online: "var(--chart-2)",
  city_center: "var(--chart-3)",
  campus_other: "var(--chart-4)",
};
export const CHANNEL_LABEL: Record<string, string> = {
  shoppe: "Shoppe",
  online: "Online",
  city_center: "City centers",
  campus_other: "Campus other",
};

export function fmtMoney(n: number): string {
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 10_000) return `$${Math.round(n / 1000)}K`;
  if (Math.abs(n) >= 1_000) return `$${(n / 1000).toFixed(1)}K`;
  return `$${Math.round(n)}`;
}

export function fmtMoneyFull(n: number): string {
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

export function fmtPct(x: number | null | undefined): string {
  if (x === null || x === undefined) return "—";
  return `${x >= 0 ? "+" : ""}${(x * 100).toFixed(0)}%`;
}

function monthLabel(ym: string): string {
  const [y, m] = ym.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: "short" });
}

function niceTicks(max: number): number[] {
  if (max <= 0) return [0];
  const raw = max / 3;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => s >= raw) ?? mag * 10;
  const ticks = [];
  // the last tick must clear the tallest stack, or bars clip at the frame
  for (let t = 0; t < max + step; t += step) ticks.push(t);
  return ticks;
}

export interface SeriesPoint {
  month: string;
  channel: string;
  revenue: number;
  units: number;
}

interface Hover {
  month: string;
  x: number; // px inside the container
  rows: { channel: string; revenue: number; units: number }[];
  total: number;
}

/** Monthly revenue, stacked by channel. Months on x, one stack per month. */
export function StackedChannelBars({
  months,
  series,
}: {
  months: string[]; // contiguous, oldest → newest (payload's period.months)
  series: SeriesPoint[];
}) {
  const [hover, setHover] = useState<Hover | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const byMonth = useMemo(() => {
    const map = new Map<string, Map<string, SeriesPoint>>();
    for (const p of series) {
      if (!map.has(p.month)) map.set(p.month, new Map());
      map.get(p.month)!.set(p.channel, p);
    }
    return map;
  }, [series]);

  const channels = useMemo(
    () => CHANNEL_ORDER.filter((c) => series.some((p) => p.channel === c && p.revenue > 0)),
    [series],
  );

  const totals = months.map((m) =>
    channels.reduce((sum, c) => sum + (byMonth.get(m)?.get(c)?.revenue ?? 0), 0),
  );
  const maxTotal = Math.max(...totals, 1);
  const ticks = niceTicks(maxTotal);
  const yMax = ticks[ticks.length - 1] || 1;

  // geometry: fixed plot height, month slots sized to fit ≥28px each
  const plotH = 200;
  const axisH = 22;
  const yAxisW = 44;
  const slot = Math.max(28, Math.min(64, Math.floor(560 / Math.max(months.length, 1))));
  const barW = Math.min(24, slot - 10);
  const width = yAxisW + months.length * slot + 8;
  const height = plotH + axisH + 6;
  const y = (v: number) => plotH - (v / yMax) * (plotH - 14) + 4;

  // ≤ 12 months → label every month; beyond, every 2nd/3rd
  const labelEvery = months.length > 18 ? 3 : months.length > 12 ? 2 : 1;

  return (
    <div ref={containerRef} className="relative overflow-x-auto" onMouseLeave={() => setHover(null)}>
      <svg width={width} height={height} role="img" aria-label="Monthly revenue by channel">
        {/* hairline gridlines + clean-number ticks */}
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={yAxisW}
              x2={width - 4}
              y1={y(t)}
              y2={y(t)}
              stroke="var(--color-outline-variant)"
              strokeWidth={1}
            />
            <text
              x={yAxisW - 6}
              y={y(t) + 3}
              textAnchor="end"
              className="fill-on-surface-variant"
              fontSize={10}
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              {fmtMoney(t)}
            </text>
          </g>
        ))}
        {months.map((m, i) => {
          const cx = yAxisW + i * slot + slot / 2;
          let cursor = y(0);
          const segs = channels
            .map((c) => ({ channel: c, point: byMonth.get(m)?.get(c) }))
            .filter((s) => (s.point?.revenue ?? 0) > 0);
          return (
            <g key={m}>
              {segs.map((s, si) => {
                const v = s.point!.revenue;
                const h = Math.max(1.5, y(0) - y(v));
                const top = cursor - h;
                cursor = top - 2; // the 2px surface gap between segments
                const isTop = si === segs.length - 1;
                const r = isTop ? 3.5 : 0; // rounded data-end, square baseline
                const x0 = cx - barW / 2;
                const path = isTop
                  ? `M${x0},${top + h} L${x0},${top + r} Q${x0},${top} ${x0 + r},${top} L${x0 + barW - r},${top} Q${x0 + barW},${top} ${x0 + barW},${top + r} L${x0 + barW},${top + h} Z`
                  : undefined;
                return path ? (
                  <path key={s.channel} d={path} fill={CHANNEL_COLOR[s.channel]} />
                ) : (
                  <rect
                    key={s.channel}
                    x={x0}
                    y={top}
                    width={barW}
                    height={h}
                    fill={CHANNEL_COLOR[s.channel]}
                  />
                );
              })}
              {i % labelEvery === 0 && (
                <text
                  x={cx}
                  y={plotH + 18}
                  textAnchor="middle"
                  className="fill-on-surface-variant"
                  fontSize={10}
                >
                  {monthLabel(m)}
                </text>
              )}
              {/* whole-column hover target — generous hit area, not pinpoint */}
              <rect
                x={yAxisW + i * slot}
                y={0}
                width={slot}
                height={plotH + axisH}
                fill="transparent"
                onMouseEnter={() =>
                  setHover({
                    month: m,
                    x: yAxisW + i * slot + slot / 2,
                    rows: channels
                      .map((c) => byMonth.get(m)?.get(c))
                      .filter((p): p is SeriesPoint => !!p && p.revenue > 0)
                      .map((p) => ({ channel: p.channel, revenue: p.revenue, units: p.units })),
                    total: totals[i],
                  })
                }
              />
            </g>
          );
        })}
      </svg>
      {hover && (
        <div
          className="pointer-events-none absolute top-1 z-10 w-52 rounded-(--radius-md) bg-inverse-surface p-3 text-inverse-on-surface shadow-(--shadow-e2)"
          style={{
            left: Math.min(
              Math.max(hover.x - 104, 0),
              (containerRef.current?.clientWidth ?? 400) - 212,
            ),
          }}
        >
          <div className="label-m mb-1.5 opacity-80">
            {monthLabel(hover.month)} {hover.month.slice(0, 4)} · {fmtMoneyFull(hover.total)}
          </div>
          {hover.rows.map((r) => (
            <div key={r.channel} className="flex items-center gap-2 text-[12px] leading-5">
              <span
                className="inline-block h-2.5 w-2.5 rounded-full"
                style={{ background: CHANNEL_COLOR[r.channel] }}
              />
              <span className="flex-1">{CHANNEL_LABEL[r.channel]}</span>
              <span style={{ fontVariantNumeric: "tabular-nums" }}>{fmtMoneyFull(r.revenue)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Per-scope accent: charts on a scoped tab wear that channel's hue so the
 * color keeps meaning across the dashboard (color follows the entity). */
export const SCOPE_COLOR: Record<string, string> = {
  all: "var(--chart-1)",
  in_person: "var(--chart-1)",
  online: "var(--chart-2)",
  city_center: "var(--chart-3)",
};

/** Horizontal revenue-by-category bars: one nominal series (single hue),
 * values direct-labeled at the bar end — no grid needed. */
export function CategoryBars({
  rows,
  color,
}: {
  rows: { label: string; revenue: number }[];
  color: string;
}) {
  const top = rows.slice(0, 10);
  const max = Math.max(...top.map((r) => r.revenue), 1);
  const labelW = 132;
  const valueW = 56;
  const rowH = 26;
  const barMax = 320;
  const width = labelW + barMax + valueW;
  const height = top.length * rowH + 4;
  if (!top.length) {
    return <p className="text-[13px] text-on-surface-variant">No sales in this period.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <svg width={width} height={height} role="img" aria-label="Revenue by category">
        {top.map((r, i) => {
          const y = i * rowH + 4;
          const w = Math.max(2, (r.revenue / max) * barMax);
          const barH = 16;
          const rr = 3.5; // rounded data-end (right); square at the baseline (left)
          return (
            <g key={r.label}>
              <text
                x={labelW - 8}
                y={y + barH / 2 + 3.5}
                textAnchor="end"
                fontSize={11}
                className="fill-on-surface"
              >
                {r.label.length > 18 ? `${r.label.slice(0, 17)}…` : r.label}
              </text>
              <path
                d={`M${labelW},${y} L${labelW + w - rr},${y} Q${labelW + w},${y} ${labelW + w},${y + rr} L${labelW + w},${y + barH - rr} Q${labelW + w},${y + barH} ${labelW + w - rr},${y + barH} L${labelW},${y + barH} Z`}
                fill={color}
              />
              <text
                x={labelW + w + 6}
                y={y + barH / 2 + 3.5}
                fontSize={10.5}
                className="fill-on-surface-variant"
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {fmtMoney(r.revenue)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** Single-series monthly trend line (order size, etc.): 2px line, ringed end
 * dot, direct end label; per-point hover tooltip. */
export function TrendLine({
  points,
  color,
  format = fmtMoney,
}: {
  points: { month: string; value: number | null; hint?: string }[];
  color: string;
  format?: (n: number) => string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const real = points.filter((p) => p.value !== null) as { month: string; value: number }[];
  if (real.length < 2) {
    return (
      <p className="text-[13px] text-on-surface-variant">
        Not enough months in this period for a trend.
      </p>
    );
  }
  const w = 460;
  const plotH = 150;
  const axisH = 20;
  const yAxisW = 46;
  const endW = 52;
  const max = Math.max(...real.map((p) => p.value));
  const min = Math.min(...real.map((p) => p.value), 0);
  const yMax = max * 1.12 || 1;
  const x = (i: number) => yAxisW + (i / Math.max(points.length - 1, 1)) * (w - yAxisW - endW);
  const y = (v: number) => plotH - ((v - min) / (yMax - min || 1)) * (plotH - 16) + 6;
  const coords = points.map((p, i) => (p.value === null ? null : [x(i), y(p.value)] as const));
  const path = coords
    .map((c, i) => (c === null ? "" : `${i === 0 || coords[i - 1] === null ? "M" : "L"}${c[0]},${c[1]}`))
    .join(" ");
  const last = [...coords].reverse().find((c) => c !== null)!;
  const lastPoint = [...points].reverse().find((p) => p.value !== null)!;
  const labelEvery = points.length > 12 ? 3 : points.length > 6 ? 2 : 1;
  const hovered = hover !== null && points[hover]?.value !== null ? hover : null;
  return (
    <div className="relative overflow-x-auto" onMouseLeave={() => setHover(null)}>
      <svg width={w} height={plotH + axisH} role="img" aria-label="Monthly trend">
        {[min, (min + yMax) / 2, yMax].map((t) => (
          <g key={t}>
            <line x1={yAxisW} x2={w - endW + 20} y1={y(t)} y2={y(t)} stroke="var(--color-outline-variant)" strokeWidth={1} />
            <text x={yAxisW - 6} y={y(t) + 3} textAnchor="end" fontSize={10} className="fill-on-surface-variant" style={{ fontVariantNumeric: "tabular-nums" }}>
              {format(t)}
            </text>
          </g>
        ))}
        <path d={path} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {coords.map(
          (c, i) =>
            c && (
              <g key={points[i].month}>
                {/* 2px surface ring keeps the dot legible on the line */}
                <circle cx={c[0]} cy={c[1]} r={hovered === i ? 6 : 4} fill={color} stroke="var(--color-surface-container-low)" strokeWidth={2} />
                <rect
                  x={c[0] - 14}
                  y={0}
                  width={28}
                  height={plotH + axisH}
                  fill="transparent"
                  onMouseEnter={() => setHover(i)}
                />
              </g>
            ),
        )}
        <circle cx={last[0]} cy={last[1]} r={4.5} fill={color} stroke="var(--color-surface-container-low)" strokeWidth={2} />
        <text x={last[0] + 8} y={last[1] + 4} fontSize={11} fontWeight={600} className="fill-on-surface" style={{ fontVariantNumeric: "tabular-nums" }}>
          {format(lastPoint.value!)}
        </text>
        {points.map((p, i) =>
          i % labelEvery === 0 ? (
            <text key={p.month} x={x(i)} y={plotH + 15} textAnchor="middle" fontSize={10} className="fill-on-surface-variant">
              {monthLabel(p.month)}
            </text>
          ) : null,
        )}
      </svg>
      {hovered !== null && (
        <div
          className="pointer-events-none absolute top-0 z-10 rounded-(--radius-md) bg-inverse-surface px-3 py-2 text-inverse-on-surface shadow-(--shadow-e2)"
          style={{ left: Math.min(Math.max(x(hovered) - 60, 0), w - 150) }}
        >
          <div className="text-[11px] opacity-80">
            {monthLabel(points[hovered].month)} {points[hovered].month.slice(0, 4)}
          </div>
          <div className="text-[12px] font-semibold" style={{ fontVariantNumeric: "tabular-nums" }}>
            {format(points[hovered].value!)}
          </div>
          {points[hovered].hint && <div className="text-[11px] opacity-80">{points[hovered].hint}</div>}
        </div>
      )}
    </div>
  );
}

/** New vs returning customers by month — emphasis form: returning (the
 * loyalty story) wears the scope hue, new wears the de-emphasis gray. */
export function CustomersBars({
  series,
  color,
}: {
  series: { month: string; new_customers: number; returning_customers: number }[];
  color: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  if (!series.length || series.every((s) => s.new_customers + s.returning_customers === 0)) {
    return (
      <p className="text-[13px] text-on-surface-variant">
        No customers on file for this period.
      </p>
    );
  }
  const plotH = 150;
  const axisH = 20;
  const yAxisW = 36;
  const slot = Math.max(30, Math.min(64, Math.floor(480 / series.length)));
  const barW = Math.min(22, slot - 10);
  const width = yAxisW + series.length * slot + 8;
  const max = Math.max(...series.map((s) => s.new_customers + s.returning_customers), 1);
  const y = (v: number) => plotH - (v / max) * (plotH - 16) + 4;
  const labelEvery = series.length > 12 ? 3 : series.length > 6 ? 2 : 1;
  const gray = "var(--color-outline)";
  return (
    <div className="relative overflow-x-auto" onMouseLeave={() => setHover(null)}>
      <div className="mb-1 flex items-center gap-4 text-[12px] text-on-surface-variant">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: color }} />
          Returning
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: gray }} />
          New
        </span>
      </div>
      <svg width={width} height={plotH + axisH} role="img" aria-label="New vs returning customers by month">
        {series.map((s, i) => {
          const cx = yAxisW + i * slot + slot / 2;
          const x0 = cx - barW / 2;
          const hRet = Math.max(s.returning_customers > 0 ? 1.5 : 0, y(0) - y(s.returning_customers));
          const hNew = Math.max(s.new_customers > 0 ? 1.5 : 0, y(0) - y(s.new_customers));
          const retTop = y(0) - hRet;
          const newTop = retTop - (hNew ? hNew + 2 : 0); // 2px surface gap
          return (
            <g key={s.month}>
              {hRet > 0 && <rect x={x0} y={retTop} width={barW} height={hRet} fill={color} />}
              {hNew > 0 && <rect x={x0} y={newTop} width={barW} height={hNew} rx={3} fill={gray} />}
              {i % labelEvery === 0 && (
                <text x={cx} y={plotH + 15} textAnchor="middle" fontSize={10} className="fill-on-surface-variant">
                  {monthLabel(s.month)}
                </text>
              )}
              <rect
                x={yAxisW + i * slot}
                y={0}
                width={slot}
                height={plotH + axisH}
                fill="transparent"
                onMouseEnter={() => setHover(i)}
              />
            </g>
          );
        })}
      </svg>
      {hover !== null && (
        <div
          className="pointer-events-none absolute top-6 z-10 rounded-(--radius-md) bg-inverse-surface px-3 py-2 text-inverse-on-surface shadow-(--shadow-e2)"
          style={{ left: Math.min(Math.max(yAxisW + hover * slot - 40, 0), width - 170) }}
        >
          <div className="text-[11px] opacity-80">
            {monthLabel(series[hover].month)} {series[hover].month.slice(0, 4)}
          </div>
          <div className="text-[12px]" style={{ fontVariantNumeric: "tabular-nums" }}>
            {series[hover].returning_customers.toLocaleString()} returning ·{" "}
            {series[hover].new_customers.toLocaleString()} new
          </div>
        </div>
      )}
    </div>
  );
}

export function ChannelLegend({ channels }: { channels: string[] }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
      {channels.map((c) => (
        <span key={c} className="flex items-center gap-1.5 text-[12px] text-on-surface-variant">
          <span
            className="inline-block h-2.5 w-2.5 rounded-full"
            style={{ background: CHANNEL_COLOR[c] }}
          />
          {CHANNEL_LABEL[c] ?? c}
        </span>
      ))}
    </div>
  );
}
