/* The city-center order form — a well-made checkout that fits in one thumb.

   Autofilled who/where, a searchable menu scoped to the center's granted
   catalogs, honest availability on every row, a sticky cart bar, and a
   review sheet with the gentle reasonability check before the big button.
   Duplicate orders arrive prefilled via router state from Order history. */
import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  useOrderCatalog,
  useOrderContext,
  usePlaceCenterOrder,
  usePreviewReasonability,
} from "../../api/hooks";
import type { CatalogItemOut, CenterOrderOut, ReasonPreviewOut } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { matchesSearch } from "../../search";
import {
  Badge,
  Button,
  Card,
  ContextMenu,
  Dialog,
  EmptyState,
  Input,
  Spinner,
  Textarea,
  isInteractiveTarget,
  useContextMenu,
  useRowSelection,
  useToast,
} from "../../design";
import { QtyInput, SetQtyDialog, productCode } from "../shared/OpsBits";
import { AvailabilityBadge, ReasonBadgeChip, money } from "./orderBits";

export interface OrderPrefill {
  center_id: number;
  notes: string;
  duplicate_of_id: number | null;
  lines: { product_id: number; qty: number }[];
}

function ItemRow({
  item,
  qty,
  onQty,
  selected,
  onSelect,
  onMenu,
  qtyEls,
  onQtyEnter,
}: {
  item: CatalogItemOut;
  qty: number;
  onQty: (q: number) => void;
  selected: boolean;
  onSelect: (e: React.MouseEvent) => void;
  onMenu: (e: React.MouseEvent) => void;
  /** live registry of qty inputs so the Enter flow can focus them */
  qtyEls: React.RefObject<Map<number, HTMLInputElement>>;
  onQtyEnter: () => void;
}) {
  const inCart = qty > 0;
  return (
    <li
      onMouseDown={(e) => e.shiftKey && e.preventDefault()}
      onClick={(e) => {
        if (!isInteractiveTarget(e)) onSelect(e);
      }}
      onContextMenu={onMenu}
      aria-selected={selected}
      className={`flex items-center justify-between gap-3 rounded-(--radius-md) px-3 py-2.5
        transition-colors ${selected ? "bg-secondary-container/50" : inCart ? "bg-primary-container/40" : ""}`}
    >
      <div className="min-w-0">
        <div className="truncate text-[14px] font-medium text-on-surface">{item.name}</div>
        <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[12px] text-on-surface-variant">
          <span className="font-semibold tabular-nums">{money(item.retail_price)}</span>
          <span className="font-mono">{productCode(item.barcode, item.sku)}</span>
          <AvailabilityBadge a={item.availability} />
          {item.case_size > 1 && <span>case of {item.case_size}</span>}
        </div>
      </div>
      <div className="shrink-0">
        {inCart ? (
          <QtyInput
            value={qty}
            onChange={onQty}
            ariaLabel={`Quantity for ${item.name}`}
            inputRef={(el) => {
              if (el) qtyEls.current.set(item.product_id, el);
              else qtyEls.current.delete(item.product_id);
            }}
            onEnter={onQtyEnter}
          />
        ) : (
          <Button
            variant="secondary"
            size="sm"
            aria-label={`Add ${item.name}`}
            onClick={() => onQty(item.case_size > 1 ? item.case_size : 1)}
          >
            Add
          </Button>
        )}
      </div>
    </li>
  );
}

function ReviewSheet({
  open,
  onClose,
  centerId,
  items,
  qtys,
  setQty,
  notes,
  setNotes,
  duplicateOfId,
  onPlaced,
}: {
  open: boolean;
  onClose: () => void;
  centerId: number;
  items: CatalogItemOut[];
  qtys: Record<number, number>;
  setQty: (productId: number, q: number) => void;
  notes: string;
  setNotes: (v: string) => void;
  duplicateOfId: number | null;
  onPlaced: (order: CenterOrderOut) => void;
}) {
  const toast = useToast();
  const place = usePlaceCenterOrder();
  const preview = usePreviewReasonability();
  const [check, setCheck] = useState<ReasonPreviewOut | null>(null);
  const debounce = useRef<ReturnType<typeof setTimeout>>(undefined);

  const lines = useMemo(
    () =>
      items
        .filter((it) => (qtys[it.product_id] ?? 0) > 0)
        .map((it) => ({ item: it, qty: qtys[it.product_id] })),
    [items, qtys],
  );
  const linesKey = lines.map((l) => `${l.item.product_id}:${l.qty}`).join(",");

  // the gentle check, debounced while quantities move (rules-only, instant)
  useEffect(() => {
    if (!open || lines.length === 0) return;
    clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      preview.mutate(
        {
          center_id: centerId,
          lines: lines.map((l) => ({ product_id: l.item.product_id, qty: l.qty })),
        },
        { onSuccess: setCheck, onError: () => setCheck(null) },
      );
    }, 350);
    return () => clearTimeout(debounce.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, linesKey, centerId]);

  const total = lines.reduce((sum, l) => sum + l.qty * l.item.retail_price, 0);

  const submit = () =>
    place.mutate(
      {
        center_id: centerId,
        notes,
        duplicate_of_id: duplicateOfId,
        lines: lines.map((l) => ({ product_id: l.item.product_id, qty: l.qty })),
      },
      {
        onSuccess: (order) => onPlaced(order as CenterOrderOut),
        onError: (e) => toast.error(e instanceof Error ? e.message : "Could not place the order."),
      },
    );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Review order"
      footer={
        <div className="flex w-full items-center justify-between gap-3">
          <div className="whitespace-nowrap text-[13px] text-on-surface-variant">
            {lines.length} item{lines.length === 1 ? "" : "s"} ·{" "}
            <span className="font-bold text-on-surface">{money(total)}</span>
          </div>
          <div className="flex shrink-0 gap-2">
            <Button variant="ghost" onClick={onClose}>
              Back
            </Button>
            <Button onClick={submit} disabled={place.isPending || lines.length === 0}>
              {place.isPending ? <Spinner size={16} /> : "Place order"}
            </Button>
          </div>
        </div>
      }
    >
      <ul className="flex flex-col gap-1">
        {lines.map(({ item, qty }) => {
          const badges = check?.lines?.[String(item.product_id)] ?? [];
          return (
            <li key={item.product_id} className="rounded-(--radius-md) bg-surface-container px-3 py-2">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-[14px] font-medium">{item.name}</div>
                  <div className="text-[12px] text-on-surface-variant">
                    {money(item.retail_price)} each
                  </div>
                </div>
                <QtyInput
                  value={qty}
                  onChange={(q) => setQty(item.product_id, q)}
                  ariaLabel={`Quantity for ${item.name}`}
                />
              </div>
              {badges.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {badges.map((b) => (
                    <ReasonBadgeChip key={b.code} b={b} />
                  ))}
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {check && (check.level === "warn" || check.level === "info") && (
        <div
          data-testid="reasonability-summary"
          className={`mt-3 rounded-(--radius-md) px-3.5 py-2.5 text-[13px] ${
            check.level === "warn"
              ? "bg-warn-container text-on-warn-container"
              : "bg-secondary-container text-on-secondary-container"
          }`}
        >
          <span className="font-bold">{check.level === "warn" ? "Worth a look: " : "Note: "}</span>
          {check.summary}
          {check.order_badges.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {check.order_badges.map((b) => (
                <ReasonBadgeChip key={b.code} b={b} />
              ))}
            </div>
          )}
          <div className="mt-1 text-[11.5px] opacity-80">
            Just a heads-up from the order checker — your coordinator decides.
          </div>
        </div>
      )}

      <div className="mt-3">
        <Textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Notes for your coordinator (optional)"
          aria-label="Order notes"
          rows={2}
        />
      </div>
    </Dialog>
  );
}

function PlacedScreen({ order, onAgain }: { order: CenterOrderOut; onAgain: () => void }) {
  const navigate = useNavigate();
  return (
    <div className="mx-auto max-w-md">
      <Card className="flex flex-col items-center gap-3 py-10 text-center">
        <span className="animate-pop-in grid h-16 w-16 place-items-center rounded-full bg-success-container text-3xl">
          ✓
        </span>
        <div className="display text-[22px]">Order placed!</div>
        <div className="text-sm text-on-surface-variant">
          <span className="font-semibold text-on-surface">{order.display_name}</span> ·{" "}
          {order.totals.items} item(s) · {money(order.totals.value)}
        </div>
        <p className="max-w-70 text-[13px] text-on-surface-variant">
          Your coordinator just got a WhatsApp ping. You'll get one the moment it's approved.
        </p>
        <div className="mt-2 flex gap-2">
          <Button variant="secondary" onClick={() => navigate(`/order/${order.id}`)}>
            View order
          </Button>
          <Button variant="ghost" onClick={onAgain}>
            Place another
          </Button>
        </div>
      </Card>
    </div>
  );
}

export function PlaceOrderPage() {
  const { user } = useAuth();
  const location = useLocation();
  const prefill = (location.state as { prefill?: OrderPrefill } | null)?.prefill;
  const toast = useToast();

  const { data: centers, isLoading: loadingCenters } = useOrderContext();
  const [centerId, setCenterId] = useState<number | null>(prefill?.center_id ?? null);
  useEffect(() => {
    if (centerId === null && centers?.length) {
      // prefer a center that actually has a catalog
      const withItems = centers.find((c) => c.item_count > 0);
      setCenterId((withItems ?? centers[0]).id);
    }
  }, [centers, centerId]);

  const { data: catalog, isLoading: loadingCatalog } = useOrderCatalog(centerId);

  const [qtys, setQtys] = useState<Record<number, number>>({});
  const [notes, setNotes] = useState(prefill?.notes ?? "");
  const [duplicateOfId, setDuplicateOfId] = useState<number | null>(
    prefill?.duplicate_of_id ?? null,
  );
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [reviewOpen, setReviewOpen] = useState(false);
  const [placed, setPlaced] = useState<CenterOrderOut | null>(null);
  const prefillApplied = useRef(false);

  // apply the duplicate prefill once the catalog is in (skip vanished items)
  useEffect(() => {
    if (!prefill || prefillApplied.current || !catalog) return;
    prefillApplied.current = true;
    const available = new Set(catalog.items.map((i) => i.product_id));
    const next: Record<number, number> = {};
    let dropped = 0;
    for (const line of prefill.lines) {
      if (available.has(line.product_id)) next[line.product_id] = line.qty;
      else dropped += 1;
    }
    setQtys(next);
    if (dropped > 0) {
      toast.info(`${dropped} item(s) from the old order aren't on your catalog anymore.`);
    }
  }, [prefill, catalog, toast]);

  const setQty = (productId: number, q: number) =>
    setQtys((prev) => {
      const next = { ...prev };
      if (q <= 0) delete next[productId];
      else next[productId] = q;
      return next;
    });

  const categories = useMemo(
    () => Array.from(new Set((catalog?.items ?? []).map((i) => i.category).filter(Boolean))),
    [catalog],
  );
  const visible = useMemo(() => {
    return (catalog?.items ?? []).filter(
      (i) =>
        (!category || i.category === category) &&
        matchesSearch(search, i.name, i.sku, i.barcode),
    );
  }, [catalog, search, category]);

  const cartCount = Object.keys(qtys).length;
  const cartTotal = (catalog?.items ?? []).reduce(
    (sum, i) => sum + (qtys[i.product_id] ?? 0) * i.retail_price,
    0,
  );

  // keyboard flow: Enter adds the top match and jumps to its qty; Enter in
  // the qty bounces back to search — type, enter, qty, enter, repeat.
  const searchRef = useRef<HTMLInputElement>(null);
  const qtyEls = useRef(new Map<number, HTMLInputElement>());
  const focusQty = (productId: number) =>
    // rAF: a just-added item's qty input mounts on this render
    requestAnimationFrame(() => qtyEls.current.get(productId)?.focus());
  const addFirstMatch = () => {
    const first = visible[0];
    if (!first) return;
    if (!(qtys[first.product_id] ?? 0)) {
      setQty(first.product_id, first.case_size > 1 ? first.case_size : 1);
    }
    focusQty(first.product_id);
  };
  const backToSearch = () => {
    searchRef.current?.focus();
    searchRef.current?.select();
  };

  const selection = useRowSelection(visible.map((i) => i.product_id));
  const menu = useContextMenu();
  const [setQtyFor, setSetQtyFor] = useState<Set<number> | null>(null);
  const rowMenu = (productId: number, e: React.MouseEvent) => {
    const ids = selection.forContext(productId);
    const byId = new Map((catalog?.items ?? []).map((i) => [i.product_id, i]));
    const inCart = [...ids].filter((id) => (qtys[id] ?? 0) > 0);
    const notInCart = [...ids].filter((id) => !(qtys[id] ?? 0));
    menu.open(e, [
      {
        label: `Add ${notInCart.length} to order`,
        disabled: notInCart.length === 0,
        onSelect: () =>
          notInCart.forEach((id) => {
            const it = byId.get(id);
            setQty(id, it && it.case_size > 1 ? it.case_size : 1);
          }),
      },
      {
        label: `Set quantity… (${ids.size})`,
        onSelect: () => setSetQtyFor(new Set(ids)),
      },
      {
        label: `Remove ${inCart.length} from order`,
        danger: true,
        disabled: inCart.length === 0,
        onSelect: () => inCart.forEach((id) => setQty(id, 0)),
      },
    ]);
  };

  if (placed) {
    return (
      <PlacedScreen
        order={placed}
        onAgain={() => {
          setPlaced(null);
          setQtys({});
          setNotes("");
          setDuplicateOfId(null);
          setReviewOpen(false);
        }}
      />
    );
  }

  if (loadingCenters) {
    return (
      <div className="grid place-items-center py-20">
        <Spinner size={22} />
      </div>
    );
  }
  if (!centers?.length) {
    return (
      <EmptyState
        title="No ordering set up yet"
        hint="You're not linked to a center or department. Ask the office to add you."
      />
    );
  }

  const center = centers.find((c) => c.id === centerId);
  const isDept = center?.zone_kind === "departments";

  return (
    <div className="mx-auto max-w-2xl">
      {/* who + where — autofilled, zero typing */}
      <div className="mb-4 flex flex-wrap items-center gap-2 text-[14px] text-on-surface-variant">
        <span>
          Ordering as <span className="font-semibold text-on-surface">{user?.display_name || user?.email}</span>
        </span>
        <span aria-hidden>·</span>
        {centers.length === 1 ? (
          <span className="font-semibold text-on-surface">{center?.name}</span>
        ) : (
          <span className="flex flex-wrap gap-1.5">
            {centers.map((c) => (
              <button
                key={c.id}
                onClick={() => {
                  setCenterId(c.id);
                  setQtys({});
                  setCategory("");
                }}
                className={`state-layer rounded-full px-3 py-1 text-[13px] font-semibold ${
                  c.id === centerId
                    ? "bg-secondary-container text-on-secondary-container"
                    : "border border-outline-variant text-on-surface-variant"
                }`}
              >
                {c.name}
              </button>
            ))}
          </span>
        )}
        {isDept && <Badge tone="tertiary">fulfilled from the Shoppe floor</Badge>}
      </div>

      <Input
        ref={searchRef}
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && search.trim() !== "") {
            e.preventDefault();
            addFirstMatch();
          }
        }}
        placeholder="Search your catalog…"
        aria-label="Search your catalog"
        className="w-full"
      />

      {categories.length > 1 && (
        <div className="scrollbar-none -mx-1 mt-3 flex gap-1.5 overflow-x-auto px-1 pb-1">
          <button
            onClick={() => setCategory("")}
            className={`shrink-0 rounded-full px-3 py-1.5 text-[12.5px] font-semibold ${
              category === ""
                ? "bg-secondary-container text-on-secondary-container"
                : "border border-outline-variant text-on-surface-variant"
            }`}
          >
            All
          </button>
          {categories.map((c) => (
            <button
              key={c}
              onClick={() => setCategory(c === category ? "" : c)}
              className={`shrink-0 rounded-full px-3 py-1.5 text-[12.5px] font-semibold ${
                category === c
                  ? "bg-secondary-container text-on-secondary-container"
                  : "border border-outline-variant text-on-surface-variant"
              }`}
            >
              {c}
            </button>
          ))}
        </div>
      )}

      {loadingCatalog ? (
        <div className="grid place-items-center py-20">
          <Spinner size={22} />
        </div>
      ) : catalog && catalog.items.length === 0 ? (
        <EmptyState
          title="No catalog granted yet"
          hint={
            isDept
              ? "No department items are marked orderable yet — ask the office."
              : "Your coordinator hasn't shared a catalog with this center yet."
          }
        />
      ) : (
        <ul className="mt-2 flex flex-col divide-y divide-outline-variant/40">
          {visible.map((item) => (
            <ItemRow
              key={item.product_id}
              item={item}
              qty={qtys[item.product_id] ?? 0}
              onQty={(q) => setQty(item.product_id, q)}
              selected={selection.selected.has(item.product_id)}
              onSelect={(e) => selection.click(item.product_id, e)}
              onMenu={(e) => rowMenu(item.product_id, e)}
              qtyEls={qtyEls}
              onQtyEnter={backToSearch}
            />
          ))}
          {visible.length === 0 && (
            <li className="py-10 text-center text-sm text-on-surface-variant">
              Nothing matches “{search.trim()}”{category ? ` in ${category}` : ""}.
            </li>
          )}
        </ul>
      )}

      {/* the floating cart bar must never hide the last rows' Add buttons */}
      {cartCount > 0 && <div className="h-24" aria-hidden />}

      {/* sticky cart bar — floats above the bottom nav on phones */}
      {cartCount > 0 && (
        <div className="fixed inset-x-0 bottom-[4.7rem] z-20 px-4 md:bottom-6">
          <div className="mx-auto max-w-2xl">
            <button
              data-testid="review-order"
              onClick={() => setReviewOpen(true)}
              className="state-layer animate-pop-in flex h-14 w-full items-center justify-between
                rounded-(--radius-lg) bg-primary px-5 text-on-primary shadow-(--shadow-e3)
                transition-transform duration-300 ease-(--ease-spring) hover:scale-[1.015]"
            >
              <span className="text-sm font-bold">
                {cartCount} item{cartCount === 1 ? "" : "s"} · {money(cartTotal)}
              </span>
              <span className="text-sm font-bold">Review order →</span>
            </button>
          </div>
        </div>
      )}

      <ContextMenu menu={menu.menu} onClose={menu.close} />
      {setQtyFor && (
        <SetQtyDialog
          count={setQtyFor.size}
          min={0}
          onApply={(q) => setQtyFor.forEach((id) => setQty(id, q))}
          onClose={() => setSetQtyFor(null)}
        />
      )}

      {centerId !== null && catalog && (
        <ReviewSheet
          open={reviewOpen}
          onClose={() => setReviewOpen(false)}
          centerId={centerId}
          items={catalog.items}
          qtys={qtys}
          setQty={setQty}
          notes={notes}
          setNotes={setNotes}
          duplicateOfId={duplicateOfId}
          onPlaced={(order) => {
            setReviewOpen(false);
            setPlaced(order);
          }}
        />
      )}
    </div>
  );
}
