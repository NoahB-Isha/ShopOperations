import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  downloadIndiaProductList,
  useAnalogies,
  useCreatePurchaseOrder,
  useCreatePurchaseOrderUpload,
  useCreateVendorOrder,
  useDeleteIndiaProductList,
  useDismissAnalogy,
  useIndiaProductList,
  useOrderingEmailSettings,
  useOrderingRules,
  usePurchaseOrders,
  useSaveOrderingEmailSettings,
  useSaveOrderingRules,
  useUploadIndiaProductList,
  useVendors,
  useVendorSuggestions,
} from "../../api/hooks";
import type { PurchaseOrderSummaryOut, VendorOut } from "../../api/types";
import {
  Badge,
  Button,
  Card,
  DataTable,
  Dialog,
  EmptyState,
  Fab,
  Field,
  Input,
  PageHeader,
  Select,
  Spinner,
  Textarea,
  useToast,
} from "../../design";
import type { Column } from "../../design";
import { fmtWhen } from "../shared/OpsBits";
import { PoStatusChip, fmtMoh, fmtUnits } from "./orderingBits";

type Tab = "india" | "domestic";

export function PurchasingPage() {
  const [tab, setTab] = useState<Tab>("india");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const orders = usePurchaseOrders("");
  const domesticPending = (orders.data ?? []).filter(
    (o) => o.order_type === "domestic" && o.pending_proposals > 0,
  ).length;
  const indiaPending = (orders.data ?? []).filter(
    (o) => o.order_type === "import" && o.pending_proposals > 0,
  ).length;

  return (
    <div>
      <PageHeader
        title="Purchasing"
        subtitle="India imports quarterly by the engine; domestic vendors weekly by email — both tracked to arrival on the same timelines."
        actions={
          <Button
            variant="ghost"
            onClick={() => setSettingsOpen(true)}
            aria-label="Ordering settings"
            title="Ordering settings"
            className="!h-10 !w-10 !px-0"
            data-testid="ordering-settings"
          >
            <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden>
              <g fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="9" cy="9" r="2.6" />
                <path d="M9 1.8v2M9 14.2v2M1.8 9h2M14.2 9h2M3.9 3.9l1.4 1.4M12.7 12.7l1.4 1.4M14.1 3.9l-1.4 1.4M5.3 12.7l-1.4 1.4" />
              </g>
            </svg>
          </Button>
        }
      />

      {/* the two ordering worlds */}
      <div className="mb-5 flex gap-1 rounded-full bg-surface-container p-1" role="tablist">
        {(
          [
            ["india", "India imports", indiaPending],
            ["domestic", "Domestic", domesticPending],
          ] as const
        ).map(([key, label, pending]) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            data-testid={`tab-${key}`}
            onClick={() => setTab(key)}
            className={`flex h-10 grow items-center justify-center gap-2 rounded-full text-[14px] font-semibold transition-colors
              ${tab === key ? "bg-secondary-container text-on-secondary-container" : "text-on-surface-variant hover:bg-on-surface/8"}`}
          >
            {label}
            {pending > 0 && <Badge tone="danger">{pending} to review</Badge>}
          </button>
        ))}
      </div>

      {tab === "india" ? (
        <IndiaTab orders={orders.data ?? []} loading={orders.isLoading} />
      ) : (
        <DomesticTab orders={orders.data ?? []} loading={orders.isLoading} />
      )}

      <SettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}

/* ------------------------------------------------------------ shared table */

const STATUS_FILTERS = [
  ["", "All"],
  ["draft", "Drafts"],
  ["placed", "Placed"],
  ["closed", "Closed"],
  ["cancelled", "Cancelled"],
] as const;

function orderColumns(kind: Tab): Column<PurchaseOrderSummaryOut>[] {
  const qtyColumns: Column<PurchaseOrderSummaryOut>[] =
    kind === "india"
      ? [
          {
            key: "sea_units",
            header: "Sea",
            align: "right",
            hideBelow: "sm",
            sortable: true,
            render: (o) => o.sea_units.toLocaleString(),
          },
          {
            key: "air_units",
            header: "Air",
            align: "right",
            hideBelow: "sm",
            sortable: true,
            render: (o) => o.air_units.toLocaleString(),
          },
        ]
      : [
          {
            key: "sea_units",
            header: "Units",
            align: "right",
            hideBelow: "sm",
            sortable: true,
            value: (o) => o.sea_units + o.air_units,
            render: (o) => (o.sea_units + o.air_units).toLocaleString(),
          },
        ];
  return [
    {
      key: "name",
      header: "Order",
      sortable: true,
      render: (o) => (
        <div>
          <div className="font-semibold">{o.name}</div>
          <div className="text-[12px] text-on-surface-variant">
            {o.order_type === "domestic" ? (o.vendor_name ?? "vendor") : "India import"} ·{" "}
            {o.reference}
          </div>
        </div>
      ),
    },
    {
      key: "status",
      header: "Status",
      sortable: true,
      render: (o) => (
        <span className="inline-flex items-center gap-1.5">
          <PoStatusChip status={o.status} />
          {o.pending_proposals > 0 && (
            <Badge tone="danger" title="Parsed reply proposals awaiting review">
              {o.pending_proposals} to review
            </Badge>
          )}
          {o.destination === "CAN" && <Badge tone="tertiary">CAN</Badge>}
        </span>
      ),
    },
    {
      key: "ordering_line_count",
      header: "Lines",
      align: "right",
      hideBelow: "md",
      sortable: true,
      value: (o) => o.ordering_line_count,
      render: (o) => (
        <span>
          {o.ordering_line_count}
          <span className="text-on-surface-variant"> / {o.line_count}</span>
        </span>
      ),
    },
    ...qtyColumns,
    {
      key: "created_at",
      header: "Created",
      hideBelow: "lg",
      sortable: true,
      render: (o) => fmtWhen(o.created_at),
    },
  ];
}

function OrdersTable({
  kind,
  orders,
  loading,
  empty,
}: {
  kind: Tab;
  orders: PurchaseOrderSummaryOut[];
  loading: boolean;
  empty: React.ReactNode;
}) {
  const navigate = useNavigate();
  const [status, setStatus] = useState("");
  const rows = orders.filter(
    (o) =>
      (o.order_type === "import") === (kind === "india") && (!status || o.status === status),
  );
  const columns = useMemo(() => orderColumns(kind), [kind]);
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {STATUS_FILTERS.map(([value, label]) => (
          <button
            key={value}
            onClick={() => setStatus(value)}
            className={`rounded-full px-3.5 py-1.5 text-[13px] font-semibold transition-colors
              ${status === value ? "bg-secondary-container text-on-secondary-container" : "text-on-surface-variant hover:bg-on-surface/8"}`}
          >
            {label}
          </button>
        ))}
      </div>
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(o) => o.id}
        loading={loading}
        onRowClick={(o) => navigate(`/purchasing/${o.id}`)}
        empty={empty}
      />
    </div>
  );
}

/* -------------------------------------------------------------- India tab */

function IndiaTab({
  orders,
  loading,
}: {
  orders: PurchaseOrderSummaryOut[];
  loading: boolean;
}) {
  const [newOpen, setNewOpen] = useState(false);
  return (
    <div className="pb-24">
      <ProductListStrip />
      <OrdersTable
        kind="india"
        orders={orders}
        loading={loading}
        empty={
          <EmptyState
            title="No import orders yet"
            hint="Generate a draft from the current snapshot — the review table is the draft."
          />
        }
      />
      <Fab
        label="New order draft"
        onClick={() => setNewOpen(true)}
        className="fixed right-6 bottom-6 z-30"
        data-testid="new-order-draft"
      />
      <NewOrderDialog open={newOpen} onClose={() => setNewOpen(false)} />
    </div>
  );
}

function ProductListStrip() {
  const meta = useIndiaProductList();
  const upload = useUploadIndiaProductList();
  const remove = useDeleteIndiaProductList();
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);

  const pick = () => fileRef.current?.click();
  const onFile = (file: File | null) => {
    if (!file) return;
    upload.mutate(file, {
      onSuccess: (m) =>
        toast.success(`${m.filename}: ${m.matched} of ${m.total_rows} rows matched.`),
      onError: (e) => toast.error(e instanceof Error ? e.message : "Upload failed"),
    });
    if (fileRef.current) fileRef.current.value = "";
  };

  const m = meta.data;
  return (
    <div
      className="mb-4 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-(--radius-md) bg-surface-container px-4 py-2.5 text-[13.5px]"
      data-testid="product-list-strip"
    >
      <input
        ref={fileRef}
        type="file"
        accept=".csv,.tsv,.xlsx,.xlsm"
        className="hidden"
        onChange={(e) => onFile(e.target.files?.[0] ?? null)}
      />
      {m ? (
        <>
          <span className="text-on-surface-variant">Generating orders from</span>
          <b>{m.filename}</b>
          <span className="text-on-surface-variant">
            ({m.matched} products, uploaded {fmtWhen(m.uploaded_at)}
            {m.unmatched_rows.length > 0 && (
              <span
                className="text-warn"
                title={m.unmatched_rows.slice(0, 10).join("\n")}
              >
                {" "}
                · {m.unmatched_rows.length} rows unrecognized
              </span>
            )}
            )
          </span>
          <span className="mx-1 h-4 w-px bg-outline-variant" aria-hidden />
          <button
            className="font-semibold text-primary hover:underline"
            onClick={() => downloadIndiaProductList(m.filename)}
          >
            download
          </button>
          <button
            className="font-semibold text-primary hover:underline"
            onClick={pick}
            disabled={upload.isPending}
          >
            {upload.isPending ? "uploading…" : "upload replacement"}
          </button>
          <button
            className="font-semibold text-on-surface-variant hover:underline"
            onClick={() =>
              remove.mutate(undefined, {
                onSuccess: () => toast.info("Product list removed — back to the full catalog."),
              })
            }
          >
            remove
          </button>
        </>
      ) : (
        <>
          <span className="text-on-surface-variant">
            Generating orders from the <b>full India-import catalog</b> — upload the current
            product list (CSV/Excel) to scope it.
          </span>
          <button
            className="font-semibold text-primary hover:underline"
            onClick={pick}
            disabled={upload.isPending}
          >
            {upload.isPending ? "uploading…" : "upload product list"}
          </button>
        </>
      )}
    </div>
  );
}

function NewOrderDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const toast = useToast();
  const create = useCreatePurchaseOrder();
  const createUpload = useCreatePurchaseOrderUpload();
  const [name, setName] = useState("");
  const [destination, setDestination] = useState("III");
  const [source, setSource] = useState<"odoo" | "upload">("odoo");
  const [file, setFile] = useState<File | null>(null);
  const busy = create.isPending || createUpload.isPending;

  const submit = async () => {
    try {
      const detail =
        source === "upload" && file
          ? await createUpload.mutateAsync({ file, name, destination })
          : await create.mutateAsync({ name, destination });
      toast.success(`Draft ${detail.order.name} generated — ${detail.order.line_count} candidates.`);
      onClose();
      navigate(`/purchasing/${detail.order.id}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not generate the draft");
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="New order draft"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={submit}
            loading={busy}
            disabled={!name.trim() || (source === "upload" && !file)}
          >
            Generate draft
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        <Field label="Order name" help="e.g. “Q3 2026” — becomes the export filename and email subject">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder=" " />
        </Field>
        <Field label="Destination" help="CAN marks the USA→Canada flow (stubbed — SO, transfer and customs paperwork are manual for now)">
          <Select value={destination} onChange={(e) => setDestination(e.target.value)}>
            <option value="III">III — Isha Institute, Tennessee</option>
            <option value="CAN">CAN — Canada (stub)</option>
          </Select>
        </Field>
        <Field
          label="Inputs"
          help="The app's own Odoo snapshot (scoped by the product list above) is the normal path; upload the USA INV CHK workbook or a sales CSV as the fallback."
        >
          <Select
            value={source}
            onChange={(e) => setSource(e.target.value as "odoo" | "upload")}
          >
            <option value="odoo">Current snapshot (Odoo)</option>
            <option value="upload">Upload workbook / CSV</option>
          </Select>
        </Field>
        {source === "upload" && (
          <label className="block rounded-(--radius-md) border border-dashed border-outline-variant p-4 text-center text-[14px] text-on-surface-variant">
            <input
              type="file"
              accept=".xlsx,.xlsm,.csv"
              className="hidden"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            {file ? (
              <span className="font-semibold text-on-surface">{file.name}</span>
            ) : (
              <span>Choose the workbook (.xlsx, SEA sheet) or a sales CSV…</span>
            )}
          </label>
        )}
      </div>
    </Dialog>
  );
}

/* ------------------------------------------------------------ Domestic tab */

function DomesticTab({
  orders,
  loading,
}: {
  orders: PurchaseOrderSummaryOut[];
  loading: boolean;
}) {
  const vendors = useVendors();
  const navigate = useNavigate();
  const usable = (vendors.data ?? []).filter(
    (v) => v.active && v.kind !== "india" && v.product_count > 0,
  );
  const [vendorId, setVendorId] = useState<number | null>(null);
  const selected = usable.find((v) => v.id === vendorId) ?? usable[0] ?? null;

  return (
    <div className="grid gap-6">
      <Card className="p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="headline text-[20px]">Quick order</h2>
          <div className="flex items-center gap-2">
            {usable.length > 0 && (
              <Select
                value={String(selected?.id ?? "")}
                onChange={(e) => setVendorId(Number(e.target.value))}
                aria-label="Vendor"
                className="w-56"
              >
                {usable.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </Select>
            )}
            <Button variant="ghost" size="sm" onClick={() => navigate("/purchasing/vendors")}>
              Manage vendors →
            </Button>
          </div>
        </div>
        {vendors.isLoading ? (
          <div className="grid min-h-24 place-items-center">
            <Spinner size={20} />
          </div>
        ) : selected ? (
          <QuickOrder vendor={selected} />
        ) : (
          <EmptyState
            title="No vendors with products yet"
            hint="Add a vendor and attach its products (search & add) — then ordering is two clicks."
            action={
              <Button onClick={() => navigate("/purchasing/vendors")}>Manage vendors</Button>
            }
          />
        )}
      </Card>

      <section>
        <h2 className="headline mb-3 text-[20px]">Domestic orders</h2>
        <OrdersTable
          kind="domestic"
          orders={orders}
          loading={loading}
          empty={
            <EmptyState
              title="No domestic orders yet"
              hint="Pick a vendor above, set quantities, and email the order — it lands here with its thread."
            />
          }
        />
      </section>
    </div>
  );
}

function QuickOrder({ vendor }: { vendor: VendorOut }) {
  const suggestions = useVendorSuggestions(vendor.id);
  const send = useCreateVendorOrder();
  const toast = useToast();
  const navigate = useNavigate();
  const [quantities, setQuantities] = useState<Record<string, string>>({});

  const items = useMemo(() => suggestions.data?.items ?? [], [suggestions.data]);
  const chosen = useMemo(() => {
    const out: Record<string, number> = {};
    for (const item of items) {
      const raw = quantities[item.global_sku];
      const qty = raw === undefined ? item.suggested_sea_round : Math.max(0, Number(raw) || 0);
      if (qty > 0) out[item.global_sku] = qty;
    }
    return out;
  }, [items, quantities]);
  const totalUnits = Object.values(chosen).reduce((a, b) => a + b, 0);

  const submit = () =>
    send.mutate(
      { vendorId: vendor.id, quantities: chosen, send: true },
      {
        onSuccess: (detail) => {
          setQuantities({});
          const simulated = detail.email_gate_reason
            ? " (dry-run — the email was rendered, not sent)"
            : "";
          toast.success(`Order emailed to ${vendor.name}${simulated}.`);
          navigate(`/purchasing/${detail.order.id}`);
        },
        onError: (e) => toast.error(e instanceof Error ? e.message : "Could not send"),
      },
    );

  if (suggestions.isLoading) {
    return (
      <div className="grid min-h-24 place-items-center">
        <Spinner size={20} />
      </div>
    );
  }
  if (items.length === 0) {
    return (
      <p className="text-[13.5px] text-on-surface-variant">
        {vendor.name} has no products attached yet — add them on the Vendors page.
      </p>
    );
  }
  return (
    <div>
      <div className="overflow-x-auto rounded-(--radius-md) bg-surface-container-low">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="bg-surface-container">
              {["Item", "On hand", "Cover", "Suggested", "Order qty"].map((h) => (
                <th key={h} className="label-m px-3.5 py-3 text-left">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const shown = quantities[item.global_sku] ?? String(item.suggested_sea_round);
              return (
                <tr key={item.global_sku} className="border-b border-outline-variant/50 last:border-b-0">
                  <td className="max-w-72 px-3.5 py-2">
                    <div className="truncate font-medium">{item.name || item.global_sku}</div>
                    <div className="text-[12px] text-on-surface-variant">{item.global_sku}</div>
                  </td>
                  <td className="px-3.5 py-2 tabular-nums">{fmtUnits(item.on_hand)}</td>
                  <td className="px-3.5 py-2 tabular-nums">
                    {fmtMoh(item.current_moh)} mo
                    {item.current_moh < 4 && (
                      <span title="Below 4 months of cover — the reorder trigger"> ⚠</span>
                    )}
                  </td>
                  <td className="px-3.5 py-2 text-[13px] text-on-surface-variant" title={item.air_split_reason}>
                    {item.suggested_sea_round > 0
                      ? item.suggested_sea_round.toLocaleString()
                      : "—"}
                  </td>
                  <td className="px-3.5 py-2">
                    <input
                      type="number"
                      min={0}
                      value={shown}
                      onChange={(e) =>
                        setQuantities((q) => ({ ...q, [item.global_sku]: e.target.value }))
                      }
                      aria-label={`Order quantity for ${item.global_sku}`}
                      className="w-24 rounded-(--radius-sm) border border-outline-variant bg-field px-2 py-1.5 text-right tabular-nums"
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex items-center justify-between">
        <span className="text-[13.5px] text-on-surface-variant">
          {Object.keys(chosen).length} item(s) · {totalUnits.toLocaleString()} units →{" "}
          {vendor.contact_email || "no email on file"}
        </span>
        <Button
          disabled={totalUnits === 0 || !vendor.contact_email}
          loading={send.isPending}
          onClick={submit}
          data-testid="email-vendor-order"
        >
          Email order to {vendor.name}
        </Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------- settings + analogies */

function SettingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast();
  const emailSettings = useOrderingEmailSettings();
  const saveEmail = useSaveOrderingEmailSettings();
  const rules = useOrderingRules();
  const saveRules = useSaveOrderingRules();
  const [indiaTo, setIndiaTo] = useState<string | null>(null);
  const [cc, setCc] = useState<string | null>(null);
  const [rulesText, setRulesText] = useState<string | null>(null);

  const indiaValue = indiaTo ?? (emailSettings.data?.india_to ?? []).join(", ");
  const ccValue = cc ?? (emailSettings.data?.cc ?? []).join(", ");
  const rulesValue = rulesText ?? JSON.stringify(rules.data?.overrides ?? {}, null, 2);

  const save = async () => {
    try {
      let overrides: Record<string, unknown> = {};
      if (rulesValue.trim()) overrides = JSON.parse(rulesValue) as Record<string, unknown>;
      await saveEmail.mutateAsync({
        india_to: indiaValue.split(",").map((s) => s.trim()).filter(Boolean),
        cc: ccValue.split(",").map((s) => s.trim()).filter(Boolean),
      });
      await saveRules.mutateAsync(overrides);
      toast.success("Ordering settings saved.");
      onClose();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not save (is the JSON valid?)");
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Ordering settings"
      wide
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={save} loading={saveEmail.isPending || saveRules.isPending}>
            Save
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        <Field
          label="India order email — To"
          help="Comma-separated. The Coimbatore exports team; placement emails (CSV+XLSX attached) go here. India orders are one email — vendor orders use each vendor's own contact."
        >
          <Input value={indiaValue} onChange={(e) => setIndiaTo(e.target.value)} placeholder=" " />
        </Field>
        <Field label="CC" help="Copied on every order email (India and vendor).">
          <Input value={ccValue} onChange={(e) => setCc(e.target.value)} placeholder=" " />
        </Field>
        <Field
          label="Category rule overrides (JSON)"
          help='Overrides the built-in workbook rules without a code change, e.g. {"category_target_moh": {"BODY CARE": 6}, "air_only_min_moh": 5}. Unknown keys are ignored.'
        >
          <Textarea
            rows={8}
            value={rulesValue}
            onChange={(e) => setRulesText(e.target.value)}
            className="font-mono text-[12.5px]"
            placeholder=" "
          />
        </Field>
        {rules.data && (
          <details className="text-[13px] text-on-surface-variant">
            <summary className="cursor-pointer font-semibold">Effective rules (read-only)</summary>
            <pre className="mt-2 max-h-56 overflow-auto rounded-(--radius-md) bg-surface-container p-3 text-[12px]">
              {JSON.stringify(rules.data.effective, null, 2)}
            </pre>
          </details>
        )}
        <AnalogiesSection />
      </div>
    </Dialog>
  );
}

function AnalogiesSection() {
  const analogies = useAnalogies();
  const dismiss = useDismissAnalogy();
  const toast = useToast();
  const rows = analogies.data ?? [];
  return (
    <div className="border-t border-outline-variant/60 pt-4">
      <h3 className="label-m mb-1 text-on-surface-variant">Forecast analogies</h3>
      <p className="mb-3 text-[13px] text-on-surface-variant">
        New products with no sales history borrow a similar product's demand until real data
        accumulates — then they graduate automatically. Create analogies from a draft's review
        table (rows flagged “new product”).
      </p>
      {rows.length === 0 && (
        <p className="text-[13px] text-on-surface-variant">
          Nothing is forecasting by analogy right now.
        </p>
      )}
      <div className="grid max-h-64 gap-2 overflow-y-auto">
        {rows.map((a) => (
          <Card key={a.id} className="p-3">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="text-[14px] font-semibold">{a.product_name}</div>
                <div className="text-[13px] text-on-surface-variant">
                  {a.analog_name
                    ? `sells like ${a.analog_name}`
                    : `manual estimate: ${a.monthly_estimate}/mo`}
                </div>
                {a.rationale && (
                  <div className="mt-1 text-[12.5px] text-on-surface-variant">{a.rationale}</div>
                )}
              </div>
              <div className="flex flex-col items-end gap-1.5">
                <Badge
                  tone={
                    a.status === "active" ? "secondary" : a.status === "graduated" ? "forest" : "neutral"
                  }
                >
                  {a.status}
                </Badge>
                {a.status === "active" && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      dismiss.mutate(a.id, { onSuccess: () => toast.success("Analogy dismissed.") })
                    }
                  >
                    Dismiss
                  </Button>
                )}
              </div>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
