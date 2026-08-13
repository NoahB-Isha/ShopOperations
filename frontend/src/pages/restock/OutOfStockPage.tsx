/* The out-of-stock page. Three scopes (the old Availability page's filter,
   merged in here):

   - Floor (default for floor roles): the actionable board — Odoo's floor
     zeros plus items the team MARKED out; marking renders the draft
     "USA-III: Inventory Adj Reduction" picking a human validates in Odoo.
   - Everywhere / Warehouse: the org-wide OOS lists over the stock snapshot,
     read-only, with "last in stock" and incoming labels. */
import { usePersistedState } from "../../persist";
import { useMemo, useState } from "react";
import {
  useAvailabilityOos,
  useMarkOos,
  useOosList,
  useRestockOosMark,
  useUnmarkOos,
  type OosRestockResult,
} from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import type { AvailabilityItemOut, OosItemOut } from "../../api/types";
import {
  Badge,
  Button,
  Card,
  ContextMenu,
  Dialog,
  EmptyState,
  Input,
  PageHeader,
  Spinner,
  SwipeBackdrop,
  Textarea,
  useContextMenu,
  useSwipeRow,
  useToast,
} from "../../design";
import { LowCountHint, OdooLink, ProductPicker, WriteStatusChip, fmtQty, fmtWhen, productCode, type PickedLine } from "../shared/OpsBits";
import type { ActionBox } from "../../design";
import { matchesSearch } from "../../search";
import { addToDraft } from "../../transferDraft";
import { boxAt, centerOf, flyToBubble } from "../../shell/flyToBubble";
import { useSillyLabel } from "../../silly";

function MarkDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [picked, setPicked] = useState<PickedLine | null>(null);
  const s = useSillyLabel();
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
          placeholder={s("Search the item that's actually out…")}
        />
      )}
    </Dialog>
  );
}

function OosRow({
  item,
  onBackInStock,
  onAdd,
  onContextMenu,
}: {
  item: OosItemOut;
  onBackInStock: () => void;
  /** swipe right / right-click — the empty shelf becomes a transfer line */
  onAdd?: (from: ActionBox) => void;
  onContextMenu?: (e: React.MouseEvent) => void;
}) {
  const m = item.mark;
  const swipe = useSwipeRow({ onRight: onAdd });
  return (
    <li className="relative overflow-hidden rounded-(--radius-lg)">
      <SwipeBackdrop side="left" label="Add to transfer" dx={swipe.dx} />
      <div
        {...swipe.handlers}
        onContextMenu={onContextMenu}
        style={swipe.motionStyle}
        className="rounded-(--radius-lg) bg-surface-container-low px-4 py-3.5"
      >
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
      </div>
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

const SCOPES = [
  { key: "floor", label: "Floor" },
  { key: "org", label: "Everywhere" },
  { key: "bwhse", label: "Warehouse" },
] as const;
type Scope = (typeof SCOPES)[number]["key"];

/** Read-only row for the org / warehouse scopes (snapshot lists). */
function ScopeRow({ item }: { item: AvailabilityItemOut }) {
  return (
    <li className="rounded-(--radius-lg) bg-surface-container-low px-4 py-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-[15px] font-medium">{item.name}</div>
          <div className="mt-0.5 text-[12px] tabular-nums text-on-surface-variant">
            <span className="font-mono">{productCode(item.barcode, item.sku)}</span> · floor{" "}
            {fmtQty(item.floor_qty)} · whse {fmtQty(item.bwhse_qty)}
            <LowCountHint qty={item.bwhse_qty} /> · {item.incoming_label}
          </div>
        </div>
        <span className="shrink-0 text-right text-[11.5px] text-on-surface-variant">
          {item.last_in_stock_on ? (
            <>
              last in stock
              <br />
              {item.last_in_stock_on}
            </>
          ) : (
            "no stock history yet"
          )}
        </span>
      </div>
    </li>
  );
}

export function OutOfStockPage() {
  const { roles } = useAuth();
  const isFloorRole = roles.has("shoppe_floor") || roles.has("floor_rotating");
  // floor folk land on their board; warehouse on their shelves; admin org-wide
  const [scope, setScope] = usePersistedState<Scope>(
    "oos.scope",
    isFloorRole ? "floor" : roles.has("warehouse") ? "bwhse" : "org",
  );
  const boardMode = scope === "floor";
  // never-stocked items (no snapshot has ever seen them in stock) are hidden
  // from the scoped lists by default — this is the peek switch
  const [includeNeverStocked, setIncludeNeverStocked] = usePersistedState("oos.includeNever", false);
  const { data: items, isLoading } = useOosList();
  const scoped = useAvailabilityOos(scope, !boardMode, includeNeverStocked);
  const [markOpen, setMarkOpen] = useState(false);
  // page-level: the list refetch removes the row (and would unmount a dialog
  // nested inside it) the moment the mark is gone — the dialog outlives that
  const [restockTarget, setRestockTarget] = useState<OosItemOut | null>(null);
  const [search, setSearch] = usePersistedState("oos.search", "");

  /* An empty shelf is usually a transfer waiting to happen — same gesture as
     the restock list (swipe right on a phone, right-click on a desk). The
     quantity is a placeholder: adjust it on the transfer itself. */
  const canRequest = roles.has("shoppe_floor") || roles.has("admin");
  const menu = useContextMenu();
  const addToTransfer = (item: OosItemOut, from?: ActionBox) => {
    addToDraft({
      product_id: item.product_id,
      sku: item.sku,
      barcode: item.barcode,
      name: item.name,
      category: item.category,
      qty: 1,
      floor_qty: item.floor_qty,
      bwhse_qty: item.bwhse_qty,
      case_size: 1,
    });
    flyToBubble(from ?? centerOf(), 1);
  };

  const visible = useMemo(
    () =>
      (items ?? []).filter((i) =>
        matchesSearch(search, i.name, i.sku, i.barcode, i.category),
      ),
    [items, search],
  );

  const scopedVisible = useMemo(
    () =>
      (scoped.data ?? []).filter((i) =>
        matchesSearch(search, i.name, i.sku, i.barcode, i.category),
      ),
    [scoped.data, search],
  );

  const loading = boardMode ? isLoading : scoped.isLoading;
  const empty = boardMode ? !items?.length : !scoped.data?.length;

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader
        title="Out of stock"
        /* Subtitle parked at Noah's request (2026-08-11) — the scope chips
           below already say which list you're looking at. Uncomment to bring
           the explanation back.
        subtitle={
          boardMode
            ? "What the floor is out of — Odoo's zeros plus anything the team marked. Marking renders the draft reduction that cleans up phantom counts."
            : scope === "bwhse"
              ? "Nothing left at the warehouse — floor stock doesn't hide a warehouse-out."
              : "Fully out everywhere — warehouse, floor, and staging together."
        }
        */
        actions={
          boardMode && (
            <Button onClick={() => setMarkOpen(true)}>Mark item out of stock</Button>
          )
        }
      />
      <div className="mb-3 flex gap-1 rounded-full bg-surface-container p-1" role="tablist">
        {SCOPES.map((s) => (
          <button
            key={s.key}
            role="tab"
            aria-selected={scope === s.key}
            data-testid={`scope-${s.key}`}
            onClick={() => setScope(s.key)}
            className={`flex h-9 grow items-center justify-center rounded-full text-[13px] font-semibold transition-colors ${
              scope === s.key
                ? "bg-secondary-container text-on-secondary-container"
                : "text-on-surface-variant hover:bg-on-surface/8"
            }`}
          >
            {s.label}
          </button>
        ))}
      </div>
      <Input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search by name, SKU, category…"
        aria-label="Search out-of-stock items"
        className="mb-3 w-full"
      />
      {!boardMode && (
        <div className="mb-3 flex items-center justify-end">
          <button
            onClick={() => setIncludeNeverStocked((v) => !v)}
            title="Items the app has never seen in stock — mostly fast sellers, digital goods, and variants never carried. Hidden to keep this list usable."
            className={`rounded-full px-3.5 py-1.5 text-[12.5px] font-semibold transition-colors ${
              includeNeverStocked
                ? "bg-secondary-container text-on-secondary-container"
                : "text-on-surface-variant hover:bg-on-surface/8"
            }`}
          >
            {includeNeverStocked ? "Showing never-stocked" : "Include never-stocked"}
          </button>
        </div>
      )}
      {loading ? (
        <div className="grid place-items-center py-24">
          <Spinner size={24} />
        </div>
      ) : empty ? (
        <EmptyState
          title="Nothing's out"
          hint={
            boardMode
              ? "Products land here when Odoo shows zero on the floor, or when the team marks an empty shelf."
              : "Nothing is fully out of stock in this scope. 🎉"
          }
        />
      ) : boardMode ? (
        visible.length === 0 ? (
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
                onAdd={canRequest ? (from) => addToTransfer(item, from) : undefined}
                onContextMenu={
                  canRequest
                    ? (e) =>
                        menu.open(e, [
                          {
                            label: "Add to transfer",
                            onSelect: () => addToTransfer(item, boxAt(e.clientX, e.clientY)),
                          },
                        ])
                    : undefined
                }
              />
            ))}
          </ul>
        )
      ) : scopedVisible.length === 0 ? (
        <div className="py-16 text-center text-sm text-on-surface-variant">
          Nothing here matches “{search.trim()}”.
        </div>
      ) : (
        <ul className="stagger-children flex flex-col gap-2 pb-8">
          {scopedVisible.map((item) => (
            <ScopeRow key={item.product_id} item={item} />
          ))}
        </ul>
      )}
      <ContextMenu menu={menu.menu} onClose={menu.close} />
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
