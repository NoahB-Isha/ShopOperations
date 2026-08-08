/* The morning restock checklists, phone-first. Floor list = the ILscripts
   accumulator (sold enough since last restock → bring more out). Back list =
   floor cover running thin vs the warehouse. Check-off resets daily. */
import { usePersistedState } from "../../persist";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  useCheckRestock,
  useResetFloorRestock,
  useRestock,
  useSnoozeRestock,
} from "../../api/hooks";
import type { RestockBackItem, RestockFloorItem, RestockOut } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { Badge, Button, Dialog, EmptyState, PageHeader, Spinner, useToast } from "../../design";
import { LowCountHint, fmtQty, productCode } from "../shared/OpsBits";

export function RestockPage() {
  const { data, isLoading } = useRestock();
  const [tab, setTab] = usePersistedState<"floor" | "back">("restock.tab", "floor");

  const floorOpen = (data?.floor ?? []).filter((i) => !i.checked).length;
  const backOpen = (data?.back ?? []).length; // no check-off — the action is a transfer

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader
        title="Restock"
        subtitle={
          data?.meta.folded_through
            ? `Sales counted through ${new Date(data.meta.folded_through + "T00:00:00").toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" })}. Checks reset every morning.`
            : "Checks reset every morning."
        }
      />

      <div className="mb-5 grid grid-cols-2 gap-1.5 rounded-full bg-surface-container p-1.5">
        {(
          [
            ["floor", "Floor", floorOpen],
            ["back", "From warehouse", backOpen],
          ] as const
        ).map(([key, label, open]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`state-layer flex items-center justify-center gap-2 rounded-full px-4 py-2.5
              text-sm font-semibold transition-colors ${
                tab === key ? "bg-primary text-on-primary" : "text-on-surface-variant"
              }`}
          >
            {label}
            {open > 0 && (
              <span
                className={`grid min-w-6 place-items-center rounded-full px-1.5 py-0.5 text-[11.5px] font-bold ${
                  tab === key
                    ? "bg-on-primary/20 text-on-primary"
                    : "bg-primary-container text-on-primary-container"
                }`}
              >
                {open}
              </span>
            )}
          </button>
        ))}
      </div>

      {isLoading || !data ? (
        <div className="grid place-items-center py-24">
          <Spinner size={24} />
        </div>
      ) : tab === "floor" ? (
        <>
          <FloorList items={data.floor} threshold={data.meta.floor_threshold} />
          <FloorResetFooter meta={data.meta} />
        </>
      ) : (
        <BackList items={data.back} />
      )}
    </div>
  );
}

/** "The floor is fully stocked" — wipes the checklist, zeroes the counters,
 *  and restarts counting from tomorrow's sales. For the morning after a big
 *  physical restock, when the list no longer reflects reality. */
function FloorResetFooter({ meta }: { meta: RestockOut["meta"] }) {
  const reset = useResetFloorRestock();
  const toast = useToast();
  const [confirm, setConfirm] = useState(false);
  return (
    <div className="mt-2 flex flex-col items-center gap-1.5 pb-6 text-center">
      {meta.last_reset_at && (
        <div className="text-[12px] text-on-surface-variant">
          Counting restarted{" "}
          {new Date(meta.last_reset_at).toLocaleDateString([], { month: "short", day: "numeric" })}
          {meta.last_reset_by ? ` by ${meta.last_reset_by}` : ""}.
        </div>
      )}
      <Button variant="ghost" size="sm" onClick={() => setConfirm(true)}>
        Just fully restocked the floor? Reset this list
      </Button>
      <Dialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title="Floor fully stocked?"
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setConfirm(false)}>
              Never mind
            </Button>
            <Button
              disabled={reset.isPending}
              onClick={() =>
                reset.mutate(undefined, {
                  onSuccess: (r) => {
                    setConfirm(false);
                    toast.success(
                      `List reset — ${r.lines_cleared} item(s) cleared. Counting restarts with tomorrow's sales.`,
                    );
                  },
                  onError: (e) => toast.error(e.message),
                })
              }
            >
              {reset.isPending ? <Spinner size={16} /> : "Yes — reset the list"}
            </Button>
          </div>
        }
      >
        <p className="text-sm leading-6 text-on-surface-variant">
          This clears the whole floor checklist and zeroes every sales counter. Today's sales
          get amnesty (the shelves are full right now — they're covered); counting restarts
          with tomorrow's sales. The "From warehouse" list isn't affected. Do this right after
          a full physical restock.
        </p>
      </Dialog>
    </div>
  );
}

function FloorList({ items, threshold }: { items: RestockFloorItem[]; threshold: number }) {
  const check = useCheckRestock();
  const snooze = useSnoozeRestock();
  const toast = useToast();
  if (items.length === 0) {
    return (
      <EmptyState
        title="Shelves are happy"
        hint={`Items appear here once ${fmtQty(threshold)}+ units sell since their last restock.`}
      />
    );
  }
  return (
    <ul className="stagger-children flex flex-col gap-2 pb-24">
      {items.map((item) => (
        <CheckRow
          key={item.line_id}
          checked={item.checked}
          onToggle={(checked) =>
            check.mutate(
              { list: "floor", line_id: item.line_id, checked },
              { onError: (e) => toast.error(e.message) },
            )
          }
          onSnooze={() =>
            snooze.mutate(
              { line_id: item.line_id, snoozed: true },
              {
                onSuccess: () => toast.success(`${item.name} — back on the list tomorrow.`),
                onError: (e) => toast.error(e.message),
              },
            )
          }
          title={item.name}
          sku={productCode(item.barcode, item.sku)}
          right={
            <span className="text-right">
              <span className="display block text-2xl leading-none">{fmtQty(item.qty)}</span>
              <span className="text-[11px] text-on-surface-variant">bring out</span>
            </span>
          }
          sub={
            <>
              sold since last restock · flagged{" "}
              {new Date(item.flagged_on + "T00:00:00").toLocaleDateString([], {
                month: "short",
                day: "numeric",
              })}
              {" · "}
              floor {fmtQty(item.floor_qty)} <LowCountHint qty={item.floor_qty} />
            </>
          }
        />
      ))}
    </ul>
  );
}

/* No checkboxes here — the point of this list IS a transfer request, so the
   one action turns the whole list into one, prefilled with the suggestions. */
function BackList({ items }: { items: RestockBackItem[] }) {
  const { roles } = useAuth();
  const navigate = useNavigate();
  const canRequest = roles.has("shoppe_floor") || roles.has("admin");
  if (items.length === 0) {
    return (
      <EmptyState
        title="Back stock looks covered"
        hint="Items appear when the shop is under a week of cover and the warehouse has stock."
      />
    );
  }
  const startTransfer = () =>
    navigate("/transfer-requests/new", {
      state: {
        prefill: {
          notes: "From the warehouse restock list",
          lines: items.map((item) => ({
            product_id: item.product_id,
            sku: item.sku,
            barcode: item.barcode,
            name: item.name,
            category: item.category,
            qty: Math.max(1, item.suggested_qty),
            floor_qty: item.floor_qty,
            bwhse_qty: item.bwhse_qty,
            case_size: 1,
          })),
        },
      },
    });
  return (
    <>
      <ul className="stagger-children flex flex-col gap-2">
        {items.map((item) => (
          <li
            key={item.product_id}
            className="flex items-center justify-between gap-3.5 rounded-(--radius-lg)
              bg-surface-container-low px-4 py-3.5"
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[15px] font-medium">{item.name}</span>
              <span className="mt-0.5 block text-[12px] tabular-nums text-on-surface-variant">
                <span className="font-mono">{productCode(item.barcode, item.sku)}</span> ·{" "}
                {item.days_of_cover === null ? (
                  <Badge tone="danger">none on floor</Badge>
                ) : (
                  <span
                    className={item.days_of_cover < 3 ? "font-semibold text-error" : undefined}
                  >
                    ~{item.days_of_cover}d of cover
                  </span>
                )}
                {" · "}floor {fmtQty(item.floor_qty)} · whse {fmtQty(item.bwhse_qty)}{" "}
                <LowCountHint qty={item.bwhse_qty} />
                {" · "}~{item.avg_daily}/day
              </span>
            </span>
            <span className="shrink-0 text-right">
              <span className="display block text-2xl leading-none">
                {fmtQty(item.suggested_qty)}
              </span>
              <span className="text-[11px] text-on-surface-variant">suggested</span>
            </span>
          </li>
        ))}
      </ul>
      {canRequest && (
        <div className="mt-4 pb-24">
          <Button className="w-full sm:w-auto" onClick={startTransfer}>
            New transfer from these items · {items.length}
          </Button>
          <div className="mt-1.5 text-center text-[12px] text-on-surface-variant sm:text-left">
            Opens a request prefilled with the suggested quantities — adjust before sending.
          </div>
        </div>
      )}
    </>
  );
}

/** How far the row must travel before "not today" commits. Below this it
 *  springs back, so a scroll that wanders sideways can't defer an item. */
const SWIPE_COMMIT_PX = 96;

function CheckRow({
  checked,
  onToggle,
  onSnooze,
  title,
  sku,
  sub,
  right,
}: {
  checked: boolean;
  onToggle: (checked: boolean) => void;
  onSnooze?: () => void;
  title: string;
  sku: string;
  sub: React.ReactNode;
  right: React.ReactNode;
}) {
  const [dx, setDx] = useState(0);
  // The commit test reads this ref, never the state: on a fast flick the last
  // pointermove and the pointerup land in the same frame, so the state in
  // endDrag's closure is still the previous value and the swipe would be
  // silently dropped. The state exists only to drive the paint.
  const dxRef = useRef(0);
  const drag = useRef<{ x0: number; y0: number; axis: "" | "x" | "y" } | null>(null);
  // A swipe ends with a synthetic click on some browsers; this swallows it so
  // deferring an item can never also tick it off.
  const swallowClick = useRef(false);
  const canSwipe = Boolean(onSnooze) && !checked;

  const onPointerDown = (e: React.PointerEvent) => {
    if (!canSwipe || e.pointerType === "mouse") return;
    drag.current = { x0: e.clientX, y0: e.clientY, axis: "" };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const mx = e.clientX - d.x0;
    const my = e.clientY - d.y0;
    if (d.axis === "") {
      if (Math.abs(mx) < 8 && Math.abs(my) < 8) return;
      // Commit to an axis once: a vertical intent stays a scroll, forever.
      d.axis = Math.abs(mx) > Math.abs(my) ? "x" : "y";
    }
    if (d.axis !== "x") return;
    const next = Math.min(0, mx); // left only — there is no right-hand action
    dxRef.current = next;
    setDx(next);
  };
  const endDrag = () => {
    const d = drag.current;
    drag.current = null;
    if (d?.axis === "x" && dxRef.current <= -SWIPE_COMMIT_PX) {
      swallowClick.current = true;
      onSnooze?.();
      return; // leave it translated; the row is being removed from the list
    }
    dxRef.current = 0;
    setDx(0);
  };

  return (
    <li className="relative overflow-hidden rounded-(--radius-lg)">
      {canSwipe && dx < 0 && (
        <span
          aria-hidden
          className="absolute inset-y-0 right-0 flex items-center gap-1.5 rounded-(--radius-lg)
            bg-tertiary-container px-4 text-[13px] font-medium text-on-tertiary-container"
        >
          Not today
        </span>
      )}
      <button
        type="button"
        role="checkbox"
        aria-checked={checked}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onClick={() => {
          if (swallowClick.current) {
            swallowClick.current = false;
            return;
          }
          onToggle(!checked);
        }}
        style={{
          transform: dx ? `translate3d(${dx}px,0,0)` : undefined,
          // No transition while the finger is down, or the row lags behind it.
          transition: drag.current ? undefined : "transform 200ms var(--ease-spring)",
          touchAction: canSwipe ? "pan-y" : undefined,
        }}
        className={`state-layer relative flex w-full items-center gap-3.5 rounded-(--radius-lg)
          px-4 py-3.5 text-left transition-all duration-200 ${
            checked ? "bg-surface-container opacity-60" : "bg-surface-container-low"
          }`}
      >
        <span
          aria-hidden
          className={`grid h-7 w-7 shrink-0 place-items-center rounded-full border-2
            transition-all duration-200 ease-(--ease-spring) ${
              checked
                ? "scale-105 border-primary bg-primary text-on-primary"
                : "border-outline text-transparent"
            }`}
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path
              d="M3 7.5 6 10.5 11 4"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
        <span className="min-w-0 flex-1">
          <span
            className={`block truncate text-[15px] font-medium ${checked ? "line-through" : ""}`}
          >
            {title}
          </span>
          <span className="mt-0.5 block text-[12px] tabular-nums text-on-surface-variant">
            <span className="font-mono">{sku}</span> · {sub}
          </span>
        </span>
        <span className="shrink-0">{right}</span>
      </button>
    </li>
  );
}
