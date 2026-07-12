/* The morning restock checklists, phone-first. Floor list = the ILscripts
   accumulator (sold enough since last restock → bring more out). Back list =
   floor cover running thin vs the warehouse. Check-off resets daily. */
import { useState } from "react";
import { useCheckRestock, useRestock } from "../../api/hooks";
import type { RestockBackItem, RestockFloorItem } from "../../api/types";
import { Badge, EmptyState, PageHeader, Spinner, useToast } from "../../design";
import { LowCountHint, fmtQty } from "../shared/OpsBits";

export function RestockPage() {
  const { data, isLoading } = useRestock();
  const [tab, setTab] = useState<"floor" | "back">("floor");

  const floorOpen = (data?.floor ?? []).filter((i) => !i.checked).length;
  const backOpen = (data?.back ?? []).filter((i) => !i.checked).length;

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
        <FloorList items={data.floor} threshold={data.meta.floor_threshold} />
      ) : (
        <BackList items={data.back} />
      )}
    </div>
  );
}

function FloorList({ items, threshold }: { items: RestockFloorItem[]; threshold: number }) {
  const check = useCheckRestock();
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
          title={item.name}
          sku={item.sku}
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
              whse {fmtQty(item.bwhse_qty)} <LowCountHint qty={item.bwhse_qty} />
            </>
          }
        />
      ))}
    </ul>
  );
}

function BackList({ items }: { items: RestockBackItem[] }) {
  const check = useCheckRestock();
  const toast = useToast();
  if (items.length === 0) {
    return (
      <EmptyState
        title="Back stock looks covered"
        hint="Items appear when the shop is under a week of cover and the warehouse has stock."
      />
    );
  }
  return (
    <ul className="stagger-children flex flex-col gap-2 pb-24">
      {items.map((item) => (
        <CheckRow
          key={item.product_id}
          checked={item.checked}
          onToggle={(checked) =>
            check.mutate(
              { list: "back", product_id: item.product_id, checked },
              { onError: (e) => toast.error(e.message) },
            )
          }
          title={item.name}
          sku={item.sku}
          right={
            <span className="text-right">
              <span className="display block text-2xl leading-none">
                {fmtQty(item.suggested_qty)}
              </span>
              <span className="text-[11px] text-on-surface-variant">suggested</span>
            </span>
          }
          sub={
            <>
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
            </>
          }
        />
      ))}
    </ul>
  );
}

function CheckRow({
  checked,
  onToggle,
  title,
  sku,
  sub,
  right,
}: {
  checked: boolean;
  onToggle: (checked: boolean) => void;
  title: string;
  sku: string;
  sub: React.ReactNode;
  right: React.ReactNode;
}) {
  return (
    <li>
      <button
        type="button"
        role="checkbox"
        aria-checked={checked}
        onClick={() => onToggle(!checked)}
        className={`state-layer flex w-full items-center gap-3.5 rounded-(--radius-lg) px-4 py-3.5
          text-left transition-all duration-200 ${
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
