/* Admin: manage order lists — create, clone, assign, and watch write
   outcomes. Row click opens the editor. */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCreateOrderList, useOrderLists } from "../../api/hooks";
import type { OrderListSummaryOut } from "../../api/types";
import {
  Badge,
  Button,
  DataTable,
  Dialog,
  EmptyState,
  Field,
  Input,
  PageHeader,
  Textarea,
  useToast,
} from "../../design";
import type { Column } from "../../design";
import { WriteStatusChip, fmtQty, fmtWhen } from "../shared/OpsBits";

const STATUS_TONE = {
  draft: "neutral",
  pending_approval: "gold",
  approved: "forest",
  returned: "danger",
} as const;

const STATUS_LABEL = {
  draft: "Draft",
  pending_approval: "Pending approval",
  approved: "Approved",
  returned: "Returned",
} as const;

const FILTERS = ["all", "draft", "pending_approval", "returned", "approved"] as const;

export function OrderListsPage() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const { data, isLoading } = useOrderLists(filter === "all" ? "" : filter);
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();

  const columns: Column<OrderListSummaryOut>[] = [
    {
      key: "name",
      header: "List",
      sortable: true,
      render: (r) => (
        <div className="min-w-0">
          <div className="truncate font-medium">{r.name}</div>
          <div className="text-[12px] text-on-surface-variant">
            {r.line_count} item{r.line_count === 1 ? "" : "s"} · {fmtQty(r.total_qty)} units
          </div>
        </div>
      ),
    },
    {
      key: "center_name",
      header: "Destination",
      sortable: true,
      hideBelow: "sm",
      render: (r) =>
        r.center_name ? (
          <span className="inline-flex items-center gap-1.5">
            {r.center_name}
            {!r.center_mapped && (
              <Badge tone="gold" title="No Odoo location matched this center yet — approval can't write live.">
                unmapped
              </Badge>
            )}
          </span>
        ) : (
          <span className="text-on-surface-variant">—</span>
        ),
    },
    { key: "zone_name", header: "Zone", sortable: true, hideBelow: "md" },
    {
      key: "status",
      header: "Status",
      sortable: true,
      render: (r) => <Badge tone={STATUS_TONE[r.status]}>{STATUS_LABEL[r.status]}</Badge>,
    },
    {
      key: "write_status",
      header: "Odoo",
      render: (r) => <WriteStatusChip status={r.write_status} />,
    },
    {
      key: "updated_at",
      header: "Updated",
      sortable: true,
      align: "right",
      hideBelow: "md",
      value: (r) => r.updated_at,
      render: (r) => <span className="text-on-surface-variant">{fmtWhen(r.updated_at)}</span>,
    },
  ];

  return (
    <>
      <PageHeader
        title="Order lists"
        subtitle="Curate replenishment lists, hand them to a zone coordinator, and the approval becomes a draft transfer in Odoo."
        actions={<Button onClick={() => setCreating(true)}>New list</Button>}
      />

      <div className="mb-4 flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`state-layer rounded-full px-3.5 py-1.5 text-[13px] font-semibold ${
              filter === f
                ? "bg-secondary-container text-on-secondary-container"
                : "border border-outline-variant text-on-surface-variant"
            }`}
          >
            {f === "all" ? "All" : STATUS_LABEL[f]}
          </button>
        ))}
      </div>

      <DataTable
        columns={columns}
        rows={data ?? []}
        rowKey={(r) => r.id}
        loading={isLoading}
        onRowClick={(r) => navigate(`/orders/${r.id}`)}
        empty={
          <EmptyState
            title="No order lists yet"
            hint="Start one and hand it to a coordinator — no more WhatsApp order threads."
            action={<Button onClick={() => setCreating(true)}>New list</Button>}
          />
        }
      />

      <CreateDialog open={creating} onClose={() => setCreating(false)} />
    </>
  );
}

function CreateDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const create = useCreateOrderList();
  const toast = useToast();
  const navigate = useNavigate();

  const submit = () =>
    create.mutate(
      { name: name.trim(), notes: notes.trim() },
      {
        onSuccess: (ol) => {
          toast.success("List created — add items.");
          onClose();
          navigate(`/orders/${(ol as { id: number }).id}`);
        },
        onError: (e) => toast.error(e.message),
      },
    );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="New order list"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={name.trim().length < 2} loading={create.isPending}>
            Create
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="Name" help="e.g. “Austin summer refill” — coordinators see this.">
          <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </Field>
        <Field label="Notes (optional)">
          <Textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />
        </Field>
      </div>
    </Dialog>
  );
}
