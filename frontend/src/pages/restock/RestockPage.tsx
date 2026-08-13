/* The morning restock checklist, phone-first: the ILscripts accumulator
   (sold enough since last restock → bring more out). Check-off resets daily.

   The old "From warehouse" tab moved out on 2026-08-13 — those computed
   back-stock suggestions now live on the Inventory Flow Manager's Suggested
   items page, under "Database Suggestions", next to what the floor team
   actually asked for. This page is one list again. */
import { useState } from "react";
import {
  useCheckRestock,
  useResetFloorRestock,
  useRestock,
  useSnoozeRestock,
} from "../../api/hooks";
import type { RestockFloorItem, RestockOut } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { addToDraft } from "../../transferDraft";
import { boxAt, centerOf, flyToBubble } from "../../shell/flyToBubble";
import {
  Button,
  ContextMenu,
  Dialog,
  EmptyState,
  PageHeader,
  Spinner,
  MorphBall,
  SwipeBackdrop,
  leavingStyle,
  useContextMenu,
  useSwipeRow,
  useToast,
} from "../../design";
import type { ActionBox } from "../../design";
import { LowCountHint, fmtQty, productCode } from "../shared/OpsBits";

export function RestockPage() {
  const { data, isLoading } = useRestock();

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

      {isLoading || !data ? (
        <div className="grid place-items-center py-24">
          <Spinner size={24} />
        </div>
      ) : (
        <>
          <FloorList items={data.floor} threshold={data.meta.floor_threshold} />
          <FloorResetFooter meta={data.meta} />
        </>
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
          with tomorrow's sales. The warehouse suggestions aren't affected. Do this right
          after a full physical restock.
        </p>
      </Dialog>
    </div>
  );
}

function FloorList({ items, threshold }: { items: RestockFloorItem[]; threshold: number }) {
  const check = useCheckRestock();
  const snooze = useSnoozeRestock();
  const toast = useToast();
  const { roles } = useAuth();
  // the Floor Team can build a draft too — theirs is sent as an ask on
  // /request-items rather than becoming a transfer
  const canRequest = roles.has("shoppe_floor") || roles.has("floor_rotating") || roles.has("admin");
  const menu = useContextMenu(); // swipes are touch-only — right-click is the desk equivalent

  /* Swipe right on a row the back stock can't cover: the item joins the
     transfer request you're building and gets slingshot into the floating
     bubble, which is the whole acknowledgement — no snackbar. The row stays
     put; the shelf still needs filling today. */
  const requestMore = (item: RestockFloorItem, from?: ActionBox) => {
    const qty = Math.max(1, Math.round(item.qty));
    addToDraft({
      product_id: item.product_id,
      sku: item.sku,
      barcode: item.barcode,
      name: item.name,
      category: item.category,
      qty,
      floor_qty: item.floor_qty,
      bwhse_qty: item.bwhse_qty,
      case_size: 1,
    });
    flyToBubble(from ?? centerOf(), qty);
  };

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
          onRequestMore={canRequest ? (from) => requestMore(item, from) : undefined}
          onContextMenu={
            canRequest
              ? (e) =>
                  menu.open(e, [
                    {
                      label: "Request more from the warehouse",
                      onSelect: () => requestMore(item, boxAt(e.clientX, e.clientY)),
                    },
                  ])
              : undefined
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
              {addedAgo(item.flagged_on)}
              {" · "}
              floor {fmtQty(item.floor_qty)} <LowCountHint qty={item.floor_qty} />
            </>
          }
        />
      ))}
      <ContextMenu menu={menu.menu} onClose={menu.close} />
    </ul>
  );
}

/* No checkboxes here — the point of this list IS a transfer request, so the
   one action turns the whole list into one, prefilled with the suggestions. */
/** "Added 3 days ago" reads faster on the floor than a bare date — the useful
 *  question is how long it has been sitting there, not which day it was. */
function addedAgo(day: string): string {
  const then = new Date(day + "T00:00:00");
  const now = new Date();
  const days = Math.round(
    (new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() -
      new Date(then.getFullYear(), then.getMonth(), then.getDate()).getTime()) /
      86_400_000,
  );
  if (days <= 0) return "Added today";
  if (days === 1) return "Added yesterday";
  return `Added ${days} days ago`;
}

function CheckRow({
  checked,
  onToggle,
  onSnooze,
  onRequestMore,
  onContextMenu,
  title,
  sku,
  sub,
  right,
}: {
  checked: boolean;
  onToggle: (checked: boolean) => void;
  /** swipe LEFT — "not today", the row leaves the list until tomorrow */
  onSnooze?: () => void;
  /** swipe RIGHT — "request more": the row stays put and the action morphs
   *  into a ball that flies into the transfer bubble */
  onRequestMore?: (from: ActionBox) => void;
  /** the same action for a mouse: swipes are touch-only by design */
  onContextMenu?: (e: React.MouseEvent) => void;
  title: string;
  sku: string;
  sub: React.ReactNode;
  right: React.ReactNode;
}) {
  const swipe = useSwipeRow({
    onLeft: onSnooze,
    onRight: onRequestMore,
    disabled: checked,
    // drag it most of the way across and the row itself becomes the ball
    morphOnRight: true,
  });

  return (
    <li className="relative overflow-hidden rounded-(--radius-lg)" style={leavingStyle(swipe.leaving)}>
      <SwipeBackdrop side="right" label="Not today" dx={swipe.dx} tone="tertiary" />
      <SwipeBackdrop side="left" label="Request more" dx={swipe.dx} morph={swipe.morph} />
      <button
        type="button"
        role="checkbox"
        aria-checked={checked}
        {...swipe.handlers}
        onContextMenu={onContextMenu}
        onClick={() => {
          if (swipe.swallowClick()) return;
          onToggle(!checked);
        }}
        style={swipe.motionStyle}
        className={`state-layer relative flex w-full items-center gap-3.5 rounded-(--radius-lg)
          px-4 py-3.5 text-left ${
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
        <MorphBall progress={swipe.morph} />
      </button>
    </li>
  );
}
