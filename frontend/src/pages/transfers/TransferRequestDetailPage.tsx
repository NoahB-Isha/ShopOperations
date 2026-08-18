/* One transfer request, live: named after its Odoo picking, one-tap stage
   buttons for the warehouse, the delivery it rode to the floor, and the
   shared timeline. The work happens in Odoo — this page just knows.

   Since the delivery-form rework, a request's own "count" is the exception
   (the direct path, straight to floor staging). Normally its stock is
   consolidated in Staging 2, rides a pallet the warehouse declares, and
   closes when that pallet lands — so the Odoo card shows the delivery, and
   any line the warehouse couldn't fill carries their reason for it. */
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTransferAction, useTransferRequest } from "../../api/hooks";
import type {
  DeliveryRefOut,
  OdooRefOut,
  TransferEventOut,
  TransferLineOut,
  TransferRequestOut,
} from "../../api/types";
import {
  Button,
  Card,
  ContextMenu,
  Dialog,
  Input,
  PageHeader,
  Spinner,
  isInteractiveTarget,
  useContextMenu,
  useRowSelection,
  useToast,
} from "../../design";
import { useAuth } from "../../auth/AuthContext";
import {
  OdooLink,
  TransferStepper,
  WriteStatusChip,
  fmtQty,
  fmtWhen,
  productCode,
} from "../shared/OpsBits";

export function TransferRequestDetailPage() {
  const { id } = useParams();
  const requestId = Number(id);
  const { data: req, isLoading } = useTransferRequest(Number.isFinite(requestId) ? requestId : null);

  if (isLoading || !req) {
    return (
      <div className="grid place-items-center py-24">
        <Spinner size={24} />
      </div>
    );
  }
  return <Detail req={req} />;
}

function Detail({ req }: { req: TransferRequestOut }) {
  const navigate = useNavigate();
  const toast = useToast();

  const ack = useTransferAction("ack");
  const sent = useTransferAction("sent");
  const prepareCount = useTransferAction("prepare-count");
  const markDone = useTransferAction("mark-done");
  const cancel = useTransferAction("cancel");
  const addNote = useTransferAction("note");

  const [confirmCancel, setConfirmCancel] = useState(false);
  const [note, setNote] = useState("");

  // shift/cmd-click rows, then right-click: spin the selection into a fresh
  // request (floor + admin — the /new route is theirs)
  const { roles } = useAuth();
  const canCreateTransfer = roles.has("shoppe_floor") || roles.has("admin");
  const selection = useRowSelection(req.lines.map((l) => l.id));
  const menu = useContextMenu();
  const lineMenu = (lineId: number, e: React.MouseEvent) => {
    if (!canCreateTransfer) return;
    const ids = selection.forContext(lineId);
    menu.open(e, [
      {
        label: `New transfer with ${ids.size} item${ids.size === 1 ? "" : "s"}`,
        onSelect: () =>
          navigate("/transfer-requests/new", {
            state: {
              prefill: {
                notes: `Follow-up to ${req.display_name}`,
                lines: req.lines
                  .filter((l) => ids.has(l.id))
                  .map((l) => ({
                    product_id: l.product_id,
                    sku: l.sku,
                    barcode: l.barcode,
                    name: l.name,
                    category: l.category,
                    qty: l.qty_requested,
                    floor_qty: l.floor_qty,
                    bwhse_qty: l.bwhse_qty,
                    case_size: 1,
                  })),
              },
            },
          }),
      },
    ]);
  };

  const a = req.actions;
  const onError = (e: Error) => toast.error(e.message);
  // a request that closed against a delivery has no per-line count of its own
  // — the pallet was counted, not this request. Don't print columns of zeros.
  const showCounted = req.lines.some((l) => l.qty_counted !== null);

  return (
    <>
      <PageHeader
        title={req.display_name}
        subtitle={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>
              by <b className="text-on-surface">{req.created_by}</b> · {fmtWhen(req.created_at)}
            </span>
            {req.notes && <span className="italic">“{req.notes}”</span>}
          </span>
        }
        actions={
          <>
            <Button variant="ghost" onClick={() => navigate(-1)}>
              Back
            </Button>
            {a.can_cancel && (
              <Button variant="ghost" className="text-error" onClick={() => setConfirmCancel(true)}>
                Cancel request
              </Button>
            )}
          </>
        }
      />

      <div className="mb-6 overflow-x-auto">
        <TransferStepper status={req.status} />
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_360px]">
        <div className="flex flex-col gap-5">
          {/* ---- the one action that matters right now ---- */}
          {a.can_ack && (
            <ActionCard
              title="New request"
              hint="Tell the floor you've laid eyes on it. Touching the picking in Odoo does this on its own — this is for when you haven't yet."
              button="I've seen it"
              loading={ack.isPending}
              onClick={() => ack.mutate({ id: req.id }, { onError })}
            />
          )}
          {a.can_mark_sent && (
            <ActionCard
              title={req.status === "requested" ? "Grab and go" : "Finishing up?"}
              hint={
                req.placement.status === "created"
                  ? `Adjust quantities in ${req.placement.picking_name} as you pick — validating it in Odoo marks this staged on its own, and quantities are read back either way.`
                  : "Tap Staged when the stock is out of the warehouse — quantities are taken from the request."
              }
              button="Mark staged"
              loading={sent.isPending}
              onClick={() => sent.mutate({ id: req.id }, { onError })}
            />
          )}
          {req.status === "counting" && req.count.status === "created" && (
            <Card tone="primary">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="text-[16px] font-semibold">Count it in Odoo</div>
                  <p className="mt-1 text-sm opacity-90">
                    {req.count.picking_name} is marked To Do with availability checked. Scan it
                    in the barcode app — this page closes itself when the transfer is validated.
                  </p>
                </div>
                <Button
                  variant="elevated"
                  onClick={() => window.open(req.count.barcode_url || req.count.url, "_blank")}
                >
                  Open barcode count ↗
                </Button>
              </div>
            </Card>
          )}
          {a.can_prepare_count && (
            <ActionCard
              title={req.count.status === "failed" ? "Count transfer failed" : "Prepare the count"}
              hint={
                req.count.status === "failed"
                  ? `${req.count.error} — retry when Odoo is happy again.`
                  : "Duplicate the picking STAGING→FLOOR, mark it To Do, and check availability."
              }
              button={req.count.status === "failed" ? "Retry" : "Prepare count transfer"}
              loading={prepareCount.isPending}
              onClick={() => prepareCount.mutate({ id: req.id }, { onError })}
            />
          )}
          {a.can_mark_done && (
            <ActionCard
              title="Close it out"
              hint="No live count transfer exists (writes gated or it failed), so confirm by hand — counted is taken as sent."
              button="Mark done"
              loading={markDone.isPending}
              onClick={() => markDone.mutate({ id: req.id }, { onError })}
            />
          )}

          {/* ---- what actually arrived ---- */}
          {showCounted && <ReceiptSummary lines={req.lines} />}

          {/* ---- lines ---- */}
          <Card pad={false}>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-surface-container text-left">
                    <th className="label-m px-4 py-3">Item</th>
                    <th className="label-m px-3 py-3 text-right">Requested</th>
                    <th className="label-m px-3 py-3 text-right">Sent</th>
                    {showCounted && <th className="label-m px-3 py-3 text-right">Counted</th>}
                    {showCounted && <th className="label-m px-3 py-3 text-right">Δ</th>}
                  </tr>
                </thead>
                <tbody>
                  {req.lines.map((line) => (
                    <tr
                      key={line.id}
                      aria-selected={selection.selected.has(line.id)}
                      onMouseDown={(e) => e.shiftKey && e.preventDefault()}
                      onClick={(e) => {
                        if (!isInteractiveTarget(e)) selection.click(line.id, e);
                      }}
                      onContextMenu={(e) => lineMenu(line.id, e)}
                      className={`border-b border-outline-variant/50 transition-colors last:border-0
                        ${selection.selected.has(line.id) ? "bg-secondary-container/40" : ""}`}
                    >
                      <td className="px-4 py-2.5">
                        <div className="font-medium">{line.name}</div>
                        <div className="flex items-center gap-2 font-mono text-[11.5px] text-on-surface-variant">
                          {productCode(line.barcode, line.sku)}
                          <span className="font-sans tabular-nums">
                            floor {fmtQty(line.floor_qty)} · whse {fmtQty(line.bwhse_qty)}
                          </span>
                        </div>
                        {/* the warehouse's own words for a line that didn't
                            come in full — the whole point of the form */}
                        {line.reason_labels.length > 0 && (
                          <div className="mt-1 flex flex-wrap items-center gap-1.5">
                            {line.reason_labels.map((label) => (
                              <span
                                key={label}
                                className="rounded-full bg-warn-container px-2 py-0.5 text-[11.5px]
                                  font-semibold text-on-surface"
                              >
                                {label}
                              </span>
                            ))}
                            {line.reason_note && (
                              <span className="text-[11.5px] text-on-surface-variant">
                                {line.reason_note}
                              </span>
                            )}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums">
                        {fmtQty(line.qty_requested)}
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums">
                        {fmtQty(line.qty_sent)}
                      </td>
                      {showCounted && (
                        <td className="px-3 py-2.5 text-right tabular-nums">
                          {fmtQty(line.qty_counted)}
                        </td>
                      )}
                      {showCounted && (
                        <td className="px-3 py-2.5 text-right font-semibold tabular-nums">
                          {line.delta === null || line.delta === 0 ? (
                            <span className="text-on-surface-variant">—</span>
                          ) : (
                            <span className={line.delta < 0 ? "text-error" : "text-warn"}>
                              {line.delta > 0 ? "+" : ""}
                              {fmtQty(line.delta)}
                            </span>
                          )}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {a.can_edit_lines && (
              // Honest wording: PUT /transfer-requests/{id}/lines exists and is
              // tested, but nothing in the UI calls it — this used to say the
              // lines were "still editable from the request form", pointing at a
              // button that has never existed. Cancel and re-raise is the real
              // path until someone wires the editor.
              <div className="border-t border-outline-variant/60 px-4 py-2.5 text-[13px] text-on-surface-variant">
                No live Odoo draft exists yet, so this request can still be cancelled and raised
                again if the lines are wrong.
              </div>
            )}
          </Card>

          {req.delivery && <DeliveryCard delivery={req.delivery} status={req.status} />}
          <OdooCard placement={req.placement} count={req.count} />
          <ContextMenu menu={menu.menu} onClose={menu.close} />
        </div>

        {/* ---- timeline ---- */}
        <Card pad={false} className="self-start">
          <div className="border-b border-outline-variant/60 px-5 py-3.5">
            <h3 className="headline text-[16px]">Timeline</h3>
          </div>
          <ol className="flex flex-col gap-0 px-5 py-4">
            {req.events.map((event, i) => (
              <TimelineItem key={event.id} event={event} last={i === req.events.length - 1} />
            ))}
          </ol>
          <div className="flex gap-2 border-t border-outline-variant/60 px-4 py-3">
            <Input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Add a note both sides see…"
              className="flex-1"
              onKeyDown={(e) => {
                if (e.key === "Enter" && note.trim()) {
                  addNote.mutate({ id: req.id, note: note.trim() }, { onError });
                  setNote("");
                }
              }}
            />
            <Button
              size="sm"
              variant="secondary"
              disabled={!note.trim()}
              loading={addNote.isPending}
              onClick={() => {
                addNote.mutate({ id: req.id, note: note.trim() }, { onError });
                setNote("");
              }}
            >
              Post
            </Button>
          </div>
        </Card>
      </div>

      <Dialog
        open={confirmCancel}
        onClose={() => setConfirmCancel(false)}
        title="Cancel this request?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmCancel(false)}>
              Keep it
            </Button>
            <Button
              variant="danger"
              loading={cancel.isPending}
              onClick={() =>
                cancel.mutate(
                  { id: req.id },
                  { onSuccess: () => setConfirmCancel(false), onError },
                )
              }
            >
              Cancel request
            </Button>
          </>
        }
      >
        <p className="text-sm leading-6 text-on-surface-variant">
          {req.placement.status === "created"
            ? `The Odoo draft ${req.placement.picking_name} is removed too (drafts move no stock).`
            : "The request stays in history as cancelled; nothing moves."}
        </p>
      </Dialog>
    </>
  );
}

function ActionCard({
  title,
  hint,
  button,
  loading,
  onClick,
}: {
  title: string;
  hint: string;
  button: string;
  loading: boolean;
  onClick: () => void;
}) {
  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[15px] font-semibold">{title}</div>
          <div className="text-[13px] text-on-surface-variant">{hint}</div>
        </div>
        <Button loading={loading} onClick={onClick}>
          {button}
        </Button>
      </div>
    </Card>
  );
}

/** The received transfer this request rode to the floor — "a link to the
 *  received transfer", plus who else was on it, because a pallet that arrived
 *  short is easier to understand when you can see it was shared. */
function DeliveryCard({
  delivery,
  status,
}: {
  delivery: DeliveryRefOut;
  status: TransferRequestOut["status"];
}) {
  const landed = delivery.validated_at !== null;
  const others = delivery.request_count - 1;
  return (
    <Card tone={status === "done" ? "primary" : undefined}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[15px] font-semibold">
            {landed ? "Received on the floor" : "On its way to the floor"}
          </div>
          <p className={`mt-0.5 text-[13px] ${status === "done" ? "opacity-90" : "text-on-surface-variant"}`}>
            <span className="font-mono">{delivery.picking_name}</span>
            {others > 0 && ` · shared with ${others} other request${others === 1 ? "" : "s"}`}
            {landed
              ? " · the floor counts the whole pallet in Odoo"
              : " · closes here the moment the warehouse validates it in Odoo"}
          </p>
        </div>
        {delivery.picking_url && (
          <a
            href={delivery.picking_url}
            target="_blank"
            rel="noreferrer"
            className="state-layer shrink-0 rounded-full px-3 py-1.5 text-[13px] font-semibold underline"
          >
            Open in Odoo ↗
          </a>
        )}
      </div>
    </Card>
  );
}

function OdooCard({ placement, count }: { placement: OdooRefOut; count: OdooRefOut }) {
  if (placement.status === "none" && count.status === "none") return null;
  return (
    <Card>
      <h3 className="headline mb-1 text-[16px]">In Odoo</h3>
      <div className="flex flex-col gap-2.5">
        <OdooRow label="Warehouse picking (BWHSE → Staging 2)" r={placement} />
        {count.status !== "none" && (
          <OdooRow label="Count transfer (Staging → Floor)" r={count} />
        )}
      </div>
    </Card>
  );
}

function OdooRow({ label, r }: { label: string; r: OdooRefOut }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="min-w-0">
        <div className="text-sm font-medium">{label}</div>
        <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[12.5px]">
          <WriteStatusChip
            status={r.status}
            error={r.error}
            createdLabel={r.picking_name || "created"}
          />
          {r.reference && (
            <span className="font-mono text-on-surface-variant">{r.reference}</span>
          )}
          {r.status === "failed" && <span className="text-error">{r.error}</span>}
        </div>
      </div>
      {r.url && r.status === "created" && <OdooLink url={r.url} name={r.picking_name} />}
    </div>
  );
}

const EVENT_ICON: Record<TransferEventOut["kind"], string> = {
  status: "→",
  note: "✎",
  lines_edited: "✚",
  odoo: "⇄",
  discrepancy: "!",
};

function TimelineItem({ event, last }: { event: TransferEventOut; last: boolean }) {
  const tone =
    event.kind === "discrepancy"
      ? "bg-error-container text-on-error-container"
      : event.kind === "odoo"
        ? "bg-tertiary-container text-on-tertiary-container"
        : "bg-secondary-container text-on-secondary-container";
  return (
    <li className="relative flex gap-3 pb-4 last:pb-0">
      {!last && (
        <span
          aria-hidden
          className="absolute top-7 left-[13px] h-[calc(100%-1.5rem)] w-0.5 rounded bg-outline-variant/70"
        />
      )}
      <span
        aria-hidden
        className={`z-10 grid h-7 w-7 shrink-0 place-items-center rounded-full text-[12px] font-bold ${tone}`}
      >
        {EVENT_ICON[event.kind]}
      </span>
      <div className="min-w-0 pt-0.5">
        <div className="text-[13.5px] leading-5">
          <span className="font-semibold">{event.actor}</span>{" "}
          <span className="text-on-surface-variant">{event.note}</span>
        </div>
        <div className="text-[11.5px] text-on-surface-variant/80">{fmtWhen(event.created_at)}</div>
      </div>
    </li>
  );
}

/** What actually landed on the floor, once the receipt is matched.
 *
 *  The per-line Δ column already colours short vs over, but it only appears on
 *  a finished request and you have to read every row to find the two that
 *  moved. This says it up front, in the same two colours: short is an error
 *  (stock the floor expected and doesn't have), over is a warning (real stock
 *  that arrived unannounced) — neither is neutral, and they aren't the same
 *  problem, so they don't get the same colour. */
function ReceiptSummary({ lines }: { lines: TransferLineOut[] }) {
  const short = lines.filter((l) => (l.delta ?? 0) < 0);
  const over = lines.filter((l) => (l.delta ?? 0) > 0);
  const counted = lines.filter((l) => l.qty_counted !== null);
  if (counted.length === 0) return null;

  if (short.length === 0 && over.length === 0) {
    return (
      <Card className="flex items-center gap-2.5 border-l-4 border-l-success">
        <span className="text-[15px] font-medium">Everything matched</span>
        <span className="text-[13px] text-on-surface-variant">
          all {counted.length} item{counted.length === 1 ? "" : "s"} arrived as sent
        </span>
      </Card>
    );
  }

  const list = (rows: TransferLineOut[]) =>
    rows
      .map((l) => `${l.name} (${l.delta! > 0 ? "+" : ""}${fmtQty(l.delta)})`)
      .join(", ");

  return (
    <Card className="flex flex-col gap-3 border-l-4 border-l-warn">
      <span className="text-[15px] font-medium">
        {short.length + over.length} of {counted.length} items didn't match what was sent
      </span>
      {short.length > 0 && (
        <div className="flex flex-col gap-1">
          <span className="text-[13px] font-semibold text-error">
            {short.length} short — less arrived than was sent
          </span>
          <span className="text-[13px] text-on-surface-variant">{list(short)}</span>
        </div>
      )}
      {over.length > 0 && (
        <div className="flex flex-col gap-1">
          <span className="text-[13px] font-semibold text-warn">
            {over.length} extra — more arrived than was sent
          </span>
          <span className="text-[13px] text-on-surface-variant">{list(over)}</span>
        </div>
      )}
      <span className="text-[12.5px] leading-5 text-on-surface-variant">
        Both directions are filed in the adjustments queue for the warehouse to reconcile.
        Staging holds whatever the counts didn't account for.
      </span>
    </Card>
  );
}
