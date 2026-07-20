/* The floor's out-of-stock board — and the data-cleanup tool.

   Two honest sources: products Odoo already shows at zero on the floor, and
   products the team MARKED out because the shelf is empty whatever Odoo
   says. A mark with phantom stock renders a draft "USA-III: Inventory Adj
   Reduction" picking removing that quantity — a human validates it in Odoo. */
import { useMemo, useState } from "react";
import {
  useMarkOos,
  useOosList,
  useRestockOosMark,
  useUnmarkOos,
  type OosRestockResult,
} from "../../api/hooks";
import type { OosItemOut } from "../../api/types";
import { Badge, Button, Card, Dialog, EmptyState, Input, PageHeader, Spinner, Textarea, useToast } from "../../design";
import { OdooLink, ProductPicker, WriteStatusChip, fmtQty, fmtWhen, productCode, type PickedLine } from "../shared/OpsBits";

function MarkDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [picked, setPicked] = useState<PickedLine | null>(null);
  const [note, setNote] = useState("");
  const mark = useMarkOos();
  const toast = useToast();

  const close = () => {
    setPicked(null);
    setNote("");
    onClose();
  };
  const submit = () =>
    picked &&
    mark.mutate(
      { product_id: picked.product_id, note },
      {
        onSuccess: (item) => {
          const p = item.mark?.picking;
          toast.success(
            p && p.status !== "none"
              ? `Marked out — reduction for ${fmtQty(item.mark!.qty_removed)} rendered (${p.status}).`
              : "Marked out — Odoo already showed zero, nothing to remove.",
          );
          close();
        },
        onError: (e) => toast.error(e.message),
      },
    );

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Mark an item out of stock"
      footer={
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button disabled={!picked || mark.isPending} onClick={submit}>
            {mark.isPending ? <Spinner size={16} /> : "Mark out of stock"}
          </Button>
        </div>
      }
    >
      {picked ? (
        <div className="flex flex-col gap-3">
          <Card className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium">{picked.name}</div>
              <div className="text-[12px] text-on-surface-variant">
                <span className="font-mono">{productCode(picked.barcode, picked.sku)}</span> · Odoo shows floor{" "}
                {fmtQty(picked.floor_qty)}
              </div>
            </div>
            <Button variant="ghost" size="sm" onClick={() => setPicked(null)}>
              change
            </Button>
          </Card>
          <p className="text-[13px] leading-5 text-on-surface-variant">
            {picked.floor_qty > 0 ? (
              <>
                This renders a <b>draft “USA-III: Inventory Adj Reduction”</b> removing{" "}
                {fmtQty(picked.floor_qty)} from the floor — someone confirms it in Odoo before
                anything changes. That's the data cleanup.
              </>
            ) : (
              <>Odoo already shows zero on the floor — this just puts it on the board.</>
            )}
          </p>
          <Textarea
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Note (optional) — e.g. shelf + back both empty"
            aria-label="Note"
          />
        </div>
      ) : (
        <ProductPicker
          pickedIds={new Set()}
          onPick={setPicked}
          placeholder="Search the item that's actually out…"
        />
      )}
    </Dialog>
  );
}

function OosRow({ item, onBackInStock }: { item: OosItemOut; onBackInStock: () => void }) {
  const m = item.mark;
  return (
    <li className="rounded-(--radius-lg) bg-surface-container-low px-4 py-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-[15px] font-medium">{item.name}</div>
          <div className="mt-0.5 text-[12px] tabular-nums text-on-surface-variant">
            <span className="font-mono">{productCode(item.barcode, item.sku)}</span> · floor {fmtQty(item.floor_qty)} · whse{" "}
            {fmtQty(item.bwhse_qty)} · {item.incoming_label}
          </div>
        </div>
        <span className="shrink-0">
          {m ? <Badge tone="danger">marked out</Badge> : <Badge tone="outline">Odoo says 0</Badge>}
        </span>
      </div>
      {m && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[12px] text-on-surface-variant">
          <span>
            by {m.created_by || "someone"} · {fmtWhen(m.created_at)}
            {m.qty_removed > 0 ? ` · removing ${fmtQty(m.qty_removed)}` : " · nothing to remove"}
          </span>
          {m.picking.status !== "none" && (
            <WriteStatusChip
              status={m.picking.status}
              error={m.picking.error}
              createdLabel={m.picking.picking_name || "in Odoo"}
            />
          )}
          <OdooLink url={m.picking.url} name={m.picking.picking_name} />
          {m.note && <span className="w-full">“{m.note}”</span>}
          <Button variant="ghost" size="sm" onClick={onBackInStock}>
            Back in stock…
          </Button>
        </div>
      )}
    </li>
  );
}

/** "The shelf has stock again" — enter the counted quantity and the app
 *  renders whichever draft reconciles Odoo to reality: "Adding Qty" when the
 *  count is higher, a reduction when lower. Skip the count for a plain undo. */
function BackInStockDialog({
  open,
  onClose,
  item,
  markId,
}: {
  open: boolean;
  onClose: () => void;
  item: OosItemOut;
  markId: number;
}) {
  const restock = useRestockOosMark();
  const unmark = useUnmarkOos();
  const toast = useToast();
  const [counted, setCounted] = useState("");
  const [result, setResult] = useState<OosRestockResult | null>(null);

  const close = () => {
    setCounted("");
    setResult(null);
    onClose();
  };
  const submit = () => {
    const n = counted.trim() === "" ? null : Number(counted);
    if (n !== null && (!Number.isFinite(n) || n < 0)) {
      toast.error("Enter the counted quantity as a plain number.");
      return;
    }
    restock.mutate(
      { markId, counted_qty: n },
      {
        onSuccess: (r) => {
          if (r.adjustment) setResult(r); // show the draft + link before closing
          else {
            toast.success(
              n === null
                ? "Unmarked."
                : "Counts already agree — unmarked, nothing to adjust.",
            );
            close();
          }
        },
        onError: (e) => toast.error(e.message),
      },
    );
  };

  const adj = result?.adjustment;
  return (
    <Dialog
      open={open}
      onClose={close}
      title={adj ? "Adjustment rendered" : "Back in stock?"}
      footer={
        adj ? (
          <div className="flex justify-end">
            <Button onClick={close}>Done</Button>
          </div>
        ) : (
          <div className="flex w-full flex-wrap items-center justify-between gap-2">
            <Button
              variant="ghost"
              size="sm"
              disabled={unmark.isPending || restock.isPending}
              onClick={() =>
                unmark.mutate(markId, {
                  onSuccess: () => {
                    toast.info("Unmarked — the draft reduction was removed too.");
                    close();
                  },
                  onError: (e) => toast.error(e.message),
                })
              }
            >
              It was a mistake — just unmark
            </Button>
            <Button disabled={restock.isPending} onClick={submit}>
              {restock.isPending ? <Spinner size={16} /> : "Confirm"}
            </Button>
          </div>
        )
      }
    >
      {adj ? (
        <div className="flex flex-col gap-2 text-[13.5px]">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={adj.direction === "add" ? "forest" : "gold"}>
              {adj.direction === "add" ? "+" : "−"}
              {fmtQty(adj.qty)} on the floor
            </Badge>
            <WriteStatusChip
              status={adj.status}
              error={adj.error}
              createdLabel={adj.picking_name || "in Odoo"}
            />
          </div>
          <OdooLink url={adj.url} name={adj.picking_name} />
          <p className="text-[12.5px] text-on-surface-variant">
            Draft only — someone reviews and validates it in Odoo.
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-[13.5px] leading-5 text-on-surface-variant">
            Odoo shows <b>{fmtQty(item.floor_qty)}</b> on the floor (as of the last stock
            sync). Count the shelf and enter what's actually there — the difference becomes a
            draft <b>“Adding Qty”</b> or <b>reduction</b>. Leave it blank to just unmark.
          </p>
          <Input
            inputMode="numeric"
            value={counted}
            onChange={(e) => setCounted(e.target.value.replace(/[^0-9.]/g, ""))}
            placeholder="Counted quantity (optional)"
            aria-label="Counted quantity"
          />
        </div>
      )}
    </Dialog>
  );
}

export function OutOfStockPage() {
  const { data: items, isLoading } = useOosList();
  const [markOpen, setMarkOpen] = useState(false);
  // page-level: the list refetch removes the row (and would unmount a dialog
  // nested inside it) the moment the mark is gone — the dialog outlives that
  const [restockTarget, setRestockTarget] = useState<OosItemOut | null>(null);
  const [search, setSearch] = useState("");

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return items ?? [];
    return (items ?? []).filter(
      (i) =>
        i.name.toLowerCase().includes(q) ||
        i.sku.toLowerCase().includes(q) ||
        i.barcode.toLowerCase().includes(q) ||
        i.category.toLowerCase().includes(q),
    );
  }, [items, search]);

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader
        title="Out of stock"
        subtitle="What the floor is out of — Odoo's zeros plus anything the team marked. Marking renders the draft reduction that cleans up phantom counts."
        actions={
          <Button onClick={() => setMarkOpen(true)}>Mark item out of stock</Button>
        }
      />
      <Input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search by name, SKU, category…"
        aria-label="Search out-of-stock items"
        className="mb-3 w-full"
      />
      {isLoading ? (
        <div className="grid place-items-center py-24">
          <Spinner size={24} />
        </div>
      ) : !items?.length ? (
        <EmptyState
          title="Nothing's out"
          hint="Products land here when Odoo shows zero on the floor, or when the team marks an empty shelf."
        />
      ) : visible.length === 0 ? (
        <div className="py-16 text-center text-sm text-on-surface-variant">
          Nothing here matches “{search.trim()}”.
        </div>
      ) : (
        <ul className="stagger-children flex flex-col gap-2 pb-8">
          {visible.map((item) => (
            <OosRow
              key={`${item.product_id}-${item.mark?.id ?? "z"}`}
              item={item}
              onBackInStock={() => setRestockTarget(item)}
            />
          ))}
        </ul>
      )}
      <MarkDialog open={markOpen} onClose={() => setMarkOpen(false)} />
      {restockTarget?.mark && (
        <BackInStockDialog
          open
          onClose={() => setRestockTarget(null)}
          item={restockTarget}
          markId={restockTarget.mark.id}
        />
      )}
    </div>
  );
}
