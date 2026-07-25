/** Inventory time machine — one slider, one honest confidence indicator.
 * Past dates replay captured snapshot history (live-captured or backfilled
 * from Odoo's move ledger); future dates run the ordering engine's
 * projection net of incoming. */
import { useEffect, useMemo, useState } from "react";
import {
  useFacets,
  useStartHistoryBackfill,
  useTimeMachine,
  useTimeMachineBounds,
} from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
// Slider shockwaves are DISABLED (Noah, 2026-07-24: firing on every jump
// broke the scrubbing experience). The entry warp lives in AppShell/warpFx;
// settleWarp stays imported — it releases that entry wave once this page
// paints. To re-enable slider waves: re-import fireWarp + requestWarpCapture
// and uncomment their call sites below.
import { settleWarp } from "../../shell/warpFx";
import type { TimeMachineItemOut } from "../../api/types";
import { Badge, Button, Card, DataTable, PageHeader, Select, Spinner, useToast } from "../../design";
import type { BadgeTone, Column } from "../../design";

const DAY_MS = 86_400_000;

function toDate(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}
function toIso(d: Date): string {
  return d.toISOString().slice(0, 10);
}
function addDays(iso: string, days: number): string {
  return toIso(new Date(toDate(iso).getTime() + days * DAY_MS));
}
function daysBetween(a: string, b: string): number {
  return Math.round((toDate(b).getTime() - toDate(a).getTime()) / DAY_MS);
}

const LEVEL_TONE: Record<string, BadgeTone> = {
  high: "forest",
  medium: "gold",
  low: "copper",
  none: "danger",
};

const MODE_LABEL = { past: "Snapshot", today: "Live snapshot", future: "Projection" } as const;

export function TimeMachinePage() {
  const bounds = useTimeMachineBounds();
  const facets = useFacets();
  const { roles } = useAuth();
  const backfill = useStartHistoryBackfill();
  const toast = useToast();
  const [category, setCategory] = useState("");
  // the slider drags `pending`; the query follows 250ms behind
  const [pending, setPending] = useState<string | null>(null);
  const [date, setDate] = useState<string | null>(null);

  useEffect(() => {
    if (bounds.data && date === null) {
      setDate(bounds.data.today);
      setPending(bounds.data.today);
    }
  }, [bounds.data, date]);

  useEffect(() => {
    if (pending === null || pending === date) return;
    const t = setTimeout(() => {
      setDate(pending);
      // fireWarp({ power: 0.6 }); // slider shockwave — disabled, see import note
    }, 250);
    return () => clearTimeout(t);
  }, [pending, date]);

  // releasing the slider (or picking a date) commits IMMEDIATELY — the
  // debounce above only smooths keyboard/wheel streams. Waiting 250ms after
  // a deliberate gesture read as lag.
  const commitNow = (iso: string | null) => {
    if (!iso || iso === date) return;
    setPending(iso);
    setDate(iso);
    // fireWarp({ power: 0.6 }); // slider shockwave — disabled, see import note
  };

  const view = useTimeMachine(date, category);

  // when the view has PAINTED (double-rAF puts us after the frame), release
  // the held ENTRY shockwave — the animation always outlives the loading.
  // (No-op when no wave is active, i.e. on every slider jump.)
  useEffect(() => {
    if (!view.data || view.isFetching) return;
    let raf2 = 0;
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => settleWarp());
    });
    // requestWarpCapture(300); // fed the slider waves — disabled with them
    return () => {
      cancelAnimationFrame(raf1);
      cancelAnimationFrame(raf2);
    };
  }, [view.data, view.isFetching]);

  const b = bounds.data;
  const totalDays = b ? daysBetween(b.min_date, b.max_date) : 0;
  const sliderValue = b && pending ? daysBetween(b.min_date, pending) : 0;
  const todayOffset = b ? daysBetween(b.min_date, b.today) : 0;

  const v = view.data;
  const columns = useMemo<Column<TimeMachineItemOut>[]>(() => {
    const base: Column<TimeMachineItemOut>[] = [
      {
        key: "name",
        header: "Product",
        value: (r) => r.name,
        sortable: true,
        render: (r) => (
          <div>
            <div className="text-on-surface">{r.name}</div>
            <div className="text-[11px] text-on-surface-variant">{r.sku}</div>
          </div>
        ),
      },
      { key: "category", header: "Category", value: (r) => r.category, sortable: true, hideBelow: "md" },
    ];
    if (v?.mode === "future") {
      return [
        ...base,
        {
          key: "total",
          header: "Projected on hand",
          align: "right",
          value: (r) => r.total_qty,
          sortable: true,
          render: (r) => (
            <span className="tm-era-text font-semibold">{r.total_qty.toLocaleString()}</span>
          ),
        },
        {
          key: "incoming",
          header: "Incoming by then",
          align: "right",
          value: (r) => r.incoming_included,
          sortable: true,
          render: (r) => (
            <span className="tm-era-text">
              {r.incoming_included > 0 ? `+${r.incoming_included.toLocaleString()}` : "—"}
            </span>
          ),
          hideBelow: "sm",
        },
        {
          key: "method",
          header: "Forecast",
          value: (r) => r.forecast_method,
          sortable: true,
          render: (r) => (
            <Badge tone={r.forecast_method === "none" ? "outline" : "tertiary"}>
              {r.forecast_method === "none" ? "no history" : r.forecast_method.replaceAll("_", " ")}
            </Badge>
          ),
          hideBelow: "lg",
        },
      ];
    }
    return [
      ...base,
      { key: "bwhse", header: "BWHSE", align: "right", value: (r) => r.bwhse_qty ?? 0, sortable: true, render: (r) => <span className="tm-era-text">{(r.bwhse_qty ?? 0).toLocaleString()}</span> },
      { key: "floor", header: "Floor", align: "right", value: (r) => r.floor_qty ?? 0, sortable: true, render: (r) => <span className="tm-era-text">{(r.floor_qty ?? 0).toLocaleString()}</span>, hideBelow: "sm" },
      { key: "staging", header: "Staging", align: "right", value: (r) => r.staging_qty ?? 0, sortable: true, render: (r) => <span className="tm-era-text">{(r.staging_qty ?? 0).toLocaleString()}</span>, hideBelow: "md" },
      {
        key: "total",
        header: "Total",
        align: "right",
        value: (r) => r.total_qty,
        sortable: true,
        render: (r) => <span className="tm-era-text font-semibold">{r.total_qty.toLocaleString()}</span>,
      },
    ];
  }, [v?.mode]);

  const [filter, setFilter] = useState("");

  const eraClass =
    v?.mode === "past" ? "tm-era-past" : v?.mode === "future" ? "tm-era-future" : "";

  return (
    <div className={`mx-auto max-w-6xl ${eraClass}`}>
      <PageHeader
        title="Time machine"
        subtitle="Inventory on any date — the past from snapshot history, the future from the ordering engine's projection net of incoming shipments."
        actions={
          b && pending ? (
            <div className="flex items-center gap-2">
              {roles.has("admin") && b.history_days.length < 8 && (
                <Button
                  variant="outlined"
                  size="sm"
                  loading={backfill.isPending}
                  onClick={() =>
                    backfill.mutate(undefined, {
                      onSuccess: (r) =>
                        toast.success(
                          `Queued ${r.queued} weekly dates — the worker reconstructs them from Odoo's move ledger over the next few minutes.`,
                        ),
                      onError: (e) =>
                        toast.error(e instanceof Error ? e.message : "Could not queue backfill"),
                    })
                  }
                >
                  Backfill history…
                </Button>
              )}
              <input
                type="date"
                value={pending}
                min={b.min_date}
                max={b.max_date}
                onChange={(e) => e.target.value && commitNow(e.target.value)}
                className="m3-control"
                aria-label="Pick a date"
              />
            </div>
          ) : undefined
        }
      />

      {bounds.isLoading && (
        <div className="grid h-40 place-items-center">
          <Spinner size={24} />
        </div>
      )}

      {b && pending && (
        <div className="space-y-5">
          <Card>
            <div className="mb-2 flex items-baseline justify-between">
              <span className="text-[12px] text-on-surface-variant">{b.min_date}</span>
              <span
                className="title-m tm-era-text text-on-surface"
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {pending}
              </span>
              <span className="text-[12px] text-on-surface-variant">
                {b.max_date} (+{b.horizon_months} months)
              </span>
            </div>
            <input
              type="range"
              min={0}
              max={totalDays}
              value={sliderValue}
              onChange={(e) => setPending(addDays(b.min_date, Number(e.target.value)))}
              onPointerUp={() => commitNow(pending)}
              className="w-full accent-(--color-primary)"
              aria-label="Date slider"
            />
            <div className="relative mt-1 h-4 text-[11px] text-on-surface-variant">
              <button
                className="absolute -translate-x-1/2 font-semibold text-primary"
                style={{ left: `${(todayOffset / Math.max(totalDays, 1)) * 100}%` }}
                onClick={() => setPending(b.today)}
              >
                today
              </button>
            </div>
          </Card>

          {v && (
            <div style={{ opacity: view.isFetching ? 0.7 : 1 }}>
              <Card className="p-4" pad={false}>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={LEVEL_TONE[v.confidence.level] ?? "outline"}>
                    {MODE_LABEL[v.mode]} · confidence {v.confidence.level}
                  </Badge>
                  {v.mode === "past" && v.effective_date !== v.requested_date && (
                    <Badge tone="outline">showing {v.effective_date}</Badge>
                  )}
                  <span className="text-[13px] text-on-surface-variant">{v.confidence.note}</span>
                </div>
              </Card>

              <div className="mt-4 mb-3 flex flex-wrap items-center gap-2">
                <Select
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                  aria-label="Category filter"
                >
                  <option value="">All categories</option>
                  {(facets.data?.categories ?? []).map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </Select>
                <input
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  placeholder="Search products…"
                  className="m3-control w-56"
                  aria-label="Search"
                />
                <span className="ml-auto text-[12px] text-on-surface-variant">
                  {v.items.length.toLocaleString()} products
                </span>
              </div>

              <DataTable
                columns={columns}
                rows={v.items}
                rowKey={(r) => r.product_id}
                filterText={filter}
                loading={view.isLoading}
                empty={
                  v.confidence.level === "none"
                    ? "No snapshot history reaches back this far."
                    : "Nothing matches."
                }
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
