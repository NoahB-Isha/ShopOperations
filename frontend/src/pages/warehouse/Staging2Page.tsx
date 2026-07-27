/* III/Staging2 — the warehouse's pallet consolidation point.

   The real process: retarget outbound transfers to Staging2, pick several
   into it, then send ONE pallet to floor staging. This page shows what's
   sitting there right now (live read) and the big button renders that
   pallet as a DRAFT transfer a human validates in Odoo. Validation is what
   flips the waiting requests to "counting". */
import { useState } from "react";
import { useSendPallet, useStaging2 } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import type { PalletOut } from "../../api/types";
import { Badge, Button, Card, Dialog, EmptyState, PageHeader, Spinner, useToast } from "../../design";
import { OdooLink, WriteStatusChip, fmtQty, fmtWhen, productCode } from "../shared/OpsBits";

function PalletRow({ p }: { p: PalletOut }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 rounded-(--radius-md) bg-surface-container-low px-3 py-2">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2 text-[13.5px]">
          <span className="font-mono font-semibold">{p.picking_name || "pallet"}</span>
          <Badge
            tone={p.status === "validated" ? "forest" : p.status === "cancelled" ? "danger" : "gold"}
          >
            {p.status === "open" ? "awaiting validation" : p.status}
          </Badge>
          <WriteStatusChip status={p.picking_status} error={p.picking_error} createdLabel="" />
        </div>
        <div className="mt-0.5 text-[12px] text-on-surface-variant">
          {p.line_count} item(s) · {fmtQty(p.total_units)} units · {fmtWhen(p.created_at)}
          {p.validated_at ? ` · landed ${fmtWhen(p.validated_at)}` : ""}
        </div>
      </div>
      <OdooLink url={p.picking_url} name={p.picking_name} />
    </li>
  );
}

export function Staging2Page() {
  const { roles } = useAuth();
  const canSend = roles.has("warehouse") || roles.has("admin");
  const { data, isLoading } = useStaging2();
  const send = useSendPallet();
  const toast = useToast();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const items = data?.items ?? [];

  const doSend = () =>
    send.mutate(undefined, {
      onSuccess: (out) => {
        setConfirmOpen(false);
        const pallet = out.pallets[0];
        toast.success(
          pallet?.picking_status === "created"
            ? `Pallet ${pallet.picking_name} rendered as a draft — validate it in Odoo.`
            : "Pallet recorded (draft simulated — writes are gated).",
        );
      },
      onError: (e) => toast.error(e.message),
    });

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader
        title="Staging 2"
        subtitle="The pallet consolidation point (III/Staging2). Retargeted transfers pile up here — one button sends everything to floor staging."
      />

      {data?.note && (
        <p className="mb-3 rounded-(--radius-md) bg-warn-container px-3 py-2 text-[13px] text-on-surface">
          {data.note}
        </p>
      )}

      {isLoading ? (
        <div className="grid place-items-center py-24">
          <Spinner size={24} />
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          title="Staging 2 is empty"
          hint="Items appear the moment a transfer is validated into III/Staging2 — then one pallet sends them all to the floor."
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
              <Button className="w-full sm:w-auto" onClick={() => setConfirmOpen(true)}>
                Send all to III-FLORR-STAGING
              </Button>
            )}
          </div>
        </>
      )}

      {(data?.pallets ?? []).length > 0 && (
        <Card className="mt-5">
          <h2 className="title-m mb-2 text-on-surface">Pallets</h2>
          <ul className="flex flex-col gap-1.5">
            {(data?.pallets ?? []).map((p) => (
              <PalletRow key={p.id} p={p} />
            ))}
          </ul>
        </Card>
      )}

      <Dialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title="Send the pallet?"
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
          Odoo — and when they do, the waiting transfer requests flip to “counting”
          automatically.
        </p>
      </Dialog>
    </div>
  );
}
