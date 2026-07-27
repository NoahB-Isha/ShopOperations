/** Sales dashboard — highlights up top, drill-down below (the June-2026
 * report shape). Numbers come straight from the app's sales snapshot;
 * generated copy (narrative + Q&A) is clearly labeled with its source and
 * never blocks the numbers. */
import { useMemo, useState } from "react";
import {
  useAskQuestion,
  useBreakdown,
  useNarrative,
  useRefreshNarrative,
  useSalesOverview,
} from "../../api/hooks";
import type { BreakdownRowOut, QaOut } from "../../api/types";
import { Badge, Button, Card, DataTable, PageHeader, Select, Spinner, Stat } from "../../design";
import type { Column } from "../../design";
import {
  CategoryBars,
  CHANNEL_LABEL,
  ChannelLegend,
  CHANNEL_ORDER,
  CustomersBars,
  fmtMoney,
  fmtMoneyFull,
  fmtPct,
  SCOPE_COLOR,
  StackedChannelBars,
} from "./chartBits";

const PERIODS = [
  { key: "mtd", label: "This month" },
  { key: "last_month", label: "Last month" },
  { key: "3m", label: "Last 3 months" },
  { key: "6m", label: "Last 6 months" },
  { key: "12m", label: "Last 12 months" },
  { key: "24m", label: "Last 24 months" },
  { key: "qtd", label: "Quarter to date" },
  { key: "ytd", label: "Year to date" },
];

const DIMS = [
  { key: "category", label: "Categories" },
  { key: "product", label: "Products" },
  { key: "channel", label: "Channels" },
  { key: "center", label: "City centers" },
];

function deltaTone(x: number | null): "good" | "bad" | "default" {
  if (x === null) return "default";
  return x >= 0 ? "good" : "bad";
}

const SCOPES = [
  { key: "all", label: "All channels" },
  { key: "in_person", label: "In-person" },
  { key: "online", label: "Online" },
  { key: "city_center", label: "City centers" },
];

export function ReportsPage() {
  const [period, setPeriod] = useState("3m");
  const [scope, setScope] = useState("all");
  const [dim, setDim] = useState("category");
  const [chartView, setChartView] = useState<"chart" | "table">("chart");
  const overview = useSalesOverview(period, scope);
  const drill = useBreakdown(period, dim, scope);

  const ov = overview.data;
  const activeChannels = useMemo(
    () => CHANNEL_ORDER.filter((c) => ov?.series.some((p) => p.channel === c && p.revenue > 0)),
    [ov],
  );
  const scopeColor = SCOPE_COLOR[scope] ?? SCOPE_COLOR.all;
  const orders = ov?.orders;
  const ot = orders?.totals;

  const pickScope = (key: string) => {
    setScope(key);
    // the center dimension only means something with city-center data in view
    if (key === "city_center") setDim("center");
    else if (dim === "center") setDim("category");
  };

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Sales"
        subtitle={ov ? `${ov.period.label} · snapshot data, refreshed by the hourly sync` : undefined}
        actions={
          <Select value={period} onChange={(e) => setPeriod(e.target.value)} aria-label="Period">
            {PERIODS.map((p) => (
              <option key={p.key} value={p.key}>
                {p.label}
              </option>
            ))}
          </Select>
        }
      />

      {/* the one filter row everything below answers to */}
      <div className="mb-6 flex gap-1 overflow-x-auto rounded-full bg-surface-container p-1" role="tablist">
        {SCOPES.map((s) => (
          <button
            key={s.key}
            role="tab"
            aria-selected={scope === s.key}
            data-testid={`scope-${s.key}`}
            onClick={() => pickScope(s.key)}
            className={`rounded-full px-4 py-1.5 text-[13px] font-semibold whitespace-nowrap transition-colors ${
              scope === s.key
                ? "bg-secondary-container text-on-secondary-container"
                : "text-on-surface-variant"
            }`}
          >
            {s.label}
          </button>
        ))}
      </div>

      {overview.isLoading && (
        <div className="grid h-48 place-items-center">
          <Spinner size={24} />
        </div>
      )}

      {ov && (
        <div className="space-y-6" style={{ opacity: overview.isFetching ? 0.7 : 1 }}>
          {/* highlights: the KPI row */}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat
              label="Revenue"
              value={fmtMoney(ov.totals.revenue)}
              hint={`${fmtPct(ov.totals.revenue_delta_pct)} vs prior period`}
              tone={deltaTone(ov.totals.revenue_delta_pct)}
            />
            <Stat
              label="Orders"
              value={(ot?.orders ?? 0).toLocaleString()}
              hint={`${fmtPct(ot?.orders_delta_pct ?? null)} vs prior period`}
              tone={deltaTone(ot?.orders_delta_pct ?? null)}
            />
            <Stat
              label="Avg order"
              value={ot?.aov != null ? `$${ot.aov.toFixed(2)}` : "—"}
              hint={`${fmtPct(ot?.aov_delta_pct ?? null)} order size vs prior`}
              tone={deltaTone(ot?.aov_delta_pct ?? null)}
            />
            <Stat
              label="New customers"
              value={(ot?.new_customers ?? 0).toLocaleString()}
              hint={
                ot?.returning_share_last_month != null
                  ? `${(ot.returning_share_last_month * 100).toFixed(0)}% returning in ${ot.returning_share_month}`
                  : "loyalty splits appear as data accrues"
              }
            />
          </div>
          {ov.totals.estimated_share > 0.001 && (
            <p className="text-[13px] text-on-surface-variant">
              <Badge tone="gold">estimate</Badge>{" "}
              {(ov.totals.estimated_share * 100).toFixed(0)}% of revenue here is estimated at
              current retail price (rows synced before amount capture
              {ov.totals.has_legacy_channel_rows ? ", incl. legacy unsplit POS rows" : ""}) — an
              admin can rebuild sales history from the Status page to fill in real amounts.
            </p>
          )}

          {/* the narrative card — generated content, clearly labeled */}
          {scope === "all" && <NarrativeCard period={period} />}

          {/* revenue by month, stacked by channel */}
          <Card>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="title-l text-on-surface">Revenue by month</h2>
                <p className="text-[13px] text-on-surface-variant">
                  {activeChannels.length > 1 ? "stacked by channel" : "monthly revenue"}
                </p>
              </div>
              <div className="flex items-center gap-3">
                {activeChannels.length > 1 && <ChannelLegend channels={activeChannels} />}
                <div className="flex gap-1 rounded-full bg-surface-container p-1" role="tablist">
                  {(["chart", "table"] as const).map((v) => (
                    <button
                      key={v}
                      role="tab"
                      aria-selected={chartView === v}
                      onClick={() => setChartView(v)}
                      className={`rounded-full px-3 py-1 text-[12px] font-semibold transition-colors ${
                        chartView === v
                          ? "bg-secondary-container text-on-secondary-container"
                          : "text-on-surface-variant"
                      }`}
                    >
                      {v === "chart" ? "Chart" : "Table"}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            {chartView === "chart" ? (
              <StackedChannelBars months={ov.period.months} series={ov.series} />
            ) : (
              <MonthTable months={ov.period.months} series={ov.series} channels={activeChannels} />
            )}
          </Card>

          {/* channel summary row */}
          {scope === "all" && (
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              {ov.channels.map((c) => (
                <Card key={c.channel} className="p-4" pad={false}>
                  <div className="label-caps text-on-surface-variant">{c.label}</div>
                  <div className="title-l mt-1 text-on-surface">{fmtMoneyFull(c.revenue)}</div>
                  <div className="mt-1 flex items-center gap-2 text-[12px] text-on-surface-variant">
                    <span>{(c.share * 100).toFixed(0)}% share</span>
                    <Badge tone={c.delta_pct === null ? "outline" : c.delta_pct >= 0 ? "forest" : "danger"}>
                      {fmtPct(c.delta_pct)}
                    </Badge>
                  </div>
                </Card>
              ))}
            </div>
          )}

          {/* category mix + order-size trend */}
          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <h2 className="title-l mb-1 text-on-surface">Revenue by category</h2>
              <p className="mb-3 text-[13px] text-on-surface-variant">
                top categories this period — full list in the drill-down below
              </p>
              <CategoryBars
                rows={ov.top_categories.map((c) => ({
                  // live Odoo categories all share the org prefix — drop it
                  label: c.label.replace(/^Isha Life USA \/\s*/i, ""),
                  revenue: c.revenue,
                }))}
                color={scopeColor}
              />
            </Card>
            <Card>
              <h2 className="title-l mb-1 text-on-surface">Order size</h2>
              <p className="mb-3 text-[13px] text-on-surface-variant">
                average order value across the period
              </p>
              <div className="flex min-h-44 flex-col items-center justify-center gap-2 py-4">
                <div className="display text-6xl leading-none text-on-surface">
                  {ot?.aov != null ? `$${ot.aov.toFixed(2)}` : "—"}
                </div>
                <div className="text-[13px] text-on-surface-variant">
                  {(ot?.orders ?? 0).toLocaleString()} orders · {fmtMoneyFull(ot?.amount ?? 0)} total
                </div>
                <div className="flex items-center gap-2 text-[12.5px] text-on-surface-variant">
                  <Badge
                    tone={
                      ot?.aov_delta_pct == null
                        ? "outline"
                        : ot.aov_delta_pct >= 0
                          ? "forest"
                          : "danger"
                    }
                  >
                    {fmtPct(ot?.aov_delta_pct ?? null)} vs prior period
                  </Badge>
                  {ot?.prior_aov != null && <span>was ${ot.prior_aov.toFixed(2)}</span>}
                </div>
              </div>
            </Card>
          </div>

          {/* loyalty: who's new, who's back */}
          <Card>
            <h2 className="title-l mb-1 text-on-surface">Customers — new vs returning</h2>
            <p className="mb-3 text-[13px] text-on-surface-variant">
              {orders?.caveat}
              {ot?.known_customer_share != null &&
                ` ${(ot.known_customer_share * 100).toFixed(0)}% of this period's orders have a customer on file.`}
            </p>
            <CustomersBars series={orders?.series ?? []} color={scopeColor} />
          </Card>

          {/* drill-down */}
          <Card>
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <h2 className="title-l text-on-surface">Drill-down</h2>
              <div className="flex gap-1 rounded-full bg-surface-container p-1" role="tablist">
                {DIMS.map((d) => (
                  <button
                    key={d.key}
                    role="tab"
                    aria-selected={dim === d.key}
                    onClick={() => setDim(d.key)}
                    className={`rounded-full px-3 py-1.5 text-[13px] font-semibold transition-colors ${
                      dim === d.key
                        ? "bg-secondary-container text-on-secondary-container"
                        : "text-on-surface-variant"
                    }`}
                  >
                    {d.label}
                  </button>
                ))}
              </div>
            </div>
            <BreakdownTable dim={dim} rows={drill.data?.rows ?? []} loading={drill.isLoading} />
          </Card>

          {/* ask the data */}
          <QaCard period={period} />
        </div>
      )}
    </div>
  );
}

function NarrativeCard({ period }: { period: string }) {
  const narrative = useNarrative(period);
  const refresh = useRefreshNarrative();
  const n = narrative.data;
  return (
    <Card tone="secondary">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <Badge tone="secondary">
              {n?.source === "heuristic" ? "Generated · rules" : `Generated · ${n?.source ?? "…"}`}
            </Badge>
            <span className="text-[12px] text-on-surface-variant">
              summary is machine-written — check the numbers, not the prose
            </span>
          </div>
          {narrative.isLoading && <Spinner size={18} />}
          {n && (
            <>
              <p className="headline text-on-surface">{n.headline}</p>
              <ul className="mt-3 list-disc space-y-1 pl-5 text-[14px] text-on-surface">
                {n.bullets.map((b, i) => (
                  <li key={i}>{b}</li>
                ))}
              </ul>
              {n.actions.length > 0 && (
                <div className="mt-4">
                  <div className="label-caps mb-1 text-on-surface-variant">Suggested (review before acting)</div>
                  <ul className="list-['→_'] space-y-1 pl-5 text-[14px] text-on-surface">
                    {n.actions.map((a, i) => (
                      <li key={i}>{a}</li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          loading={refresh.isPending}
          onClick={() => refresh.mutate(period)}
        >
          Refresh
        </Button>
      </div>
    </Card>
  );
}

function MonthTable({
  months,
  series,
  channels,
}: {
  months: string[];
  series: { month: string; channel: string; revenue: number }[];
  channels: string[];
}) {
  const cell = (m: string, c: string) =>
    series.find((p) => p.month === m && p.channel === c)?.revenue ?? 0;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]" style={{ fontVariantNumeric: "tabular-nums" }}>
        <thead>
          <tr className="text-left text-on-surface-variant">
            <th className="py-1.5 pr-3 font-medium">Month</th>
            {channels.map((c) => (
              <th key={c} className="py-1.5 pr-3 text-right font-medium">
                {CHANNEL_LABEL[c]}
              </th>
            ))}
            <th className="py-1.5 text-right font-medium">Total</th>
          </tr>
        </thead>
        <tbody>
          {months.map((m) => (
            <tr key={m} className="border-t border-outline-variant text-on-surface">
              <td className="py-1.5 pr-3">{m}</td>
              {channels.map((c) => (
                <td key={c} className="py-1.5 pr-3 text-right">
                  {fmtMoneyFull(cell(m, c))}
                </td>
              ))}
              <td className="py-1.5 text-right font-semibold">
                {fmtMoneyFull(channels.reduce((s, c) => s + cell(m, c), 0))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BreakdownTable({
  dim,
  rows,
  loading,
}: {
  dim: string;
  rows: BreakdownRowOut[];
  loading: boolean;
}) {
  const [filter, setFilter] = useState("");
  const columns: Column<BreakdownRowOut>[] = [
    {
      key: "label",
      header: dim === "product" ? "Product" : dim === "center" ? "Center" : dim === "channel" ? "Channel" : "Category",
      value: (r) => r.label,
      sortable: true,
      render: (r) => (
        <div>
          <div className="text-on-surface">{r.label}</div>
          {r.sku && <div className="text-[11px] text-on-surface-variant">{r.sku}</div>}
        </div>
      ),
    },
    ...(dim === "product"
      ? [{ key: "category", header: "Category", value: (r: BreakdownRowOut) => r.category ?? "", sortable: true, hideBelow: "md" } as Column<BreakdownRowOut>]
      : []),
    { key: "units", header: "Units", align: "right", value: (r) => r.units, sortable: true, render: (r) => r.units.toLocaleString() },
    {
      key: "revenue",
      header: "Revenue",
      align: "right",
      value: (r) => r.revenue,
      sortable: true,
      render: (r) => (
        <span>
          {fmtMoneyFull(r.revenue)}
          {r.estimated_share > 0.01 && (
            <span className="ml-1 align-middle text-[10px] text-on-surface-variant" title={`${(r.estimated_share * 100).toFixed(0)}% estimated at current retail`}>
              ≈
            </span>
          )}
        </span>
      ),
    },
    {
      key: "share",
      header: "Share",
      align: "right",
      value: (r) => r.share,
      sortable: true,
      render: (r) => `${(r.share * 100).toFixed(1)}%`,
      hideBelow: "sm",
    },
    {
      key: "delta",
      header: "vs prior",
      align: "right",
      value: (r) => r.delta_pct ?? -999,
      sortable: true,
      render: (r) => (
        <Badge tone={r.delta_pct === null ? "outline" : r.delta_pct >= 0 ? "forest" : "danger"}>
          {fmtPct(r.delta_pct)}
        </Badge>
      ),
    },
  ];
  return (
    <div>
      <div className="mb-3 max-w-60">
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter…"
          className="m3-control w-full"
          aria-label="Filter rows"
        />
      </div>
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.key}
        filterText={filter}
        loading={loading}
        empty="No sales in this period."
      />
    </div>
  );
}

function QaCard({ period }: { period: string }) {
  const ask = useAskQuestion();
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<QaOut | null>(null);
  const submit = () => {
    const q = question.trim();
    if (!q || ask.isPending) return;
    ask.mutate(
      { question: q, period },
      { onSuccess: (data) => setAnswer(data) },
    );
  };
  return (
    <Card>
      <h2 className="title-l text-on-surface">Ask the data</h2>
      <p className="mt-1 text-[13px] text-on-surface-variant">
        e.g. “which centers grew fastest this quarter?” — answers only use this dashboard's
        numbers and are machine-generated.
      </p>
      <div className="mt-3 flex gap-2">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder="Ask about this period's sales…"
          className="m3-control flex-1"
          aria-label="Question"
        />
        <Button onClick={submit} loading={ask.isPending} disabled={!question.trim()}>
          Ask
        </Button>
      </div>
      {answer && (
        <div className="mt-4 rounded-(--radius-md) bg-surface-container-low p-4">
          <div className="mb-1.5">
            <Badge tone="secondary">
              {answer.source === "heuristic" ? "Generated · rules" : `Generated · ${answer.source}`}
            </Badge>
          </div>
          <p className="text-[14px] text-on-surface">{answer.answer}</p>
        </div>
      )}
    </Card>
  );
}
