/* Order history — one page, two audiences.

   Orderers get simple cards with the one-click "Order again" (prefills the
   form via router state). Coordinators/liaisons get the same data with a
   center column and status filter across their whole zone. */
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCenterOrder, useCenterOrders } from "../../api/hooks";
import type { CenterOrderStatus, CenterOrderSummaryOut } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { Badge, Button, EmptyState, PageHeader, Spinner } from "../../design";
import { fmtQty, fmtWhen } from "../shared/OpsBits";
import type { OrderPrefill } from "./PlaceOrderPage";
import { OrderStatusChip, ReasonDot, money } from "./orderBits";

const FILTERS: { key: string; label: string }[] = [
  { key: "", label: "All" },
  { key: "pending", label: "Pending" },
  { key: "approved,shipped", label: "Approved" },
  { key: "rejected,cancelled", label: "Closed" },
];

/** Fetches the full order on demand, then bounces to the form prefilled. */
function DuplicateButton({ order }: { order: CenterOrderSummaryOut }) {
  const navigate = useNavigate();
  const [wanted, setWanted] = useState(false);
  const { data } = useCenterOrder(wanted ? order.id : null);

  useEffect(() => {
    if (!wanted || !data) return;
    const prefill: OrderPrefill = {
      center_id: data.center.id,
      notes: data.notes,
      duplicate_of_id: data.id,
      lines: data.lines
        .filter((l) => l.qty_requested > 0)
        .map((l) => ({ product_id: l.product_id, qty: l.qty_requested })),
    };
    navigate("/place-order", { state: { prefill } });
  }, [wanted, data, navigate]);

  return (
    <Button
      variant="secondary"
      size="sm"
      disabled={wanted}
      onClick={() => setWanted(true)}
      aria-label={`Duplicate ${order.display_name}`}
    >
      {wanted ? <Spinner size={14} /> : "Order again"}
    </Button>
  );
}

export function OrderHistoryPage() {
  const { roles } = useAuth();
  const navigate = useNavigate();
  const isCoordinator =
    roles.has("zone_coordinator") || roles.has("admin");
  const isOrderer = roles.has("center_orderer");
  const [filter, setFilter] = useState("");
  const { data: orders, isLoading } = useCenterOrders(filter ? { status: filter } : {});

  const sorted = useMemo(() => orders ?? [], [orders]);

  return (
    <>
      <PageHeader
        title="Order history"
        subtitle={
          isCoordinator
            ? "Every order across your review zone — tap one for the full story."
            : "Everything you've ordered. One tap re-orders the same basket."
        }
        actions={
          <div className="flex flex-wrap gap-1.5">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                className={`rounded-full px-3 py-1.5 text-[12.5px] font-semibold ${
                  filter === f.key
                    ? "bg-secondary-container text-on-secondary-container"
                    : "border border-outline-variant text-on-surface-variant"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        }
      />

      {isLoading ? (
        <div className="grid place-items-center py-20">
          <Spinner size={22} />
        </div>
      ) : sorted.length === 0 ? (
        <EmptyState
          title="No orders yet"
          hint={isOrderer ? "Place your first one — it takes about a minute." : "Orders land here as your centers place them."}
          action={
            isOrderer ? (
              <Button onClick={() => navigate("/place-order")}>Place an order</Button>
            ) : undefined
          }
        />
      ) : (
        <div className="flex flex-col gap-2.5">
          {sorted.map((o) => (
            <div
              key={o.id}
              role="link"
              tabIndex={0}
              data-testid={`order-card-${o.id}`}
              onClick={() => navigate(`/order/${o.id}`)}
              onKeyDown={(e) => e.key === "Enter" && navigate(`/order/${o.id}`)}
              className="cursor-pointer rounded-(--radius-lg) bg-surface-container-low p-5
                transition-transform duration-200 ease-(--ease-spring) hover:-translate-y-0.5"
            >
              {/* status lives top-right — thumb-scannable on a phone */}
              <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2">
                  <ReasonDot level={o.reasonability_level} />
                  <span className="truncate font-bold text-on-surface">
                    {o.display_name}
                    {isCoordinator && (
                      <span className="ml-2 font-medium text-on-surface-variant">
                        {o.center_name}
                      </span>
                    )}
                  </span>
                </div>
                <span className="shrink-0">
                  <OrderStatusChip status={o.status as CenterOrderStatus} />
                </span>
              </div>
              <div className="mt-1 text-[12.5px] text-on-surface-variant">
                {fmtWhen(o.created_at)} · {o.line_count} item(s) ·{" "}
                {fmtQty(o.total_units)} units · {money(o.total_value)}
                {isCoordinator ? ` · by ${o.created_by}` : ""}
              </div>
              {(o.odoo_picking_name || isOrderer) && (
                <div className="mt-2 flex items-center justify-between gap-2">
                  <span>
                    {o.odoo_picking_name && (
                      <Badge tone="outline" title="The draft transfer in Odoo">
                        {o.odoo_picking_name}
                      </Badge>
                    )}
                  </span>
                  {isOrderer && (
                    <span onClick={(e) => e.stopPropagation()}>
                      <DuplicateButton order={o} />
                    </span>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
