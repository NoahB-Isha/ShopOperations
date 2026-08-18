/* "The warehouse must fill out a form that details the transfer being sent."

   Three questions, one per step, in the order the warehouse thinks about it:

     1. which transfer did you just send?   (recent staging2 → floor staging,
                                             plus "Don't see it?" to search)
     2. which requests are in it?           (pre-ticked where the evidence is
                                             strong; the rest a click away)
     3. why is anything off by more than N? (item by item, four chips and a
                                             note — nothing else to read)

   The app suggests; the human decides. Nothing here guesses on submit: the
   server recomputes the review from the same rules and refuses the form if a
   gap has no reason, so a stale dialog can't sneak one through. */
import { useEffect, useMemo, useState } from "react";
import {
  useDeclareDelivery,
  useDeliveryCandidates,
  useDeliveryPreview,
} from "../../api/hooks";
import type {
  DeliveryCandidateOut,
  DeliveryPreviewOut,
  DiscrepancyReason,
} from "../../api/types";
import {
  Badge,
  Button,
  Dialog,
  EmptyState,
  Field,
  Input,
  Spinner,
  Textarea,
  useToast,
} from "../../design";
import { TransferStatusChip, fmtQty, productCode } from "../shared/OpsBits";

type Answer = { reasons: DiscrepancyReason[]; note: string };

const STEP_TITLES = [
  "Which transfer are you sending?",
  "What's included in it?",
  "Anything not as asked?",
];

export function DeliveryFormDialog({
  open,
  onClose,
  initialPickingId = null,
}: {
  open: boolean;
  onClose: () => void;
  /** jumping straight in from a "this landed, tell us what was in it" prompt */
  initialPickingId?: number | null;
}) {
  const toast = useToast();
  const [step, setStep] = useState(0);
  const [picking, setPicking] = useState<DeliveryCandidateOut | null>(null);
  const [pickingId, setPickingId] = useState<number | null>(initialPickingId);
  const [searching, setSearching] = useState(false);
  const [term, setTerm] = useState("");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [showAll, setShowAll] = useState(false);
  const [answers, setAnswers] = useState<Record<number, Answer>>({});
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState<DeliveryPreviewOut | null>(null);

  // recent transfers need no term; the "Don't see it?" search waits for one
  const candidates = useDeliveryCandidates(
    searching ? term : "",
    open && (!searching || term.trim().length > 1),
  );
  const runPreview = useDeliveryPreview();
  const declare = useDeclareDelivery();

  // reset every time it opens — a half-filled form from last time is worse
  // than starting over
  useEffect(() => {
    if (!open) return;
    setStep(0);
    setSelected(new Set());
    setAnswers({});
    setNote("");
    setPreview(null);
    setShowAll(false);
    setSearching(false);
    setTerm("");
    setPickingId(initialPickingId);
  }, [open, initialPickingId]);

  // arriving with a picking already chosen (the "needs details" prompt)
  useEffect(() => {
    if (!open || pickingId === null || preview !== null || runPreview.isPending) return;
    void loadPreview(pickingId, []);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, pickingId]);

  const loadPreview = async (id: number, requestIds: number[], nextStep = 1) => {
    try {
      const out = await runPreview.mutateAsync({
        odoo_picking_id: id,
        request_ids: requestIds,
      });
      setPreview(out);
      if (out.picking) setPicking(out.picking);
      if (requestIds.length === 0) {
        // first look: adopt the app's own suggestions as the starting ticks
        setSelected(
          new Set(out.suggestions.filter((s) => s.auto_select).map((s) => s.request_id)),
        );
      }
      setAnswers((prev) => {
        const next = { ...prev };
        for (const row of out.review) {
          if (!next[row.product_id] && (row.reasons.length || row.note)) {
            next[row.product_id] = { reasons: row.reasons, note: row.note };
          }
        }
        return next;
      });
      setStep(nextStep);
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  const review = preview?.review ?? [];
  const unanswered = review.filter((row) => {
    const a = answers[row.product_id];
    return !a || (a.reasons.length === 0 && !a.note.trim());
  });
  const otherNeedsNote = review.some((row) => {
    const a = answers[row.product_id];
    return a?.reasons.includes("other") && !a.note.trim();
  });

  const submit = () => {
    if (pickingId === null) return;
    declare.mutate(
      {
        odoo_picking_id: pickingId,
        request_ids: [...selected],
        reasons: review.map((row) => ({
          product_id: row.product_id,
          reasons: answers[row.product_id]?.reasons ?? [],
          note: answers[row.product_id]?.note ?? "",
        })),
        note,
      },
      {
        onSuccess: (delivery) => {
          const n = delivery.requests.length;
          toast.success(
            delivery.status === "open"
              ? `Logged ${delivery.picking_name} — ${n} request${n === 1 ? "" : "s"} on it. ` +
                  "They close when you validate it in Odoo."
              : `${delivery.picking_name} logged — ${n} request${n === 1 ? "" : "s"} received.`,
          );
          onClose();
        },
        onError: (e) => toast.error(e.message),
      },
    );
  };

  const footer = (
    <div className="flex w-full flex-wrap items-center justify-between gap-2">
      <span className="text-[12.5px] text-on-surface-variant">
        Step {step + 1} of 3
        {picking && step > 0 ? ` · ${picking.name}` : ""}
      </span>
      <div className="flex gap-2">
        {step > 0 && (
          <Button variant="ghost" onClick={() => setStep(step - 1)}>
            Back
          </Button>
        )}
        {step === 1 && (
          <Button
            loading={runPreview.isPending}
            disabled={selected.size === 0 || pickingId === null}
            onClick={() => pickingId !== null && loadPreview(pickingId, [...selected], 2)}
          >
            Next
          </Button>
        )}
        {step === 2 && (
          <Button
            loading={declare.isPending}
            disabled={unanswered.length > 0 || otherNeedsNote}
            onClick={submit}
          >
            {unanswered.length > 0
              ? `${unanswered.length} still need a reason`
              : "Log this transfer"}
          </Button>
        )}
      </div>
    </div>
  );

  return (
    <Dialog open={open} onClose={onClose} title={STEP_TITLES[step]} footer={footer} wide>
      {step === 0 && (
        <div className="flex flex-col gap-3">
          {!searching ? (
            <>
              <p className="text-[13px] text-on-surface-variant">
                Transfers that went from III/Staging2 to floor staging recently.
              </p>
              {candidates.isLoading ? (
                <div className="grid place-items-center py-10">
                  <Spinner size={22} />
                </div>
              ) : (candidates.data?.candidates ?? []).length === 0 ? (
                <EmptyState
                  title="Nothing recent"
                  hint={candidates.data?.note || "Make the transfer in Odoo first, then log it here."}
                />
              ) : (
                <ul className="flex flex-col gap-1.5">
                  {(candidates.data?.candidates ?? []).map((c) => (
                    <CandidateRow
                      key={c.odoo_picking_id}
                      c={c}
                      busy={runPreview.isPending}
                      onPick={() => {
                        setPickingId(c.odoo_picking_id);
                        setPicking(c);
                        void loadPreview(c.odoo_picking_id, []);
                      }}
                    />
                  ))}
                </ul>
              )}
              <button
                className="state-layer self-start rounded-full px-3 py-1.5 text-[13px] font-semibold text-primary"
                onClick={() => setSearching(true)}
              >
                Don't see it?
              </button>
            </>
          ) : (
            <>
              <Field label="Search Odoo by transfer name" help="e.g. III/INT/04712">
                <Input
                  autoFocus
                  value={term}
                  onChange={(e) => setTerm(e.target.value)}
                  aria-label="Search transfers by name"
                />
              </Field>
              {term.length > 1 && candidates.isFetching && (
                <div className="grid place-items-center py-6">
                  <Spinner size={20} />
                </div>
              )}
              <ul className="flex flex-col gap-1.5">
                {(candidates.data?.candidates ?? []).map((c) => (
                  <CandidateRow
                    key={c.odoo_picking_id}
                    c={c}
                    busy={runPreview.isPending}
                    onPick={() => {
                      setPickingId(c.odoo_picking_id);
                      setPicking(c);
                      void loadPreview(c.odoo_picking_id, []);
                    }}
                  />
                ))}
              </ul>
              {term.length > 1 && !candidates.isFetching && candidates.data?.note && (
                <p className="text-[13px] text-on-surface-variant">{candidates.data.note}</p>
              )}
              <button
                className="state-layer self-start rounded-full px-3 py-1.5 text-[13px] font-semibold text-primary"
                onClick={() => setSearching(false)}
              >
                ← Back to recent transfers
              </button>
            </>
          )}
        </div>
      )}

      {step === 1 && preview && (
        <div className="flex flex-col gap-3">
          <p className="text-[13px] text-on-surface-variant">
            {picking?.name} carries <b className="text-on-surface">{picking?.item_count} item(s)</b>{" "}
            · {fmtQty(picking?.total_units ?? 0)} units. Which floor requests are in it?
          </p>
          {preview.suggestions.filter((s) => s.suggested).length === 0 && (
            <p className="rounded-(--radius-md) bg-warn-container px-3 py-2 text-[13px]">
              Nothing matches this pallet automatically — pick the requests it covers below.
            </p>
          )}
          <ul className="flex flex-col gap-1.5">
            {preview.suggestions
              .filter((s) => s.suggested || showAll || selected.has(s.request_id))
              .map((s) => (
                <li key={s.request_id}>
                  <label
                    className={`state-layer flex cursor-pointer items-start gap-3 rounded-(--radius-lg) px-3 py-2.5
                      ${selected.has(s.request_id) ? "bg-secondary-container/50" : "bg-surface-container-low"}`}
                  >
                    <input
                      type="checkbox"
                      className="mt-1 h-4 w-4 accent-[var(--color-primary)]"
                      checked={selected.has(s.request_id)}
                      onChange={(e) => {
                        const next = new Set(selected);
                        if (e.target.checked) next.add(s.request_id);
                        else next.delete(s.request_id);
                        setSelected(next);
                      }}
                      aria-label={`Include ${s.display_name}`}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-[13px] font-semibold">
                          {s.display_name}
                        </span>
                        <TransferStatusChip status={s.status} />
                        <span className="text-[12.5px] text-on-surface-variant">
                          {s.line_count} item{s.line_count === 1 ? "" : "s"} ·{" "}
                          {fmtQty(s.total_requested)} units · {s.created_by}
                        </span>
                      </div>
                      <div className="mt-0.5 text-[12.5px] text-on-surface-variant">
                        {s.reason}
                      </div>
                    </div>
                  </label>
                </li>
              ))}
          </ul>
          {preview.suggestions.some((s) => !s.suggested) && (
            <button
              className="state-layer self-start rounded-full px-3 py-1.5 text-[13px] font-semibold text-primary"
              onClick={() => setShowAll(!showAll)}
            >
              {showAll
                ? "Hide the rest"
                : `Add another transfer… (${preview.suggestions.filter((s) => !s.suggested).length} more open)`}
            </button>
          )}
        </div>
      )}

      {step === 2 && preview && (
        <div className="flex flex-col gap-4">
          {review.length === 0 ? (
            <div className="rounded-(--radius-lg) border-l-4 border-l-success bg-surface-container-low px-4 py-3">
              <div className="text-[15px] font-medium">Everything matches</div>
              <p className="mt-0.5 text-[13px] text-on-surface-variant">
                Nothing is off by more than {fmtQty(preview.threshold)} units, so there's
                nothing to explain.
              </p>
            </div>
          ) : (
            <>
              <p className="text-[13px] text-on-surface-variant">
                These differ from what was asked by more than {fmtQty(preview.threshold)}{" "}
                units. Tell the floor why — they'll see it on their request.
              </p>
              <ul className="flex flex-col gap-2.5">
                {review.map((row) => (
                  <li
                    key={row.product_id}
                    className="rounded-(--radius-lg) bg-surface-container-low px-4 py-3"
                  >
                    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                      <div className="min-w-0">
                        <div className="text-[15px] font-medium">{row.name}</div>
                        <div className="font-mono text-[11.5px] text-on-surface-variant">
                          {productCode(row.barcode, row.sku)}
                        </div>
                      </div>
                      <div className="shrink-0 text-[13.5px] tabular-nums">
                        asked <b>{fmtQty(row.qty_requested)}</b> · sending{" "}
                        <b>{fmtQty(row.qty_sent)}</b>{" "}
                        <span className={row.delta < 0 ? "text-error" : "text-warn"}>
                          ({row.delta > 0 ? "+" : ""}
                          {fmtQty(row.delta)})
                        </span>
                      </div>
                    </div>
                    {row.requested_by.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {row.requested_by.map((name, i) => (
                          <Badge key={`${name}-${i}`} tone="neutral">
                            {name}
                          </Badge>
                        ))}
                      </div>
                    )}
                    <ReasonChips
                      options={preview.reason_options}
                      answer={answers[row.product_id]}
                      onChange={(a) =>
                        setAnswers({ ...answers, [row.product_id]: a })
                      }
                    />
                  </li>
                ))}
              </ul>
            </>
          )}

          {preview.extras.length > 0 && (
            <div className="rounded-(--radius-lg) bg-surface-container-low px-4 py-3">
              <div className="text-[13.5px] font-semibold">
                Also on this pallet — nobody asked for these
              </div>
              <ul className="mt-1 flex flex-col gap-0.5">
                {preview.extras.map((x) => (
                  <li key={x.product_id} className="text-[13px] text-on-surface-variant">
                    {x.name} · {fmtQty(x.qty_sent)} units
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-[12.5px] text-on-surface-variant">
                No reason needed — extra stock going to the floor is a good thing.
              </p>
            </div>
          )}

          <Field label="Anything else about this pallet? (optional)">
            <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
        </div>
      )}

      {step > 0 && !preview && (
        <div className="grid place-items-center py-10">
          <Spinner size={22} />
        </div>
      )}
    </Dialog>
  );
}

function CandidateRow({
  c,
  busy,
  onPick,
}: {
  c: DeliveryCandidateOut;
  busy: boolean;
  onPick: () => void;
}) {
  return (
    <li>
      <button
        disabled={busy}
        onClick={onPick}
        className="state-layer flex w-full items-center justify-between gap-3 rounded-(--radius-lg)
          bg-surface-container-low px-4 py-3 text-left disabled:opacity-60"
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[13.5px] font-semibold">{c.name}</span>
            <Badge tone={c.state === "done" ? "forest" : "gold"}>
              {c.state === "done" ? "validated" : c.state}
            </Badge>
            {c.already_declared && <Badge tone="secondary">already logged</Badge>}
            {!c.from_staging2 && <Badge tone="neutral">not from Staging 2</Badge>}
          </div>
          <div className="mt-0.5 text-[12.5px] text-on-surface-variant">
            {c.item_count} item{c.item_count === 1 ? "" : "s"} · {fmtQty(c.total_units)} units
            {c.date ? ` · ${c.date.slice(0, 16)}` : ""}
          </div>
        </div>
        <span aria-hidden className="shrink-0 text-on-surface-variant">
          →
        </span>
      </button>
    </li>
  );
}

/** The four answers Noah wrote, as toggle chips, plus the note. "Other" makes
 *  the note required — that's the "required if none are selected" rule, and
 *  the server enforces the same thing. */
function ReasonChips({
  options,
  answer,
  onChange,
}: {
  options: { value: DiscrepancyReason; label: string }[];
  answer: Answer | undefined;
  onChange: (a: Answer) => void;
}) {
  const reasons = useMemo(() => answer?.reasons ?? [], [answer]);
  const note = answer?.note ?? "";
  const toggle = (value: DiscrepancyReason) =>
    onChange({
      reasons: reasons.includes(value)
        ? reasons.filter((r) => r !== value)
        : [...reasons, value],
      note,
    });
  const needsNote = reasons.includes("other") && !note.trim();
  return (
    <div className="mt-2.5 flex flex-col gap-2">
      <div className="flex flex-wrap gap-1.5">
        {options.map((o) => (
          <button
            key={o.value}
            onClick={() => toggle(o.value)}
            aria-pressed={reasons.includes(o.value)}
            className={`state-layer rounded-full px-3 py-1.5 text-[12.5px] font-semibold ${
              reasons.includes(o.value)
                ? "bg-secondary-container text-on-secondary-container"
                : "border border-outline-variant text-on-surface-variant"
            }`}
          >
            {reasons.includes(o.value) ? "✓ " : ""}
            {o.label}
          </button>
        ))}
      </div>
      <Input
        value={note}
        onChange={(e) => onChange({ reasons, note: e.target.value })}
        aria-label="Reason note"
        placeholder={needsNote ? "Say what happened (required)" : "Add a detail (optional)"}
      />
    </div>
  );
}
