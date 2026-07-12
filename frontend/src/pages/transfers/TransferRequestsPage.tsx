/* The BWHSE→Floor board — live like a food-POS screen (the query layer polls
   every few seconds, and the backend listens for Odoo barcode validations on
   each refresh). Orders carry their Odoo picking names. */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import { useTransferRequests } from "../../api/hooks";
import type { TransferSummaryOut } from "../../api/types";
import { Badge, Button, DataTable, EmptyState, Fab, PageHeader } from "../../design";
import type { Column } from "../../design";
import {
  TRANSFER_LABELS,
  TransferStatusChip,
  WriteStatusChip,
  fmtQty,
  fmtWhen,
} from "../shared/OpsBits";

const FILTERS = [
  ["active", "Active"],
  ["requested", "Requested"],
  ["working_on_it", "Working on it"],
  ["counting", "Counting"],
  ["done", "Done"],
  ["", "All"],
] as const;

const ACTIVE = "requested,working_on_it,sent,counting";

export function TransferRequestsPage() {
  const { roles } = useAuth();
  const isFloor = roles.has("shoppe_floor") || roles.has("admin");
  const [filter, setFilter] = useState<string>("active");
  const { data, isLoading, dataUpdatedAt } = useTransferRequests(
    filter === "active" ? ACTIVE : filter,
  );
  const navigate = useNavigate();

  const columns: Column<TransferSummaryOut>[] = [
    {
      key: "display_name",
      header: "Order",
      sortable: true,
      render: (r) => (
        <div>
          <span className="font-mono text-[13px] font-semibold">{r.display_name}</span>
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
          {r.picking_status !== "created" && r.picking_status !== "none" && (
            <WriteStatusChip status={r.picking_status} />
          )}
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
        subtitle={
          <span className="flex items-center gap-2">
            Floor asks, warehouse sends, the barcode count closes it — live board, no refreshing.
            {dataUpdatedAt > 0 && (
              <span className="inline-flex items-center gap-1 text-[12px] text-on-surface-variant">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-success" aria-hidden />
                live
              </span>
            )}
          </span>
        }
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
            title="Board's clear"
            hint={
              isFloor
                ? "Start a request when the floor needs stock from the warehouse."
                : "When the floor requests stock, it lands here the moment they place it."
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
