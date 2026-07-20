import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  downloadOrderAttachment,
  downloadOrderExport,
  useAddOrderEvent,
  useCreateAnalogy,
  useDecideProposal,
  useIngestOrderEmail,
  useOrderTimeline,
  useOverrideOrderLine,
  usePlacePurchaseOrder,
  usePurchaseOrder,
  usePurchaseOrderAction,
  useSuggestAnalogy,
  useUploadOrderAttachment,
} from "../../api/hooks";
import type {
  AnalogSuggestionOut,
  OrderAttachmentOut,
  OrderEmailOut,
  OrderEventKind,
  OrderEventOut,
  OrderProposalOut,
  PurchaseOrderDetailOut,
  PurchaseOrderLineOut,
} from "../../api/types";
import {
  Badge,
  Button,
  Card,
  Dialog,
  EmptyState,
  Fab,
  Field,
  Input,
  Pagination,
  Select,
  Spinner,
  Textarea,
  useToast,
} from "../../design";
import { fmtWhen } from "../shared/OpsBits";
import {
  EVENT_META,
  FLAG_META,
  FlagChips,
  PoStatusChip,
  ProjectionSparkline,
  confidenceLabel,
  describePayload,
  fmtMoh,
  fmtUnits,
} from "./orderingBits";

const PAGE_SIZE = 100;

export function PurchaseOrderPage() {
  const { id } = useParams();
  const orderId = Number(id);
  const detail = usePurchaseOrder(Number.isFinite(orderId) ? orderId : null);

  if (detail.isLoading || !detail.data) {
    return (
      <div className="grid min-h-64 place-items-center">
        <Spinner size={24} />
      </div>
    );
  }
  const data = detail.data;
  return data.order.status === "draft" ? (
    <DraftReview detail={data} />
  ) : (
    <OrderTracking detail={data} />
  );
}

/* ================================================================ DRAFT — the review table */

function DraftReview({ detail }: { detail: PurchaseOrderDetailOut }) {
  const { order, lines } = detail;
  const toast = useToast();
  const [search, setSearch] = useState("");
  const [flagFilter, setFlagFilter] = useState<string>("");
  const [category, setCategory] = useState("");
  const [onlyOrdering, setOnlyOrdering] = useState(false);
  const [page, setPage] = useState(1);
  const [inspecting, setInspecting] = useState<PurchaseOrderLineOut | null>(null);
  const [analogyFor, setAnalogyFor] = useState<PurchaseOrderLineOut | null>(null);
  const [placeOpen, setPlaceOpen] = useState(false);
  const cancelAction = usePurchaseOrderAction("cancel");

  const categories = useMemo(
    () =>
      [...new Set(lines.map((ln) => ln.suggestion.category ?? ""))].filter(Boolean).sort(),
    [lines],
  );
  const flagCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const ln of lines) for (const f of ln.suggestion.flags ?? []) counts[f] = (counts[f] ?? 0) + 1;
    return counts;
  }, [lines]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    setPage(1);
    return lines.filter((ln) => {
      const s = ln.suggestion;
      if (onlyOrdering && ln.final_sea_qty <= 0 && ln.final_air_qty <= 0) return false;
      if (category && s.category !== category) return false;
      if (flagFilter && !(s.flags ?? []).includes(flagFilter)) return false;
      if (
        needle &&
        !`${s.name ?? ""} ${ln.global_sku} ${s.us_sku ?? ""}`.toLowerCase().includes(needle)
      )
        return false;
      return true;
    });
  }, [lines, search, category, flagFilter, onlyOrdering]);
  const pageRows = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  const totals = useMemo(() => {
    let sea = 0;
    let air = 0;
    let ordering = 0;
    for (const ln of lines) {
      sea += ln.final_sea_qty;
      air += ln.final_air_qty;
      if (ln.final_sea_qty > 0 || ln.final_air_qty > 0) ordering += 1;
    }
    return { sea, air, ordering };
  }, [lines]);

  return (
    <div className="pb-24">
      <OrderHeader detail={detail}>
        <Button
          variant="ghost"
          onClick={() =>
            cancelAction.mutate(
              { orderId: order.id },
              { onSuccess: () => toast.info("Draft cancelled.") },
            )
          }
        >
          Discard draft
        </Button>
        <Button variant="secondary" onClick={() => downloadOrderExport(order.id, "csv", order.name)}>
          CSV
        </Button>
        <Button variant="secondary" onClick={() => downloadOrderExport(order.id, "xlsx", order.name)}>
          XLSX
        </Button>
      </OrderHeader>

      {/* filter rail */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search name or SKU…"
          className="w-60"
          aria-label="Search lines"
        />
        <Select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          aria-label="Category filter"
          className="w-44"
        >
          <option value="">All categories</option>
          {categories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </Select>
        <button
          onClick={() => setOnlyOrdering((v) => !v)}
          className={`rounded-full px-3.5 py-1.5 text-[13px] font-semibold transition-colors
            ${onlyOrdering ? "bg-secondary-container text-on-secondary-container" : "text-on-surface-variant hover:bg-on-surface/8"}`}
        >
          Ordering only ({totals.ordering})
        </button>
        <span className="mx-1 h-5 w-px bg-outline-variant" aria-hidden />
        {Object.entries(FLAG_META)
          .filter(([flag]) => flagCounts[flag])
          .map(([flag, meta]) => (
            <button
              key={flag}
              title={meta.help}
              onClick={() => setFlagFilter((f) => (f === flag ? "" : flag))}
              className={`rounded-full px-3 py-1.5 text-[12.5px] font-semibold transition-colors
                ${flagFilter === flag ? "bg-secondary-container text-on-secondary-container" : "text-on-surface-variant hover:bg-on-surface/8"}`}
            >
              {meta.label} · {flagCounts[flag]}
            </button>
          ))}
      </div>

      <ReviewTable
        orderId={order.id}
        rows={pageRows}
        onInspect={setInspecting}
        onAnalogy={setAnalogyFor}
      />
      <div className="mt-2 flex items-center justify-between text-[13px] text-on-surface-variant">
        <span>
          {totals.sea.toLocaleString()} sea units · {totals.air.toLocaleString()} air units across{" "}
          {totals.ordering} lines
        </span>
        <Pagination page={page} pageSize={PAGE_SIZE} total={filtered.length} onPage={setPage} />
      </div>

      <Fab
        label="Place order"
        icon={<span aria-hidden>⛴</span>}
        onClick={() => setPlaceOpen(true)}
        className="fixed right-6 bottom-6 z-30"
        data-testid="place-order"
      />

      <LineDrawer line={inspecting} onClose={() => setInspecting(null)} />
      <AnalogyDialog
        line={analogyFor}
        onClose={() => setAnalogyFor(null)}
        onSaved={() => {
          setAnalogyFor(null);
          toast.success("Analogy saved — regenerate a draft to apply it.");
        }}
      />
      <PlaceDialog
        open={placeOpen}
        onClose={() => setPlaceOpen(false)}
        detail={detail}
        onPlaced={() => {
          setPlaceOpen(false);
          // the invalidated detail query refetches as "placed" and the page
          // switches to the tracking view on its own
          toast.success("Order placed — exports attached and the order email dispatched.");
        }}
      />
    </div>
  );
}

function ReviewTable({
  orderId,
  rows,
  onInspect,
  onAnalogy,
}: {
  orderId: number;
  rows: PurchaseOrderLineOut[];
  onInspect: (line: PurchaseOrderLineOut) => void;
  onAnalogy: (line: PurchaseOrderLineOut) => void;
}) {
  return (
    <div className="overflow-x-auto rounded-(--radius-lg) bg-surface-container-low">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="bg-surface-container">
            {["Item", "On hand", "Sales /mo", "6-mo projection", "Sea", "Air", "Flags"].map(
              (h, i) => (
                <th
                  key={h}
                  className={`label-m px-3.5 py-3 text-left ${i >= 4 && i <= 5 ? "w-28" : ""}`}
                >
                  {h}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map((ln) => (
            <ReviewRow
              key={ln.id}
              orderId={orderId}
              line={ln}
              onInspect={() => onInspect(ln)}
              onAnalogy={() => onAnalogy(ln)}
            />
          ))}
        </tbody>
      </table>
      {rows.length === 0 && (
        <div className="p-6">
          <EmptyState title="No lines match" hint="Loosen the filters above." />
        </div>
      )}
    </div>
  );
}

function ReviewRow({
  orderId,
  line,
  onInspect,
  onAnalogy,
}: {
  orderId: number;
  line: PurchaseOrderLineOut;
  onInspect: () => void;
  onAnalogy: () => void;
}) {
  const s = line.suggestion;
  const flags = s.flags ?? [];
  const diverges = s.diverges_from_baseline;
  return (
    <tr
      className="cursor-pointer border-b border-outline-variant/50 transition-colors last:border-b-0 hover:bg-primary/8"
      onClick={onInspect}
    >
      <td className="max-w-72 px-3.5 py-2">
        <div className="truncate font-medium" title={s.name}>
          {s.name || line.global_sku}
        </div>
        <div className="text-[12px] text-on-surface-variant">
          {line.global_sku}
          {s.category ? ` · ${s.category}` : ""}
        </div>
      </td>
      <td className="px-3.5 py-2 tabular-nums">
        {fmtUnits(s.on_hand)}
        <span className="text-[12px] text-on-surface-variant"> ({fmtMoh(s.current_moh)} mo)</span>
      </td>
      <td className="px-3.5 py-2 tabular-nums" title={`method: ${s.forecast_method ?? "flat"} · confidence: ${s.forecast_confidence ?? "low"}`}>
        {Math.round(s.forecast_mean ?? 0)}
        {diverges && (
          <span className="text-[12px] font-semibold text-tertiary"> ⚠ vs {Math.round(s.baseline_monthly_sales ?? 0)} base</span>
        )}
        {!diverges && (s.forecast_method ?? "flat_avg") !== "flat_avg" && (
          <span className="text-[12px] text-on-surface-variant"> · base {Math.round(s.baseline_monthly_sales ?? 0)}</span>
        )}
      </td>
      <td className="px-3.5 py-2">
        <ProjectionSparkline
          values={s.projected_moh_with_order ?? []}
          target={s.target_moh ?? 0}
        />
      </td>
      <QtyCell orderId={orderId} line={line} leg="sea" />
      <QtyCell orderId={orderId} line={line} leg="air" />
      <td className="px-3.5 py-2" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-1">
          <FlagChips flags={flags} />
          {flags.includes("new_product") && (
            <Button variant="ghost" size="sm" onClick={onAnalogy}>
              analogy…
            </Button>
          )}
        </div>
      </td>
    </tr>
  );
}

function QtyCell({
  orderId,
  line,
  leg,
}: {
  orderId: number;
  line: PurchaseOrderLineOut;
  leg: "sea" | "air";
}) {
  const override = useOverrideOrderLine();
  const toast = useToast();
  const current = leg === "sea" ? line.final_sea_qty : line.final_air_qty;
  const suggested = leg === "sea" ? line.suggested_sea_qty : line.suggested_air_qty;
  const baseline = leg === "sea" ? line.baseline_sea_qty : line.baseline_air_qty;
  const [value, setValue] = useState<string | null>(null);
  const shown = value ?? String(current);

  const commit = () => {
    if (value === null) return;
    const parsed = Math.max(0, Math.round(Number(value) || 0));
    setValue(null);
    if (parsed === current) return;
    override.mutate(
      {
        orderId,
        lineId: line.id,
        [leg === "sea" ? "final_sea_qty" : "final_air_qty"]: parsed,
      },
      { onError: (e) => toast.error(e instanceof Error ? e.message : "Override failed") },
    );
  };

  return (
    <td className="px-3.5 py-2" onClick={(e) => e.stopPropagation()}>
      <input
        type="number"
        min={0}
        value={shown}
        onChange={(e) => setValue(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
        aria-label={`${leg} quantity for ${line.global_sku}`}
        className={`w-20 rounded-(--radius-sm) border bg-field px-2 py-1.5 text-right tabular-nums
          ${current !== suggested ? "border-tertiary font-semibold" : "border-outline-variant"}`}
      />
      <div className="mt-0.5 text-right text-[11.5px] text-on-surface-variant">
        sug {suggested}
        {baseline !== suggested ? ` · base ${baseline}` : ""}
      </div>
    </td>
  );
}

function LineDrawer({
  line,
  onClose,
}: {
  line: PurchaseOrderLineOut | null;
  onClose: () => void;
}) {
  if (!line) return null;
  const s = line.suggestion;
  return (
    <Dialog open onClose={onClose} title={s.name || line.global_sku} wide>
      <div className="grid gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="outline">{line.global_sku}</Badge>
          {s.us_sku && s.us_sku !== line.global_sku && <Badge tone="outline">US {s.us_sku}</Badge>}
          {s.category && <Badge tone="neutral">{s.category}</Badge>}
          <FlagChips flags={s.flags ?? []} max={10} />
        </div>
        <Card className="bg-secondary-container/40 p-4 text-[14px] leading-relaxed">
          <div className="label-m mb-1 text-on-surface-variant">Why this split</div>
          {s.air_split_reason || "No reasoning recorded."}
          {(s.notes ?? []).map((n, i) => (
            <div key={i} className="mt-1.5 text-[13px] text-on-surface-variant">
              • {n}
            </div>
          ))}
        </Card>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MiniStat label="On hand" value={`${fmtUnits(s.on_hand)} (${fmtMoh(s.current_moh)} mo)`} />
          <MiniStat
            label="Sales / month"
            value={`${Math.round(s.forecast_mean ?? 0)} fc · ${Math.round(s.baseline_monthly_sales ?? 0)} base`}
          />
          <MiniStat label="Target cover" value={`${s.target_moh ?? "—"} months`} />
          <MiniStat
            label="History"
            value={`${s.months_active ?? 0} mo · ${s.forecast_confidence ?? "low"} conf`}
          />
        </div>
        <div>
          <div className="label-m mb-1 text-on-surface-variant">
            Projection without / with this order (months-on-hand)
          </div>
          <div className="flex items-center gap-6">
            <ProjectionSparkline values={s.projected_moh ?? []} target={s.target_moh ?? 0} width={180} height={44} />
            <span aria-hidden>→</span>
            <ProjectionSparkline
              values={s.projected_moh_with_order ?? []}
              target={s.target_moh ?? 0}
              width={180}
              height={44}
            />
          </div>
          <div className="mt-1 text-[12px] text-on-surface-variant">
            Month 4: {fmtMoh(s.projected_moh_m4)} → floor 3 · Month 6: {fmtMoh(s.projected_moh_m6)} → target {s.target_moh}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MiniStat label="Suggested" value={`${s.suggested_sea_round ?? 0} sea · ${s.suggested_air_round ?? 0} air`} />
          <MiniStat label="Workbook baseline" value={`${s.baseline_sea_round ?? 0} sea · ${s.baseline_air_round ?? 0} air`} />
          <MiniStat label="Case size" value={String(s.case_size ?? 1)} />
          <MiniStat
            label="Economics"
            value={`margin ${Number(s.margin ?? 0).toFixed(2)} · air loss ${Number(s.profit_lost_by_air ?? 0).toFixed(0)}`}
          />
        </div>
      </div>
    </Dialog>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-(--radius-md) bg-surface-container p-3">
      <div className="label-m text-on-surface-variant">{label}</div>
      <div className="mt-0.5 text-[14px] font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function AnalogyDialog({
  line,
  onClose,
  onSaved,
}: {
  line: PurchaseOrderLineOut | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const suggest = useSuggestAnalogy();
  const create = useCreateAnalogy();
  const toast = useToast();
  const [suggestion, setSuggestion] = useState<AnalogSuggestionOut | null>(null);
  const [estimate, setEstimate] = useState("");

  if (!line || !line.product_id) return null;
  const productId = line.product_id;

  const fetchSuggestion = () =>
    suggest.mutate(productId, {
      onSuccess: setSuggestion,
      onError: (e) => toast.error(e instanceof Error ? e.message : "No suggestion available"),
    });

  const saveAnalog = () =>
    suggestion &&
    create.mutate(
      {
        product_id: productId,
        analog_product_id: suggestion.analog_product_id,
        rationale: suggestion.rationale,
        source: "llm",
      },
      { onSuccess: onSaved },
    );

  const saveEstimate = () =>
    create.mutate(
      {
        product_id: productId,
        monthly_estimate: Number(estimate),
        rationale: "manual monthly estimate",
        source: "manual",
      },
      { onSuccess: onSaved },
    );

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Forecast for ${line.suggestion.name ?? line.global_sku}`}
    >
      <div className="grid gap-4">
        <p className="text-[13.5px] text-on-surface-variant">
          No sales history yet. Borrow a similar product's demand (proposal + your confirmation) or
          set a flat monthly estimate. Either way it's flagged “analogy” and graduates once real
          data accumulates.
        </p>
        <div className="rounded-(--radius-md) bg-surface-container p-3">
          {suggestion ? (
            <div>
              <div className="text-[14px] font-semibold">{suggestion.analog_name}</div>
              <div className="text-[12.5px] text-on-surface-variant">
                {suggestion.analog_sku} · {suggestion.rationale}
              </div>
              <div className="mt-1 text-[12px] text-on-surface-variant">
                suggested by {suggestion.source === "heuristic" ? "name matching" : suggestion.source}
              </div>
              <div className="mt-2 flex gap-2">
                <Button size="sm" onClick={saveAnalog} loading={create.isPending}>
                  Use this analog
                </Button>
                <Button size="sm" variant="ghost" onClick={fetchSuggestion} loading={suggest.isPending}>
                  Try again
                </Button>
              </div>
            </div>
          ) : (
            <Button variant="secondary" onClick={fetchSuggestion} loading={suggest.isPending}>
              Suggest a similar product
            </Button>
          )}
        </div>
        <div className="flex items-end gap-2">
          <Field label="…or monthly estimate" className="grow">
            <Input
              type="number"
              min={1}
              value={estimate}
              onChange={(e) => setEstimate(e.target.value)}
              placeholder=" "
            />
          </Field>
          <Button
            variant="secondary"
            disabled={!Number(estimate)}
            loading={create.isPending}
            onClick={saveEstimate}
          >
            Save estimate
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

function PlaceDialog({
  open,
  onClose,
  detail,
  onPlaced,
}: {
  open: boolean;
  onClose: () => void;
  detail: PurchaseOrderDetailOut;
  onPlaced: () => void;
}) {
  const place = usePlacePurchaseOrder();
  const toast = useToast();
  const { order, email_gate_reason } = detail;
  const ordering = detail.lines.filter((ln) => ln.final_sea_qty > 0 || ln.final_air_qty > 0);
  const sea = ordering.reduce((n, ln) => n + ln.final_sea_qty, 0);
  const air = ordering.reduce((n, ln) => n + ln.final_air_qty, 0);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`Place ${order.name}?`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Keep reviewing
          </Button>
          <Button
            loading={place.isPending}
            data-testid="confirm-place"
            onClick={() =>
              place.mutate(order.id, {
                onSuccess: onPlaced,
                onError: (e) => toast.error(e instanceof Error ? e.message : "Placing failed"),
              })
            }
          >
            Place order
          </Button>
        </>
      }
    >
      <div className="grid gap-3 text-[14px]">
        <p>
          <b>{ordering.length}</b> lines — <b>{sea.toLocaleString()}</b> sea units and{" "}
          <b>{air.toLocaleString()}</b> air units, destination <b>{order.destination}</b>.
        </p>
        <p className="text-on-surface-variant">
          Placing freezes the quantities, stores the ORDER LIST exports (CSV + XLSX) on the order
          forever, and sends the order email with both attached.
        </p>
        {email_gate_reason ? (
          <Card className="bg-warn-container p-3 text-[13.5px]">
            Dry-run: the email will be rendered and recorded but <b>not sent</b> — {email_gate_reason}.
          </Card>
        ) : (
          <Card className="bg-success-container/50 p-3 text-[13.5px]">
            Live sending is enabled — the email goes out to the configured recipients.
          </Card>
        )}
      </div>
    </Dialog>
  );
}

/* ============================================================== TRACKING — the timeline */

function OrderTracking({ detail }: { detail: PurchaseOrderDetailOut }) {
  const { order } = detail;
  const timeline = useOrderTimeline(order.id, order.status === "placed");
  const toast = useToast();
  const closeAction = usePurchaseOrderAction("close");
  const [ingestOpen, setIngestOpen] = useState(false);
  const [eventOpen, setEventOpen] = useState(false);
  const [deciding, setDeciding] = useState<OrderProposalOut | null>(null);

  const t = timeline.data;
  const pending = (t?.proposals ?? []).filter((p) => p.status === "pending");

  return (
    <div className="pb-16">
      <OrderHeader detail={detail}>
        {order.status === "placed" && (
          <>
            <Button variant="ghost" onClick={() => setEventOpen(true)}>
              Add event
            </Button>
            <Button variant="secondary" onClick={() => setIngestOpen(true)} data-testid="ingest-reply">
              Paste a reply
            </Button>
            <Button
              variant="ghost"
              onClick={() =>
                closeAction.mutate(
                  { orderId: order.id },
                  { onSuccess: () => toast.success("Order closed.") },
                )
              }
            >
              Close order
            </Button>
          </>
        )}
        <Button variant="secondary" onClick={() => downloadOrderExport(order.id, "csv", order.name)}>
          CSV
        </Button>
        <Button variant="secondary" onClick={() => downloadOrderExport(order.id, "xlsx", order.name)}>
          XLSX
        </Button>
      </OrderHeader>

      {!t ? (
        <div className="grid min-h-40 place-items-center">
          <Spinner size={22} />
        </div>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
          <div>
            {pending.length > 0 && (
              <section className="mb-6">
                <h2 className="headline mb-3 text-[20px]">
                  Proposals to review{" "}
                  <Badge tone="danger">{pending.length}</Badge>
                </h2>
                <div className="grid gap-3">
                  {pending.map((p) => (
                    <ProposalCard key={p.id} proposal={p} onEdit={() => setDeciding(p)} />
                  ))}
                </div>
              </section>
            )}

            <h2 className="headline mb-3 text-[20px]">Timeline</h2>
            <ol className="rounded-(--radius-lg) bg-surface-container-low p-4">
              {t.events.map((e, i) => (
                <TimelineEvent key={e.id} event={e} last={i === t.events.length - 1} />
              ))}
            </ol>

            <LinesSummary detail={detail} />
          </div>

          <aside className="grid content-start gap-4">
            <LegsCard legs={t.legs} />
            <EmailsCard emails={t.emails} />
            <AttachmentsCard
              orderId={order.id}
              attachments={t.attachments}
            />
          </aside>
        </div>
      )}

      <IngestDialog open={ingestOpen} onClose={() => setIngestOpen(false)} orderId={order.id} />
      <ManualEventDialog
        open={eventOpen}
        onClose={() => setEventOpen(false)}
        detail={detail}
      />
      {deciding && (
        <DecideDialog proposal={deciding} detail={detail} onClose={() => setDeciding(null)} />
      )}
    </div>
  );
}

function OrderHeader({
  detail,
  children,
}: {
  detail: PurchaseOrderDetailOut;
  children?: React.ReactNode;
}) {
  const navigate = useNavigate();
  const { order } = detail;
  return (
    <div className="mb-6">
      <button
        onClick={() => navigate("/purchasing")}
        className="mb-2 text-[13px] font-semibold text-on-surface-variant hover:text-primary"
      >
        ← Purchasing
      </button>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="display-l text-on-surface">{order.name}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[13.5px] text-on-surface-variant">
            <PoStatusChip status={order.status} />
            {order.destination === "CAN" && (
              <Badge tone="tertiary" title="USA→CAN flow is stubbed: handle the SO, transfer and customs paperwork manually">
                Canada
              </Badge>
            )}
            <span>
              {order.order_type === "domestic"
                ? `Vendor: ${order.vendor_name ?? "—"}`
                : "India import"}
            </span>
            <span>· {order.reference}</span>
            <span>
              · snapshot {order.snapshot_source}
              {detail.snapshot_at ? ` @ ${fmtWhen(detail.snapshot_at)}` : ""}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">{children}</div>
      </div>
      {detail.notes && <p className="mt-2 text-[13.5px] text-on-surface-variant">{detail.notes}</p>}
    </div>
  );
}

function ProposalCard({
  proposal,
  onEdit,
}: {
  proposal: OrderProposalOut;
  onEdit: () => void;
}) {
  const decide = useDecideProposal();
  const toast = useToast();
  const meta = EVENT_META[proposal.kind];
  const summary = describePayload(proposal.kind, proposal.payload);
  return (
    <div data-testid="proposal-card">
    <Card className="border-l-4 border-l-tertiary p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="tertiary">
          {meta.icon} {meta.label}
        </Badge>
        {proposal.line_sku ? (
          <Badge tone="outline">{proposal.line_sku}</Badge>
        ) : (
          <Badge tone="gold" title="The parser couldn't match this to a line — pick one via Edit">
            no line matched
          </Badge>
        )}
        <Badge tone="neutral" title={`parsed by ${proposal.parsed_by}`}>
          {confidenceLabel(proposal.confidence)} confident
        </Badge>
        {summary && <span className="text-[13px] text-on-surface-variant">{summary}</span>}
      </div>
      <blockquote className="mt-2 border-l-2 border-outline-variant pl-3 text-[13.5px] italic text-on-surface-variant">
        “{proposal.quote}”
      </blockquote>
      <div className="mt-3 flex gap-2">
        <Button
          size="sm"
          data-testid="confirm-proposal"
          loading={decide.isPending}
          disabled={!proposal.line_id && proposal.kind !== "split"}
          onClick={() =>
            decide.mutate(
              { proposalId: proposal.id, accept: true },
              {
                onSuccess: () => toast.success("Confirmed — the timeline and quantities updated."),
                onError: (e) => toast.error(e instanceof Error ? e.message : "Could not apply"),
              },
            )
          }
        >
          Confirm
        </Button>
        <Button size="sm" variant="secondary" onClick={onEdit}>
          Edit…
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() =>
            decide.mutate(
              { proposalId: proposal.id, accept: false },
              { onSuccess: () => toast.info("Rejected — nothing changed.") },
            )
          }
        >
          Reject
        </Button>
      </div>
    </Card>
    </div>
  );
}

function TimelineEvent({ event, last }: { event: OrderEventOut; last: boolean }) {
  const meta = EVENT_META[event.kind] ?? { icon: "•", label: event.kind };
  const tone =
    event.kind === "discontinued"
      ? "bg-error-container text-on-error-container"
      : event.kind === "email"
        ? "bg-tertiary-container text-on-tertiary-container"
        : event.kind === "status"
          ? "bg-primary-container text-on-primary-container"
          : "bg-secondary-container text-on-secondary-container";
  return (
    <li className="relative flex gap-3 pb-4 last:pb-0">
      {!last && (
        <span
          aria-hidden
          className="absolute top-7 left-[13px] h-[calc(100%-1.5rem)] w-0.5 rounded bg-outline-variant/70"
        />
      )}
      <span
        aria-hidden
        className={`z-10 grid h-7 w-7 shrink-0 place-items-center rounded-full text-[13px] ${tone}`}
      >
        {meta.icon}
      </span>
      <div className="min-w-0">
        <div className="text-[13px] text-on-surface-variant">
          <b className="text-on-surface">{meta.label}</b>
          {event.line_sku ? ` · ${event.line_sku}` : ""}
          {event.status ? ` · ${event.status}` : ""} · {fmtWhen(event.created_at)} ·{" "}
          {event.actor_label}
        </div>
        {event.note && <div className="mt-0.5 text-[14px] whitespace-pre-wrap">{event.note}</div>}
        {event.source_quote && (
          <blockquote className="mt-1 border-l-2 border-outline-variant pl-2 text-[12.5px] italic text-on-surface-variant">
            “{event.source_quote}”
            {event.confidence != null && ` — ${confidenceLabel(event.confidence)}`}
          </blockquote>
        )}
      </div>
    </li>
  );
}

function LinesSummary({ detail }: { detail: PurchaseOrderDetailOut }) {
  const [showAll, setShowAll] = useState(false);
  const lines = detail.lines.filter(
    (ln) =>
      showAll ||
      ln.origin_sea_qty > 0 ||
      ln.origin_air_qty > 0 ||
      ln.final_sea_qty > 0 ||
      ln.final_air_qty > 0,
  );
  return (
    <section className="mt-8">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="headline text-[20px]">Lines — origin → now</h2>
        <Button variant="ghost" size="sm" onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Ordering lines only" : `All ${detail.lines.length} candidates`}
        </Button>
      </div>
      <div className="overflow-x-auto rounded-(--radius-lg) bg-surface-container-low">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="bg-surface-container">
              {["Item", "Origin", "Now", "Status"].map((h) => (
                <th key={h} className="label-m px-3.5 py-3 text-left">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {lines.map((ln) => {
              const changed =
                ln.final_sea_qty !== ln.origin_sea_qty || ln.final_air_qty !== ln.origin_air_qty;
              return (
                <tr key={ln.id} className="border-b border-outline-variant/50 last:border-b-0">
                  <td className="max-w-80 px-3.5 py-2">
                    <div className="truncate font-medium">
                      {ln.suggestion.name || ln.global_sku}
                    </div>
                    <div className="text-[12px] text-on-surface-variant">{ln.global_sku}</div>
                  </td>
                  <td className="px-3.5 py-2 tabular-nums text-on-surface-variant">
                    {ln.origin_sea_qty} sea · {ln.origin_air_qty} air
                  </td>
                  <td className={`px-3.5 py-2 tabular-nums ${changed ? "font-semibold" : ""}`}>
                    {ln.final_sea_qty} sea · {ln.final_air_qty} air
                  </td>
                  <td className="px-3.5 py-2">
                    {ln.line_status === "discontinued" && <Badge tone="danger">discontinued</Badge>}
                    {ln.line_status === "substituted" && (
                      <Badge tone="tertiary">→ {ln.substitute_sku}</Badge>
                    )}
                    {ln.line_status === "active" && changed && <Badge tone="gold">revised</Badge>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function LegsCard({ legs }: { legs: PurchaseOrderDetailOut["legs"] }) {
  return (
    <Card className="p-4">
      <h3 className="label-m mb-2 text-on-surface-variant">Shipment legs</h3>
      {legs.length === 0 && (
        <p className="text-[13px] text-on-surface-variant">Created when the order is placed.</p>
      )}
      <div className="grid gap-2">
        {legs.map((leg) => (
          <div key={leg.id} className="rounded-(--radius-md) bg-surface-container p-3">
            <div className="flex items-center justify-between">
              <span className="font-semibold">{leg.label}</span>
              <Badge tone={leg.method === "air" ? "copper" : "secondary"}>
                {leg.method === "air" ? "✈ air" : "⛴ sea"}
              </Badge>
            </div>
            <div className="mt-1 text-[12.5px] text-on-surface-variant">
              {Object.keys(leg.line_quantities).length} lines · {leg.status}
              {leg.eta ? ` · ETA ${leg.eta}` : ""}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

function EmailsCard({ emails }: { emails: OrderEmailOut[] }) {
  return (
    <Card className="p-4">
      <h3 className="label-m mb-2 text-on-surface-variant">Email thread</h3>
      {emails.length === 0 && (
        <p className="text-[13px] text-on-surface-variant">No messages yet.</p>
      )}
      <div className="grid gap-2">
        {emails.map((m) => (
          <details key={m.id} className="rounded-(--radius-md) bg-surface-container p-3">
            <summary className="cursor-pointer text-[13px]">
              <span className="font-semibold">{m.direction === "out" ? "→ sent" : "← received"}</span>{" "}
              {m.subject || "(no subject)"}
              <span className="text-on-surface-variant">
                {" "}
                · {fmtWhen(m.occurred_at)}
                {m.status === "simulated" && " · DRY-RUN"}
                {m.status === "failed" && " · FAILED"}
              </span>
            </summary>
            <div className="mt-2 text-[12.5px] text-on-surface-variant">
              {m.direction === "out" ? `to ${m.recipients}` : `from ${m.sender}`}
            </div>
            <pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap rounded bg-surface-container-high p-2 text-[12.5px]">
              {m.body}
            </pre>
          </details>
        ))}
      </div>
    </Card>
  );
}

function AttachmentsCard({
  orderId,
  attachments,
}: {
  orderId: number;
  attachments: OrderAttachmentOut[];
}) {
  const upload = useUploadOrderAttachment();
  const toast = useToast();
  return (
    <Card className="p-4">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="label-m text-on-surface-variant">Attachments</h3>
        <label className="cursor-pointer text-[13px] font-semibold text-primary">
          + upload
          <input
            type="file"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file)
                upload.mutate(
                  { orderId, file },
                  {
                    onSuccess: () => toast.success(`${file.name} attached.`),
                    onError: (err) =>
                      toast.error(err instanceof Error ? err.message : "Upload failed"),
                  },
                );
              e.target.value = "";
            }}
          />
        </label>
      </div>
      {attachments.length === 0 && (
        <p className="text-[13px] text-on-surface-variant">Nothing attached yet.</p>
      )}
      <ul className="grid gap-1.5">
        {attachments.map((a) => (
          <li key={a.id} className="flex items-center justify-between gap-2 text-[13px]">
            <button
              className="truncate text-left font-medium hover:text-primary"
              title={a.note || a.filename}
              onClick={() => downloadOrderAttachment(orderId, a.id, a.filename)}
            >
              {a.source === "export" ? "🧾 " : "📎 "}
              {a.filename}
            </button>
            <span className="shrink-0 text-on-surface-variant">
              {(a.size_bytes / 1024).toFixed(0)} KB
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function IngestDialog({
  open,
  onClose,
  orderId,
}: {
  open: boolean;
  onClose: () => void;
  orderId: number;
}) {
  const ingest = useIngestOrderEmail();
  const toast = useToast();
  const [sender, setSender] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Paste a vendor reply"
      wide
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!body.trim()}
            loading={ingest.isPending}
            data-testid="ingest-submit"
            onClick={() =>
              ingest.mutate(
                { orderId, sender, subject, body },
                {
                  onSuccess: (t) => {
                    const n = t.proposals.filter((p) => p.status === "pending").length;
                    toast.success(`Reply ingested — ${n} proposal(s) parsed for review.`);
                    setBody("");
                    onClose();
                  },
                  onError: (e) => toast.error(e instanceof Error ? e.message : "Ingest failed"),
                },
              )
            }
          >
            Ingest & parse
          </Button>
        </>
      }
    >
      <p className="mb-3 text-[13.5px] text-on-surface-variant">
        The email is stored verbatim on the thread and parsed into proposals — nothing changes
        until you confirm each one. (The worker ingests the real mailbox automatically once IMAP
        is configured.)
      </p>
      <div className="grid gap-3">
        <div className="grid grid-cols-2 gap-3">
          <Field label="From">
            <Input value={sender} onChange={(e) => setSender(e.target.value)} placeholder=" " />
          </Field>
          <Field label="Subject">
            <Input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder=" " />
          </Field>
        </div>
        <Field label="Body">
          <Textarea
            rows={7}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder=" "
            data-testid="ingest-body"
          />
        </Field>
      </div>
    </Dialog>
  );
}

const MANUAL_KINDS: { kind: OrderEventKind; label: string }[] = [
  { kind: "qty_change", label: "Quantity change" },
  { kind: "discontinued", label: "Discontinued" },
  { kind: "substitution", label: "Substitution" },
  { kind: "method_change", label: "Sea/air change" },
  { kind: "split", label: "Shipment split (new leg)" },
  { kind: "availability", label: "Availability / ETA" },
  { kind: "note", label: "Note" },
];

function ManualEventDialog({
  open,
  onClose,
  detail,
}: {
  open: boolean;
  onClose: () => void;
  detail: PurchaseOrderDetailOut;
}) {
  const add = useAddOrderEvent();
  const toast = useToast();
  const [kind, setKind] = useState<OrderEventKind>("note");
  const [lineId, setLineId] = useState<string>("");
  const [note, setNote] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});

  const submit = () => {
    const payload = buildEventPayload(kind, fields);
    add.mutate(
      {
        orderId: detail.order.id,
        kind,
        line_id: lineId ? Number(lineId) : undefined,
        payload,
        note,
      },
      {
        onSuccess: () => {
          toast.success("Event recorded on the timeline.");
          setNote("");
          setFields({});
          onClose();
        },
        onError: (e) => toast.error(e instanceof Error ? e.message : "Could not record"),
      },
    );
  };

  const orderingLines = detail.lines.filter(
    (ln) => ln.final_sea_qty > 0 || ln.final_air_qty > 0 || ln.origin_sea_qty > 0,
  );
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setFields((f) => ({ ...f, [k]: e.target.value }));

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Add a timeline event"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={add.isPending}>
            Record event
          </Button>
        </>
      }
    >
      <p className="mb-3 text-[13.5px] text-on-surface-variant">
        For anything the parser missed — applied immediately, append-only.
      </p>
      <div className="grid gap-3">
        <Field label="What happened">
          <Select value={kind} onChange={(e) => setKind(e.target.value as OrderEventKind)}>
            {MANUAL_KINDS.map((k) => (
              <option key={k.kind} value={k.kind}>
                {k.label}
              </option>
            ))}
          </Select>
        </Field>
        {kind !== "split" && kind !== "note" && (
          <Field label="Line">
            <Select value={lineId} onChange={(e) => setLineId(e.target.value)}>
              <option value="">— pick the product —</option>
              {orderingLines.map((ln) => (
                <option key={ln.id} value={ln.id}>
                  {ln.suggestion.name ?? ln.global_sku} ({ln.global_sku})
                </option>
              ))}
            </Select>
          </Field>
        )}
        {kind === "qty_change" && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="New sea qty (blank = unchanged)">
              <Input type="number" min={0} value={fields.sea ?? ""} onChange={set("sea")} placeholder=" " />
            </Field>
            <Field label="New air qty (blank = unchanged)">
              <Input type="number" min={0} value={fields.air ?? ""} onChange={set("air")} placeholder=" " />
            </Field>
          </div>
        )}
        {kind === "substitution" && (
          <Field label="Substitute SKU">
            <Input value={fields.substitute_sku ?? ""} onChange={set("substitute_sku")} placeholder=" " />
          </Field>
        )}
        {kind === "method_change" && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="Direction">
              <Select
                value={fields.direction ?? "sea>air"}
                onChange={(e) => setFields((f) => ({ ...f, direction: e.target.value }))}
              >
                <option value="sea>air">sea → air</option>
                <option value="air>sea">air → sea</option>
              </Select>
            </Field>
            <Field label="Units to move">
              <Input type="number" min={1} value={fields.qty ?? ""} onChange={set("qty")} placeholder=" " />
            </Field>
          </div>
        )}
        {kind === "split" && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="Leg label" help="e.g. “Q3 ADD AIR”">
              <Input value={fields.label ?? ""} onChange={set("label")} placeholder=" " />
            </Field>
            <Field label="Method">
              <Select
                value={fields.method ?? "air"}
                onChange={(e) => setFields((f) => ({ ...f, method: e.target.value }))}
              >
                <option value="air">air</option>
                <option value="sea">sea</option>
              </Select>
            </Field>
            <Field label="ETA (YYYY-MM-DD)" className="col-span-2">
              <Input value={fields.eta ?? ""} onChange={set("eta")} placeholder=" " />
            </Field>
          </div>
        )}
        {kind === "availability" && (
          <Field label="Expected when" help="As the vendor said it — “mid-August”, “next container”…">
            <Input value={fields.eta_text ?? ""} onChange={set("eta_text")} placeholder=" " />
          </Field>
        )}
        <Field label="Note">
          <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder=" " />
        </Field>
      </div>
    </Dialog>
  );
}

function buildEventPayload(kind: OrderEventKind, fields: Record<string, string>): Record<string, unknown> {
  if (kind === "qty_change") {
    const payload: Record<string, unknown> = {};
    if (fields.sea !== undefined && fields.sea !== "") payload.sea = { to: Number(fields.sea) };
    if (fields.air !== undefined && fields.air !== "") payload.air = { to: Number(fields.air) };
    return payload;
  }
  if (kind === "substitution") return { substitute_sku: fields.substitute_sku ?? "" };
  if (kind === "method_change") {
    const [from, to] = (fields.direction ?? "sea>air").split(">");
    return { from, to, qty: fields.qty ? Number(fields.qty) : undefined };
  }
  if (kind === "split")
    return {
      label: fields.label ?? "",
      method: fields.method ?? "air",
      eta: fields.eta || undefined,
      lines: {},
    };
  if (kind === "availability") return { eta_text: fields.eta_text ?? "" };
  return {};
}

function DecideDialog({
  proposal,
  detail,
  onClose,
}: {
  proposal: OrderProposalOut;
  detail: PurchaseOrderDetailOut;
  onClose: () => void;
}) {
  const decide = useDecideProposal();
  const toast = useToast();
  const [lineId, setLineId] = useState<string>(proposal.line_id ? String(proposal.line_id) : "");
  const [fields, setFields] = useState<Record<string, string>>(() => seedFields(proposal));
  const kind = proposal.kind;

  const submit = () => {
    const payload = buildEventPayload(kind, fields);
    decide.mutate(
      {
        proposalId: proposal.id,
        accept: true,
        payload: Object.keys(payload).length ? payload : undefined,
        line_id: lineId ? Number(lineId) : undefined,
      },
      {
        onSuccess: () => {
          toast.success("Confirmed with your edits.");
          onClose();
        },
        onError: (e) => toast.error(e instanceof Error ? e.message : "Could not apply"),
      },
    );
  };

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setFields((f) => ({ ...f, [k]: e.target.value }));

  return (
    <Dialog
      open
      onClose={onClose}
      title="Edit & confirm proposal"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={decide.isPending} disabled={!lineId && kind !== "split"}>
            Confirm with edits
          </Button>
        </>
      }
    >
      <blockquote className="mb-3 border-l-2 border-outline-variant pl-3 text-[13.5px] italic text-on-surface-variant">
        “{proposal.quote}”
      </blockquote>
      <div className="grid gap-3">
        {kind !== "split" && (
          <Field label="Line">
            <Select value={lineId} onChange={(e) => setLineId(e.target.value)}>
              <option value="">— pick the product —</option>
              {detail.lines
                .filter((ln) => ln.origin_sea_qty > 0 || ln.origin_air_qty > 0 || ln.final_sea_qty > 0)
                .map((ln) => (
                  <option key={ln.id} value={ln.id}>
                    {ln.suggestion.name ?? ln.global_sku} ({ln.global_sku})
                  </option>
                ))}
            </Select>
          </Field>
        )}
        {kind === "qty_change" && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="New sea qty (blank = unchanged)">
              <Input type="number" min={0} value={fields.sea ?? ""} onChange={set("sea")} placeholder=" " />
            </Field>
            <Field label="New air qty (blank = unchanged)">
              <Input type="number" min={0} value={fields.air ?? ""} onChange={set("air")} placeholder=" " />
            </Field>
          </div>
        )}
        {kind === "substitution" && (
          <Field label="Substitute SKU">
            <Input value={fields.substitute_sku ?? ""} onChange={set("substitute_sku")} placeholder=" " />
          </Field>
        )}
        {kind === "availability" && (
          <Field label="Expected when">
            <Input value={fields.eta_text ?? ""} onChange={set("eta_text")} placeholder=" " />
          </Field>
        )}
        {kind === "method_change" && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="Direction">
              <Select
                value={fields.direction ?? "sea>air"}
                onChange={(e) => setFields((f) => ({ ...f, direction: e.target.value }))}
              >
                <option value="sea>air">sea → air</option>
                <option value="air>sea">air → sea</option>
              </Select>
            </Field>
            <Field label="Units to move">
              <Input type="number" min={1} value={fields.qty ?? ""} onChange={set("qty")} placeholder=" " />
            </Field>
          </div>
        )}
      </div>
    </Dialog>
  );
}

function seedFields(proposal: OrderProposalOut): Record<string, string> {
  const p = proposal.payload as Record<string, { to?: number } | string | number | undefined>;
  const out: Record<string, string> = {};
  const sea = p.sea as { to?: number } | undefined;
  const air = p.air as { to?: number } | undefined;
  if (sea?.to !== undefined) out.sea = String(sea.to);
  if (air?.to !== undefined) out.air = String(air.to);
  if (typeof p.substitute_hint === "string") out.substitute_sku = p.substitute_hint;
  if (typeof p.substitute_sku === "string" && p.substitute_sku) out.substitute_sku = p.substitute_sku;
  if (typeof p.eta_text === "string") out.eta_text = p.eta_text;
  if (typeof p.to === "string") out.direction = p.from === "air" ? "air>sea" : "sea>air";
  return out;
}
