/* Suggested items, right where the request is being built.

   Same two voices as the /suggested-items page, in the same order — PEOPLE
   FIRST (someone stood at an empty shelf and said so), then what the numbers
   found. It reuses that page's data and its add semantics exactly: adding at
   the suggested quantity, merging into the shared draft, and marking a floor
   ask "picked up" so the asker sees it landed.

   Deliberately quiet: tap only (no swipe — left would mean snooze, which is a
   bigger commitment than it looks mid-request), five rows with the rest a link
   away, and NOTHING at all when there's nothing to suggest. It collapses once
   the draft has items so a long strip can never push Send off a phone screen,
   and starts open when the draft is empty — that's when it's the most useful
   thing on the page.

   No flyToBubble here: the bubble is hidden on this route, so the item joining
   the list above IS the acknowledgement. */
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  useFloorRequests,
  useResolveFloorRequest,
  useRestock,
} from "../../api/hooks";
import type { FloorRequestOut, RestockBackItem } from "../../api/types";
import { Badge, Card, ScrollingText, useToast } from "../../design";
import { addToDraft, useDraftLines } from "../../transferDraft";
import { OnTheWayChip, fmtQty, productCode, useOnTheWay } from "../shared/OpsBits";
import { splitSlots } from "./suggestedRows";

const SHOWN = 5;

function Row({
  name,
  code,
  detail,
  chip,
  qty,
  onAdd,
  onTheWay,
}: {
  name: string;
  code: string;
  detail: string;
  chip?: React.ReactNode;
  qty: number;
  onAdd: () => void;
  onTheWay?: React.ReactNode;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onAdd}
        className="state-layer flex w-full items-center justify-between gap-3 border-b
          border-outline-variant/50 px-4 py-2.5 text-left last:border-b-0"
      >
        <span className="min-w-0">
          <ScrollingText text={name} className="text-sm font-medium" />
          <span className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[12px] tabular-nums text-on-surface-variant">
            <span className="font-mono">{code}</span>
            <span>{detail}</span>
            {chip}
            {onTheWay}
          </span>
        </span>
        <span className="shrink-0 text-[13px] font-semibold text-primary">
          + {fmtQty(qty)}
        </span>
      </button>
    </li>
  );
}

export function SuggestedStrip() {
  const lines = useDraftLines();
  const { data } = useRestock();
  const asks = useFloorRequests();
  const resolve = useResolveFloorRequest();
  const onTheWay = useOnTheWay();
  const toast = useToast();
  const [open, setOpen] = useState(false);

  const inDraft = new Set(lines.map((l) => l.product_id));
  const floorAsks = (asks.data ?? []).filter((a) => !inDraft.has(a.product_id));
  const suggestions = (data?.back ?? []).filter((s) => !inDraft.has(s.product_id));
  const total = floorAsks.length + suggestions.length;
  if (total === 0) return null;

  // open when there's nothing else to look at; a disclosure once you're working
  const expanded = lines.length === 0 || open;
  const { asks: askRows, suggestions: suggestionRows, hidden } = splitSlots(
    floorAsks,
    suggestions,
    SHOWN,
  );

  const takeAsk = (req: FloorRequestOut) => {
    const qty = Math.max(1, Math.round(req.qty));
    addToDraft({
      product_id: req.product_id,
      sku: req.sku,
      barcode: req.barcode,
      name: req.name,
      category: req.category,
      qty,
      floor_qty: req.floor_qty,
      bwhse_qty: req.bwhse_qty,
      case_size: 1,
    });
    // on a transfer now — off the board, and the asker can see why
    resolve.mutate({ id: req.id, action: "picked-up" }, { onError: (e) => toast.error(e.message) });
  };

  const takeSuggestion = (item: RestockBackItem) => {
    addToDraft({
      product_id: item.product_id,
      sku: item.sku,
      barcode: item.barcode,
      name: item.name,
      category: item.category,
      qty: Math.max(1, Math.round(item.suggested_qty)),
      floor_qty: item.floor_qty,
      bwhse_qty: item.bwhse_qty,
      case_size: 1,
    });
  };

  return (
    <Card pad={false} className="mb-4">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={lines.length === 0}
        className="state-layer flex w-full items-center justify-between gap-2 px-4 py-3 text-left
          disabled:cursor-default"
      >
        <span className="title-m">
          Suggested items{" "}
          <span className="text-[13px] font-normal text-on-surface-variant">({total})</span>
        </span>
        {lines.length > 0 && (
          <span aria-hidden className="text-on-surface-variant">
            {expanded ? "▲" : "▼"}
          </span>
        )}
      </button>

      {expanded && (
        <ul>
          {askRows.map((a) => (
            <Row
              key={`ask-${a.id}`}
              name={a.name}
              code={productCode(a.barcode, a.sku)}
              detail={`floor ${fmtQty(a.floor_qty)} · whse ${fmtQty(a.bwhse_qty)}`}
              chip={<Badge tone="gold">{a.requested_by} asked</Badge>}
              onTheWay={<OnTheWayChip item={onTheWay.get(a.product_id)} />}
              qty={Math.max(1, Math.round(a.qty))}
              onAdd={() => takeAsk(a)}
            />
          ))}
          {suggestionRows.map((s) => (
            <Row
              key={`sug-${s.product_id}`}
              name={s.name}
              code={productCode(s.barcode, s.sku)}
              detail={
                s.days_of_cover === null
                  ? `nothing on the floor · whse ${fmtQty(s.bwhse_qty)}`
                  : `${s.days_of_cover}d cover · whse ${fmtQty(s.bwhse_qty)}`
              }
              onTheWay={<OnTheWayChip item={onTheWay.get(s.product_id)} />}
              qty={Math.max(1, Math.round(s.suggested_qty))}
              onAdd={() => takeSuggestion(s)}
            />
          ))}
          {hidden > 0 && (
            <li className="px-4 py-2.5 text-[13px]">
              <Link to="/suggested-items" className="font-semibold text-primary hover:underline">
                See all {total} →
              </Link>
            </li>
          )}
        </ul>
      )}
    </Card>
  );
}
