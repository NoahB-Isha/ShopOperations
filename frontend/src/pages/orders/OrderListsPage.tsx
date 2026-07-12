/* Admin: curated catalogs people order FROM (no quantities). Grant them to
   zones here; coordinators decide which centers see them. */
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
  Toggle,
  useToast,
} from "../../design";
import type { Column } from "../../design";
import { fmtWhen } from "../shared/OpsBits";

export function OrderListsPage() {
  const [showArchived, setShowArchived] = useState(false);
  const { data, isLoading } = useOrderLists(showArchived);
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();

  const columns: Column<OrderListSummaryOut>[] = [
    {
      key: "name",
      header: "List",
      sortable: true,
      render: (r) => (
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate font-medium">{r.name}</span>
            {r.is_archived && <Badge tone="neutral">archived</Badge>}
          </div>
          <div className="text-[12px] text-on-surface-variant">
            {r.line_count} product{r.line_count === 1 ? "" : "s"}
            {r.stale_line_count > 0 && (
              <span className="ml-1.5 text-warn">
                · {r.stale_line_count} inactive — prune
              </span>
            )}
          </div>
        </div>
      ),
    },
    {
      key: "zone_names",
      header: "Granted to zones",
      hideBelow: "sm",
      value: (r) => r.zone_names.join(", "),
      render: (r) =>
        r.zone_names.length ? (
          <span className="flex flex-wrap gap-1">
            {r.zone_names.map((z) => (
              <Badge key={z} tone="secondary">
                {z}
              </Badge>
            ))}
          </span>
        ) : (
          <span className="text-on-surface-variant">not granted yet</span>
        ),
    },
    {
      key: "center_count",
      header: "Centers",
      align: "right",
      hideBelow: "md",
      sortable: true,
      render: (r) => (
        <span className="tabular-nums text-on-surface-variant">
          {r.center_count || "—"}
        </span>
      ),
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
        subtitle="Safe catalogs of currently-active products. Zones get lists from you; coordinators open them to their centers; centers order from them (phase 3)."
        actions={
          <>
            <Toggle
              checked={showArchived}
              onChange={setShowArchived}
              label="Archived"
            />
            <Button onClick={() => setCreating(true)}>New list</Button>
          </>
        }
      />

      <DataTable
        columns={columns}
        rows={data ?? []}
        rowKey={(r) => r.id}
        loading={isLoading}
        onRowClick={(r) => navigate(`/orders/${r.id}`)}
        empty={
          <EmptyState
            title="No order lists yet"
            hint="Start with a “Center starter kit” — the safe default catalog for any pop-up."
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
          toast.success("List created — add products.");
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
        <Field label="Name" help="e.g. “Center starter kit” — coordinators and centers see this.">
          <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </Field>
        <Field label="Notes (optional)">
          <Textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />
        </Field>
      </div>
    </Dialog>
  );
}
