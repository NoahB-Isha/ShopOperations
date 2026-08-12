/* The BWHSE→Floor board — live like a food-POS screen (the query layer polls
   every few seconds, and the backend listens for Odoo barcode validations on
   each refresh). Orders carry their Odoo picking names.

   This is the "Past transfers" half of TransfersPage — the page title, the
   tabs and the route live there; everything below is just the board. */
import { usePersistedState } from "../../persist";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import {
  useFetchTransferRequest,
  useTransferAction,
  useTransferRequests,
} from "../../api/hooks";
import type { TransferSummaryOut } from "../../api/types";
import {
  Badge,
  Button,
  ContextMenu,
  DataTable,
  Dialog,
  EmptyState,
  useContextMenu,
  useToast,
} from "../../design";
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

// Cancelling stops at "sent" — past that the stock has physically moved and
// the count closes it out (the state machine agrees; this just keeps the menu
// from offering something the API would reject).
const CANCELLABLE = new Set(["requested", "working_on_it"]);

export function PastTransfersPanel() {
  const { roles } = useAuth();
  const isFloor = roles.has("shoppe_floor") || roles.has("admin");
  const [filter, setFilter] = usePersistedState<string>("transfers.filter", "active");
  const { data, isLoading, dataUpdatedAt } = useTransferRequests(
    filter === "active" ? ACTIVE : filter,
  );
  const navigate = useNavigate();

  // right-click a row: duplicate it into a fresh request, or cancel it
  // without opening it first
  const menu = useContextMenu();
  const toast = useToast();
  const fetchRequest = useFetchTransferRequest();
  const cancel = useTransferAction("cancel");
  const [cancelTarget, setCancelTarget] = useState<TransferSummaryOut | null>(null);

  // the summary rows carry no lines — fetch the detail, then hand it to the
  // request page as a prefill (which, as everywhere, replaces the open draft)
  const duplicate = async (row: TransferSummaryOut) => {
    try {
      const req = await fetchRequest(row.id);
      navigate("/transfer-requests/new", {
        state: {
          prefill: {
            notes: `Duplicate of ${req.display_name}`,
            lines: req.lines.map((l) => ({
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
      });
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  const rowMenu = (row: TransferSummaryOut, e: React.MouseEvent) => {
    menu.open(e, [
      ...(isFloor
        ? [
            {
              label: `Duplicate — ${row.line_count} item${row.line_count === 1 ? "" : "s"}`,
              onSelect: () => void duplicate(row),
            },
          ]
        : []),
      ...(CANCELLABLE.has(row.status)
        ? [
            {
              label: "Cancel request",
              danger: true,
              onSelect: () => setCancelTarget(row),
            },
          ]
        : []),
    ]);
  };

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
      <div className="mb-4 flex flex-wrap items-center gap-1.5">
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
        {dataUpdatedAt > 0 && (
          <span className="ml-auto inline-flex items-center gap-1 text-[12px] text-on-surface-variant">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-success" aria-hidden />
            live
          </span>
        )}
      </div>

      <DataTable
        columns={columns}
        rows={data ?? []}
        rowKey={(r) => r.id}
        loading={isLoading}
        onRowClick={(r) => navigate(`/transfer-requests/${r.id}`)}
        onRowContextMenu={rowMenu}
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
                <Button onClick={() => navigate("/transfer-requests/new")}>
                  Start a transfer
                </Button>
              ) : undefined
            }
          />
        }
      />

      <ContextMenu menu={menu.menu} onClose={menu.close} />

      <Dialog
        open={cancelTarget !== null}
        onClose={() => setCancelTarget(null)}
        title={cancelTarget ? `Cancel ${cancelTarget.display_name}?` : "Cancel request?"}
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setCancelTarget(null)}>
              Keep it
            </Button>
            <Button
              variant="danger"
              loading={cancel.isPending}
              onClick={() =>
                cancelTarget &&
                cancel.mutate(
                  { id: cancelTarget.id },
                  {
                    onSuccess: () => {
                      toast.success(`${cancelTarget.display_name} cancelled.`);
                      setCancelTarget(null);
                    },
                    onError: (e) => toast.error(e.message),
                  },
                )
              }
            >
              Cancel request
            </Button>
          </div>
        }
      >
        <p className="text-sm leading-6 text-on-surface-variant">
          {cancelTarget?.picking_status === "created"
            ? "The Odoo draft is removed too (drafts move no stock)."
            : "The request stays in history as cancelled; nothing moves."}
        </p>
      </Dialog>
    </>
  );
}
