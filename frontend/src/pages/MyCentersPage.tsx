import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useCenterOrders, useCenters } from "../api/hooks";
import { useAuth } from "../auth/AuthContext";
import { Badge, Card, EmptyState, PageHeader, Spinner } from "../design";
import { fmtWhen } from "./shared/OpsBits";

export function MyCentersPage() {
  const { isDepartments } = useAuth();
  const navigate = useNavigate();
  const { data: centers, isLoading } = useCenters();
  const { data: pending } = useCenterOrders({ status: "pending" });
  // departments vs centers follows the review zone, not the role
  const noun = isDepartments ? "departments" : "centers";

  const pendingByCenter = useMemo(() => {
    const map = new Map<number, { count: number; latest: string }>();
    for (const o of pending ?? []) {
      const cur = map.get(o.center_id);
      map.set(o.center_id, {
        count: (cur?.count ?? 0) + 1,
        latest: cur && cur.latest > o.created_at ? cur.latest : o.created_at,
      });
    }
    return map;
  }, [pending]);

  return (
    <>
      <PageHeader
        title={isDepartments ? "My departments" : "My centers"}
        subtitle={`The ${noun} in your review zone, with whatever's waiting on you.`}
      />
      {isLoading ? (
        <div className="grid place-items-center py-20"><Spinner size={22} /></div>
      ) : !centers?.length ? (
        <EmptyState
          title={`No ${noun} assigned yet`}
          hint="Ask the office to assign your review zone — everything in it then shows up here."
        />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {centers.map((c) => {
            const p = pendingByCenter.get(c.id);
            return (
              <Card key={c.id} className="flex flex-col gap-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="display text-[17px]">{c.name}</div>
                  {c.is_active ? (
                    <Badge tone="forest">active</Badge>
                  ) : (
                    <Badge tone="danger">inactive</Badge>
                  )}
                </div>
                <div className="text-[13px] text-ink-faint">
                  {[c.state, c.country].filter(Boolean).join(", ")}
                  {c.zone_name ? ` · ${c.zone_name}` : ""}
                </div>
                {p ? (
                  <button
                    onClick={() => navigate("/pending-orders")}
                    className="state-layer -mx-1.5 flex items-center justify-between rounded-(--radius-md)
                      bg-warn-container px-3 py-2 text-left text-[13px] font-semibold text-on-warn-container"
                  >
                    <span>
                      {p.count} pending order{p.count === 1 ? "" : "s"}
                    </span>
                    <span className="text-[11.5px] font-medium">latest {fmtWhen(p.latest)} →</span>
                  </button>
                ) : (
                  <div className="text-[12.5px] text-ink-faint">No pending orders.</div>
                )}
                {c.contacts.length > 0 && (
                  <div className="mt-1 border-t border-line/70 pt-2 text-[13px] text-ink-soft">
                    {c.contacts.slice(0, 2).map((ct) => (
                      <div key={ct.name} className="truncate">
                        {ct.name}
                        {ct.phone && <span className="text-ink-faint"> · {ct.phone}</span>}
                      </div>
                    ))}
                  </div>
                )}
                {c.needs_followup && (
                  <Badge tone="gold" title={c.followup_reasons.join(", ")}>
                    needs follow-up
                  </Badge>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </>
  );
}
