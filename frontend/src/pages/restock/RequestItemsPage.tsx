/* Floor Team: "we need more of this."

   The Floor Team works the lists but doesn't raise transfers — so this is
   deliberately NOT the transfer page. It builds the same draft (the shared
   store, so the floating bubble follows them around exactly as it does for
   the Inventory Flow Manager) and sends it as a set of ASKS, which land on
   the manager's Suggested items page above the app's own suggestions.

   Below the picker: what they've already asked for, and what happened to it. */
import { useMemo, useRef, useState } from "react";
import { usePersistedState } from "../../persist";
import { clearDraft, setDraftLines, useDraftLines } from "../../transferDraft";
import { useFloorRequests, useRaiseFloorRequests } from "../../api/hooks";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  PageHeader,
  ScrollingText,
  Spinner,
  Textarea,
  useToast,
} from "../../design";
import {
  LowCountHint,
  ProductPicker,
  QtyInput,
  SetQtyDialog,
  fmtQty,
  fmtWhen,
  productCode,
  type PickedLine,
} from "../shared/OpsBits";

const STATUS_TONE = {
  open: "gold",
  picked_up: "forest",
  dismissed: "outline",
} as const;

const STATUS_LABEL = {
  open: "waiting",
  picked_up: "on a transfer",
  dismissed: "not needed",
} as const;

export function RequestItemsPage() {
  const lines = useDraftLines();
  const setLines = setDraftLines;
  const [note, setNote] = usePersistedState("floorRequest.note", "");
  const raise = useRaiseFloorRequests();
  const toast = useToast();
  const searchRef = useRef<HTMLInputElement>(null);
  const pendingQtyFocus = useRef<number | null>(null);
  const [tapPick, setTapPick] = useState<PickedLine | null>(null);

  // "did my ask land?" — their own board, every outcome
  const mine = useFloorRequests({ mine: true, status: "open,picked_up,dismissed" });

  const pickedIds = useMemo(() => new Set(lines.map((l) => l.product_id)), [lines]);
  const totalQty = lines.reduce((sum, l) => sum + l.qty, 0);

  const backToSearch = () => {
    searchRef.current?.focus();
    searchRef.current?.select();
  };
  const addLine = (line: PickedLine, viaEnter?: boolean) => {
    if (viaEnter) {
      pendingQtyFocus.current = line.product_id;
      setLines((prev) => [...prev, line]);
    } else {
      setTapPick(line);
    }
  };

  const submit = () =>
    raise.mutate(
      { note: note.trim(), lines: lines.map(({ product_id, qty }) => ({ product_id, qty })) },
      {
        onSuccess: (raised) => {
          const sent = raised as { id: number }[];
          clearDraft();
          setNote("");
          toast.success(
            `Asked for ${sent.length} item${sent.length === 1 ? "" : "s"} — the floor manager sees it now.`,
          );
        },
        onError: (e) => toast.error(e.message),
      },
    );

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader
        title="Request items"
        subtitle="Tell the floor manager what the shelves need. Your asks sit at the top of their suggestions — above anything the app worked out on its own."
      />

      <Card className="mb-4">
        <ProductPicker pickedIds={pickedIds} onPick={addLine} inputRef={searchRef} />
      </Card>

      {lines.length === 0 ? (
        <EmptyState
          title="Nothing asked for yet"
          hint="Search above — tap an item and say how many the floor needs."
        />
      ) : (
        <Card pad={false} className="mb-4">
          <ul>
            {lines.map((line) => (
              <li
                data-name-press
                key={line.product_id}
                className="flex items-center justify-between gap-2 border-b border-outline-variant/50
                  px-4 py-3 last:border-b-0"
              >
                <div className="min-w-0">
                  <ScrollingText text={line.name} className="text-sm font-medium" />
                  <div className="flex flex-wrap items-center gap-x-2 text-[12px] tabular-nums text-on-surface-variant">
                    <span className="font-mono">{productCode(line.barcode, line.sku)}</span>
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
                      setLines((prev) =>
                        prev.map((x) => (x.product_id === line.product_id ? { ...x, qty } : x)),
                      )
                    }
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={`Remove ${line.name}`}
                    onClick={() =>
                      setLines((prev) => prev.filter((x) => x.product_id !== line.product_id))
                    }
                  >
                    ✕
                  </Button>
                </div>
              </li>
            ))}
          </ul>
          <div className="flex items-center justify-between bg-surface-container px-4 py-2.5 text-sm">
            <span className="text-on-surface-variant">
              {lines.length} item{lines.length === 1 ? "" : "s"}
            </span>
            <span className="font-semibold tabular-nums">{fmtQty(totalQty)} units</span>
          </div>
        </Card>
      )}

      <Card className="mb-8">
        <Field label="Anything they should know (optional)">
          <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
        <div className="mt-4 flex justify-end">
          <Button
            disabled={lines.length === 0}
            loading={raise.isPending}
            onClick={submit}
            className="w-full sm:w-auto"
          >
            Send request{lines.length > 0 ? ` · ${lines.length} item${lines.length === 1 ? "" : "s"}` : ""}
          </Button>
        </div>
      </Card>

      <h2 className="headline mb-2 text-[17px]">What you've asked for</h2>
      {mine.isLoading ? (
        <div className="grid place-items-center py-10">
          <Spinner size={20} />
        </div>
      ) : !mine.data?.length ? (
        <p className="pb-24 text-sm text-on-surface-variant">
          Nothing yet. Anything you send shows up here with what happened to it.
        </p>
      ) : (
        <ul className="flex flex-col gap-2 pb-24">
          {mine.data.map((r) => (
            <li
              key={r.id}
              className="flex items-center justify-between gap-3 rounded-(--radius-lg)
                bg-surface-container-low px-4 py-3"
            >
              <div className="min-w-0">
                <div className="truncate text-[14.5px] font-medium">{r.name}</div>
                <div className="text-[12px] tabular-nums text-on-surface-variant">
                  {fmtQty(r.qty)} asked · {fmtWhen(r.created_at)}
                  {r.resolved_by && ` · ${r.resolved_by}`}
                </div>
              </div>
              <Badge tone={STATUS_TONE[r.status]}>{STATUS_LABEL[r.status]}</Badge>
            </li>
          ))}
        </ul>
      )}

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
    </div>
  );
}
