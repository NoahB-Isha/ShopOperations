/* Staging 2 → the floor. The warehouse's own page.

   The real process: pull requests into III/Staging2 however suits you, and
   when there's a pallet's worth, make ONE transfer to floor staging IN ODOO —
   then log it here so the floor's requests can close against it. Logging is
   the primary action on this page; "Send all" is still here for anyone who'd
   rather have the app draft that transfer for them.

   A validated pallet nobody logged is called out at the top: it moved real
   stock, and until someone says whose it was, every request it carried is
   still sitting in "Staged". */
import { useState } from "react";
import { useDeliveries, useRetryDeliveryCount, useSendPallet, useStaging2 } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import type { DeliveryOut } from "../../api/types";
import {
  Badge,
  Button,
  Card,
  Dialog,
  EmptyState,
  PageHeader,
  Spinner,
  useToast,
} from "../../design";
import { OdooLink, WriteStatusChip, fmtQty, fmtWhen, productCode } from "../shared/OpsBits";
import { DeliveryFormDialog } from "./DeliveryFormDialog";

const DELIVERY_TONE = {
  open: "gold",
  validated: "tertiary",
  counting: "copper",
  counted: "forest",
  cancelled: "neutral",
} as const;

const DELIVERY_LABEL = {
  open: "waiting to be validated in Odoo",
  validated: "landed at floor staging",
  counting: "counting onto the floor",
  counted: "counted onto the floor",
  cancelled: "cancelled",
} as const;

function DeliveryCard({
  d,
  canSend,
  onAddDetails,
}: {
  d: DeliveryOut;
  canSend: boolean;
  onAddDetails: () => void;
}) {
  const retry = useRetryDeliveryCount();
  const toast = useToast();
  return (
    <li className="rounded-(--radius-lg) bg-surface-container-low px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 text-[13.5px]">
            <span className="font-mono font-semibold">{d.picking_name}</span>
            <Badge tone={DELIVERY_TONE[d.status]}>{DELIVERY_LABEL[d.status]}</Badge>
            {d.needs_details && <Badge tone="danger">needs details</Badge>}
            {d.picking_status !== "none" && d.picking_status !== "created" && (
              <WriteStatusChip status={d.picking_status} error={d.picking_error} createdLabel="" />
            )}
          </div>
          <div className="mt-0.5 text-[12px] text-on-surface-variant">
            {d.item_count} item{d.item_count === 1 ? "" : "s"} · {fmtQty(d.total_units)} units ·{" "}
            {fmtWhen(d.created_at)}
            {d.validated_at ? ` · landed ${fmtWhen(d.validated_at)}` : ""}
            {d.declared_by ? ` · logged by ${d.declared_by}` : ""}
          </div>
        </div>
        <OdooLink url={d.picking_url} name={d.picking_name} />
      </div>

      {d.requests.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[12.5px] text-on-surface-variant">Carrying</span>
          {d.requests.map((r) => (
            <Badge key={r.id} tone="secondary">
              {r.display_name}
            </Badge>
          ))}
        </div>
      )}

      {d.discrepancies.length > 0 && (
        <ul className="mt-2 flex flex-col gap-0.5">
          {d.discrepancies.map((x) => (
            <li key={x.product_id} className="text-[12.5px] text-on-surface-variant">
              <b className="text-on-surface">{x.name}</b> — asked {fmtQty(x.qty_requested)}, sent{" "}
              {fmtQty(x.qty_sent)}: {x.reason_labels.join("; ")}
              {x.note ? ` — ${x.note}` : ""}
            </li>
          ))}
        </ul>
      )}

      {d.note && <p className="mt-1.5 text-[12.5px] italic">“{d.note}”</p>}

      <div className="mt-2 flex flex-wrap items-center gap-2">
        {d.needs_details && (
          <Button size="sm" onClick={onAddDetails}>
            Add details
          </Button>
        )}
        {d.count.status === "created" && (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => window.open(d.count.barcode_url || d.count.url, "_blank")}
          >
            Count onto the floor ↗
          </Button>
        )}
        {d.count.status === "failed" && canSend && (
          <Button
            size="sm"
            variant="ghost"
            loading={retry.isPending}
            onClick={() =>
              retry.mutate(d.id, { onError: (e) => toast.error(e.message) })
            }
          >
            Retry the count transfer
          </Button>
        )}
        {d.count.status === "failed" && (
          <span className="text-[12.5px] text-error">{d.count.error}</span>
        )}
      </div>
    </li>
  );
}

export function Staging2Page() {
  const { roles } = useAuth();
  const canSend = roles.has("warehouse") || roles.has("admin");
  const { data, isLoading } = useStaging2();
  const deliveries = useDeliveries();
  const send = useSendPallet();
  const toast = useToast();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [formPicking, setFormPicking] = useState<number | null>(null);

  const items = data?.items ?? [];
  const rows = deliveries.data ?? [];
  const undeclared = rows.filter((d) => d.needs_details);

  const openForm = (pickingId: number | null) => {
    setFormPicking(pickingId);
    setFormOpen(true);
  };

  const doSend = () =>
    send.mutate(undefined, {
      onSuccess: (out) => {
        setConfirmOpen(false);
        const pallet = out.pallets[0];
        toast.success(
          pallet?.picking_status === "created"
            ? `Pallet ${pallet.picking_name} rendered as a draft — validate it in Odoo, then log what's on it.`
            : "Pallet recorded (draft simulated — writes are gated).",
        );
      },
      onError: (e) => toast.error(e.message),
    });

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader
        title="Send to floor"
        subtitle="Staging 2 is where transfers pile up. Make the pallet transfer in Odoo, then log it here so the floor's requests close against it."
      />

      {undeclared.length > 0 && canSend && (
        <Card tone="primary" className="mb-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[16px] font-semibold">
                {undeclared.length === 1
                  ? `${undeclared[0].picking_name} landed — what was in it?`
                  : `${undeclared.length} pallets landed with no details`}
              </div>
              <p className="mt-1 text-sm opacity-90">
                Until someone says which requests it carried, the floor's requests stay open.
              </p>
            </div>
            <Button variant="elevated" onClick={() => openForm(undeclared[0].odoo_picking_id)}>
              Add details
            </Button>
          </div>
        </Card>
      )}

      {canSend && (
        <Card className="mb-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[15px] font-semibold">Sent a pallet to the floor?</div>
              <div className="text-[13px] text-on-surface-variant">
                Log the Odoo transfer, say which requests are in it, and explain anything short.
              </div>
            </div>
            <Button onClick={() => openForm(null)}>Log a transfer</Button>
          </div>
        </Card>
      )}

      {data?.note && (
        <p className="mb-3 rounded-(--radius-md) bg-warn-container px-3 py-2 text-[13px] text-on-surface">
          {data.note}
        </p>
      )}

      <h2 className="title-m mb-2 text-on-surface">In Staging 2 right now</h2>
      {isLoading ? (
        <div className="grid place-items-center py-24">
          <Spinner size={24} />
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          title="Staging 2 is empty"
          hint="Items appear the moment a transfer is validated into III/Staging2 — then one pallet takes them all to the floor."
        />
      ) : (
        <>
          <ul className="stagger-children flex flex-col gap-2">
            {items.map((item) => (
              <li
                key={item.product_id}
                className="flex items-center justify-between gap-3 rounded-(--radius-lg) bg-surface-container-low px-4 py-3"
              >
                <div className="min-w-0">
                  <div className="truncate text-[15px] font-medium">{item.name}</div>
                  <div className="mt-0.5 font-mono text-[12px] text-on-surface-variant">
                    {productCode(item.barcode, item.sku)}
                  </div>
                </div>
                <span className="display shrink-0 text-2xl leading-none">{fmtQty(item.qty)}</span>
              </li>
            ))}
          </ul>
          <div className="mt-4 mb-2 flex flex-wrap items-center justify-between gap-2">
            <span className="text-[13.5px] text-on-surface-variant">
              {items.length} item(s) · {fmtQty(data?.total_units ?? 0)} units
              {data?.source === "snapshot" ? " · from the last stock sync" : ""}
            </span>
            {canSend && (
              <Button
                variant="secondary"
                className="w-full sm:w-auto"
                onClick={() => setConfirmOpen(true)}
              >
                Or let the app draft it
              </Button>
            )}
          </div>
        </>
      )}

      <Card className="mt-5" pad={false}>
        <div className="border-b border-outline-variant/60 px-5 py-3.5">
          <h2 className="title-m text-on-surface">Recent deliveries to the floor</h2>
        </div>
        {deliveries.isLoading ? (
          <div className="grid place-items-center py-10">
            <Spinner size={22} />
          </div>
        ) : rows.length === 0 ? (
          <p className="px-5 py-4 text-[13px] text-on-surface-variant">
            Nothing yet. When you send a pallet to floor staging, log it here.
          </p>
        ) : (
          <ul className="flex flex-col gap-1.5 p-3">
            {rows.map((d) => (
              <DeliveryCard
                key={d.id}
                d={d}
                canSend={canSend}
                onAddDetails={() => openForm(d.odoo_picking_id)}
              />
            ))}
          </ul>
        )}
      </Card>

      <DeliveryFormDialog
        open={formOpen}
        onClose={() => setFormOpen(false)}
        initialPickingId={formPicking}
      />

      <Dialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title="Let the app draft the pallet?"
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button loading={send.isPending} onClick={doSend}>
              Render the pallet draft
            </Button>
          </div>
        }
      >
        <p className="text-[13.5px] leading-5 text-on-surface-variant">
          This renders <b>one draft transfer</b> moving all{" "}
          <b>
            {items.length} item(s) · {fmtQty(data?.total_units ?? 0)} units
          </b>{" "}
          from III/Staging2 to III-FLORR-STAGING. Nothing moves until someone validates it in
          Odoo — and once it's validated you still log what's on it here, so the floor's
          requests know they've arrived.
        </p>
      </Dialog>
    </div>
  );
}
