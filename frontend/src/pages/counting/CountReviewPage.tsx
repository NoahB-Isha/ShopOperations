/* Count review — deciding what becomes Odoo's number.

   The queue is ranked by the backend (recounts first: a recount is someone's
   second trip to the same shelf). Each item is decided on its own, and the
   submission it came from can also be decided as a group — but an individual
   decision always outranks a bulk one, so bulk actions skip anything already
   settled.

   The history table is the point of the page: Original → Recount 1 → Recount 2
   side by side, each with its counter and the Odoo quantity as it stood then,
   so a reviewer can see the progression instead of one number that keeps
   changing. */
import { useState } from "react";
import {
  useApproveCountItem,
  useApproveWholeCount,
  useCountAssignees,
  useCountQueue,
  useCounts,
  useRecountWholeCount,
  useRejectCountItem,
  useRejectWholeCount,
  useRequestRecount,
} from "../../api/hooks";
import type { CountItemOut, CountSummaryOut } from "../../api/types";
import {
  Badge,
  Button,
  Dialog,
  EmptyState,
  Field,
  PageHeader,
  ScrollingText,
  Select,
  Spinner,
  Textarea,
  useToast,
} from "../../design";
import type { BadgeTone } from "../../design";
import { usePersistedState } from "../../persist";
import { OdooLink, fmtQty, fmtWhen, productCode } from "../shared/OpsBits";

const STATUS_LABEL: Record<string, string> = {
  pending: "Needs review",
  recount_requested: "Out for recount",
  approved: "Approved",
  rejected: "Rejected",
  partially_reviewed: "Partly reviewed",
  recount_required: "Recount required",
  completed: "Completed",
};

const STATUS_TONE: Record<string, BadgeTone> = {
  pending: "gold",
  recount_requested: "danger",
  approved: "forest",
  rejected: "neutral",
  partially_reviewed: "gold",
  recount_required: "danger",
  completed: "forest",
};

function StatusChip({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? "neutral"}>{STATUS_LABEL[status] ?? status}</Badge>;
}

/** Original → Recount 1 → Recount 2, as a table, because that is how the
 *  progression reads. Nothing here is ever overwritten. */
function HistoryTable({ item }: { item: CountItemOut }) {
  if (item.entries.length === 0) return null;
  const heads = item.entries.map((e) => (e.attempt === 1 ? "Original" : `Recount ${e.attempt - 1}`));
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full min-w-[17rem] text-[12.5px] tabular-nums">
        <thead>
          <tr className="text-on-surface-variant">
            <th className="py-1 pr-3 text-left font-medium">&nbsp;</th>
            {heads.map((h) => (
              <th key={h} className="px-2 py-1 text-right font-semibold">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr className="border-t border-outline-variant/60">
            <td className="py-1 pr-3 text-on-surface-variant">Count</td>
            {item.entries.map((e) => (
              <td key={e.attempt} className="px-2 py-1 text-right font-semibold">
                {fmtQty(e.counted_qty)}
              </td>
            ))}
          </tr>
          <tr>
            <td className="py-1 pr-3 text-on-surface-variant">Counter</td>
            {item.entries.map((e) => (
              <td key={e.attempt} className="px-2 py-1 text-right">
                {e.counted_by}
              </td>
            ))}
          </tr>
          <tr>
            <td className="py-1 pr-3 text-on-surface-variant">Odoo qty</td>
            {item.entries.map((e) => (
              <td key={e.attempt} className="px-2 py-1 text-right">
                {fmtQty(e.odoo_qty)}
                {e.odoo_qty_source === "snapshot" && (
                  <span title="From the last stock sync — Odoo wasn't answering when this was counted">
                    {" "}
                    *
                  </span>
                )}
              </td>
            ))}
          </tr>
          <tr>
            <td className="py-1 pr-3 text-on-surface-variant">Difference</td>
            {item.entries.map((e) => (
              <td
                key={e.attempt}
                className={`px-2 py-1 text-right ${e.delta === 0 ? "text-on-surface-variant" : ""}`}
              >
                {e.delta > 0 ? "+" : ""}
                {fmtQty(e.delta)}
              </td>
            ))}
          </tr>
          <tr>
            <td className="py-1 pr-3 text-on-surface-variant">When</td>
            {item.entries.map((e) => (
              <td key={e.attempt} className="px-2 py-1 text-right text-on-surface-variant">
                {fmtWhen(e.created_at)}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
      {item.entries.some((e) => e.reason) && (
        <p className="mt-1.5 text-[12px] leading-4 text-on-surface-variant">
          <b>Recount asked for:</b> {item.entries.filter((e) => e.reason).slice(-1)[0]?.reason}
        </p>
      )}
    </div>
  );
}

type Ask = { kind: "reject" | "recount"; item?: CountItemOut; count?: CountSummaryOut };

function ReasonDialog({
  ask,
  onClose,
}: {
  ask: Ask | null;
  onClose: () => void;
}) {
  const [note, setNote] = useState("");
  const [assignee, setAssignee] = useState<string>("");
  const assignees = useCountAssignees(ask?.kind === "recount");
  const rejectItem = useRejectCountItem();
  const recountItem = useRequestRecount();
  const rejectAll = useRejectWholeCount();
  const recountAll = useRecountWholeCount();
  const toast = useToast();
  const busy =
    rejectItem.isPending || recountItem.isPending || rejectAll.isPending || recountAll.isPending;

  const title = !ask
    ? ""
    : ask.item
      ? ask.kind === "reject"
        ? `Reject this count of ${ask.item.name}?`
        : `Ask for a recount of ${ask.item.name}?`
      : ask.kind === "reject"
        ? `Reject all of ${ask.count?.display_name}?`
        : `Send ${ask.count?.display_name} for recount?`;

  const submit = () => {
    if (!ask) return;
    const done = {
      onSuccess: () => {
        toast.success(ask.kind === "reject" ? "Count rejected." : "Recount requested.");
        setNote("");
        onClose();
      },
      onError: (e: Error) => toast.error(e.message),
    };
    const assignee_id = assignee ? Number(assignee) : null;
    if (ask.item) {
      if (ask.kind === "reject") rejectItem.mutate({ itemId: ask.item.id, note }, done);
      else recountItem.mutate({ itemId: ask.item.id, note, assignee_id }, done);
    } else if (ask.count) {
      if (ask.kind === "reject") rejectAll.mutate({ countId: ask.count.id, note }, done);
      else recountAll.mutate({ countId: ask.count.id, note, assignee_id }, done);
    }
  };

  return (
    <Dialog
      open={ask !== null}
      onClose={onClose}
      title={title}
      footer={
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant={ask?.kind === "reject" ? "danger" : undefined}
            disabled={note.trim() === ""}
            loading={busy}
            onClick={submit}
          >
            {ask?.kind === "reject" ? "Reject" : "Request recount"}
          </Button>
        </div>
      }
    >
      <p className="mb-3 text-[13px] leading-5 text-on-surface-variant">
        {ask?.kind === "reject"
          ? "The count is thrown out and Odoo is not touched. Your reason stays on the item's history, and the person who counted will read it."
          : "The item goes back for another physical count. Your reason stays on the history and is shown to whoever counts it."}
      </p>
      <Field label="Reason (required)">
        <Textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
      {ask?.kind === "recount" && (
        <div className="mt-3">
          <Field label="Assign to (optional)">
            <Select value={assignee} onChange={(e) => setAssignee(e.target.value)}>
              <option value="">Anyone who counts</option>
              {(assignees.data ?? []).map((a) => (
                <option key={a.id} value={String(a.id)}>
                  {a.name}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      )}
    </Dialog>
  );
}

function QueueItem({ item, onAsk }: { item: CountItemOut; onAsk: (a: Ask) => void }) {
  const approve = useApproveCountItem();
  const toast = useToast();
  const isRecount = item.attempts > 1;
  return (
    <li
      data-name-press
      className={`rounded-(--radius-lg) px-4 py-3 ${
        isRecount ? "bg-warn-container" : "bg-surface-container-low"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <ScrollingText text={item.name} className="text-[15px] font-medium" />
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-on-surface-variant">
            <span className="font-mono">{productCode(item.barcode, item.sku)}</span>
            <span>· {item.location_key}</span>
            <span>· Count #{item.count_id}</span>
            <StatusChip status={item.status} />
            {isRecount && <Badge tone="danger">recount {item.attempts - 1}</Badge>}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="display text-2xl leading-none tabular-nums">
            {fmtQty(item.counted_qty ?? 0)}
          </div>
          <div className="text-[11px] text-on-surface-variant">
            counted · Odoo {fmtQty(item.odoo_qty ?? 0)}
          </div>
        </div>
      </div>

      <HistoryTable item={item} />

      {item.also_counted && item.status !== "approved" && item.status !== "rejected" && (
        /* The other count of the same product at the same shelf. Approving
           both while neither has reached Odoo subtracts two deltas from one
           starting number — that's how 2026-08-22 put a product at zero that
           was counted 3, 6 and 5. Advisory: the reviewer decides, but they
           decide knowing. */
        <div
          data-testid="also-counted"
          className={`mt-2 rounded-(--radius-sm) px-2.5 py-1.5 text-[12.5px] leading-snug ${
            item.also_counted.applied
              ? "bg-surface-container text-on-surface-variant"
              : "bg-error-container text-on-error-container"
          }`}
        >
          {!item.also_counted.applied && (
            <span className="font-semibold">Also counted, not yet applied — </span>
          )}
          {item.also_counted.note} (count #{item.also_counted.count_id}).
          {!item.also_counted.applied &&
            " Approving both takes each difference off the same starting number."}
        </div>
      )}

      {item.status === "recount_requested" ? (
        <p className="mt-2 text-[12.5px] text-on-surface-variant">
          Waiting on {item.recount_assignee || "whoever counts it"} to recount.
        </p>
      ) : (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            size="sm"
            loading={approve.isPending}
            onClick={() =>
              approve.mutate(
                { itemId: item.id },
                {
                  onSuccess: (out) => {
                    const applied = out.items.find((i) => i.id === item.id);
                    toast.success(
                      !applied?.picking_name
                        ? "Approved."
                        : applied.picking_status === "validated"
                          ? `Approved — ${applied.picking_name} posted in Odoo.`
                          : `Approved — draft ${applied.picking_name} is waiting in Odoo.`,
                    );
                  },
                  onError: (e) => toast.error(e.message),
                },
              )
            }
          >
            Approve {fmtQty(item.counted_qty ?? 0)}
          </Button>
          <Button size="sm" variant="secondary" onClick={() => onAsk({ kind: "recount", item })}>
            Request recount
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onAsk({ kind: "reject", item })}>
            Reject
          </Button>
        </div>
      )}

      {item.picking_status === "failed" && (
        <p className="mt-2 rounded-(--radius-sm) bg-error-container px-2.5 py-1.5 text-[12.5px]">
          Odoo wasn't updated: {item.picking_error}
        </p>
      )}
      {item.picking_url && (
        <div className="mt-2">
          <OdooLink url={item.picking_url} name={item.picking_name} />
        </div>
      )}
    </li>
  );
}

function SubmissionRow({
  count,
  onAsk,
}: {
  count: CountSummaryOut;
  onAsk: (a: Ask) => void;
}) {
  const approveAll = useApproveWholeCount();
  const toast = useToast();
  const open = count.pending_items + count.recount_items > 0;
  return (
    <li className="rounded-(--radius-lg) bg-surface-container-low px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[13.5px] font-semibold">{count.display_name}</span>
            <StatusChip status={count.status} />
          </div>
          <div className="mt-0.5 text-[12px] text-on-surface-variant">
            {count.location_label} · {count.item_count} item
            {count.item_count === 1 ? "" : "s"} · {count.counted_by} · {fmtWhen(count.submitted_at)}
            {count.recount_items > 0 && ` · ${count.recount_items} out for recount`}
          </div>
        </div>
        {open && (
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              loading={approveAll.isPending}
              onClick={() =>
                approveAll.mutate(
                  { countId: count.id },
                  {
                    onSuccess: () => toast.success(`${count.display_name}: open items approved.`),
                    onError: (e) => toast.error(e.message),
                  },
                )
              }
            >
              Approve all
            </Button>
            <Button size="sm" variant="secondary" onClick={() => onAsk({ kind: "recount", count })}>
              Recount all
            </Button>
            <Button size="sm" variant="ghost" onClick={() => onAsk({ kind: "reject", count })}>
              Reject all
            </Button>
          </div>
        )}
      </div>
      {open && (
        <p className="mt-1.5 text-[11.5px] text-on-surface-variant">
          Items already approved or rejected on their own are left as they are.
        </p>
      )}
    </li>
  );
}

export function CountReviewPage() {
  const [tab, setTab] = usePersistedState<"queue" | "submissions">("countReview.tab", "queue");
  const queue = useCountQueue(tab === "queue");
  const counts = useCounts({ openOnly: tab === "submissions" });
  const [ask, setAsk] = useState<Ask | null>(null);

  const items = queue.data ?? [];
  const recountCount = items.filter((i) => i.attempts > 1).length;

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="Count review"
        subtitle="Approve a count and it becomes Odoo's number. Recounts come first."
      />

      <div className="mb-4 flex flex-wrap items-center gap-1.5">
        {(
          [
            ["queue", `Items to review${items.length ? ` (${items.length})` : ""}`],
            ["submissions", "Submissions"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            onClick={() => setTab(value)}
            className={`state-layer rounded-full px-3.5 py-1.5 text-[13px] font-semibold ${
              tab === value
                ? "bg-secondary-container text-on-secondary-container"
                : "border border-outline-variant text-on-surface-variant"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "queue" ? (
        queue.isLoading ? (
          <div className="grid place-items-center py-24">
            <Spinner size={24} />
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            title="Nothing to review"
            hint="Counts appear here the moment someone submits one."
          />
        ) : (
          <>
            {recountCount > 0 && (
              <p className="mb-2.5 text-[12.5px] text-on-surface-variant">
                {recountCount} recount{recountCount === 1 ? "" : "s"} at the top — someone has
                already been back to that shelf once.
              </p>
            )}
            <ul className="stagger-children flex flex-col gap-2 pb-24">
              {items.map((item) => (
                <QueueItem key={item.id} item={item} onAsk={setAsk} />
              ))}
            </ul>
          </>
        )
      ) : (counts.data ?? []).length === 0 ? (
        <EmptyState title="No open submissions" hint="Every count has been fully reviewed." />
      ) : (
        <ul className="flex flex-col gap-2 pb-24">
          {(counts.data ?? []).map((c) => (
            <SubmissionRow key={c.id} count={c} onAsk={setAsk} />
          ))}
        </ul>
      )}

      <ReasonDialog ask={ask} onClose={() => setAsk(null)} />
    </div>
  );
}
