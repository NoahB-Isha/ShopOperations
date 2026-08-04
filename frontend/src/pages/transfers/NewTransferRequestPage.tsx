/* Floor: build a BWHSE→Floor request on a phone in the aisle — search,
   tap to add, adjust quantities, send. Floor vs warehouse quantities are
   right there so nobody requests what the warehouse doesn't have.
   The restock page's "New transfer from these items" arrives prefilled
   via router state.

   Keyboard flow for fast entry: type, Enter (adds the top result and jumps
   to its qty), type the qty, Enter (back to search). Rows multi-select with
   shift/cmd-click; right-click for set-qty / remove. */
import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { clearPersisted, usePersistedState } from "../../persist";
import { useCreateTransferRequest } from "../../api/hooks";
import type { TransferRequestOut } from "../../api/types";
import {
  Button,
  Card,
  ContextMenu,
  EmptyState,
  Field,
  PageHeader,
  Textarea,
  isInteractiveTarget,
  useContextMenu,
  useRowSelection,
  useToast,
} from "../../design";
import {
  LowCountHint,
  ProductPicker,
  QtyInput,
  SetQtyDialog,
  fmtQty,
  productCode,
  type PickedLine,
} from "../shared/OpsBits";

export interface TransferPrefill {
  notes?: string;
  lines: PickedLine[];
}

export function NewTransferRequestPage() {
  const location = useLocation();
  const prefill = (location.state as { prefill?: TransferPrefill } | null)?.prefill;
  // the draft survives menu navigation (a half-built pull list is real work);
  // a prefill (restock / detail-page selection) deliberately replaces it
  const [lines, setLines] = usePersistedState<PickedLine[]>("transfer.new.lines", []);
  const [notes, setNotes] = usePersistedState("transfer.new.notes", "");
  const prefillApplied = useRef(false);
  useEffect(() => {
    if (!prefill || prefillApplied.current) return;
    prefillApplied.current = true;
    setLines(prefill.lines);
    setNotes(prefill.notes ?? "");
  }, [prefill, setLines, setNotes]);
  const create = useCreateTransferRequest();
  const toast = useToast();
  const navigate = useNavigate();

  const searchRef = useRef<HTMLInputElement>(null);
  const pendingQtyFocus = useRef<number | null>(null);
  const selection = useRowSelection(lines.map((l) => l.product_id));
  const menu = useContextMenu();
  const [setQtyFor, setSetQtyFor] = useState<Set<number> | null>(null);

  const pickedIds = useMemo(() => new Set(lines.map((l) => l.product_id)), [lines]);
  const totalQty = lines.reduce((sum, l) => sum + l.qty, 0);

  // taps ask for the quantity up front, then hand focus back to search —
  // the phone-in-the-aisle loop. Enter keeps the inline keyboard flow.
  const [tapPick, setTapPick] = useState<PickedLine | null>(null);
  const addLine = (line: PickedLine, viaEnter?: boolean) => {
    if (viaEnter) {
      pendingQtyFocus.current = line.product_id;
      setLines((prev) => [...prev, line]);
    } else {
      setTapPick(line);
    }
  };
  const backToSearch = () => {
    searchRef.current?.focus();
    searchRef.current?.select();
  };
  const removeIds = (ids: Set<number>) => {
    setLines((prev) => prev.filter((l) => !ids.has(l.product_id)));
    selection.clear();
  };
  const applyQty = (ids: Set<number>, qty: number) =>
    setLines((prev) => prev.map((l) => (ids.has(l.product_id) ? { ...l, qty } : l)));

  const rowMenu = (productId: number, e: React.MouseEvent) => {
    const ids = selection.forContext(productId);
    menu.open(e, [
      {
        label: `Set quantity… (${ids.size})`,
        onSelect: () => setSetQtyFor(new Set(ids)),
      },
      {
        label: `Remove ${ids.size} item${ids.size === 1 ? "" : "s"}`,
        danger: true,
        onSelect: () => removeIds(new Set(ids)),
      },
    ]);
  };

  const submit = () =>
    create.mutate(
      {
        notes: notes.trim(),
        lines: lines.map(({ product_id, qty }) => ({ product_id, qty })),
      },
      {
        onSuccess: (req) => {
          // imperative: a setState's write effect can miss when navigation
          // unmounts the page — the stored draft must not resurrect
          clearPersisted("transfer.new.lines", "transfer.new.notes");
          toast.success("Request placed — the warehouse board has it.");
          navigate(`/transfer-requests/${(req as TransferRequestOut).id}`);
        },
        onError: (e) => toast.error(e.message),
      },
    );

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader
        title="Request stock"
        subtitle="From the Blue Warehouse to the Shoppe floor — sending it renders the draft transfer in Odoo immediately."
        actions={
          <Button variant="ghost" onClick={() => navigate("/transfer-requests")}>
            Back
          </Button>
        }
      />

      <Card className="mb-4">
        <ProductPicker pickedIds={pickedIds} onPick={addLine} inputRef={searchRef} />
      </Card>

      {lines.length === 0 ? (
        <EmptyState
          title="Nothing picked yet"
          hint="Search above — tap an item, or press Enter to add the top match and type its quantity."
        />
      ) : (
        <Card pad={false} className="mb-4">
          <ul>
            {lines.map((line) => {
              const isSelected = selection.selected.has(line.product_id);
              return (
                <li
                  key={line.product_id}
                  onMouseDown={(e) => e.shiftKey && e.preventDefault()}
                  onClick={(e) => {
                    if (!isInteractiveTarget(e)) selection.click(line.product_id, e);
                  }}
                  onContextMenu={(e) => rowMenu(line.product_id, e)}
                  className={`flex cursor-default items-center justify-between gap-2 border-b
                    border-outline-variant/50 px-4 py-3 transition-colors last:border-b-0
                    ${isSelected ? "bg-secondary-container/40" : ""}`}
                  aria-selected={isSelected}
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">{line.name}</div>
                    <div className="flex flex-wrap items-center gap-x-2 text-[12px] tabular-nums text-on-surface-variant">
                      <span className="font-mono">{productCode(line.barcode, line.sku)}</span>
                      {line.case_size > 1 && (
                        <span className="whitespace-nowrap">case of {line.case_size}</span>
                      )}
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
                      inputRef={(el) => {
                        if (el && pendingQtyFocus.current === line.product_id) {
                          pendingQtyFocus.current = null;
                          el.focus();
                        }
                      }}
                      onEnter={backToSearch}
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
                      onClick={() => removeIds(new Set([line.product_id]))}
                    >
                      ✕
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
          <div className="flex items-center justify-between bg-surface-container px-4 py-2.5 text-sm">
            <span className="text-on-surface-variant">
              {lines.length} item{lines.length === 1 ? "" : "s"}
              {selection.selected.size > 1 && ` · ${selection.selected.size} selected`}
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

      <ContextMenu menu={menu.menu} onClose={menu.close} />
      {tapPick && (
        <SetQtyDialog
          count={1}
          title={`How many — ${tapPick.name}?`}
          help={tapPick.case_size > 1 ? `Comes in cases of ${tapPick.case_size}.` : null}
          initial={tapPick.case_size > 1 ? tapPick.case_size : 1}
          min={1}
          applyLabel="Add to request"
          onApply={(qty) => setLines((prev) => [...prev, { ...tapPick, qty }])}
          onClose={() => {
            setTapPick(null);
            backToSearch();
          }}
        />
      )}
      {setQtyFor && (
        <SetQtyDialog
          count={setQtyFor.size}
          min={1}
          onApply={(qty) => applyQty(setQtyFor, qty)}
          onClose={() => setSetQtyFor(null)}
        />
      )}
    </div>
  );
}
