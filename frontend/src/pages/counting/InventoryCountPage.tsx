/* Inventory counting — standing at a shelf with a phone.

   Pick the location, add products, type or tap what's actually there, submit.
   The Odoo quantity sits next to every row because the whole job is comparing
   the two numbers, and it is READ FROM ODOO LIVE for the chosen location (the
   server re-reads it at submit, so what the reviewer judges can't be edited
   by a client).

   The counted quantity is never pre-filled with Odoo's number: a pre-filled
   count is a count nobody took. Rows start blank and stay blank until someone
   commits to a figure. */
import { useEffect, useMemo, useState } from "react";
import {
  useCountLocations,
  useMyRecounts,
  useStockAt,
  useSubmitCount,
  useSubmitRecount,
} from "../../api/hooks";
import type { CountItemOut, RecentCountOut } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  PageHeader,
  ScrollingText,
  Select,
  Spinner,
  Textarea,
  useToast,
} from "../../design";
import { usePersistedState } from "../../persist";
import { ProductPicker, fmtQty, productCode } from "../shared/OpsBits";
import type { PickedLine } from "../shared/OpsBits";

interface CountLine {
  product_id: number;
  sku: string;
  barcode: string;
  name: string;
  /** what the counter says is there — null until they enter something */
  counted: number | null;
}

/** Big touch controls: a count is dozens of small decisions in a row. */
function QtyStepper({
  value,
  onChange,
  ariaLabel,
}: {
  value: number | null;
  onChange: (v: number | null) => void;
  ariaLabel: string;
}) {
  const step = (by: number) => onChange(Math.max(0, (value ?? 0) + by));
  return (
    <div className="flex items-center gap-1.5">
      <button
        type="button"
        aria-label={`One fewer ${ariaLabel}`}
        onClick={() => step(-1)}
        className="state-layer grid h-11 w-11 shrink-0 place-items-center rounded-full
          bg-surface-container text-xl font-semibold text-on-surface"
      >
        −
      </button>
      <input
        type="number"
        min={0}
        inputMode="numeric"
        aria-label={ariaLabel}
        value={value ?? ""}
        placeholder="—"
        onChange={(e) => onChange(e.target.value === "" ? null : Math.max(0, Number(e.target.value)))}
        onFocus={(e) => e.currentTarget.select()}
        className="m3-control h-11 w-20 rounded-(--radius-md) border border-outline-variant
          bg-field text-center text-lg font-semibold tabular-nums"
      />
      <button
        type="button"
        aria-label={`One more ${ariaLabel}`}
        onClick={() => step(1)}
        className="state-layer grid h-11 w-11 shrink-0 place-items-center rounded-full
          bg-primary text-xl font-semibold text-on-primary"
      >
        +
      </button>
    </div>
  );
}

function CountRow({
  line,
  odooQty,
  alsoCounted,
  onChange,
  onRemove,
}: {
  line: CountLine;
  odooQty: number | undefined;
  alsoCounted: RecentCountOut | undefined;
  onChange: (v: number | null) => void;
  onRemove: () => void;
}) {
  const diff = line.counted === null || odooQty === undefined ? null : line.counted - odooQty;
  return (
    <li
      data-name-press
      className="rounded-(--radius-lg) bg-surface-container-low px-4 py-3"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <ScrollingText text={line.name} className="text-[15px] font-medium" />
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[12px] tabular-nums text-on-surface-variant">
            <span className="font-mono">{productCode(line.barcode, line.sku)}</span>
            {diff !== null && diff !== 0 && (
              <Badge tone={diff > 0 ? "gold" : "danger"}>
                {diff > 0 ? "+" : ""}
                {fmtQty(diff)} vs Odoo
              </Badge>
            )}
            {diff === 0 && <Badge tone="forest">matches Odoo</Badge>}
          </div>
        </div>
        <button
          type="button"
          aria-label={`Remove ${line.name}`}
          onClick={onRemove}
          className="state-layer -mr-1 grid h-8 w-8 shrink-0 place-items-center rounded-full
            text-on-surface-variant"
        >
          ✕
        </button>
      </div>

      {alsoCounted && (
        /* Somebody already counted this. Loud when their count hasn't reached
           Odoo yet: both counts would then be measured against the same
           starting number, which is how a shelf ends up at a quantity nobody
           counted (2026-08-22). Quiet when it's already applied — then it's
           just why the Odoo number moved. */
        <div
          data-testid="also-counted"
          className={`mt-2 rounded-(--radius-sm) px-2.5 py-1.5 text-[12px] leading-snug ${
            alsoCounted.applied
              ? "bg-surface-container text-on-surface-variant"
              : "bg-warn-container text-on-warn-container"
          }`}
        >
          {!alsoCounted.applied && <span className="font-semibold">Just counted — </span>}
          {alsoCounted.note}
        </div>
      )}

      <div className="mt-2.5 flex items-center justify-between gap-3">
        {/* the number the counter is checking against — deliberately loud */}
        <div className="shrink-0 text-center">
          <div className="label-caps text-on-surface-variant">Odoo</div>
          <div className="display text-2xl leading-none tabular-nums">
            {odooQty === undefined ? "…" : fmtQty(odooQty)}
          </div>
        </div>
        <QtyStepper
          value={line.counted}
          onChange={onChange}
          ariaLabel={`Counted quantity for ${line.name}`}
        />
      </div>
    </li>
  );
}

/** A recount someone assigned to me — same shelf, one number. */
function RecountCard({ item }: { item: CountItemOut }) {
  const [value, setValue] = useState<number | null>(null);
  const submit = useSubmitRecount();
  const toast = useToast();
  const reason = item.events.filter((e) => e.kind === "recount_requested").slice(-1)[0];
  return (
    <li data-name-press className="rounded-(--radius-lg) bg-warn-container px-4 py-3">
      <ScrollingText text={item.name} className="text-[15px] font-medium" />
      <div className="mt-0.5 text-[12px] text-on-surface-variant">
        <span className="font-mono">{productCode(item.barcode, item.sku)}</span> ·{" "}
        {item.location_key} · counted {fmtQty(item.counted_qty ?? 0)} before
      </div>
      {reason && (
        <p className="mt-1.5 text-[12.5px] leading-4.5">
          <b>Why:</b> {reason.note}
        </p>
      )}
      <div className="mt-2.5 flex items-center justify-between gap-3">
        <div className="shrink-0 text-center">
          <div className="label-caps text-on-surface-variant">Odoo</div>
          <div className="display text-2xl leading-none tabular-nums">
            {fmtQty(item.odoo_qty ?? 0)}
          </div>
        </div>
        <QtyStepper value={value} onChange={setValue} ariaLabel={`Recount for ${item.name}`} />
      </div>
      <Button
        className="mt-2.5 w-full"
        size="sm"
        disabled={value === null}
        loading={submit.isPending}
        onClick={() =>
          submit.mutate(
            { itemId: item.id, counted_qty: value ?? 0 },
            {
              onSuccess: () => toast.success(`Recount sent for review — ${item.name}.`),
              onError: (e) => toast.error(e.message),
            },
          )
        }
      >
        Submit recount
      </Button>
    </li>
  );
}

export function InventoryCountPage() {
  const { roles } = useAuth();
  const { data: config, isLoading } = useCountLocations();
  const [location, setLocation] = usePersistedState<string>("count.location", "");
  const [lines, setLines] = usePersistedState<CountLine[]>("count.lines", []);
  const [note, setNote] = usePersistedState<string>("count.note", "");
  const [odooQty, setOdooQty] = useState<Record<number, number>>({});
  const [alsoCounted, setAlsoCounted] = useState<Record<number, RecentCountOut>>({});
  const [source, setSource] = useState<string>("live");
  const stockAt = useStockAt();
  const submit = useSubmitCount();
  const recounts = useMyRecounts(true);
  const toast = useToast();

  // the role's default location, until the counter picks one
  useEffect(() => {
    if (!location && config?.default) setLocation(config.default);
  }, [config?.default, location, setLocation]);

  const chosen = config?.locations.find((l) => l.key === location);
  const productIds = useMemo(() => lines.map((l) => l.product_id), [lines]);
  const pickedIds = useMemo(() => new Set(productIds), [productIds]);

  /* Ask Odoo what's here whenever the location or the product set changes.
     Changing location invalidates every number on screen, which is why the
     answers are keyed by product and refetched wholesale. */
  useEffect(() => {
    if (!location || productIds.length === 0) return;
    stockAt.mutate(
      { location_key: location, product_ids: productIds },
      {
        onSuccess: (out) => {
          setOdooQty(
            Object.fromEntries(Object.entries(out.quantities).map(([k, v]) => [Number(k), v])),
          );
          setSource(out.source);
          setAlsoCounted(
            Object.fromEntries(Object.entries(out.recent).map(([k, v]) => [Number(k), v])),
          );
        },
      },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location, productIds.join(",")]);

  const addLine = (p: PickedLine) => {
    // a product appears once per count: adding it again just focuses it
    if (pickedIds.has(p.product_id)) {
      toast.success(`${p.name} is already on this count — change its quantity instead.`);
      return;
    }
    // newest at the TOP — on a phone the list runs off the bottom of the
    // screen, and the row you just added is the one you're about to type into
    setLines([
      {
        product_id: p.product_id,
        sku: p.sku,
        barcode: p.barcode ?? "",
        name: p.name,
        counted: null,
      },
      ...lines,
    ]);
  };

  const counted = lines.filter((l) => l.counted !== null);
  const canSubmit = counted.length > 0 && !!chosen;

  const doSubmit = () =>
    submit.mutate(
      {
        location_key: location,
        note,
        items: counted.map((l) => ({ product_id: l.product_id, counted_qty: l.counted ?? 0 })),
      },
      {
        onSuccess: (out) => {
          setLines([]);
          setNote("");
          setOdooQty({});
          toast.success(`${out.display_name} submitted — ${out.items.length} item(s) for review.`);
        },
        onError: (e) => toast.error(e.message),
      },
    );

  if (isLoading) {
    return (
      <div className="grid place-items-center py-24">
        <Spinner size={24} />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader
        title="Inventory counting"
        subtitle="Count what's on the shelf, submit it for review. Approved counts become Odoo's number."
      />

      {(recounts.data ?? []).length > 0 && (
        <Card className="mb-4">
          <h2 className="title-m mb-1">Recounts assigned to you</h2>
          <p className="mb-2.5 text-[12.5px] text-on-surface-variant">
            Someone reviewed a count and asked for another look.
          </p>
          <ul className="flex flex-col gap-2">
            {(recounts.data ?? []).map((item) => (
              <RecountCard key={item.id} item={item} />
            ))}
          </ul>
        </Card>
      )}

      <Card className="mb-4">
        <Field label="Location">
          <Select value={location} onChange={(e) => setLocation(e.target.value)}>
            {(config?.locations ?? []).map((l) => (
              <option key={l.key} value={l.key}>
                {l.label}
              </option>
            ))}
          </Select>
        </Field>
        {chosen?.note && (
          <p className="mt-1.5 rounded-(--radius-sm) bg-warn-container px-2.5 py-1.5 text-[12.5px]">
            {chosen.note}
          </p>
        )}
        {source === "snapshot" && lines.length > 0 && (
          <p className="mt-1.5 text-[12px] text-gold">
            Odoo isn't answering — the quantities below are from the last stock sync, and the
            count will say so.
          </p>
        )}
        <div className="mt-3">
          <ProductPicker
            pickedIds={pickedIds}
            onPick={addLine}
            placeholder="Add a product to count…"
          />
        </div>
      </Card>

      {lines.length === 0 ? (
        <EmptyState
          title="Nothing counted yet"
          hint="Search above to add the products you're counting. Only rows with a number are submitted."
        />
      ) : (
        <ul className="stagger-children mb-4 flex flex-col gap-2">
          {lines.map((line) => (
            <CountRow
              key={line.product_id}
              line={line}
              odooQty={odooQty[line.product_id]}
              alsoCounted={alsoCounted[line.product_id]}
              onChange={(v) =>
                setLines(
                  lines.map((x) => (x.product_id === line.product_id ? { ...x, counted: v } : x)),
                )
              }
              onRemove={() => setLines(lines.filter((x) => x.product_id !== line.product_id))}
            />
          ))}
        </ul>
      )}

      <Card className="mb-24 md:mb-6">
        <Field label="Note for the reviewer (optional)">
          <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <span className="text-[13px] text-on-surface-variant">
            {counted.length} of {lines.length} row{lines.length === 1 ? "" : "s"} counted
            {lines.length > counted.length && " — blank rows are left out"}
          </span>
          <Button
            className="w-full sm:w-auto"
            disabled={!canSubmit}
            loading={submit.isPending}
            onClick={doSubmit}
          >
            Submit for review
          </Button>
        </div>
      </Card>

      {!roles.has("shoppe_floor") && !roles.has("admin") && (
        <p className="pb-6 text-center text-[12px] text-on-surface-variant">
          Someone with review access checks every count before it reaches Odoo.
        </p>
      )}
    </div>
  );
}
