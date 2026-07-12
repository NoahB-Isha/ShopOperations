/* Admin: edit one order list — lines with quantities, clone, assign to a
   zone coordinator, delete. Approved lists show the write outcome and the
   Odoo deep link (the human handoff is part of the feature). */
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  useAssignOrderList,
  useCenters,
  useCloneOrderList,
  useDeleteOrderList,
  useOrderList,
  usePatchOrderList,
  usePutOrderListLines,
  useZones,
} from "../../api/hooks";
import type { OrderListOut } from "../../api/types";
import {
  Badge,
  Button,
  Card,
  Dialog,
  EmptyState,
  Field,
  Input,
  PageHeader,
  Select,
  Spinner,
  Textarea,
  useToast,
} from "../../design";
import {
  LowCountHint,
  OdooLink,
  ProductPicker,
  QtyInput,
  WriteStatusChip,
  fmtQty,
  type PickedLine,
} from "../shared/OpsBits";

const EDITABLE = new Set(["draft", "returned"]);

export function OrderListEditorPage() {
  const { id } = useParams();
  const listId = Number(id);
  const { data: ol, isLoading } = useOrderList(Number.isFinite(listId) ? listId : null);

  if (isLoading || !ol) {
    return (
      <div className="grid place-items-center py-24">
        <Spinner size={24} />
      </div>
    );
  }
  return <Editor ol={ol} />;
}

function Editor({ ol }: { ol: OrderListOut }) {
  const editable = EDITABLE.has(ol.status);
  const [lines, setLines] = useState<PickedLine[]>(() => fromApi(ol));
  const [dirty, setDirty] = useState(false);
  const [assigning, setAssigning] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const toast = useToast();
  const navigate = useNavigate();

  const patch = usePatchOrderList();
  const putLines = usePutOrderListLines();
  const clone = useCloneOrderList();
  const del = useDeleteOrderList();

  // server wins whenever we aren't mid-edit (e.g. after assign/approve)
  useEffect(() => {
    if (!dirty) setLines(fromApi(ol));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ol.updated_at, ol.id]);

  const pickedIds = useMemo(() => new Set(lines.map((l) => l.product_id)), [lines]);
  const totalQty = lines.reduce((sum, l) => sum + l.qty, 0);

  const mutateLines = (next: PickedLine[]) => {
    setLines(next);
    setDirty(true);
  };

  const saveLines = () =>
    putLines.mutate(
      { id: ol.id, lines: lines.map(({ product_id, qty }) => ({ product_id, qty })) },
      {
        onSuccess: () => {
          setDirty(false);
          toast.success("Lines saved.");
        },
        onError: (e) => toast.error(e.message),
      },
    );

  return (
    <>
      <PageHeader
        title={ol.name}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge ol={ol} />
            <WriteStatusChip
              status={ol.write_status}
              dryRunReason={ol.write_dry_run_reason}
              error={ol.write_error}
            />
            {ol.zone_name && <span>→ {ol.center_name} ({ol.zone_name})</span>}
          </span>
        }
        actions={
          <>
            <Button variant="ghost" onClick={() => navigate("/orders")}>
              All lists
            </Button>
            <Button
              variant="outlined"
              loading={clone.isPending}
              onClick={() =>
                clone.mutate(ol.id, {
                  onSuccess: (c) => {
                    toast.success("Cloned.");
                    navigate(`/orders/${(c as { id: number }).id}`);
                  },
                  onError: (e) => toast.error(e.message),
                })
              }
            >
              Clone
            </Button>
            {editable && (
              <>
                <Button variant="ghost" className="text-error" onClick={() => setConfirmDelete(true)}>
                  Delete
                </Button>
                <Button onClick={() => setAssigning(true)} disabled={lines.length === 0 || dirty}>
                  Assign…
                </Button>
              </>
            )}
          </>
        }
      />

      {ol.status === "returned" && ol.returned_note && (
        <Card tone="secondary" className="mb-5">
          <div className="text-xs font-bold tracking-wide uppercase opacity-75">
            Returned by the coordinator
          </div>
          <p className="mt-1 text-[15px]">“{ol.returned_note}”</p>
          <p className="mt-1 text-[13px] opacity-75">Edit the list and re-assign when ready.</p>
        </Card>
      )}

      {ol.status === "approved" && <ApprovedPanel ol={ol} />}

      <div className="grid gap-5 lg:grid-cols-[1fr_360px]">
        <Card pad={false}>
          <div className="flex items-center justify-between border-b border-outline-variant/60 px-5 py-3.5">
            <h3 className="headline text-[16px]">
              Items <span className="text-on-surface-variant">— {fmtQty(totalQty)} units</span>
            </h3>
            {editable && dirty && (
              <Button size="sm" loading={putLines.isPending} onClick={saveLines}>
                Save lines
              </Button>
            )}
          </div>
          {lines.length === 0 ? (
            <div className="p-6">
              <EmptyState
                title="Nothing on this list yet"
                hint={editable ? "Search on the right to add items." : "This list is empty."}
              />
            </div>
          ) : (
            <ul>
              {lines.map((line) => (
                <li
                  key={line.product_id}
                  className="flex items-center justify-between gap-3 border-b
                    border-outline-variant/50 px-5 py-2.5 last:border-b-0"
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">{line.name}</div>
                    <div className="flex items-center gap-2 text-[12px] text-on-surface-variant">
                      <span className="font-mono">{line.sku}</span>
                      <span className="tabular-nums">
                        whse {fmtQty(line.bwhse_qty)} <LowCountHint qty={line.bwhse_qty} />
                      </span>
                      {line.qty > line.bwhse_qty && (
                        <Badge tone="gold" title="Requested more than the warehouse shows on hand.">
                          over stock
                        </Badge>
                      )}
                    </div>
                  </div>
                  {editable ? (
                    <div className="flex shrink-0 items-center gap-1">
                      <QtyInput
                        value={line.qty}
                        min={1}
                        ariaLabel={`Quantity for ${line.name}`}
                        onChange={(qty) =>
                          mutateLines(
                            lines.map((x) => (x.product_id === line.product_id ? { ...x, qty } : x)),
                          )
                        }
                      />
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label={`Remove ${line.name}`}
                        onClick={() =>
                          mutateLines(lines.filter((x) => x.product_id !== line.product_id))
                        }
                      >
                        ✕
                      </Button>
                    </div>
                  ) : (
                    <span className="text-sm font-semibold tabular-nums">{fmtQty(line.qty)}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>

        <div className="flex flex-col gap-5">
          {editable && (
            <Card>
              <h3 className="headline mb-3 text-[16px]">Add items</h3>
              <ProductPicker
                pickedIds={pickedIds}
                onPick={(line) => mutateLines([...lines, line])}
              />
            </Card>
          )}
          <NotesCard ol={ol} editable={editable} onSave={(name, notes) =>
            patch.mutate(
              { id: ol.id, name, notes },
              { onSuccess: () => toast.success("Saved."), onError: (e) => toast.error(e.message) },
            )
          } saving={patch.isPending} />
        </div>
      </div>

      <AssignDialog ol={ol} open={assigning} onClose={() => setAssigning(false)} />

      <Dialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title="Delete this list?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
              Keep it
            </Button>
            <Button
              variant="danger"
              loading={del.isPending}
              onClick={() =>
                del.mutate(ol.id, {
                  onSuccess: () => {
                    toast.info("List deleted.");
                    navigate("/orders");
                  },
                  onError: (e) => toast.error(e.message),
                })
              }
            >
              Delete
            </Button>
          </>
        }
      >
        <p className="text-sm leading-6 text-on-surface-variant">
          “{ol.name}” and its {ol.lines.length} line(s) will be removed. Only drafts and returned
          lists can be deleted — approved lists stay as the paper trail.
        </p>
      </Dialog>
    </>
  );
}

function fromApi(ol: OrderListOut): PickedLine[] {
  return ol.lines.map((l) => ({
    product_id: l.product_id,
    sku: l.sku,
    name: l.name,
    category: l.category,
    qty: l.qty,
    floor_qty: 0,
    bwhse_qty: l.bwhse_qty,
  }));
}

function StatusBadge({ ol }: { ol: OrderListOut }) {
  const map = {
    draft: ["neutral", "Draft"],
    pending_approval: ["gold", "Pending approval"],
    approved: ["forest", "Approved"],
    returned: ["danger", "Returned"],
  } as const;
  const [tone, label] = map[ol.status];
  return <Badge tone={tone}>{label}</Badge>;
}

function ApprovedPanel({ ol }: { ol: OrderListOut }) {
  return (
    <Card
      tone={ol.write_status === "created" ? "none" : "none"}
      variant="outlined"
      className="mb-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-sm">
            Approved by <span className="font-semibold">{ol.approved_by || "—"}</span>
            {ol.approved_at ? ` on ${new Date(ol.approved_at).toLocaleString()}` : ""}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[13px] text-on-surface-variant">
            <span className="font-mono">{ol.write_reference}</span>
            {ol.write_status === "simulated" && (
              <span>
                No Odoo record exists — writes are gated ({ol.write_dry_run_reason.replace("_", " ")}).
              </span>
            )}
            {ol.write_status === "failed" && <span className="text-error">{ol.write_error}</span>}
          </div>
        </div>
        {ol.odoo_url && <OdooLink url={ol.odoo_url} name={ol.odoo_picking_name || "the draft"} />}
      </div>
    </Card>
  );
}

function NotesCard({
  ol,
  editable,
  onSave,
  saving,
}: {
  ol: OrderListOut;
  editable: boolean;
  onSave: (name: string, notes: string) => void;
  saving: boolean;
}) {
  const [name, setName] = useState(ol.name);
  const [notes, setNotes] = useState(ol.notes);
  useEffect(() => {
    setName(ol.name);
    setNotes(ol.notes);
  }, [ol.id, ol.name, ol.notes]);
  const changed = name !== ol.name || notes !== ol.notes;

  if (!editable) {
    return ol.notes ? (
      <Card>
        <h3 className="headline mb-2 text-[16px]">Notes</h3>
        <p className="text-sm whitespace-pre-wrap text-on-surface-variant">{ol.notes}</p>
      </Card>
    ) : null;
  }
  return (
    <Card>
      <h3 className="headline mb-3 text-[16px]">Details</h3>
      <div className="flex flex-col gap-4">
        <Field label="Name">
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Notes">
          <Textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />
        </Field>
        {changed && (
          <Button size="sm" loading={saving} onClick={() => onSave(name.trim(), notes.trim())}>
            Save details
          </Button>
        )}
      </div>
    </Card>
  );
}

function AssignDialog({
  ol,
  open,
  onClose,
}: {
  ol: OrderListOut;
  open: boolean;
  onClose: () => void;
}) {
  const { data: zones } = useZones();
  const [zoneId, setZoneId] = useState<number | "">(ol.zone_id ?? "");
  const { data: centers } = useCenters(zoneId === "" ? {} : { zone_id: zoneId });
  const [centerId, setCenterId] = useState<number | "">(ol.center_id ?? "");
  const assign = useAssignOrderList();
  const toast = useToast();

  const zoneCenters = (centers ?? []).filter((c) => c.is_active);
  const chosenCenter = zoneCenters.find((c) => c.id === centerId);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Assign for approval"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={zoneId === "" || centerId === ""}
            loading={assign.isPending}
            onClick={() =>
              assign.mutate(
                { id: ol.id, zone_id: Number(zoneId), center_id: Number(centerId) },
                {
                  onSuccess: () => {
                    toast.success("Assigned — the coordinator will see it under Pending orders.");
                    onClose();
                  },
                  onError: (e) => toast.error(e.message),
                },
              )
            }
          >
            Assign
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm leading-6 text-on-surface-variant">
          The zone's coordinator reviews and approves; approval creates a <b>draft</b> internal
          transfer in Odoo from BWHSE to the center's location. A human still validates it in Odoo.
        </p>
        <Field label="Zone">
          <Select
            value={String(zoneId)}
            onChange={(e) => {
              setZoneId(e.target.value === "" ? "" : Number(e.target.value));
              setCenterId("");
            }}
          >
            <option value="">Choose a zone…</option>
            {(zones ?? []).map((z) => (
              <option key={z.id} value={z.id}>
                {z.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Destination center">
          <Select
            value={String(centerId)}
            onChange={(e) => setCenterId(e.target.value === "" ? "" : Number(e.target.value))}
            disabled={zoneId === ""}
          >
            <option value="">Choose a center…</option>
            {zoneCenters.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
        </Field>
        {chosenCenter && !chosenCenter.odoo_location_id && (
          <div className="rounded-(--radius-md) bg-warn-container px-3.5 py-2.5 text-[13px] text-on-warn-container">
            No Odoo location is mapped for {chosenCenter.name} yet — approval will fail until a
            stock sync matches “III/CityCenter/{chosenCenter.name}”. You can still assign now.
          </div>
        )}
      </div>
    </Dialog>
  );
}
