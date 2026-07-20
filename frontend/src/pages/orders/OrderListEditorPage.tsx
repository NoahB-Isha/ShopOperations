/* Admin: edit one catalog — its products (no quantities: it's a menu) and
   which zones' coordinators may use it. */
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  useCloneOrderList,
  useDeleteOrderList,
  useOrderList,
  usePatchOrderList,
  usePutOrderListLines,
  useSetOrderListZones,
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
  Spinner,
  Textarea,
  useToast,
} from "../../design";
import { LowCountHint, ProductPicker, fmtQty } from "../shared/OpsBits";

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

interface PickedProduct {
  product_id: number;
  sku: string;
  name: string;
  is_active: boolean;
  bwhse_qty: number;
}

function fromApi(ol: OrderListOut): PickedProduct[] {
  return ol.lines.map((l) => ({
    product_id: l.product_id,
    sku: l.sku,
    name: l.name,
    is_active: l.is_active,
    bwhse_qty: l.bwhse_qty,
  }));
}

function Editor({ ol }: { ol: OrderListOut }) {
  const [products, setProducts] = useState<PickedProduct[]>(() => fromApi(ol));
  const [dirty, setDirty] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const toast = useToast();
  const navigate = useNavigate();

  const patch = usePatchOrderList();
  const putLines = usePutOrderListLines();
  const clone = useCloneOrderList();
  const del = useDeleteOrderList();

  useEffect(() => {
    if (!dirty) setProducts(fromApi(ol));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ol.updated_at, ol.id]);

  const pickedIds = useMemo(() => new Set(products.map((p) => p.product_id)), [products]);

  const mutate = (next: PickedProduct[]) => {
    setProducts(next);
    setDirty(true);
  };

  const save = () =>
    putLines.mutate(
      { id: ol.id, product_ids: products.map((p) => p.product_id) },
      {
        onSuccess: () => {
          setDirty(false);
          toast.success("Products saved.");
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
            {ol.is_archived && <Badge tone="neutral">archived</Badge>}
            <span>
              {products.length} product{products.length === 1 ? "" : "s"} · a catalog centers
              order from — no quantities here
            </span>
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
            <Button
              variant="outlined"
              loading={patch.isPending}
              onClick={() =>
                patch.mutate(
                  { id: ol.id, is_archived: !ol.is_archived },
                  {
                    onSuccess: () =>
                      toast.info(ol.is_archived ? "Restored." : "Archived — hidden from zones."),
                    onError: (e) => toast.error(e.message),
                  },
                )
              }
            >
              {ol.is_archived ? "Restore" : "Archive"}
            </Button>
            <Button variant="ghost" className="text-error" onClick={() => setConfirmDelete(true)}>
              Delete
            </Button>
          </>
        }
      />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_360px]">
        <Card pad={false}>
          <div className="flex items-center justify-between border-b border-outline-variant/60 px-5 py-3.5">
            <h3 className="headline text-[16px]">Products</h3>
            {dirty && (
              <Button size="sm" loading={putLines.isPending} onClick={save}>
                Save products
              </Button>
            )}
          </div>
          {products.length === 0 ? (
            <div className="p-6">
              <EmptyState
                title="Nothing in this catalog yet"
                hint="Search on the right — only active, Odoo-tracked products can be added."
              />
            </div>
          ) : (
            <ul>
              {products.map((p) => (
                <li
                  key={p.product_id}
                  className="flex items-center justify-between gap-3 border-b
                    border-outline-variant/50 px-5 py-2.5 last:border-b-0"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium">{p.name}</span>
                      {!p.is_active && (
                        <Badge tone="gold" title="Discontinued since it was added — remove it.">
                          inactive
                        </Badge>
                      )}
                    </div>
                    <div className="flex items-center gap-2 text-[12px] text-on-surface-variant">
                      <span className="font-mono">{p.sku}</span>
                      <span className="tabular-nums">
                        whse {fmtQty(p.bwhse_qty)} <LowCountHint qty={p.bwhse_qty} />
                      </span>
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={`Remove ${p.name}`}
                    onClick={() => mutate(products.filter((x) => x.product_id !== p.product_id))}
                  >
                    ✕
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <div className="flex flex-col gap-5">
          <Card>
            <h3 className="headline mb-3 text-[16px]">Add products</h3>
            <ProductPicker
              pickedIds={pickedIds}
              excludeClothing
              onPick={(line) =>
                mutate([
                  ...products,
                  {
                    product_id: line.product_id,
                    sku: line.sku,
                    name: line.name,
                    is_active: true,
                    bwhse_qty: line.bwhse_qty,
                  },
                ])
              }
            />
          </Card>

          <ZoneGrantsCard ol={ol} />

          <DetailsCard
            ol={ol}
            onSave={(name, notes) =>
              patch.mutate(
                { id: ol.id, name, notes },
                { onSuccess: () => toast.success("Saved."), onError: (e) => toast.error(e.message) },
              )
            }
            saving={patch.isPending}
          />
        </div>
      </div>

      <Dialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title="Delete this catalog?"
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
          “{ol.name}” disappears for every zone and center it was granted to. Centers that order
          from it lose it as a catalog (their past orders are unaffected). Prefer{" "}
          <b>Archive</b> if it might come back.
        </p>
      </Dialog>
    </>
  );
}

function ZoneGrantsCard({ ol }: { ol: OrderListOut }) {
  const { data: zones } = useZones();
  const setZones = useSetOrderListZones();
  const toast = useToast();
  const granted = new Set(ol.zones.map((z) => z.zone_id));

  const toggle = (zoneId: number) => {
    const next = new Set(granted);
    if (next.has(zoneId)) next.delete(zoneId);
    else next.add(zoneId);
    setZones.mutate(
      { id: ol.id, zone_ids: [...next] },
      {
        onSuccess: () => toast.success("Zone grants updated."),
        onError: (e) => toast.error(e.message),
      },
    );
  };

  return (
    <Card>
      <h3 className="headline mb-1 text-[16px]">Zones</h3>
      <p className="mb-3 text-[13px] text-on-surface-variant">
        Granted zones' coordinators can open this catalog to their centers. Revoking a zone also
        revokes its centers.
      </p>
      <div className="flex flex-wrap gap-1.5">
        {(zones ?? []).map((z) => {
          const on = granted.has(z.id);
          return (
            <button
              key={z.id}
              onClick={() => toggle(z.id)}
              disabled={setZones.isPending}
              className={`state-layer rounded-full px-3.5 py-1.5 text-[13px] font-semibold ${
                on
                  ? "bg-secondary-container text-on-secondary-container"
                  : "border border-outline-variant text-on-surface-variant"
              }`}
              aria-pressed={on}
            >
              {on ? "✓ " : ""}
              {z.name}
            </button>
          );
        })}
      </div>
      {ol.centers.length > 0 && (
        <p className="mt-3 text-[12.5px] text-on-surface-variant">
          Centers opened by coordinators:{" "}
          {ol.centers.map((c) => c.center_name).join(", ")}
        </p>
      )}
    </Card>
  );
}

function DetailsCard({
  ol,
  onSave,
  saving,
}: {
  ol: OrderListOut;
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
