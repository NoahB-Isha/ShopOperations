/* Warehouse: staging-count discrepancies to review — the queue that used to
   be a WhatsApp scrollback. Resolve (fixed in Odoo / found the stock) or
   dismiss (bad count), always with the trail kept. */
import { useState } from "react";
import { Link } from "react-router-dom";
import { useAdjustments, useResolveAdjustment } from "../../api/hooks";
import type { AdjustmentOut } from "../../api/types";
import {
  Badge,
  Button,
  DataTable,
  Dialog,
  EmptyState,
  Field,
  PageHeader,
  Textarea,
  useToast,
} from "../../design";
import type { Column } from "../../design";
import { fmtQty, fmtWhen } from "../shared/OpsBits";

const FILTERS = [
  ["open", "Open"],
  ["resolved", "Resolved"],
  ["dismissed", "Dismissed"],
  ["all", "All"],
] as const;

export function AdjustmentsPage() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number][0]>("open");
  const { data, isLoading } = useAdjustments(filter);
  const [reviewing, setReviewing] = useState<AdjustmentOut | null>(null);

  const columns: Column<AdjustmentOut>[] = [
    {
      key: "name",
      header: "Product",
      sortable: true,
      render: (r) => (
        <div>
          <div className="font-medium">{r.name}</div>
          <div className="font-mono text-[11.5px] text-on-surface-variant">{r.sku}</div>
        </div>
      ),
    },
    {
      key: "delta",
      header: "Sent → counted",
      align: "right",
      sortable: true,
      render: (r) => (
        <span className="tabular-nums">
          {fmtQty(r.qty_expected)} → {fmtQty(r.qty_counted)}{" "}
          <span className={`font-bold ${r.delta < 0 ? "text-error" : "text-warn"}`}>
            ({r.delta > 0 ? "+" : ""}
            {fmtQty(r.delta)})
          </span>
        </span>
      ),
    },
    {
      key: "request_id",
      header: "From",
      hideBelow: "sm",
      render: (r) =>
        r.request_id ? (
          <Link
            to={`/transfer-requests/${r.request_id}`}
            onClick={(e) => e.stopPropagation()}
            className="font-medium text-primary hover:underline"
          >
            request #{r.request_id}
          </Link>
        ) : (
          <span className="text-on-surface-variant">—</span>
        ),
    },
    {
      key: "status",
      header: "Status",
      render: (r) =>
        r.status === "open" ? (
          <Badge tone="danger">open</Badge>
        ) : r.status === "resolved" ? (
          <Badge tone="forest" title={r.resolution_note}>
            resolved
          </Badge>
        ) : (
          <Badge tone="neutral" title={r.resolution_note}>
            dismissed
          </Badge>
        ),
    },
    {
      key: "created_at",
      header: "Flagged",
      align: "right",
      hideBelow: "md",
      sortable: true,
      value: (r) => r.created_at,
      render: (r) => <span className="text-on-surface-variant">{fmtWhen(r.created_at)}</span>,
    },
  ];

  return (
    <>
      <PageHeader
        title="Adjustments to review"
        subtitle="Staging counts that didn't match what was sent. Fix the inventory in Odoo, then close them out here."
      />

      <div className="mb-4 flex flex-wrap gap-1.5">
        {FILTERS.map(([value, label]) => (
          <button
            key={value}
            onClick={() => setFilter(value)}
            className={`state-layer rounded-full px-3.5 py-1.5 text-[13px] font-semibold ${
              filter === value
                ? "bg-secondary-container text-on-secondary-container"
                : "border border-outline-variant text-on-surface-variant"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <DataTable
        columns={columns}
        rows={data ?? []}
        rowKey={(r) => r.id}
        loading={isLoading}
        onRowClick={(r) => (r.status === "open" ? setReviewing(r) : undefined)}
        empty={
          <EmptyState
            title={filter === "open" ? "Queue's clear" : "Nothing here"}
            hint={
              filter === "open"
                ? "Staging counts are matching what's sent. Beautiful."
                : "Adjustments in this state will appear here."
            }
          />
        }
      />

      <ResolveDialog adj={reviewing} onClose={() => setReviewing(null)} />
    </>
  );
}

function ResolveDialog({ adj, onClose }: { adj: AdjustmentOut | null; onClose: () => void }) {
  const resolve = useResolveAdjustment();
  const toast = useToast();
  const [note, setNote] = useState("");

  const act = (action: "resolved" | "dismissed") =>
    resolve.mutate(
      { id: adj!.id, action, note: note.trim() },
      {
        onSuccess: () => {
          toast.success(action === "resolved" ? "Marked resolved." : "Dismissed.");
          setNote("");
          onClose();
        },
        onError: (e) => toast.error(e.message),
      },
    );

  if (!adj) return null;
  return (
    <Dialog
      open
      onClose={onClose}
      title={`${adj.name}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Later
          </Button>
          <Button variant="outlined" loading={resolve.isPending} onClick={() => act("dismissed")}>
            Dismiss (bad count)
          </Button>
          <Button loading={resolve.isPending} onClick={() => act("resolved")}>
            Resolved
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm leading-6 text-on-surface-variant">
          Warehouse sent <b className="text-on-surface">{fmtQty(adj.qty_expected)}</b>, the floor
          counted <b className="text-on-surface">{fmtQty(adj.qty_counted)}</b> (
          <span className={adj.delta < 0 ? "text-error" : "text-warn"}>
            {adj.delta > 0 ? "+" : ""}
            {fmtQty(adj.delta)}
          </span>
          ). “Resolved” means you fixed it — found the stock, or made the inventory adjustment in
          Odoo. “Dismiss” means the count was wrong and nothing needed doing.
        </p>
        <Field label="Note (what happened?)">
          <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </div>
    </Dialog>
  );
}
