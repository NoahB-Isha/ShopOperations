/* Floor: build a BWHSE→Floor request on a phone in the aisle — search,
   tap to add, adjust quantities, send. Floor vs warehouse quantities are
   right there so nobody requests what the warehouse doesn't have. */
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCreateTransferRequest } from "../../api/hooks";
import type { TransferRequestOut } from "../../api/types";
import { Button, Card, EmptyState, Field, PageHeader, Textarea, useToast } from "../../design";
import {
  LowCountHint,
  ProductPicker,
  QtyInput,
  fmtQty,
  type PickedLine,
} from "../shared/OpsBits";

export function NewTransferRequestPage() {
  const [lines, setLines] = useState<PickedLine[]>([]);
  const [notes, setNotes] = useState("");
  const create = useCreateTransferRequest();
  const toast = useToast();
  const navigate = useNavigate();

  const pickedIds = useMemo(() => new Set(lines.map((l) => l.product_id)), [lines]);
  const totalQty = lines.reduce((sum, l) => sum + l.qty, 0);

  const submit = () =>
    create.mutate(
      {
        notes: notes.trim(),
        lines: lines.map(({ product_id, qty }) => ({ product_id, qty })),
      },
      {
        onSuccess: (req) => {
          toast.success("Request sent to the warehouse.");
          navigate(`/transfer-requests/${(req as TransferRequestOut).id}`);
        },
        onError: (e) => toast.error(e.message),
      },
    );

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader
        title="Request stock"
        subtitle="From the Blue Warehouse to the Shoppe floor."
        actions={
          <Button variant="ghost" onClick={() => navigate("/transfer-requests")}>
            Back
          </Button>
        }
      />

      <Card className="mb-4">
        <ProductPicker pickedIds={pickedIds} onPick={(line) => setLines([...lines, line])} />
      </Card>

      {lines.length === 0 ? (
        <EmptyState
          title="Nothing picked yet"
          hint="Search above — tap an item to add it to the request."
        />
      ) : (
        <Card pad={false} className="mb-4">
          <ul>
            {lines.map((line) => (
              <li
                key={line.product_id}
                className="flex items-center justify-between gap-2 border-b
                  border-outline-variant/50 px-4 py-3 last:border-b-0"
              >
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{line.name}</div>
                  <div className="flex items-center gap-2 text-[12px] tabular-nums text-on-surface-variant">
                    <span className="font-mono">{line.sku}</span>
                    <span>
                      floor {fmtQty(line.floor_qty)} · whse {fmtQty(line.bwhse_qty)}{" "}
                      <LowCountHint qty={line.bwhse_qty} />
                    </span>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <QtyInput
                    value={line.qty}
                    min={1}
                    ariaLabel={`Quantity for ${line.name}`}
                    onChange={(qty) =>
                      setLines(
                        lines.map((x) => (x.product_id === line.product_id ? { ...x, qty } : x)),
                      )
                    }
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={`Remove ${line.name}`}
                    onClick={() => setLines(lines.filter((x) => x.product_id !== line.product_id))}
                  >
                    ✕
                  </Button>
                </div>
              </li>
            ))}
          </ul>
          <div className="flex items-center justify-between bg-surface-container px-4 py-2.5 text-sm">
            <span className="text-on-surface-variant">
              {lines.length} item{lines.length === 1 ? "" : "s"}
            </span>
            <span className="font-semibold tabular-nums">{fmtQty(totalQty)} units</span>
          </div>
        </Card>
      )}

      <Card className="mb-24 md:mb-6">
        <Field label="Note for the warehouse (optional)">
          <Textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
        </Field>
        <div className="mt-4 flex justify-end">
          <Button
            disabled={lines.length === 0}
            loading={create.isPending}
            onClick={submit}
            className="w-full sm:w-auto"
          >
            Send request{lines.length > 0 ? ` · ${lines.length} item${lines.length === 1 ? "" : "s"}` : ""}
          </Button>
        </div>
      </Card>
    </div>
  );
}
