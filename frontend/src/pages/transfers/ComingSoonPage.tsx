/* Everything already on its way from the warehouse to the floor — every item
   on an ACTIVE transfer request, totalled per product. The answer to "should
   I request this?" before anyone requests it twice. */
import { useNavigate } from "react-router-dom";
import { useComingSoon } from "../../api/hooks";
import { EmptyState, PageHeader, Spinner } from "../../design";
import { LowCountHint, TransferStatusChip, fmtQty } from "../shared/OpsBits";

export function ComingSoonPage() {
  const { data: items, isLoading } = useComingSoon();
  const navigate = useNavigate();

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader
        title="Coming soon"
        subtitle="Already on an active transfer from the warehouse — no need to request it again."
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
      ) : (
        <ul className="stagger-children flex flex-col gap-2 pb-8">
          {items.map((item) => (
            <li
              key={item.product_id}
              className="rounded-(--radius-lg) bg-surface-container-low px-4 py-3.5"
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-[15px] font-medium">{item.name}</div>
                  <div className="mt-0.5 text-[12px] tabular-nums text-on-surface-variant">
                    <span className="font-mono">{item.sku}</span> · floor{" "}
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
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
