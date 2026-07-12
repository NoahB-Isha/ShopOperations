/* One transfer request: lines with requested/sent/counted quantities, the
   shared timeline, role-appropriate actions for each stage, and optional
   draft Odoo transfers for each physical leg. */
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  useCountTransfer,
  useFulfillTransfer,
  useOdooDraft,
  useTransferAction,
  useTransferRequest,
} from "../../api/hooks";
import type { TransferEventOut, TransferRequestOut } from "../../api/types";
import {
  Badge,
  Button,
  Card,
  Dialog,
  Input,
  PageHeader,
  Spinner,
  useToast,
} from "../../design";
import {
  OdooLink,
  TransferStepper,
  WriteStatusChip,
  fmtQty,
  fmtWhen,
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

  const fulfill = useFulfillTransfer();
  const count = useCountTransfer();
  const stage = useTransferAction("stage");
  const complete = useTransferAction("complete");
  const cancel = useTransferAction("cancel");
  const addNote = useTransferAction("note");
  const odooDraft = useOdooDraft();

  const [sent, setSent] = useState<Record<number, number>>({});
  const [counted, setCounted] = useState<Record<number, number>>({});
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [note, setNote] = useState("");

  // re-seed editable quantities whenever the stage changes
  useEffect(() => {
    setSent(Object.fromEntries(req.lines.map((l) => [l.id, l.qty_sent ?? l.qty_requested])));
    setCounted(Object.fromEntries(req.lines.map((l) => [l.id, l.qty_counted ?? l.qty_sent ?? 0])));
  }, [req.id, req.status, req.lines]);

  const a = req.actions;
  const editingSent = a.can_fulfill;
  const editingCounted = a.can_count;
  // warehouse edits Sent while the request is still 'requested'
  const showSent = req.status !== "requested" || editingSent;
  const showCounted = ["counted", "on_floor"].includes(req.status) || editingCounted;
  const discrepancies = req.lines.filter((l) => (l.delta ?? 0) !== 0);

  const onError = (e: Error) => toast.error(e.message);

  return (
    <>
      <PageHeader
        title={`Request #${req.id}`}
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

      {req.status === "counted" && discrepancies.length > 0 && (
        <Card tone="secondary" className="mb-5">
          <div className="text-xs font-bold tracking-wide uppercase opacity-75">
            Count didn't match
          </div>
          <p className="mt-1 text-sm">
            {discrepancies.length} line{discrepancies.length === 1 ? "" : "s"} differ from what the
            warehouse sent — each is now in the warehouse's{" "}
            <b>adjustments queue</b> instead of vanishing into chat.
          </p>
        </Card>
      )}

      <div className="grid gap-5 lg:grid-cols-[1fr_360px]">
        <div className="flex flex-col gap-5">
          <Card pad={false}>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-surface-container text-left">
                    <th className="label-m px-4 py-3">Item</th>
                    <th className="label-m px-3 py-3 text-right">Requested</th>
                    {showSent && <th className="label-m px-3 py-3 text-right">Sent</th>}
                    {showCounted && <th className="label-m px-3 py-3 text-right">Counted</th>}
                    {showCounted && !editingCounted && (
                      <th className="label-m px-3 py-3 text-right">Δ</th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {req.lines.map((line) => (
                    <tr key={line.id} className="border-b border-outline-variant/50 last:border-0">
                      <td className="px-4 py-2.5">
                        <div className="font-medium">{line.name}</div>
                        <div className="flex items-center gap-2 font-mono text-[11.5px] text-on-surface-variant">
                          {line.sku}
                          <span className="font-sans tabular-nums">
                            floor {fmtQty(line.floor_qty)} · whse {fmtQty(line.bwhse_qty)}
                          </span>
                        </div>
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums">
                        {fmtQty(line.qty_requested)}
                      </td>
                      {showSent && (
                        <td className="px-3 py-2.5 text-right tabular-nums">
                          {editingSent ? (
                            <QtyCell
                              value={sent[line.id] ?? 0}
                              onChange={(v) => setSent({ ...sent, [line.id]: v })}
                              label={`Sent quantity for ${line.name}`}
                            />
                          ) : (
                            fmtQty(line.qty_sent)
                          )}
                        </td>
                      )}
                      {showCounted && (
                        <td className="px-3 py-2.5 text-right tabular-nums">
                          {editingCounted ? (
                            <QtyCell
                              value={counted[line.id] ?? 0}
                              onChange={(v) => setCounted({ ...counted, [line.id]: v })}
                              label={`Counted quantity for ${line.name}`}
                            />
                          ) : (
                            fmtQty(line.qty_counted)
                          )}
                        </td>
                      )}
                      {showCounted && !editingCounted && (
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
          </Card>

          {/* stage actions */}
          {(a.can_fulfill || a.can_stage || a.can_count || a.can_complete) && (
            <Card>
              {a.can_fulfill && (
                <ActionRow
                  title="Pick the stock"
                  hint="Adjust “Sent” to what's actually going on the cart, then mark it picked."
                  button="Mark as picked"
                  loading={fulfill.isPending}
                  onClick={() =>
                    fulfill.mutate(
                      {
                        id: req.id,
                        lines: req.lines.map((l) => ({
                          line_id: l.id,
                          qty_sent: sent[l.id] ?? l.qty_requested,
                        })),
                      },
                      { onError },
                    )
                  }
                />
              )}
              {a.can_stage && (
                <ActionRow
                  title="Deliver to staging"
                  hint="The cart physically arrived at III-FLOOR-STAGING."
                  button="It's in staging"
                  loading={stage.isPending}
                  onClick={() => stage.mutate({ id: req.id }, { onError })}
                />
              )}
              {a.can_count && (
                <ActionRow
                  title="Count the staged stock"
                  hint="Enter what actually arrived — mismatches go straight to the warehouse's adjustments queue."
                  button="Submit count"
                  loading={count.isPending}
                  onClick={() =>
                    count.mutate(
                      {
                        id: req.id,
                        lines: req.lines
                          .filter((l) => (l.qty_sent ?? 0) > 0)
                          .map((l) => ({
                            line_id: l.id,
                            qty_counted: counted[l.id] ?? 0,
                          })),
                      },
                      { onError },
                    )
                  }
                />
              )}
              {a.can_complete && (
                <ActionRow
                  title="Shelve it"
                  hint="Counted stock has been moved out to the floor."
                  button="Everything's on the floor"
                  loading={complete.isPending}
                  onClick={() => complete.mutate({ id: req.id }, { onError })}
                />
              )}
            </Card>
          )}

          <OdooDraftsCard req={req} onCreate={(leg) => odooDraft.mutate({ id: req.id, leg }, { onError })} creating={odooDraft.isPending} />
        </div>

        {/* timeline */}
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
                  {
                    onSuccess: () => setConfirmCancel(false),
                    onError,
                  },
                )
              }
            >
              Cancel request
            </Button>
          </>
        }
      >
        <p className="text-sm leading-6 text-on-surface-variant">
          The request stays in history as cancelled; nothing moves.
        </p>
      </Dialog>
    </>
  );
}

function QtyCell({
  value,
  onChange,
  label,
}: {
  value: number;
  onChange: (v: number) => void;
  label: string;
}) {
  return (
    <Input
      inputMode="numeric"
      aria-label={label}
      className="!h-9 w-20 text-right tabular-nums"
      value={String(value)}
      onChange={(e) => {
        const n = Number(e.target.value.replace(/[^0-9.]/g, ""));
        onChange(Number.isFinite(n) ? n : 0);
      }}
    />
  );
}

function ActionRow({
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
    <div className="flex flex-wrap items-center justify-between gap-3 py-1.5">
      <div>
        <div className="text-[15px] font-semibold">{title}</div>
        <div className="text-[13px] text-on-surface-variant">{hint}</div>
      </div>
      <Button loading={loading} onClick={onClick}>
        {button}
      </Button>
    </div>
  );
}

const EVENT_ICON: Record<TransferEventOut["kind"], string> = {
  status: "→",
  note: "✎",
  lines_edited: "✚",
  odoo_draft: "⇄",
  discrepancy: "!",
};

function TimelineItem({ event, last }: { event: TransferEventOut; last: boolean }) {
  const tone =
    event.kind === "discrepancy"
      ? "bg-error-container text-on-error-container"
      : event.kind === "odoo_draft"
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

const LEG_LABEL: Record<string, string> = {
  bwhse_staging: "BWHSE → Staging (sent quantities)",
  staging_floor: "Staging → Floor (counted quantities)",
};

function OdooDraftsCard({
  req,
  onCreate,
  creating,
}: {
  req: TransferRequestOut;
  onCreate: (leg: string) => void;
  creating: boolean;
}) {
  const latestByLeg = new Map(req.odoo_drafts.map((d) => [d.leg, d]));
  const anyAvailable = req.actions.odoo_legs.length > 0 || req.odoo_drafts.length > 0;
  if (!anyAvailable) return null;

  return (
    <Card>
      <h3 className="headline mb-1 text-[16px]">Odoo drafts</h3>
      <p className="mb-3 text-[13px] text-on-surface-variant">
        Each physical leg can be logged as a <b>draft</b> internal transfer — a human still
        validates it in Odoo. Honest outcomes only: created, simulated, or failed.
      </p>
      <div className="flex flex-col gap-3">
        {(["bwhse_staging", "staging_floor"] as const).map((leg) => {
          const latest = latestByLeg.get(leg);
          const allowed = req.actions.odoo_legs.includes(leg);
          if (!latest && !allowed) return null;
          return (
            <div key={leg} className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="text-sm font-medium">{LEG_LABEL[leg]}</div>
                {latest && (
                  <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[12.5px]">
                    <WriteStatusChip
                      status={latest.status}
                      dryRunReason={latest.dry_run_reason}
                      error={latest.error}
                    />
                    <span className="font-mono text-on-surface-variant">{latest.reference}</span>
                    {latest.odoo_url && <OdooLink url={latest.odoo_url} name="draft" />}
                    {latest.status === "failed" && (
                      <span className="text-error">{latest.error}</span>
                    )}
                  </div>
                )}
              </div>
              {allowed && (
                <Button
                  size="sm"
                  variant={latest?.status === "created" ? "outlined" : "secondary"}
                  loading={creating}
                  onClick={() => onCreate(leg)}
                >
                  {latest ? (latest.status === "failed" ? "Retry draft" : "Re-render") : "Create draft"}
                </Button>
              )}
            </div>
          );
        })}
        {req.actions.odoo_legs.length === 0 && req.odoo_drafts.length === 0 && (
          <Badge tone="outline">available once the request is picked</Badge>
        )}
      </div>
    </Card>
  );
}
