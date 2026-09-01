/* The morning restock checklist, phone-first: the ILscripts accumulator
   (sold enough since last restock → bring more out). Check-off resets daily.

   The old "From warehouse" tab moved out on 2026-08-13 — those computed
   back-stock suggestions now live on the Inventory Flow Manager's Suggested
   items page, under "Database Suggestions", next to what the floor team
   actually asked for. This page is one list again. */
import { Fragment, useEffect, useRef, useState } from "react";
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
  MorphBall,
  PageHeader,
  ScrollingText,
  Spinner,
  SwipeBackdrop,
  leavingStyle,
  useContextMenu,
  useSwipeRow,
  useToast,
} from "../../design";
import type { ActionBox } from "../../design";
import { LowCountHint, fmtQty, productCode } from "../shared/OpsBits";
import { playCheck, playChime, playFanfare, playScribble, playUncheck } from "../../sound";
import { celebrate } from "../../celebrate";
import { groupJustFinished, listJustFinished, milestoneFor } from "./restockCheer";
import { CHEER_MS, CelebrationOverlay, type Cheer } from "./CelebrationOverlay";

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
      {data && <Freshness meta={data.meta} />}

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

/** How current the numbers on these rows actually are.
 *
 *  The floor quantities come from the stock sync, which on a deployment with
 *  no background worker only runs when something asks it to — this page does,
 *  on a throttle, every time it polls. Saying so plainly beats letting someone
 *  trust a two-day-old shelf count. */
function Freshness({ meta }: { meta: RestockOut["meta"] }) {
  const stamp = meta.stock_synced_at;
  if (!stamp) return null;
  const mins = Math.max(0, Math.round((Date.now() - new Date(stamp).getTime()) / 60_000));
  const stale = mins > 90;
  const label =
    mins < 2 ? "just now" : mins < 60 ? `${mins} min ago` : `${Math.round(mins / 60)} h ago`;
  return (
    <div
      className={`-mt-2 mb-4 text-[12px] ${stale ? "text-warn" : "text-on-surface-variant"}`}
      title={new Date(stamp).toLocaleString()}
    >
      Shelf counts updated {label}
      {stale ? " — refreshing" : ""}
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

  /* The full-screen moment for finishing an aisle or the list. A later cheer
     replaces a running one (its `key` restarts the animation); the timer is
     the only dismissal, and it's cleaned up on unmount. */
  const [cheer, setCheer] = useState<Cheer | null>(null);
  const cheerTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(cheerTimer.current), []);
  const showCheer = (title: string, subtitle?: string) => {
    clearTimeout(cheerTimer.current);
    setCheer({ key: Date.now(), title, subtitle });
    cheerTimer.current = setTimeout(() => setCheer(null), CHEER_MS);
  };

  /* The tick is the gesture, so the sound plays NOW (also what unlocks iOS
     audio) and the aisle/list celebrations are computed optimistically from
     the rows on screen — the refetch is still in flight when they should
     land. Only the milestone waits for the server: the lifetime total rides
     back on the check response. */
  const toggle = (item: RestockFloorItem, checked: boolean) => {
    if (checked) {
      const aisle = groupJustFinished(items, item.line_id);
      if (listJustFinished(items, item.line_id)) {
        playFanfare();
        celebrate();
        showCheer("Floor fully stocked! 🎉", "Every item on the list. Nice work.");
      } else if (aisle) {
        playChime();
        celebrate();
        showCheer(`${aisle}: cleared! 🎉`, "The whole aisle is stocked.");
      } else {
        playCheck();
      }
    } else {
      playUncheck();
    }
    check.mutate(
      { list: "floor", line_id: item.line_id, checked },
      {
        onSuccess: (out) => {
          const m = checked ? milestoneFor(out?.my_restocked_total) : null;
          if (m) {
            playFanfare();
            celebrate();
            showCheer(`Your ${m.toLocaleString()}th item! 🎉`, "Restocked, lifetime. Thank you!");
          }
        },
        onError: (e) => toast.error(e.message),
      },
    );
  };
  const done = items.filter((i) => i.checked).length;

  if (items.length === 0) {
    return (
      <EmptyState
        title="Shelves are happy"
        hint={`Items appear here once ${fmtQty(threshold)}+ units sell since their last restock.`}
      />
    );
  }
  /* The list arrives grouped by aisle and ranked by sales (backend
     restock/grouping.py: best-selling group first, best sellers inside it), so
     all this does is draw a heading where the group changes. Never re-sort
     here — the order encodes sales figures the client doesn't have. */
  const groupSizes = new Map<string, number>();
  const groupSold = new Map<string, number>();
  for (const item of items) {
    const label = item.group || "Other";
    groupSizes.set(label, (groupSizes.get(label) ?? 0) + 1);
    groupSold.set(label, item.group_popularity);
  }

  return (
    <>
    <div className="mb-3 px-1">
      <div className="mb-1.5 flex items-baseline justify-between text-[12px] tabular-nums text-on-surface-variant">
        <span>
          {done === items.length
            ? "All stocked — the shelves thank you 🎉"
            : `Restocked ${done} of ${items.length}`}
        </span>
        {done > 0 && done < items.length && (
          <span>{Math.round((done / items.length) * 100)}%</span>
        )}
      </div>
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={items.length}
        aria-valuenow={done}
        aria-label="Restock progress"
        className="h-1.5 overflow-hidden rounded-full bg-surface-container"
      >
        <div
          className="h-full rounded-full bg-primary transition-[width] duration-500 ease-(--ease-spring)"
          style={{ width: `${(done / items.length) * 100}%` }}
        />
      </div>
    </div>
    <ul className="stagger-children flex flex-col gap-2 pb-24">
      {items.map((item, i) => (
        <Fragment key={item.line_id}>
        {(i === 0 || (items[i - 1].group || "Other") !== (item.group || "Other")) && (
          <li className="mt-4 flex items-baseline justify-between gap-2 px-1 first:mt-0">
            <h3 className="title-m text-on-surface">{item.group || "Other"}</h3>
            <span className="tabular-nums text-[11.5px] text-on-surface-variant">
              {groupSizes.get(item.group || "Other")} item
              {groupSizes.get(item.group || "Other") === 1 ? "" : "s"}
              {(groupSold.get(item.group || "Other") ?? 0) > 0 &&
                ` · ${fmtQty(groupSold.get(item.group || "Other") ?? 0)} sold`}
            </span>
          </li>
        )}
        <CheckRow
          checked={item.checked}
          onToggle={(checked) => toggle(item, checked)}
          onSnooze={() => {
            playScribble(); // "not today" — the line gets scribbled out
            snooze.mutate(
              { line_id: item.line_id, snoozed: true },
              {
                onSuccess: () => toast.success(`${item.name} — back on the list tomorrow.`),
                onError: (e) => toast.error(e.message),
              },
            );
          }}
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
              {item.popularity > 0 && (
                <span title="Units sold on the shop floor in the last 90 days — what puts this near the top of its group.">
                  {" · "}
                  {fmtQty(item.popularity)} sold
                </span>
              )}
            </>
          }
        />
        </Fragment>
      ))}
      <ContextMenu menu={menu.menu} onClose={menu.close} />
    </ul>
    <CelebrationOverlay cheer={cheer} />
    </>
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
    <li
      data-name-press
      className="relative overflow-hidden rounded-(--radius-lg)"
      style={leavingStyle(swipe.leaving)}
    >
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
          {/* long-press to scroll a name too long for the row (ScrollingText) */}
          <ScrollingText
            text={title}
            className={`text-[15px] font-medium ${checked ? "line-through" : ""}`}
          />
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
