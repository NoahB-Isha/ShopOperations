/* One order, the whole story: lines (with live availability), the gentle
   reasonability verdict, the shared timeline, and the honest Odoo linkage.

   Coordinators adjust quantities inline and approve/reject from here —
   approval shows the draft transfer chip + deep link the moment it exists.
   Orderers see status and can withdraw while it's still pending. */
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  useApproveCenterOrder,
  useCancelCenterOrder,
  useCenterOrder,
  useRejectCenterOrder,
} from "../../api/hooks";
import type { CenterOrderOut } from "../../api/types";
import {
  Button,
  Card,
  Dialog,
  Spinner,
  Textarea,
  useToast,
} from "../../design";
import { OdooLink, QtyInput, WriteStatusChip, fmtQty, fmtWhen, productCode } from "../shared/OpsBits";
import {
  AvailabilityBadge,
  OrderStatusChip,
  ReasonBadgeChip,
  money,
} from "./orderBits";

function EventIcon({ kind }: { kind: string }) {
  const glyph =
    kind === "status" ? "◈" : kind === "odoo" ? "⇄" : kind === "notify" ? "✉" :
    kind === "reasonability" ? "☂" : kind === "lines_edited" ? "✎" : "·";
  return (
    <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-surface-container-high text-[12px]">
      {glyph}
    </span>
  );
}

function Timeline({ order }: { order: CenterOrderOut }) {
  return (
    <ol className="flex flex-col gap-2.5">
      {order.events.map((e) => (
        <li key={e.id} className="flex items-start gap-2.5">
          <EventIcon kind={e.kind} />
          <div className="min-w-0 text-[13px] leading-5">
            <span className="text-on-surface">{e.note}</span>
            <span className="block text-[11.5px] text-on-surface-variant">
              {e.actor} · {fmtWhen(e.created_at)}
            </span>
          </div>
        </li>
      ))}
    </ol>
  );
}

/** The Odoo side, honestly: created (link), simulated (why), failed (error),
 *  or the legitimate "no transfer needed" for untracked department orders. */
function PlacementCard({ order }: { order: CenterOrderOut }) {
  const p = order.placement;
  if (order.status !== "approved" && order.status !== "shipped" && p.status === "none") {
    return null; // nothing rendered until approval
  }
  return (
    <Card className="flex flex-col gap-2">
      <div className="text-[13px] font-bold text-on-surface-variant">Odoo transfer</div>
      {p.status === "none" ? (
        <div className="text-[13.5px] text-on-surface">
          No Odoo transfer — fulfilled directly from the Shoppe floor.
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <WriteStatusChip
              status={p.status}
              error={p.error}
              createdLabel={p.picking_name || "in Odoo"}
            />
            {p.status === "failed" && (
              <span className="text-[12.5px] text-error">{p.error}</span>
            )}
          </div>
          <OdooLink url={p.url} name={p.picking_name} />
          {p.status === "created" && order.status === "approved" && (
            <div className="text-[12px] text-on-surface-variant">
              The warehouse validates it in Odoo — this page flips to “Shipped” on its own.
            </div>
          )}
        </>
      )}
    </Card>
  );
}

export function OrderDetailPage() {
  const { id } = useParams();
  const orderId = Number(id);
  const navigate = useNavigate();
  const toast = useToast();
  const { data: order, isLoading } = useCenterOrder(Number.isFinite(orderId) ? orderId : null);

  const approve = useApproveCenterOrder();
  const reject = useRejectCenterOrder();
  const cancel = useCancelCenterOrder();

  // coordinator adjustments live here until approve/save
  const [qtys, setQtys] = useState<Record<number, number>>({});
  const [confirm, setConfirm] = useState<"approve" | "reject" | "cancel" | null>(null);
  const [note, setNote] = useState("");

  useEffect(() => {
    if (order) {
      setQtys(Object.fromEntries(order.lines.map((l) => [l.product_id, l.qty_final])));
    }
  }, [order]);

  const dirty = useMemo(
    () => order?.lines.some((l) => (qtys[l.product_id] ?? l.qty_final) !== l.qty_final) ?? false,
    [order, qtys],
  );

  if (isLoading || !order) {
    return (
      <div className="grid place-items-center py-20">
        <Spinner size={22} />
      </div>
    );
  }

  const a = order.actions;
  const editable = a.can_adjust && order.status === "pending";
  const stored = order.reasonability;

  const act = (kind: "approve" | "reject" | "cancel") => {
    const args = { id: order.id, note };
    const onError = (e: unknown) =>
      toast.error(e instanceof Error ? e.message : "That didn't go through.");
    const done = () => {
      setConfirm(null);
      setNote("");
    };
    if (kind === "approve") {
      const lines = dirty
        ? order.lines.map((l) => ({ product_id: l.product_id, qty: qtys[l.product_id] ?? l.qty_final }))
        : undefined;
      approve.mutate({ ...args, lines }, {
        onSuccess: () => { done(); toast.success("Approved — the orderer's been pinged."); },
        onError,
      });
    } else if (kind === "reject") {
      reject.mutate({ id: order.id, note }, {
        onSuccess: () => { done(); toast.info("Rejected — the orderer's been told."); },
        onError,
      });
    } else {
      cancel.mutate(args, {
        onSuccess: () => { done(); toast.info("Order withdrawn."); },
        onError,
      });
    }
  };
  const busy = approve.isPending || reject.isPending || cancel.isPending;

  return (
    <div className="mx-auto max-w-3xl">
      {/* status pinned top-right on every width — never wraps below */}
      <div className="mb-6 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="display-l text-on-surface">{order.display_name}</h1>
          <div className="mt-1 text-[13px] text-on-surface-variant">
            {order.center.name}
            {order.center.zone_name ? ` · ${order.center.zone_name}` : ""} · by{" "}
            {order.created_by} · {fmtWhen(order.created_at)}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2 pt-2">
          <OrderStatusChip status={order.status} />
          <span className="hidden md:block">
            <Button variant="ghost" onClick={() => navigate(-1)}>
              ← Back
            </Button>
          </span>
        </div>
      </div>

      {order.notes && (
        <Card tone="tertiary" className="mb-4 text-[13.5px]">
          “{order.notes}”
        </Card>
      )}

      {stored.summary && (order.reasonability_level === "warn" || order.reasonability_level === "info") && (
        <Card className="mb-4 flex flex-col gap-2">
          {/* "Order Notes" is the user-facing name for the reasonability
              assessment (Noah 2026-08-02) — API fields and testids keep the
              old name, same UI-only-rebrand rule as Order lists→Catalogs */}
          <h3 data-testid="order-reasonability" className="display text-[20px]">
            Order Notes:{" "}
            {order.reasonability_level === "warn" ? "Worth a look" : "Minor notes"}
          </h3>
          <div className="text-[13.5px]">{stored.summary}</div>
          {(stored.order_badges ?? []).length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {(stored.order_badges ?? []).map((b) => (
                <ReasonBadgeChip key={b.code} b={b} />
              ))}
            </div>
          )}
          <div className="text-[11.5px] text-on-surface-variant">
            Advisory only — you decide.
          </div>
        </Card>
      )}

      {/* lines — stacked, phone-first: nothing ever scrolls sideways */}
      <Card pad={false} className="mb-4">
        <ul className="divide-y divide-outline-variant/40">
          {order.lines.map((l) => {
            const qty = qtys[l.product_id] ?? l.qty_final;
            const adjusted =
              l.qty_approved !== null && l.qty_approved !== l.qty_requested;
            return (
              <li key={l.id} className="px-4 py-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-medium text-on-surface">{l.name}</div>
                    <div className="text-[11.5px] text-on-surface-variant">
                      <span className="font-mono">{productCode(l.barcode, l.sku)}</span>
                      {l.untracked && " · untracked"} · {money(l.unit_price)}
                    </div>
                  </div>
                  <AvailabilityBadge a={l.availability} />
                </div>
                <div className="mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
                  <div className="flex min-w-0 flex-wrap gap-1">
                    {l.badges.map((b) => (
                      <ReasonBadgeChip key={b.code} b={b} />
                    ))}
                  </div>
                  <div className="ml-auto flex shrink-0 items-center gap-2">
                    {editable ? (
                      <>
                        <span className="text-[12px] text-on-surface-variant">
                          asked {fmtQty(l.qty_requested)}
                        </span>
                        <QtyInput
                          value={qty}
                          onChange={(q) =>
                            setQtys((prev) => ({ ...prev, [l.product_id]: q }))
                          }
                          ariaLabel={`Approved quantity for ${l.name}`}
                        />
                      </>
                    ) : (
                      <span className="text-[14px] tabular-nums">
                        {adjusted && (
                          <span className="mr-1.5 text-[12px] text-on-surface-variant line-through">
                            {fmtQty(l.qty_requested)}
                          </span>
                        )}
                        <span className="font-semibold">{fmtQty(l.qty_final)}</span>
                        <span className="ml-1 text-[12px] text-on-surface-variant">
                          {l.qty_final === 1 ? "unit" : "units"}
                        </span>
                      </span>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
        <div className="flex items-center justify-between border-t border-outline-variant/60 px-4 py-3 text-[13.5px] font-semibold">
          <span>Total</span>
          <span className="tabular-nums">
            {fmtQty(order.totals.units)} units · {money(order.totals.value)}
          </span>
        </div>
      </Card>

      {/* actions */}
      {(a.can_approve || a.can_reject || a.can_cancel) && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          {a.can_approve && (
            <Button data-testid="approve-order" onClick={() => setConfirm("approve")} disabled={busy}>
              {dirty ? "Approve with adjustments" : "Approve"}
            </Button>
          )}
          {a.can_reject && (
            <Button variant="outlined" onClick={() => setConfirm("reject")} disabled={busy}>
              Reject
            </Button>
          )}
          {a.can_cancel && (
            <Button variant="ghost" onClick={() => setConfirm("cancel")} disabled={busy}>
              Withdraw order
            </Button>
          )}
          {dirty && (
            <span className="text-[12.5px] text-on-surface-variant">
              Quantity changes apply when you approve.
            </span>
          )}
        </div>
      )}

      <PlacementCard order={order} />

      {order.decision_note && (
        <Card className="mt-4 text-[13.5px]">
          <span className="font-bold">{order.decided_by}:</span> “{order.decision_note}”
        </Card>
      )}

      <div className="mt-6">
        <div className="mb-2.5 text-[13px] font-bold text-on-surface-variant">Timeline</div>
        <Timeline order={order} />
      </div>

      {/* confirm dialogs */}
      <Dialog
        open={confirm !== null}
        onClose={() => setConfirm(null)}
        title={
          confirm === "approve"
            ? "Approve this order?"
            : confirm === "reject"
              ? "Reject this order?"
              : "Withdraw this order?"
        }
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setConfirm(null)}>
              Never mind
            </Button>
            <Button
              data-testid="confirm-action"
              variant={confirm === "reject" ? "danger" : "primary"}
              disabled={busy || (confirm === "reject" && !note.trim())}
              onClick={() => confirm && act(confirm)}
            >
              {busy ? <Spinner size={16} /> : confirm === "approve" ? "Approve" : confirm === "reject" ? "Reject" : "Withdraw"}
            </Button>
          </div>
        }
      >
        {confirm === "approve" && (
          <p className="mb-3 text-[13.5px] text-on-surface-variant">
            {dirty ? "Your quantity adjustments will be applied. " : ""}
            This renders the draft transfer in Odoo and pings the orderer over WhatsApp.
          </p>
        )}
        {confirm === "reject" && (
          <p className="mb-3 text-[13.5px] text-on-surface-variant">
            The reason goes straight to the orderer — say why.
          </p>
        )}
        <Textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={2}
          aria-label="Note"
          placeholder={confirm === "reject" ? "Reason (required)" : "Note (optional)"}
        />
      </Dialog>
    </div>
  );
}
