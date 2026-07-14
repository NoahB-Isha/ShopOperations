/* Shared bits for the phase-3 ordering flows: order status chips, honest
   availability badges (the OOS timeline), and gentle reasonability badges.
   Page-level, not design system — these know API shapes. */
import type {
  AvailabilityOut,
  CenterOrderStatus,
  ReasonBadge,
  ReasonLevel,
} from "../../api/types";
import { Badge } from "../../design";
import type { BadgeTone } from "../../design";
import { fmtQty } from "../shared/OpsBits";

export const ORDER_LABELS: Record<CenterOrderStatus, string> = {
  pending: "Pending approval",
  approved: "Approved",
  shipped: "Shipped",
  rejected: "Rejected",
  cancelled: "Cancelled",
};

export function orderTone(status: CenterOrderStatus): BadgeTone {
  switch (status) {
    case "pending":
      return "gold";
    case "approved":
      return "tertiary";
    case "shipped":
      return "forest";
    case "rejected":
      return "danger";
    case "cancelled":
      return "neutral";
  }
}

export function OrderStatusChip({ status }: { status: CenterOrderStatus }) {
  return <Badge tone={orderTone(status)}>{ORDER_LABELS[status]}</Badge>;
}

/** The OOS timeline, worn as a chip: in / low (verify) / out + when it's back. */
export function AvailabilityBadge({ a }: { a: AvailabilityOut | null }) {
  if (a === null || a.status === "untracked") {
    return <Badge tone="outline" title="Not tracked in Odoo — always orderable.">always available</Badge>;
  }
  if (a.status === "out") {
    return (
      <Badge tone="danger" title={a.incoming_qty ? `${fmtQty(a.incoming_qty)} on the way` : undefined}>
        out — {a.incoming_label || "no restock scheduled yet"}
      </Badge>
    );
  }
  if (a.status === "low") {
    return (
      <Badge
        tone="gold"
        title="Low counts are often wrong in Odoo — the warehouse will verify physically."
      >
        low · {fmtQty(a.qty)} left
      </Badge>
    );
  }
  return <Badge tone="forest">{fmtQty(a.qty)} in stock</Badge>;
}

export function reasonTone(level: ReasonLevel): BadgeTone {
  return level === "warn" ? "gold" : level === "info" ? "secondary" : "forest";
}

/** One reasonability badge — deliberately gentle: advice, never a blocker. */
export function ReasonBadgeChip({ b }: { b: ReasonBadge }) {
  return (
    <Badge tone={b.level === "warn" ? "gold" : "secondary"} title="Advisory — you decide.">
      {b.text}
    </Badge>
  );
}

/** Compact dot for list rows: only speaks up when there's something to say. */
export function ReasonDot({ level }: { level: ReasonLevel }) {
  if (level !== "warn" && level !== "info") return null;
  return (
    <span
      title={level === "warn" ? "Worth a second look" : "Minor notes"}
      className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${
        level === "warn" ? "bg-warn" : "bg-secondary"
      }`}
    />
  );
}

export function money(n: number): string {
  return n.toLocaleString(undefined, { style: "currency", currency: "USD" });
}
