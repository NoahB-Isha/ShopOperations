import type { BadgeTone } from "../../design";
import { Badge } from "../../design";
import type {
  OrderEventKind,
  PurchaseOrderStatus,
} from "../../api/types";

export const PO_STATUS_LABELS: Record<PurchaseOrderStatus, string> = {
  draft: "Draft — in review",
  placed: "Placed",
  closed: "Closed",
  cancelled: "Cancelled",
};

export function poTone(status: PurchaseOrderStatus): BadgeTone {
  switch (status) {
    case "draft":
      return "gold";
    case "placed":
      return "forest";
    case "closed":
      return "neutral";
    case "cancelled":
      return "danger";
  }
}

export function PoStatusChip({ status }: { status: PurchaseOrderStatus }) {
  return <Badge tone={poTone(status)}>{PO_STATUS_LABELS[status]}</Badge>;
}

/** Engine flags → human chips. Quirk-free: these sit inside a dense table. */
export const FLAG_META: Record<string, { label: string; tone: BadgeTone; help: string }> = {
  low_confidence: { label: "low conf", tone: "gold", help: "Thin sales history — the forecast fell back to the flat baseline." },
  divergence: { label: "diverges", tone: "tertiary", help: "The seasonal forecast departs >30% from the flat baseline — sanity-check before trusting it." },
  air_only: { label: "air only", tone: "copper", help: "Bhoomi/Gold/Silver rule: never ships by sea; topped up to the minimum months-on-hand." },
  sea_only: { label: "sea only", tone: "secondary", help: "Air is not allowed for this item; any near-term gap was folded into the sea quantity." },
  bulk_cycle: { label: "bulk cycle", tone: "copper", help: "Toothpaste/camphor rule: ordered in bulk roughly yearly — refill target raised to a year." },
  expiry: { label: "expiry", tone: "danger", help: "Expiry-sensitive (Bloom): refill target capped so stock sells before it expires." },
  analogy: { label: "analogy", tone: "secondary", help: "No real history yet — forecast borrowed from a similar product. Graduates automatically." },
  domestic: { label: "domestic", tone: "outline", help: "Domestic vendor item — MOQ rule, no sea/air split." },
  low_count: { label: "verify count", tone: "gold", help: "2 or fewer on hand — low counts are often wrong; verify physically." },
  new_product: { label: "new product", tone: "danger", help: "No sales history and no analogy — the engine can't suggest. Assign an analogy or estimate." },
};

export function FlagChips({ flags, max = 3 }: { flags: string[]; max?: number }) {
  const shown = flags.slice(0, max);
  const extra = flags.length - shown.length;
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {shown.map((flag) => {
        const meta = FLAG_META[flag] ?? { label: flag, tone: "neutral" as BadgeTone, help: "" };
        return (
          <Badge key={flag} tone={meta.tone} title={meta.help}>
            {meta.label}
          </Badge>
        );
      })}
      {extra > 0 && <Badge tone="outline">+{extra}</Badge>}
    </span>
  );
}

export const EVENT_META: Record<OrderEventKind, { icon: string; label: string }> = {
  status: { icon: "→", label: "Status" },
  note: { icon: "✎", label: "Note" },
  qty_change: { icon: "⇄", label: "Quantity change" },
  substitution: { icon: "↷", label: "Substitution" },
  discontinued: { icon: "⊘", label: "Discontinued" },
  method_change: { icon: "⇅", label: "Sea/air change" },
  split: { icon: "⑂", label: "Shipment split" },
  availability: { icon: "◷", label: "Availability" },
  email: { icon: "✉", label: "Email" },
  attachment: { icon: "📎", label: "Attachment" },
};

/** Tiny inline projection sparkline: months-on-hand 1..6 with the planned
 *  order landed, against the target line. Pure SVG, no dependency; sits
 *  quietly inside the dense review table (no animation by design). */
export function ProjectionSparkline({
  values,
  target,
  width = 96,
  height = 26,
}: {
  values: number[];
  target: number;
  width?: number;
  height?: number;
}) {
  if (!values.length) return null;
  const pad = 3;
  const top = Math.max(...values, target, 1);
  const x = (i: number) => pad + (i * (width - pad * 2)) / Math.max(values.length - 1, 1);
  const y = (v: number) => height - pad - (Math.max(v, 0) / top) * (height - pad * 2);
  const points = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const targetY = y(target);
  const title = `Projected months-on-hand with this order: ${values
    .map((v, i) => `M${i + 1} ${v.toFixed(1)}`)
    .join(", ")} (target ${target})`;
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={title}
      className="shrink-0"
    >
      <title>{title}</title>
      <line
        x1={pad}
        x2={width - pad}
        y1={targetY}
        y2={targetY}
        stroke="var(--color-outline-variant)"
        strokeDasharray="3 3"
        strokeWidth="1"
      />
      <polyline
        points={points}
        fill="none"
        stroke="var(--color-primary)"
        strokeWidth="1.8"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle
        cx={x(values.length - 1)}
        cy={y(values[values.length - 1])}
        r="2.2"
        fill="var(--color-primary)"
      />
    </svg>
  );
}

export function fmtMoh(v: number | undefined | null): string {
  if (v === undefined || v === null) return "—";
  if (v >= 99) return "∞";
  return v.toFixed(1);
}

export function fmtUnits(v: number | undefined | null): string {
  if (v === undefined || v === null) return "—";
  return Math.round(v).toLocaleString();
}

export function confidenceLabel(c: number): string {
  return `${Math.round(c * 100)}%`;
}

/** Payload → one readable clause, per event kind. */
export function describePayload(kind: OrderEventKind, payload: Record<string, unknown>): string {
  const p = payload as Record<string, { from?: number; to?: number } | string | number | undefined>;
  if (kind === "qty_change") {
    const parts: string[] = [];
    for (const leg of ["sea", "air"] as const) {
      const change = payload[leg] as { from?: number; to?: number } | undefined;
      if (change && change.to !== undefined)
        parts.push(`${leg} ${change.from ?? "?"} → ${change.to}`);
    }
    return parts.join(", ");
  }
  if (kind === "substitution") return `replace with ${String(p.substitute_sku || p.substitute_hint || "…")}`;
  if (kind === "method_change") return `${String(p.qty ?? "all")} units ${String(p.from)} → ${String(p.to)}`;
  if (kind === "split")
    return `${String(p.label)} (${String(p.method)}${p.eta ? `, ETA ${String(p.eta)}` : ""})`;
  if (kind === "availability") return p.eta_text ? `expected ${String(p.eta_text)}` : "";
  return "";
}
