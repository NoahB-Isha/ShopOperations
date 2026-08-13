/* Inventory Flow Manager: everything worth pulling from the warehouse, from
   both directions —

     Floor Team Requests   people asked for this
     Database Suggestions  the app worked this out from sales and cover

   People first, deliberately: a volunteer standing at an empty shelf knows
   something the numbers don't, and burying that under a computed list would
   waste it. Each section says plainly where its items came from, so nobody
   has to guess which is which.

   Both sections feed the same transfer draft (the floating bubble), so a
   mixed pull is one request. */
import {
  useFloorRequests,
  useResolveFloorRequest,
  useRestock,
  useSnoozeSuggestion,
} from "../../api/hooks";
import type { FloorRequestOut, RestockBackItem } from "../../api/types";
import { addToDraft } from "../../transferDraft";
import { boxAt, flyToBubble } from "../../shell/flyToBubble";
import {
  Badge,
  Button,
  EmptyState,
  PageHeader,
  Spinner,
  SwipeBackdrop,
  leavingStyle,
  useSwipeRow,
  useToast,
} from "../../design";
import type { ActionBox } from "../../design";
import { LowCountHint, fmtQty, fmtWhen, productCode } from "../shared/OpsBits";

/** The label that makes the provenance unmissable. */
function SourceChip({ kind }: { kind: "people" | "app" }) {
  return (
    <Badge tone={kind === "people" ? "secondary" : "tertiary"}>
      {kind === "people" ? "asked for by the floor team" : "found by the app"}
    </Badge>
  );
}

function Section({
  title,
  chip,
  children,
}: {
  title: string;
  chip: "people" | "app";
  children: React.ReactNode;
}) {
  return (
    <section className="mb-10">
      {/* the chip is the whole explanation — where these came from is the
          only thing a heading here needs to say */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="headline text-[19px]">{title}</h2>
        <SourceChip kind={chip} />
      </div>
      {children}
    </section>
  );
}

/* ------------------------------------------------- floor team asks (people) */

function RequestRow({
  req,
  alsoAsked,
  onAdd,
  onDismiss,
}: {
  req: FloorRequestOut;
  /** how many other people have an open ask for this same product */
  alsoAsked: number;
  onAdd: (from: ActionBox) => void;
  onDismiss: () => void;
}) {
  // left = "not needed" (a person judged it, so it's settled for good)
  const swipe = useSwipeRow({ onLeft: onDismiss, onRight: onAdd, morphOnRight: true });
  return (
    <li
      className="relative overflow-hidden rounded-(--radius-lg)"
      style={leavingStyle(swipe.leaving)}
    >
      <SwipeBackdrop side="right" label="Not needed" dx={swipe.dx} tone="tertiary" />
      <SwipeBackdrop side="left" label="Add to transfer" dx={swipe.dx} morph={swipe.morph} />
      <div
        {...swipe.handlers}
        style={swipe.motionStyle}
        className="relative rounded-(--radius-lg) bg-surface-container-low px-4 py-3.5"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate text-[15px] font-medium">{req.name}</div>
            <div className="mt-0.5 text-[12px] tabular-nums text-on-surface-variant">
              <span className="font-mono">{productCode(req.barcode, req.sku)}</span> · floor{" "}
              {fmtQty(req.floor_qty)} · whse {fmtQty(req.bwhse_qty)}{" "}
              <LowCountHint qty={req.bwhse_qty} />
            </div>
            <div className="mt-1 text-[12px] text-on-surface-variant">
              <b className="text-on-surface">{req.requested_by}</b> · {fmtWhen(req.created_at)}
              {req.note && <span className="italic"> — “{req.note}”</span>}
            </div>
            {alsoAsked > 0 && (
              // every ask stands on its own, so the same shelf can appear
              // twice — say so, or adding both silently doubles the pull
              <div className="mt-1 text-[12px] font-medium text-secondary">
                {alsoAsked} other {alsoAsked === 1 ? "person has" : "people have"} asked for this
              </div>
            )}
          </div>
          <span className="shrink-0 text-right">
            <span className="display block text-2xl leading-none">{fmtQty(req.qty)}</span>
            <span className="text-[11px] text-on-surface-variant">asked for</span>
          </span>
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={(e) => onAdd(boxAt(e.clientX, e.clientY))}
          >
            Add to transfer
          </Button>
          <Button size="sm" variant="ghost" onClick={onDismiss}>
            Not needed
          </Button>
        </div>
      </div>
    </li>
  );
}

/* --------------------------------------------- computed back stock (the app) */

function SuggestionRow({
  item,
  onAdd,
  onSnooze,
}: {
  item: RestockBackItem;
  onAdd: (from: ActionBox) => void;
  onSnooze: () => void;
}) {
  // left = "not this week": the numbers will say the same thing tomorrow, so
  // this one goes quiet rather than away
  const swipe = useSwipeRow({ onLeft: onSnooze, onRight: onAdd, morphOnRight: true });
  return (
    <li
      className="relative overflow-hidden rounded-(--radius-lg)"
      style={leavingStyle(swipe.leaving)}
    >
      <SwipeBackdrop side="right" label="Not this week" dx={swipe.dx} tone="tertiary" />
      <SwipeBackdrop side="left" label="Add to transfer" dx={swipe.dx} morph={swipe.morph} />
      <div
        {...swipe.handlers}
        onContextMenu={(e) => {
          e.preventDefault();
          onAdd(boxAt(e.clientX, e.clientY));
        }}
        style={swipe.motionStyle}
        className="relative flex items-center justify-between gap-3.5 rounded-(--radius-lg)
          bg-surface-container-low px-4 py-3.5"
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[15px] font-medium">{item.name}</span>
          <span className="mt-0.5 block text-[12px] tabular-nums text-on-surface-variant">
            <span className="font-mono">{productCode(item.barcode, item.sku)}</span> ·{" "}
            {item.days_of_cover === null ? (
              <Badge tone="danger">none on floor</Badge>
            ) : (
              <span className={item.days_of_cover < 3 ? "font-semibold text-error" : undefined}>
                ~{item.days_of_cover}d of cover
              </span>
            )}
            {" · "}floor {fmtQty(item.floor_qty)} · whse {fmtQty(item.bwhse_qty)}{" "}
            <LowCountHint qty={item.bwhse_qty} />
            {" · "}~{item.avg_daily}/day
          </span>
        </span>
        <span className="shrink-0 text-right">
          <span className="display block text-2xl leading-none">{fmtQty(item.suggested_qty)}</span>
          <span className="text-[11px] text-on-surface-variant">suggested</span>
        </span>
      </div>
    </li>
  );
}

/* ----------------------------------------------------------------- the page */

export function SuggestedItemsPage() {
  const { data, isLoading } = useRestock();
  const requests = useFloorRequests();
  const resolve = useResolveFloorRequest();
  const snooze = useSnoozeSuggestion();
  const toast = useToast();

  const asks = requests.data ?? [];
  const suggestions = data?.back ?? [];

  const takeRequest = (req: FloorRequestOut, from: ActionBox) => {
    addToDraft({
      product_id: req.product_id,
      sku: req.sku,
      barcode: req.barcode,
      name: req.name,
      category: req.category,
      qty: Math.max(1, Math.round(req.qty)),
      floor_qty: req.floor_qty,
      bwhse_qty: req.bwhse_qty,
      case_size: 1,
    });
    flyToBubble(from, Math.max(1, Math.round(req.qty)));
    // it's on a transfer now — off the board, and the asker can see why
    resolve.mutate({ id: req.id, action: "picked-up" }, { onError: (e) => toast.error(e.message) });
  };

  const takeSuggestion = (item: RestockBackItem, from: ActionBox) => {
    const qty = Math.max(1, Math.round(item.suggested_qty));
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
    flyToBubble(from, qty);
  };

  /** Everything the app found, into whatever transfer is already being
   *  built — no navigation, no replacing a half-picked draft. With nothing
   *  in progress the draft simply starts here (the bubble appears). */
  const addAll = (from: ActionBox) => {
    let total = 0;
    for (const item of suggestions) {
      const qty = Math.max(1, Math.round(item.suggested_qty));
      total += qty;
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
    }
    if (total > 0) flyToBubble(from, total);
  };

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader title="Suggested items" />

      {isLoading || requests.isLoading ? (
        <div className="grid place-items-center py-24">
          <Spinner size={24} />
        </div>
      ) : (
        <>
          <Section title="Floor Team Requests" chip="people">
            {asks.length === 0 ? (
              <EmptyState
                title="No asks right now"
                hint="When the floor team requests something, it lands here first."
              />
            ) : (
              <ul className="stagger-children flex flex-col gap-2">
                {asks.map((req) => (
                  <RequestRow
                    key={req.id}
                    req={req}
                    alsoAsked={asks.filter((o) => o.product_id === req.product_id).length - 1}
                    onAdd={(from) => takeRequest(req, from)}
                    onDismiss={() =>
                      resolve.mutate(
                        { id: req.id, action: "dismiss" },
                        {
                          onSuccess: () => toast.info(`${req.name} — marked not needed.`),
                          onError: (e) => toast.error(e.message),
                        },
                      )
                    }
                  />
                ))}
              </ul>
            )}
          </Section>

          <Section title="Database Suggestions" chip="app">
            {suggestions.length === 0 ? (
              <EmptyState
                title="Back stock looks covered"
                hint="Items appear when the shop is under a week of cover and the warehouse has stock."
              />
            ) : (
              <>
                <ul className="stagger-children flex flex-col gap-2">
                  {suggestions.map((item) => (
                    <SuggestionRow
                      key={item.product_id}
                      item={item}
                      onAdd={(from) => takeSuggestion(item, from)}
                      onSnooze={() =>
                        snooze.mutate(
                          { productId: item.product_id },
                          {
                            onSuccess: () =>
                              toast.info(`${item.name} — hidden for a week.`),
                            onError: (e) => toast.error(e.message),
                          },
                        )
                      }
                    />
                  ))}
                </ul>
                <div className="mt-4 pb-24">
                  <Button
                    className="w-full sm:w-auto"
                    onClick={(e) => addAll(boxAt(e.clientX, e.clientY))}
                  >
                    Add all · {suggestions.length}
                  </Button>
                </div>
              </>
            )}
          </Section>
        </>
      )}
    </div>
  );
}
