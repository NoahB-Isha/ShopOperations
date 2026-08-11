/* Shared bits for the phase-2 internal flows: honest write-status chips,
   transfer status colors and stepper, a searchable product picker with
   stock context, and small formatting helpers. Page-level, not design
   system — these know API shapes. */
import { useState } from "react";
import { useProducts } from "../../api/hooks";
import type { ProductOut, TransferStatus } from "../../api/types";
import { Badge, Button, Dialog, Field, Input, Spinner, toneForLabel } from "../../design";
import type { BadgeTone } from "../../design";

/** The identifier the team actually uses on the floor: barcode when the
 *  product has one (93% do), Global SKU as the honest fallback. */
export function productCode(barcode: string | undefined, sku: string): string {
  return barcode || sku;
}

export function fmtQty(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

export function fmtWhen(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay
    ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    : d.toLocaleDateString([], { month: "short", day: "numeric" }) +
        " " +
        d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/* ------------------------------------------------------------ write status */
const DRY_RUN_EXPLANATIONS: Record<string, string> = {
  kill_switch: "Global kill switch is off (ODOO_WRITES_ENABLED) — nothing was written.",
  feature_flag: "This write's feature flag is off — nothing was written.",
  fixture_mode: "Fixture mode (no Odoo credentials) — nothing was written.",
  requested: "Dry-run preview — nothing was written.",
};

export function WriteStatusChip({
  status,
  dryRunReason = "",
  error = "",
  createdLabel = "in Odoo",
}: {
  status: "none" | "created" | "simulated" | "failed";
  dryRunReason?: string;
  error?: string;
  createdLabel?: string;
}) {
  if (status === "none") return null;
  if (status === "created") return <Badge tone="forest">{createdLabel}</Badge>;
  if (status === "failed") return <Badge tone="danger" title={error}>write failed</Badge>;
  return (
    <Badge tone="gold" title={DRY_RUN_EXPLANATIONS[dryRunReason] ?? "Dry run — nothing written."}>
      simulated{dryRunReason ? ` · ${dryRunReason.replace("_", " ")}` : ""}
    </Badge>
  );
}

/** The human handoff: every record the app creates links straight to Odoo. */
export function OdooLink({ url, name }: { url: string; name?: string }) {
  if (!url) return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1 text-sm font-semibold text-primary hover:underline"
    >
      {name ? `Open ${name} in Odoo` : "Open in Odoo"} ↗
    </a>
  );
}

/* -------------------------------------------------------- transfer status */
export const TRANSFER_STEPS: TransferStatus[] = [
  "requested",
  "working_on_it",
  "sent",
  "counting",
  "done",
];

export const TRANSFER_LABELS: Record<TransferStatus, string> = {
  requested: "Requested",
  working_on_it: "Working on it",
  sent: "Sent",
  counting: "Counting",
  done: "Done",
  cancelled: "Cancelled",
};

export function transferTone(status: TransferStatus): BadgeTone {
  switch (status) {
    case "requested":
      return "secondary";
    case "working_on_it":
      return "gold";
    case "sent":
      return "tertiary";
    case "counting":
      return "copper";
    case "done":
      return "forest";
    case "cancelled":
      return "neutral";
  }
}

export function TransferStatusChip({ status }: { status: TransferStatus }) {
  return <Badge tone={transferTone(status)}>{TRANSFER_LABELS[status]}</Badge>;
}

/** Compact M3 stepper: filled dots up to the current stage. */
export function TransferStepper({ status }: { status: TransferStatus }) {
  if (status === "cancelled") {
    return <Badge tone="neutral">Cancelled</Badge>;
  }
  const idx = TRANSFER_STEPS.indexOf(status);
  return (
    <ol className="flex flex-wrap items-center gap-0" aria-label={`Status: ${TRANSFER_LABELS[status]}`}>
      {TRANSFER_STEPS.map((step, i) => (
        <li key={step} className="flex items-center">
          {i > 0 && (
            <span
              aria-hidden
              className={`mx-1 h-0.5 w-5 rounded-full sm:w-9 ${
                i <= idx ? "bg-primary" : "bg-outline-variant"
              }`}
            />
          )}
          <span
            className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12px] font-semibold ${
              i < idx
                ? "bg-primary-container text-on-primary-container"
                : i === idx
                  ? "bg-primary text-on-primary"
                  : "text-on-surface-variant"
            }`}
          >
            {i < idx ? "✓" : ""}
            {TRANSFER_LABELS[step]}
          </span>
        </li>
      ))}
    </ol>
  );
}

/* ------------------------------------------------------------- qty input */
export function QtyInput({
  value,
  onChange,
  min = 0,
  ariaLabel,
  inputRef,
  onEnter,
}: {
  value: number;
  onChange: (v: number) => void;
  min?: number;
  ariaLabel?: string;
  /** for the keyboard flow: parents focus the field after an Enter-add */
  inputRef?: React.Ref<HTMLInputElement>;
  /** Enter inside the field — quick flows bounce focus back to search */
  onEnter?: () => void;
}) {
  return (
    <span className="inline-flex items-center gap-1">
      <Button
        variant="ghost"
        size="sm"
        aria-label="decrease"
        className="!h-8 !w-8 !px-0 text-base"
        onClick={() => onChange(Math.max(min, value - 1))}
      >
        −
      </Button>
      <Input
        ref={inputRef}
        inputMode="numeric"
        aria-label={ariaLabel}
        className="!h-9 w-16 text-center tabular-nums"
        value={String(value)}
        onFocus={(e) => e.target.select()}
        onChange={(e) => {
          const n = Number(e.target.value.replace(/[^0-9.]/g, ""));
          onChange(Number.isFinite(n) ? n : min);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && onEnter) {
            e.preventDefault();
            onEnter();
          }
        }}
      />
      <Button
        variant="ghost"
        size="sm"
        aria-label="increase"
        className="!h-8 !w-8 !px-0 text-base"
        onClick={() => onChange(value + 1)}
      >
        +
      </Button>
    </span>
  );
}

/* --------------------------------------------------------- product picker */
export interface PickedLine {
  product_id: number;
  sku: string;
  barcode?: string;
  name: string;
  category: string;
  qty: number;
  floor_qty: number;
  bwhse_qty: number;
  case_size: number;
}

export function toPicked(p: ProductOut, qty = 1): PickedLine {
  return {
    product_id: p.id,
    sku: p.global_sku,
    barcode: p.barcode,
    name: p.name,
    category: p.category,
    qty,
    floor_qty: p.stock?.floor ?? 0,
    bwhse_qty: p.stock?.bwhse ?? 0,
    case_size: p.case_size || 1,
  };
}

/** Search-and-add product picker showing floor vs warehouse quantities —
 *  the numbers volunteers actually need when deciding what to pull. */
export function ProductPicker({
  onPick,
  pickedIds,
  placeholder = "Search products by name, SKU, barcode…",
  excludeClothing = false,
  inputRef,
}: {
  /** viaEnter is true when the keyboard flow added the first result */
  onPick: (line: PickedLine, viaEnter?: boolean) => void;
  pickedIds: Set<number>;
  placeholder?: string;
  /** PURCHASING surfaces (vendor rosters) never offer clothing — out of
   *  scope for buying. Catalogs and stock flows allow it, so this is
   *  opt-in. */
  excludeClothing?: boolean;
  /** quick flows refocus the search after a qty is typed */
  inputRef?: React.Ref<HTMLInputElement>;
}) {
  const [search, setSearch] = useState("");
  const { data, isLoading } = useProducts({
    search,
    category: "",
    tag: "",
    page: 1,
    sort: "name",
    dir: "asc",
  });
  const results = (data?.items ?? [])
    .filter((p) => p.is_stock_tracked)
    .filter((p) => !excludeClothing || !/clothing/i.test(p.category))
    .slice(0, 30);

  return (
    <div>
      <Input
        ref={inputRef}
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        onKeyDown={(e) => {
          // Enter = take the top result: type, enter, type qty, enter, repeat
          if (e.key !== "Enter" || search.trim() === "") return;
          e.preventDefault();
          const first = results.find((p) => !pickedIds.has(p.id));
          if (first) onPick(toPicked(first), true);
        }}
        placeholder={placeholder}
        aria-label="Search products"
        className="w-full"
      />
      {search.trim() !== "" && (
        <div className="mt-2 max-h-80 overflow-y-auto rounded-(--radius-md) bg-surface-container">
          {isLoading && (
            <div className="grid place-items-center py-8">
              <Spinner size={18} />
            </div>
          )}
          {!isLoading && results.length === 0 && (
            <div className="px-4 py-6 text-center text-sm text-on-surface-variant">
              Nothing tracked in Odoo matches “{search.trim()}”.
            </div>
          )}
          <ul>
            {results.map((p) => {
              const already = pickedIds.has(p.id);
              return (
                <li key={p.id}>
                  <button
                    type="button"
                    disabled={already}
                    // the search stays open after a pick: multi-add flows
                    // (catalogs, transfers, vendor rosters) add several items
                    // from one search; the row just flips to "already picked".
                    // Single-pick dialogs unmount the picker anyway.
                    onClick={() => onPick(toPicked(p))}
                    className="state-layer flex w-full items-center justify-between gap-3 px-4
                      py-3 text-left disabled:opacity-45"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium">{p.name}</span>
                      <span className="mt-0.5 flex items-center gap-2 text-[12px] text-on-surface-variant">
                        <span className="font-mono">{productCode(p.barcode, p.global_sku)}</span>
                        {p.case_size > 1 && (
                          <span className="whitespace-nowrap">case of {p.case_size}</span>
                        )}
                        <Badge tone={toneForLabel(p.category)}>{p.category}</Badge>
                      </span>
                    </span>
                    <span className="shrink-0 text-right text-[12px] leading-5 tabular-nums text-on-surface-variant">
                      <span className="block">floor {fmtQty(p.stock?.floor ?? 0)}</span>
                      <span className="block">whse {fmtQty(p.stock?.bwhse ?? 0)}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

/** "Set quantity…" for a multi-selection — one number applied to N lines.
 *  Context menus across the order/transfer tables share this popup. */
export function SetQtyDialog({
  count,
  noun = "item",
  initial = 1,
  min = 0,
  title,
  help,
  applyLabel,
  onApply,
  onClose,
}: {
  count: number;
  noun?: string;
  initial?: number;
  min?: number;
  /** override the default "Set quantity — N items" title (tap-to-add flow) */
  title?: string;
  /** override the helper line; null hides it */
  help?: string | null;
  applyLabel?: string;
  onApply: (qty: number) => void;
  onClose: () => void;
}) {
  const [value, setValue] = useState(String(initial));
  const qty = Math.max(min, Math.round(Number(value) || 0));
  const apply = () => {
    onApply(qty);
    onClose();
  };
  return (
    <Dialog
      open
      onClose={onClose}
      title={title ?? `Set quantity — ${count} ${noun}${count === 1 ? "" : "s"}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={apply}>{applyLabel ?? `Apply to ${count}`}</Button>
        </>
      }
    >
      <Field
        label="Quantity"
        help={help === null ? undefined : (help ?? `Every selected ${noun} gets this quantity.`)}
      >
        <Input
          type="number"
          min={min}
          value={value}
          autoFocus
          onFocus={(e) => e.target.select()}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && apply()}
        />
      </Field>
    </Dialog>
  );
}

/** Low-count honesty, straight from the project brief. */
export function LowCountHint({ qty }: { qty: number }) {
  if (qty <= 0 || qty > 4) return null;
  return (
    <span
      className="text-[11px] text-warn"
      title="Low counts are often wrong in Odoo — check the shelf before promising it."
    >
      verify
    </span>
  );
}
