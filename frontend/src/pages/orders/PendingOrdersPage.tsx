/* Zone coordinator: order lists waiting on your approval. Approving creates
   a DRAFT internal transfer in Odoo — the outcome (created / simulated /
   failed) is shown honestly, with the deep link when a record exists. */
import { useState } from "react";
import {
  useApproveOrderList,
  useOrderList,
  useOrderLists,
  useReturnOrderList,
} from "../../api/hooks";
import type { OrderListOut, OrderListSummaryOut } from "../../api/types";
import {
  Badge,
  Button,
  Card,
  Dialog,
  EmptyState,
  Field,
  PageHeader,
  Spinner,
  Textarea,
  useToast,
} from "../../design";
import { OdooLink, WriteStatusChip, fmtQty, fmtWhen } from "../shared/OpsBits";

export function PendingOrdersPage() {
  const pending = useOrderLists("pending_approval");
  const recent = useOrderLists("approved,returned");
  const [openId, setOpenId] = useState<number | null>(null);

  return (
    <>
      <PageHeader
        title="Pending orders"
        subtitle="Order lists the office assigned to your zone. Approving creates a draft transfer in Odoo for the warehouse to validate."
      />

      {pending.isLoading ? (
        <div className="grid place-items-center py-24">
          <Spinner size={24} />
        </div>
      ) : (pending.data ?? []).length === 0 ? (
        <EmptyState
          title="Nothing waiting on you"
          hint="When the office assigns an order list to your zone, it lands here."
        />
      ) : (
        <div className="stagger-children grid gap-3 md:grid-cols-2">
          {(pending.data ?? []).map((ol) => (
            <PendingCard key={ol.id} summary={ol} onOpen={() => setOpenId(ol.id)} />
          ))}
        </div>
      )}

      {(recent.data ?? []).length > 0 && (
        <>
          <h2 className="headline mt-10 mb-3">Recently decided</h2>
          <div className="flex flex-col gap-2">
            {(recent.data ?? []).slice(0, 8).map((ol) => (
              <button
                key={ol.id}
                onClick={() => setOpenId(ol.id)}
                className="state-layer flex items-center justify-between gap-3 rounded-(--radius-md)
                  bg-surface-container-low px-4 py-3 text-left"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{ol.name}</span>
                  <span className="text-[12px] text-on-surface-variant">
                    {ol.center_name} · {fmtWhen(ol.updated_at)}
                  </span>
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  {ol.status === "returned" ? (
                    <Badge tone="danger">returned</Badge>
                  ) : (
                    <WriteStatusChip status={ol.write_status} />
                  )}
                </span>
              </button>
            ))}
          </div>
        </>
      )}

      <ReviewDialog id={openId} onClose={() => setOpenId(null)} />
    </>
  );
}

function PendingCard({
  summary,
  onOpen,
}: {
  summary: OrderListSummaryOut;
  onOpen: () => void;
}) {
  return (
    <Card className="flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[16px] font-semibold">{summary.name}</div>
          <div className="mt-0.5 text-[13px] text-on-surface-variant">
            → {summary.center_name} · {summary.line_count} item
            {summary.line_count === 1 ? "" : "s"} · {fmtQty(summary.total_qty)} units
          </div>
        </div>
        <Badge tone="gold">pending</Badge>
      </div>
      {!summary.center_mapped && (
        <div className="rounded-(--radius-sm) bg-warn-container px-3 py-2 text-[12.5px] text-on-warn-container">
          {summary.center_name} has no Odoo location mapped — approval can't write live yet.
        </div>
      )}
      <div className="mt-1">
        <Button size="sm" onClick={onOpen}>
          Review
        </Button>
      </div>
    </Card>
  );
}

function ReviewDialog({ id, onClose }: { id: number | null; onClose: () => void }) {
  const { data: ol } = useOrderList(id);
  const approve = useApproveOrderList();
  const sendBack = useReturnOrderList();
  const toast = useToast();
  const [returning, setReturning] = useState(false);
  const [note, setNote] = useState("");

  const close = () => {
    setReturning(false);
    setNote("");
    onClose();
  };

  if (id === null) return null;

  return (
    <Dialog open onClose={close} title={ol?.name ?? "Order list"} wide>
      {!ol ? (
        <div className="grid place-items-center py-16">
          <Spinner size={22} />
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2 text-sm text-on-surface-variant">
            <span>
              → <b className="text-on-surface">{ol.center_name}</b> ({ol.zone_name})
            </span>
            <span>· {fmtQty(ol.total_qty)} units</span>
            {ol.assigned_at && <span>· assigned {fmtWhen(ol.assigned_at)}</span>}
            <WriteStatusChip
              status={ol.write_status}
              dryRunReason={ol.write_dry_run_reason}
              error={ol.write_error}
            />
          </div>

          {ol.notes && (
            <p className="rounded-(--radius-md) bg-surface-container px-3.5 py-2.5 text-sm whitespace-pre-wrap">
              {ol.notes}
            </p>
          )}

          <div className="overflow-hidden rounded-(--radius-md) bg-surface-container-low">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-surface-container text-left">
                  <th className="label-m px-3.5 py-2.5">Item</th>
                  <th className="label-m px-3.5 py-2.5 text-right">Qty</th>
                  <th className="label-m hidden px-3.5 py-2.5 text-right sm:table-cell">
                    Warehouse
                  </th>
                </tr>
              </thead>
              <tbody>
                {ol.lines.map((line) => (
                  <tr key={line.id} className="border-b border-outline-variant/50 last:border-0">
                    <td className="px-3.5 py-2">
                      <div className="font-medium">{line.name}</div>
                      <div className="font-mono text-[11.5px] text-on-surface-variant">
                        {line.sku}
                      </div>
                    </td>
                    <td className="px-3.5 py-2 text-right font-semibold tabular-nums">
                      {fmtQty(line.qty)}
                    </td>
                    <td className="hidden px-3.5 py-2 text-right tabular-nums text-on-surface-variant sm:table-cell">
                      {fmtQty(line.bwhse_qty)}
                      {line.qty > line.bwhse_qty && (
                        <span className="ml-1.5 align-middle">
                          <Badge tone="gold" title="More than the warehouse shows on hand.">
                            short
                          </Badge>
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {ol.status === "approved" && (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-(--radius-md) bg-surface-container px-3.5 py-3">
              <div className="text-sm">
                Approved{ol.approved_at ? ` ${fmtWhen(ol.approved_at)}` : ""} —{" "}
                <span className="font-mono text-[12.5px]">{ol.write_reference}</span>
                {ol.write_status === "simulated" && (
                  <span className="block text-[13px] text-on-surface-variant">
                    Writes are gated right now ({ol.write_dry_run_reason.replace("_", " ")}) — no
                    Odoo record was created.
                  </span>
                )}
              </div>
              <OdooLink url={ol.odoo_url} name={ol.odoo_picking_name || "the draft"} />
            </div>
          )}
          {ol.write_status === "failed" && (
            <div className="rounded-(--radius-md) bg-error-container px-3.5 py-3 text-sm text-on-error-container">
              The Odoo write failed: {ol.write_error} — approving again retries safely (same
              reference, no duplicates).
            </div>
          )}

          {returning && (
            <Field label="Why are you sending it back?" help="The office sees this note.">
              <Textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} autoFocus />
            </Field>
          )}

          {ol.status === "pending_approval" && (
            <div className="flex flex-wrap justify-end gap-2">
              {returning ? (
                <>
                  <Button variant="ghost" onClick={() => setReturning(false)}>
                    Never mind
                  </Button>
                  <Button
                    variant="danger"
                    disabled={note.trim().length < 3}
                    loading={sendBack.isPending}
                    onClick={() =>
                      sendBack.mutate(
                        { id: ol.id, note: note.trim() },
                        {
                          onSuccess: () => {
                            toast.info("Sent back to the office.");
                            close();
                          },
                          onError: (e) => toast.error(e.message),
                        },
                      )
                    }
                  >
                    Send back
                  </Button>
                </>
              ) : (
                <>
                  <Button variant="outlined" onClick={() => setReturning(true)}>
                    Send back…
                  </Button>
                  <Button
                    loading={approve.isPending}
                    onClick={() =>
                      approve.mutate(
                        { id: ol.id },
                        {
                          onSuccess: (res) => {
                            const out = res as OrderListOut;
                            if (out.write_status === "created") {
                              toast.success("Approved — draft transfer created in Odoo.");
                            } else if (out.write_status === "simulated") {
                              toast.info("Approved — simulated (writes are gated).");
                            } else {
                              toast.error("Approval ran but the Odoo write failed — see details.");
                            }
                          },
                          onError: (e) => toast.error(e.message),
                        },
                      )
                    }
                  >
                    Approve
                  </Button>
                </>
              )}
            </div>
          )}
        </div>
      )}
    </Dialog>
  );
}
