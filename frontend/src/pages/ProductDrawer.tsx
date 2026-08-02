import { useEffect, useState } from "react";
import type { ProductOut, StockHistoryPointOut, TagOut } from "../api/types";
import { usePatchProduct, useProductStockHistory, useSaveTags } from "../api/hooks";
import { Badge, Button, Drawer, Field, Input, Toggle, useToast } from "../design";
import { TAG_LABELS, TAG_TONES } from "./shared/tags";

const ALL_TAGS: { tag: string; label: string }[] = [
  { tag: "air_only", label: "Air only" },
  { tag: "sea_only", label: "Sea only" },
  { tag: "gold", label: "Gold" },
  { tag: "silver", label: "Silver" },
  { tag: "bloom", label: "Bloom" },
  { tag: "camphor", label: "Camphor" },
  { tag: "toothpaste", label: "Toothpaste" },
  { tag: "expires", label: "Expires" },
];

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-line/60 py-2 text-sm last:border-0">
      <span className="label-caps shrink-0">{label}</span>
      <span className="min-w-0 text-right">{children}</span>
    </div>
  );
}

export function ProductDrawer({
  product,
  onClose,
  isAdmin,
}: {
  product: ProductOut | null;
  onClose: () => void;
  isAdmin: boolean;
}) {
  const toast = useToast();
  const saveTags = useSaveTags();
  const patch = usePatchProduct();

  const [tags, setTags] = useState<Record<string, string | true>>({});
  const [caseSize, setCaseSize] = useState("1");
  const [deptOrderable, setDeptOrderable] = useState(false);
  const [restockExclude, setRestockExclude] = useState(false);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!product) return;
    const t: Record<string, string | true> = {};
    for (const tag of product.tags) t[tag.tag] = tag.expires_on ?? true;
    setTags(t);
    setCaseSize(String(product.case_size));
    setDeptOrderable(product.dept_orderable);
    setRestockExclude(product.restock_exclude);
    setDirty(false);
  }, [product]);

  if (!product) return null;

  const stock = product.stock;
  const lowNote =
    product.is_stock_tracked &&
    Object.values(stock).some((v) => v > 0 && v <= 3);

  const toggleTag = (tag: string) => {
    setDirty(true);
    setTags((prev) => {
      const next = { ...prev };
      if (tag in next) delete next[tag];
      else next[tag] = tag === "expires" ? "" : true;
      // air/sea are mutually exclusive
      if (tag === "air_only") delete next.sea_only;
      if (tag === "sea_only") delete next.air_only;
      return next;
    });
  };

  const save = async () => {
    const tagList: TagOut[] = Object.entries(tags).map(([tag, v]) => ({
      tag,
      expires_on: tag === "expires" ? (typeof v === "string" && v ? v : null) : null,
    }));
    if (tagList.some((t) => t.tag === "expires" && !t.expires_on)) {
      toast.error("The Expires tag needs a date.");
      return;
    }
    try {
      await saveTags.mutateAsync({ id: product.id, tags: tagList });
      await patch.mutateAsync({
        id: product.id,
        case_size: Number(caseSize) || 1,
        dept_orderable: deptOrderable,
        restock_exclude: restockExclude,
      });
      toast.success("Saved.");
      setDirty(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed.");
    }
  };

  return (
    <Drawer
      open
      onClose={onClose}
      title={product.name}
      footer={
        isAdmin ? (
          <>
            <Button variant="ghost" onClick={onClose}>Close</Button>
            <Button onClick={save} loading={saveTags.isPending || patch.isPending} disabled={!dirty}>
              Save changes
            </Button>
          </>
        ) : undefined
      }
    >
      <div className="flex flex-col gap-5">
        <div>
          <Row label="Global SKU">
            <span className="font-mono text-[13px]">{product.global_sku}</span>
          </Row>
          {product.us_sku !== product.global_sku && (
            <Row label="US SKU"><span className="font-mono text-[13px]">{product.us_sku}</span></Row>
          )}
          {product.barcode && (
            <Row label="Barcode"><span className="font-mono text-[13px]">{product.barcode}</span></Row>
          )}
          <Row label="Category">{product.category || "—"}</Row>
          <Row label="Price">${product.retail_price.toFixed(2)}</Row>
          <Row label="Cost">${product.cost.toFixed(2)}</Row>
          <Row label="Source">
            {product.source === "odoo" ? (
              <Badge tone="forest">Odoo-synced</Badge>
            ) : (
              <Badge tone="outline">app-only · untracked</Badge>
            )}
          </Row>
          {product.sourcing && (
            <Row label="Sourcing">
              <Badge tone="outline">
                {product.sourcing === "domestic" ? "Domestic" : "India"} · Odoo tag
              </Badge>
            </Row>
          )}
          {!isAdmin && product.tags.length > 0 && (
            <Row label="Tags">
              <span className="flex flex-wrap justify-end gap-1">
                {product.tags.map((t) => (
                  <Badge key={t.tag} tone={TAG_TONES[t.tag] ?? "neutral"}>
                    {TAG_LABELS[t.tag] ?? t.tag}
                    {t.expires_on ? ` ${t.expires_on}` : ""}
                  </Badge>
                ))}
              </span>
            </Row>
          )}
        </div>

        {product.is_stock_tracked && (
          <div>
            <div className="label-caps mb-2">On hand</div>
            <div className="grid grid-cols-3 gap-2">
              {(["bwhse", "floor", "staging"] as const).map((k) => (
                <div key={k} className="rounded-(--radius-sm) border border-line bg-raised/60 p-2.5 text-center">
                  <div className="label-caps">{k}</div>
                  <div className="display mt-0.5 text-xl tabular-nums">{stock[k] ?? 0}</div>
                </div>
              ))}
              {(stock.staging2 ?? 0) > 0 && (
                <div
                  className="rounded-(--radius-sm) border border-line bg-raised/60 p-2.5 text-center"
                  title="Staging 2 — warehouse consolidation pallets committed to the floor"
                >
                  <div className="label-caps">stag 2</div>
                  <div className="display mt-0.5 text-xl tabular-nums">{stock.staging2}</div>
                </div>
              )}
            </div>
            {lowNote && (
              <p className="mt-2 text-[12.5px] leading-4.5 text-gold">
                ⚠ Low counts are often wrong at this scale — verify physically before promising
                stock.
              </p>
            )}
            <StockHistorySection productId={product.id} />
          </div>
        )}

        {isAdmin && (
          <>
            <div>
              <div className="label-caps mb-2">App tags</div>
              {/* one compact wrap of toggle chips — same one-click toggling as
                  the old checkbox grid at a third of the height */}
              <div className="flex flex-wrap items-center gap-1.5">
                {ALL_TAGS.map(({ tag, label }) => {
                  const active = tag in tags;
                  return (
                    <button
                      key={tag}
                      type="button"
                      aria-pressed={active}
                      onClick={() => toggleTag(tag)}
                      className={`rounded-full border px-2.5 py-1 text-[12px] transition-colors
                        ${active
                          ? "border-transparent bg-secondary-container font-semibold text-on-secondary-container"
                          : "border-line font-medium text-ink-soft hover:border-line-strong hover:text-ink"}`}
                    >
                      {active && <span aria-hidden>✓ </span>}
                      {label}
                    </button>
                  );
                })}
                {"expires" in tags && (
                  <input
                    type="date"
                    aria-label="Expiry date"
                    value={typeof tags.expires === "string" ? tags.expires : ""}
                    onChange={(e) => {
                      setDirty(true);
                      setTags((prev) => ({ ...prev, expires: e.target.value }));
                    }}
                    className="h-7 rounded-full border border-line bg-field px-2.5 text-[12px] text-ink"
                  />
                )}
              </div>
            </div>

            <div className="grid grid-cols-2 items-end gap-3">
              <Field label="Case size" help="Units per case for ordering math">
                <Input
                  value={caseSize}
                  inputMode="numeric"
                  onChange={(e) => {
                    setDirty(true);
                    setCaseSize(e.target.value.replace(/\D/g, ""));
                  }}
                />
              </Field>
              <div className="flex flex-col gap-2.5 pb-1">
                <Toggle
                  checked={deptOrderable}
                  onChange={(v) => {
                    setDirty(true);
                    setDeptOrderable(v);
                  }}
                  label="Dept-orderable"
                />
                <Toggle
                  checked={restockExclude}
                  onChange={(v) => {
                    setDirty(true);
                    setRestockExclude(v);
                  }}
                  label="Exclude from restock lists"
                />
              </div>
            </div>
          </>
        )}

        {product.odoo_url && (
          <a
            href={product.odoo_url}
            target="_blank"
            rel="noreferrer"
            className="text-[13px] font-medium text-copper-deep underline-offset-2 hover:underline"
          >
            Open in Odoo ↗
          </a>
        )}
      </div>
    </Drawer>
  );
}

/* ------------------------------------------------- availability over time */

const RANGE_CHOICES = [
  { days: 90, label: "3 mo" },
  { days: 180, label: "6 mo" },
  { days: 365, label: "1 yr" },
] as const;

function fmtDay(iso: string, withYear = false): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(withYear ? { year: "numeric" } : {}),
  });
}

function StockHistorySection({ productId }: { productId: number }) {
  const [days, setDays] = useState<number>(180);
  const history = useProductStockHistory(productId, days);
  const data = history.data;
  return (
    <div className="mt-4">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className="label-caps">Availability over time</span>
        <div className="flex gap-0.5" role="group" aria-label="History range">
          {RANGE_CHOICES.map((r) => (
            <button
              key={r.days}
              onClick={() => setDays(r.days)}
              aria-pressed={days === r.days}
              className={`rounded-full px-2.5 py-1 text-[11.5px] font-semibold transition-colors
                ${days === r.days
                  ? "bg-secondary-container text-on-secondary-container"
                  : "text-ink-soft hover:bg-on-surface/8"}`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>
      {!data ? (
        <div className="h-26 animate-pulse rounded-(--radius-md) bg-raised/60" aria-hidden />
      ) : data.covered_days === 0 ? (
        <p className="text-[12.5px] leading-4.5 text-ink-soft">
          No stock history in this window yet — history accumulates from the daily stock sync
          {data.first_covered ? ` (capture began ${fmtDay(data.first_covered, true)})` : ""}.
        </p>
      ) : (
        <>
          <StockHistoryChart points={data.points} />
          <p className="mt-1 text-[11.5px] leading-4 text-ink-faint">
            {data.covered_days} capture day{data.covered_days === 1 ? "" : "s"} + live now
            {data.reconstructed_days > 0 &&
              ` · ${data.reconstructed_days} reconstructed weekly from Odoo's move ledger`}
            {" · uncovered gaps over 3 weeks break the line"}
          </p>
          <details className="mt-1">
            <summary className="cursor-pointer text-[11.5px] font-semibold text-ink-soft hover:text-ink">
              View as table
            </summary>
            <div className="mt-1.5 max-h-44 overflow-y-auto rounded-(--radius-sm) border border-line/60">
              <table className="w-full text-[11.5px]">
                <thead className="sticky top-0 bg-raised">
                  <tr className="text-left text-ink-soft">
                    <th className="px-2 py-1 font-semibold">Date</th>
                    <th className="px-2 py-1 text-right font-semibold">Bwhse</th>
                    <th className="px-2 py-1 text-right font-semibold">Floor</th>
                    <th className="px-2 py-1 text-right font-semibold" title="Floor staging + Staging 2 pallets">
                      Staging
                    </th>
                    <th className="px-2 py-1 text-right font-semibold">Total</th>
                  </tr>
                </thead>
                <tbody className="tabular-nums">
                  {[...data.points].reverse().map((p) => (
                    <tr key={p.day} className="border-t border-line/40">
                      <td className="px-2 py-1">
                        {fmtDay(p.day, true)}
                        {p.source === "live" && <span className="text-ink-faint"> · live</span>}
                        {p.source === "reconstructed" && (
                          <span className="text-ink-faint"> · recon.</span>
                        )}
                      </td>
                      <td className="px-2 py-1 text-right">{p.bwhse}</td>
                      <td className="px-2 py-1 text-right">{p.floor}</td>
                      <td className="px-2 py-1 text-right">{p.staging + p.staging2}</td>
                      <td className="px-2 py-1 text-right font-semibold">{p.total}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </div>
  );
}

function niceCeil(v: number): number {
  if (v <= 1) return 1;
  const mag = 10 ** Math.floor(Math.log10(v));
  for (const s of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) {
    if (s * mag >= v) return s * mag;
  }
  return 10 * mag;
}

const DAY_MS = 86_400_000;
const GAP_BREAK_DAYS = 21; // beyond this, the line breaks — no fabricated continuity

/** Single-series on-hand line, time-scaled x (capture days are unevenly
 *  spaced). Touching the solid baseline means out of stock — a covered day
 *  with no stock rows is a genuine zero, so the dips are real. Static SVG
 *  (no animation in data displays) with a per-point hover tooltip. */
function StockHistoryChart({ points }: { points: StockHistoryPointOut[] }) {
  const [hover, setHover] = useState<number | null>(null);

  const w = 352;
  const plotH = 88;
  const axisH = 16;
  const yAxisW = 30;
  const endW = 42; // room for the direct end label
  const height = plotH + axisH;

  const times = points.map((p) => Date.parse(p.day));
  const t0 = times[0];
  const span = Math.max(times[times.length - 1] - t0, 1);
  const x = (i: number) => yAxisW + ((times[i] - t0) / span) * (w - yAxisW - endW);
  const yMax = niceCeil(Math.max(...points.map((p) => p.total), 1));
  const y = (v: number) => plotH - (v / yMax) * (plotH - 12) + 4;

  const path = points
    .map((p, i) => {
      const gap = i > 0 && times[i] - times[i - 1] > GAP_BREAK_DAYS * DAY_MS;
      return `${i === 0 || gap ? "M" : "L"}${x(i).toFixed(1)},${y(p.total).toFixed(1)}`;
    })
    .join(" ");

  const ticks = yMax >= 4 ? [0, Math.round(yMax / 2), yMax] : [0, yMax];
  const last = points[points.length - 1];
  const xLabelIdx = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])];
  const hoverPoint = hover !== null ? points[hover] : null;

  return (
    <div className="relative" onMouseLeave={() => setHover(null)}>
      <svg
        width={w}
        height={height}
        viewBox={`0 0 ${w} ${height}`}
        role="img"
        aria-label={`On-hand stock over time: ${points.length} points from ${fmtDay(points[0].day, true)} to today, currently ${last.total}`}
      >
        {ticks.map((t) => (
          <g key={t}>
            {/* the zero line is solid and darker — touching it = out of stock */}
            <line
              x1={yAxisW}
              x2={w - endW + 16}
              y1={y(t)}
              y2={y(t)}
              stroke={t === 0 ? "var(--color-on-surface-variant)" : "var(--color-outline-variant)"}
              strokeWidth={t === 0 ? 1.2 : 1}
            />
            <text
              x={yAxisW - 5}
              y={y(t) + 3}
              textAnchor="end"
              fontSize={9.5}
              className="fill-on-surface-variant"
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              {t.toLocaleString()}
            </text>
          </g>
        ))}
        <path
          d={path}
          fill="none"
          stroke="var(--chart-1)"
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {points.length <= 60 &&
          points.map((p, i) => (
            <circle key={p.day} cx={x(i)} cy={y(p.total)} r={2} fill="var(--chart-1)" />
          ))}
        {hover !== null && (
          <circle
            cx={x(hover)}
            cy={y(points[hover].total)}
            r={5}
            fill="var(--chart-1)"
            stroke="var(--color-surface-container-low)"
            strokeWidth={2}
          />
        )}
        {/* live end dot + direct label */}
        <circle
          cx={x(points.length - 1)}
          cy={y(last.total)}
          r={3.5}
          fill="var(--chart-1)"
          stroke="var(--color-surface-container-low)"
          strokeWidth={2}
        />
        <text
          x={x(points.length - 1) + 7}
          y={y(last.total) + 3.5}
          fontSize={11}
          fontWeight={600}
          className="fill-on-surface"
          style={{ fontVariantNumeric: "tabular-nums" }}
        >
          {last.total.toLocaleString()}
        </text>
        {xLabelIdx.map((i) => (
          <text
            key={i}
            x={x(i)}
            y={plotH + 13}
            textAnchor={i === 0 ? "start" : i === points.length - 1 ? "end" : "middle"}
            fontSize={9.5}
            className="fill-on-surface-variant"
          >
            {i === points.length - 1 ? "today" : fmtDay(points[i].day)}
          </text>
        ))}
        {/* generous hover targets: midpoint boundaries around each point */}
        {points.map((p, i) => {
          const left = i === 0 ? yAxisW : (x(i - 1) + x(i)) / 2;
          const right = i === points.length - 1 ? w : (x(i) + x(i + 1)) / 2;
          return (
            <rect
              key={p.day}
              x={left}
              y={0}
              width={Math.max(right - left, 1)}
              height={height}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
            />
          );
        })}
      </svg>
      {hoverPoint && (
        <div
          className="pointer-events-none absolute -top-1 z-10 rounded-(--radius-md) bg-inverse-surface
            px-2.5 py-1.5 text-inverse-on-surface shadow-(--shadow-e2)"
          style={{ left: Math.min(Math.max(x(hover!) - 60, 0), w - 150) }}
        >
          <div className="text-[10.5px] opacity-80">
            {fmtDay(hoverPoint.day, true)}
            {hoverPoint.source === "live" && " · live now"}
            {hoverPoint.source === "reconstructed" && " · reconstructed"}
          </div>
          <div className="text-[12px] font-semibold" style={{ fontVariantNumeric: "tabular-nums" }}>
            {hoverPoint.total.toLocaleString()} on hand
          </div>
          <div className="text-[10.5px] opacity-80" style={{ fontVariantNumeric: "tabular-nums" }}>
            {(["bwhse", "floor", "staging", "staging2"] as const)
              .filter((k) => hoverPoint[k] > 0)
              .map((k) => `${k} ${hoverPoint[k].toLocaleString()}`)
              .join(" · ") || "out of stock"}
          </div>
        </div>
      )}
    </div>
  );
}
