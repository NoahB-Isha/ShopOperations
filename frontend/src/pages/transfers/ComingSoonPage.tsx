/* Everything already on its way from the warehouse to the floor, totalled
   per product — items on ACTIVE transfer requests PLUS staging-bound
   transfers someone made directly in Odoo (drafts included; the transfers
   sync discovers those). The answer to "should I request this?" before
   anyone requests it twice. */
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useComingSoon } from "../../api/hooks";
import { Badge, EmptyState, Input, PageHeader, Spinner } from "../../design";
import { LowCountHint, TransferStatusChip, fmtQty, productCode } from "../shared/OpsBits";

const PICKING_STATE_LABEL: Record<string, string> = {
  draft: "draft",
  waiting: "waiting",
  confirmed: "to do",
  assigned: "ready",
};

export function ComingSoonPage() {
  const { data: items, isLoading } = useComingSoon();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return items ?? [];
    return (items ?? []).filter(
      (i) =>
        i.name.toLowerCase().includes(q) ||
        i.sku.toLowerCase().includes(q) ||
        i.barcode.toLowerCase().includes(q) ||
        i.category.toLowerCase().includes(q),
    );
  }, [items, search]);

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader
        title="Coming soon"
        subtitle="Already on its way from the warehouse — app requests and transfers made directly in Odoo alike. No need to request it again."
      />
      <Input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search by name, SKU, category…"
        aria-label="Search items on the way"
        className="mb-3 w-full"
      />
      {isLoading ? (
        <div className="grid place-items-center py-24">
          <Spinner size={24} />
        </div>
      ) : !items?.length ? (
        <EmptyState
          title="Nothing on the way"
          hint="Items appear here the moment a transfer request is placed, and leave when it's done."
        />
      ) : visible.length === 0 ? (
        <div className="py-16 text-center text-sm text-on-surface-variant">
          Nothing on the way matches “{search.trim()}”.
        </div>
      ) : (
        <ul className="stagger-children flex flex-col gap-2 pb-8">
          {visible.map((item) => (
            <li
              key={item.product_id}
              className="rounded-(--radius-lg) bg-surface-container-low px-4 py-3.5"
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-[15px] font-medium">{item.name}</div>
                  <div className="mt-0.5 text-[12px] tabular-nums text-on-surface-variant">
                    <span className="font-mono">{productCode(item.barcode, item.sku)}</span> · floor{" "}
                    {fmtQty(item.floor_qty)} · whse {fmtQty(item.bwhse_qty)}{" "}
                    <LowCountHint qty={item.bwhse_qty} />
                  </div>
                </div>
                <span className="shrink-0 text-right">
                  <span className="display block text-2xl leading-none">
                    {fmtQty(item.qty_on_the_way)}
                  </span>
                  <span className="text-[11px] text-on-surface-variant">on the way</span>
                </span>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                {item.requests.map((req) => (
                  <button
                    key={`${item.product_id}-${req.id}`}
                    onClick={() => navigate(`/transfer-requests/${req.id}`)}
                    className="state-layer flex items-center gap-1.5 rounded-full border
                      border-outline-variant px-2.5 py-1 text-[12px] font-medium
                      text-on-surface-variant"
                    title={`Open ${req.display_name}`}
                  >
                    {req.display_name} · {fmtQty(req.qty)}
                    <TransferStatusChip status={req.status} />
                  </button>
                ))}
                {(item.odoo_pickings ?? []).map((pick, i) => (
                  <span
                    key={`${item.product_id}-odoo-${i}`}
                    className="flex items-center gap-1.5 rounded-full border border-dashed
                      border-outline-variant px-2.5 py-1 text-[12px] font-medium
                      text-on-surface-variant"
                    title={`Made directly in Odoo${pick.expected_date ? ` · expected ${pick.expected_date}` : ""}`}
                  >
                    {pick.picking_name} · {fmtQty(pick.qty)}
                    <Badge tone="tertiary">
                      Odoo · {PICKING_STATE_LABEL[pick.state] ?? pick.state}
                    </Badge>
                  </span>
                ))}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
