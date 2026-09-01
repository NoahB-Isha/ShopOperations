/* Stock status — the "Out of stock" tab. A searchable, READ-ONLY list in
   three scopes (the old Availability page's filter, merged in here):

   - Floor (default for floor roles): floor-relevant products Odoo shows at
     zero on the floor.
   - Everywhere / Warehouse: the org-wide OOS lists over the stock snapshot,
     with "last in stock" and incoming labels.

   Marking left the app 2026-08-24 — counted numbers enter only through the
   counting page. The one gesture kept: swipe right (or right-click) adds the
   empty shelf to the transfer draft. */
import { usePersistedState } from "../../persist";
import { useMemo } from "react";
import { useAvailabilityOos, useOosList } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import type { AvailabilityItemOut, OosItemOut } from "../../api/types";
import {
  ContextMenu,
  EmptyState,
  Input,
  MorphBall,
  PageHeader,
  ScrollingText,
  Spinner,
  SwipeBackdrop,
  useContextMenu,
  useSwipeRow,
} from "../../design";
import { LowCountHint, fmtQty, productCode } from "../shared/OpsBits";
import type { ActionBox } from "../../design";
import { SectionTabs } from "../shared/SectionTabs";
import { STOCK_STATUS_TABS } from "./stockStatusTabs";
import { matchesSearch } from "../../search";
import { addToDraft } from "../../transferDraft";
import { boxAt, centerOf, flyToBubble } from "../../shell/flyToBubble";

function OosRow({
  item,
  onAdd,
  onContextMenu,
}: {
  item: OosItemOut;
  /** swipe right / right-click — the empty shelf becomes a transfer line */
  onAdd?: (from: ActionBox) => void;
  onContextMenu?: (e: React.MouseEvent) => void;
}) {
  const swipe = useSwipeRow({ onRight: onAdd, morphOnRight: true });
  return (
    <li data-name-press className="relative overflow-hidden rounded-(--radius-lg)">
      <SwipeBackdrop side="left" label="Add to transfer" dx={swipe.dx} morph={swipe.morph} />
      <div
        {...swipe.handlers}
        onContextMenu={onContextMenu}
        style={swipe.motionStyle}
        className="relative rounded-(--radius-lg) bg-surface-container-low px-4 py-3.5"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <ScrollingText text={item.name} className="text-[15px] font-medium" />
            <div className="mt-0.5 text-[12px] tabular-nums text-on-surface-variant">
              <span className="font-mono">{productCode(item.barcode, item.sku)}</span> · floor{" "}
              {fmtQty(item.floor_qty)} · whse {fmtQty(item.bwhse_qty)} · {item.incoming_label}
            </div>
          </div>
        </div>
        <MorphBall progress={swipe.morph} />
      </div>
    </li>
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
    <li data-name-press className="rounded-(--radius-lg) bg-surface-container-low px-4 py-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <ScrollingText text={item.name} className="text-[15px] font-medium" />
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
  const [search, setSearch] = usePersistedState("oos.search", "");

  /* An empty shelf is usually a transfer waiting to happen — same gesture as
     the restock list (swipe right on a phone, right-click on a desk). The
     quantity is a placeholder: adjust it on the transfer itself. */
  const canRequest = roles.has("shoppe_floor") || roles.has("floor_rotating") || roles.has("admin");
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
        title="Stock status"
        /* Subtitle parked at Noah's request (2026-08-11) — the scope chips
           below already say which list you're looking at. Uncomment to bring
           the explanation back.
        subtitle={
          boardMode
            ? "What the floor is out of — Odoo's zeros over the floor-relevant range."
            : scope === "bwhse"
              ? "Nothing left at the warehouse — floor stock doesn't hide a warehouse-out."
              : "Fully out everywhere — warehouse, floor, and staging together."
        }
        */
      />
      <SectionTabs tabs={STOCK_STATUS_TABS} active="/out-of-stock" />
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
              ? "Products land here when Odoo shows zero on the floor."
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
                key={item.product_id}
                item={item}
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
    </div>
  );
}
