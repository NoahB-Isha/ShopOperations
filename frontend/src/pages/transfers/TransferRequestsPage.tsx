/* The shared BWHSE→Floor request list. Floor volunteers start requests;
   warehouse works the queue. Both see the same rows and statuses. */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import { useTransferRequests } from "../../api/hooks";
import type { TransferSummaryOut } from "../../api/types";
import { Badge, Button, DataTable, EmptyState, Fab, PageHeader } from "../../design";
import type { Column } from "../../design";
import { TRANSFER_LABELS, TransferStatusChip, fmtQty, fmtWhen } from "../shared/OpsBits";

const FILTERS = [
  ["active", "Active"],
  ["requested", "Requested"],
  ["picked,in_staging", "In motion"],
  ["counted,on_floor", "Landed"],
  ["cancelled", "Cancelled"],
  ["", "All"],
] as const;

const ACTIVE = "requested,picked,in_staging,counted";

export function TransferRequestsPage() {
  const { roles } = useAuth();
  const isFloor = roles.has("shoppe_floor") || roles.has("admin");
  const [filter, setFilter] = useState<string>("active");
  const { data, isLoading } = useTransferRequests(filter === "active" ? ACTIVE : filter);
  const navigate = useNavigate();

  const columns: Column<TransferSummaryOut>[] = [
    {
      key: "id",
      header: "Request",
      sortable: true,
      render: (r) => (
        <div>
          <span className="font-semibold">#{r.id}</span>
          <span className="ml-2 text-[12.5px] text-on-surface-variant">
            {r.line_count} item{r.line_count === 1 ? "" : "s"} · {fmtQty(r.total_requested)} units
          </span>
        </div>
      ),
    },
    {
      key: "status",
      header: "Status",
      sortable: true,
      value: (r) => TRANSFER_LABELS[r.status],
      render: (r) => (
        <span className="inline-flex items-center gap-1.5">
          <TransferStatusChip status={r.status} />
          {r.open_adjustments > 0 && (
            <Badge tone="danger" title="Open discrepancies in the adjustments queue">
              {r.open_adjustments} to review
            </Badge>
          )}
        </span>
      ),
    },
    { key: "created_by", header: "Requested by", hideBelow: "sm", sortable: true },
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
        title="Transfer requests"
        subtitle="Floor asks, warehouse picks, staging gets counted — one shared timeline instead of a WhatsApp thread."
        actions={
          isFloor ? (
            <Button onClick={() => navigate("/transfer-requests/new")}>New request</Button>
          ) : undefined
        }
      />

      <div className="mb-4 flex flex-wrap gap-1.5">
        {FILTERS.map(([value, label]) => (
          <button
            key={label}
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
        onRowClick={(r) => navigate(`/transfer-requests/${r.id}`)}
        empty={
          <EmptyState
            title="No requests here"
            hint={
              isFloor
                ? "Start one when the floor needs stock from the warehouse."
                : "When the floor requests stock, it lands in this queue."
            }
            action={
              isFloor ? (
                <Button onClick={() => navigate("/transfer-requests/new")}>New request</Button>
              ) : undefined
            }
          />
        }
      />

      {isFloor && (
        <div className="fixed right-5 bottom-24 z-30 md:hidden">
          <Fab label="New request" onClick={() => navigate("/transfer-requests/new")} />
        </div>
      )}
    </>
  );
}
