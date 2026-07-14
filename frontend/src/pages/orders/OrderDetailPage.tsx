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
  Badge,
  Button,
  Card,
  Dialog,
  PageHeader,
  Spinner,
  Textarea,
  useToast,
} from "../../design";
import { OdooLink, QtyInput, WriteStatusChip, fmtQty, fmtWhen } from "../shared/OpsBits";
import {
  AvailabilityBadge,
  OrderStatusChip,
  ReasonBadgeChip,
  money,
  reasonTone,
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
      <PageHeader
        title={order.display_name}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <OrderStatusChip status={order.status} />
            <span>
              {order.center.name}
              {order.center.zone_name ? ` · ${order.center.zone_name}` : ""} · by{" "}
              {order.created_by} · {fmtWhen(order.created_at)}
            </span>
          </span>
        }
        actions={
          <Button variant="ghost" onClick={() => navigate(-1)}>
            ← Back
          </Button>
        }
      />

      {order.notes && (
        <Card tone="tertiary" className="mb-4 text-[13.5px]">
          “{order.notes}”
        </Card>
      )}

      {stored.summary && (order.reasonability_level === "warn" || order.reasonability_level === "info") && (
        <Card className="mb-4 flex flex-col gap-2">
          <div data-testid="order-reasonability" className="flex items-center gap-2">
            <Badge tone={reasonTone(order.reasonability_level)}>
              {order.reasonability_level === "warn" ? "worth a look" : "notes"}
            </Badge>
            <span className="text-[11.5px] text-on-surface-variant">
              {stored.source === "rules+llm" ? "order checker + assistant" : "order checker"} · advisory only
            </span>
          </div>
          <div className="text-[13.5px]">{stored.summary}</div>
          {(stored.order_badges ?? []).length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {(stored.order_badges ?? []).map((b) => (
                <ReasonBadgeChip key={b.code} b={b} />
              ))}
            </div>
          )}
        </Card>
      )}

      {/* lines */}
      <Card pad={false} className="mb-4 overflow-x-auto">
        <table className="w-full text-[13.5px]">
          <thead>
            <tr className="text-left text-[11.5px] uppercase tracking-wide text-on-surface-variant">
              <th className="px-4 py-2.5 font-semibold">Item</th>
              <th className="px-2 py-2.5 text-right font-semibold">Requested</th>
              <th className="px-2 py-2.5 text-right font-semibold">
                {editable ? "Approve qty" : "Approved"}
              </th>
              <th className="hidden px-2 py-2.5 text-right font-semibold sm:table-cell">Price</th>
              <th className="px-4 py-2.5 text-right font-semibold">Availability</th>
            </tr>
          </thead>
          <tbody>
            {order.lines.map((l) => (
              <tr key={l.id} className="border-t border-outline-variant/40 align-top">
                <td className="px-4 py-2.5">
                  <div className="font-medium text-on-surface">{l.name}</div>
                  <div className="text-[11.5px] text-on-surface-variant">
                    <span className="font-mono">{l.sku}</span>
                    {l.untracked && " · untracked"}
                  </div>
                  {l.badges.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {l.badges.map((b) => (
                        <ReasonBadgeChip key={b.code} b={b} />
                      ))}
                    </div>
                  )}
                </td>
                <td className="px-2 py-2.5 text-right tabular-nums">{fmtQty(l.qty_requested)}</td>
                <td className="px-2 py-2.5 text-right">
                  {editable ? (
                    <QtyInput
                      value={qtys[l.product_id] ?? l.qty_final}
                      onChange={(q) =>
                        setQtys((prev) => ({ ...prev, [l.product_id]: q }))
                      }
                      ariaLabel={`Approved quantity for ${l.name}`}
                    />
                  ) : (
                    <span className="tabular-nums">
                      {l.qty_approved === null ? "—" : fmtQty(l.qty_approved)}
                    </span>
                  )}
                </td>
                <td className="hidden px-2 py-2.5 text-right tabular-nums sm:table-cell">
                  {money(l.unit_price)}
                </td>
                <td className="px-4 py-2.5 text-right">
                  <AvailabilityBadge a={l.availability} />
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t border-outline-variant/60 font-semibold">
              <td className="px-4 py-2.5">Total</td>
              <td className="px-2 py-2.5 text-right tabular-nums">
                {fmtQty(order.lines.reduce((s, l) => s + l.qty_requested, 0))}
              </td>
              <td className="px-2 py-2.5 text-right tabular-nums">
                {fmtQty(order.totals.units)}
              </td>
              <td className="hidden px-2 py-2.5 text-right tabular-nums sm:table-cell">
                {money(order.totals.value)}
              </td>
              <td />
            </tr>
          </tfoot>
        </table>
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
