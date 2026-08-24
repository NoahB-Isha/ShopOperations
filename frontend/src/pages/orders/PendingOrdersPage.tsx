/* The coordinator's approvals board — polls like the phase-2 POS board, so
   new orders just appear. Each card carries the reasonability verdict; the
   real work happens on the order detail page. */
import { useNavigate } from "react-router-dom";
import { useCenterOrders } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { Badge, EmptyState, PageHeader, Spinner } from "../../design";
import { fmtQty, fmtWhen } from "../shared/OpsBits";
import { money } from "./orderBits";

export function PendingOrdersPage() {
  const navigate = useNavigate();
  const { isDepartments } = useAuth();
  const noun = isDepartments ? "department" : "center";
  const { data: all, isLoading } = useCenterOrders({ status: "pending" });
  // "Waiting on you" has to mean it. A shop team member holding the
  // dept-orders add-on can SEE every pending order (their own role sees
  // everything) but may only decide a department's — showing them the rest
  // would be a queue of other people's work with no buttons on it.
  const orders = all?.filter((o) => o.can_decide);

  return (
    <>
      <PageHeader
        title="Pending orders"
        subtitle={`Waiting on you. Approving renders the draft transfer and pings the ${noun} over WhatsApp.`}
      />
      {isLoading ? (
        <div className="grid place-items-center py-20">
          <Spinner size={22} />
        </div>
      ) : !orders?.length ? (
        <EmptyState
          title="All caught up"
          hint={`New orders from your ${noun}s appear here on their own — no refresh needed.`}
        />
      ) : (
        <div className="flex flex-col gap-2.5">
          {orders.map((o) => (
            <div
              key={o.id}
              role="link"
              tabIndex={0}
              data-testid={`pending-order-${o.id}`}
              onClick={() => navigate(`/order/${o.id}`)}
              onKeyDown={(e) => e.key === "Enter" && navigate(`/order/${o.id}`)}
              className={`cursor-pointer rounded-(--radius-lg) p-5 transition-transform
                duration-200 ease-(--ease-spring) hover:-translate-y-0.5
                ${
                  o.reasonability_level === "warn"
                    ? "bg-warn-container/50"
                    : "bg-surface-container-low"
                }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="display text-[17px]">{o.center_name}</span>
                    <span className="text-[13px] font-semibold text-on-surface-variant">
                      {o.display_name}
                    </span>
                    {o.reasonability_level === "warn" && (
                      <Badge tone="gold">worth a look</Badge>
                    )}
                    {o.reasonability_level === "info" && <Badge tone="secondary">notes</Badge>}
                  </div>
                  <div className="mt-1 text-[12.5px] text-on-surface-variant">
                    {fmtWhen(o.created_at)} · by {o.created_by} · {o.line_count} item(s) ·{" "}
                    {fmtQty(o.total_units)} units · {money(o.total_value)}
                  </div>
                </div>
                <span className="text-sm font-bold text-primary">Review →</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
