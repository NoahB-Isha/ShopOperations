import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  useAddVendorProduct,
  useRemoveVendorProduct,
  useSaveVendor,
  useVendorProducts,
  useVendors,
} from "../../api/hooks";
import type { VendorOut, VendorProductOut } from "../../api/types";
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
import { ProductPicker } from "../shared/OpsBits";

export function VendorsPage() {
  const vendors = useVendors();
  const navigate = useNavigate();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [editing, setEditing] = useState<VendorOut | "new" | null>(null);

  const list = vendors.data ?? [];
  const selected = list.find((v) => v.id === selectedId) ?? list[0] ?? null;

  return (
    <div>
      <PageHeader
        title="Vendors"
        subtitle="Who supplies what, and where their order emails go. Ordering itself lives on Purchasing → Domestic."
        actions={
          <>
            <Button variant="ghost" onClick={() => navigate("/purchasing")}>
              ← Purchasing
            </Button>
            <Button variant="secondary" onClick={() => setEditing("new")}>
              New vendor
            </Button>
          </>
        }
      />
      {vendors.isLoading ? (
        <div className="grid min-h-40 place-items-center">
          <Spinner size={22} />
        </div>
      ) : list.length === 0 ? (
        <EmptyState
          title="No vendors yet"
          hint="Add a vendor, attach its products below, and order from Purchasing → Domestic."
          action={<Button onClick={() => setEditing("new")}>New vendor</Button>}
        />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[300px_1fr]">
          <div className="grid content-start gap-2">
            {list.map((v) => (
              <div key={v.id} onClick={() => setSelectedId(v.id)}>
                <Card
                  className={`cursor-pointer p-3.5 transition-colors ${selected?.id === v.id ? "outline-2 outline-primary" : "hover:bg-on-surface/4"}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate font-semibold">{v.name}</div>
                      <div className="truncate text-[12.5px] text-on-surface-variant">
                        {v.contact_email || "no email"} · {v.product_count} product(s)
                      </div>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-1">
                      <Badge tone={v.kind === "india" ? "copper" : "secondary"}>{v.kind}</Badge>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          setEditing(v);
                        }}
                      >
                        edit
                      </Button>
                    </div>
                  </div>
                </Card>
              </div>
            ))}
          </div>
          <div>
            {selected ? (
              <VendorRoster vendor={selected} />
            ) : (
              <EmptyState title="Pick a vendor" hint="Its products appear here." />
            )}
          </div>
        </div>
      )}
      {editing && (
        <VendorDialog
          vendor={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

/** The vendor's product roster: search the catalog, add, set MOQ, remove. */
function VendorRoster({ vendor }: { vendor: VendorOut }) {
  const products = useVendorProducts(vendor.id);
  const add = useAddVendorProduct();
  const remove = useRemoveVendorProduct();
  const toast = useToast();
  const navigate = useNavigate();
  const roster = products.data ?? [];
  const pickedIds = new Set(roster.map((p) => p.product_id));

  return (
    <div className="grid gap-4">
      <Card className="p-4">
        <div className="mb-1 flex items-center justify-between">
          <h2 className="headline text-[18px]">Add products</h2>
          <Button variant="ghost" size="sm" onClick={() => navigate("/purchasing")}>
            Order from {vendor.name} →
          </Button>
        </div>
        <p className="mb-3 text-[13px] text-on-surface-variant">
          Search the catalog and add what {vendor.name} supplies. A product belongs to one
          vendor; its suggested reorder uses the MOQ when cover runs low.
        </p>
        <ProductPicker
          pickedIds={pickedIds}
          excludeClothing
          placeholder={`Search products to add to ${vendor.name}…`}
          onPick={(line) =>
            add.mutate(
              { vendorId: vendor.id, productId: line.product_id },
              {
                onSuccess: () => toast.success(`${line.name} added to ${vendor.name}.`),
                onError: (e) => toast.error(e instanceof Error ? e.message : "Could not add"),
              },
            )
          }
        />
      </Card>

      <div className="overflow-x-auto rounded-(--radius-lg) bg-surface-container-low">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="bg-surface-container">
              {["Product", "MOQ", ""].map((h, i) => (
                <th key={i} className="label-m px-3.5 py-3 text-left">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {roster.map((p) => (
              <RosterRow key={p.product_id} vendor={vendor} product={p} onRemove={() =>
                remove.mutate(
                  { vendorId: vendor.id, productId: p.product_id },
                  { onSuccess: () => toast.info(`${p.name} removed.`) },
                )
              } />
            ))}
          </tbody>
        </table>
        {!products.isLoading && roster.length === 0 && (
          <div className="p-6">
            <EmptyState
              title={`Nothing attached to ${vendor.name} yet`}
              hint="Search above to add its products."
            />
          </div>
        )}
      </div>
    </div>
  );
}

function RosterRow({
  vendor,
  product,
  onRemove,
}: {
  vendor: VendorOut;
  product: VendorProductOut;
  onRemove: () => void;
}) {
  const add = useAddVendorProduct(); // MOQ upsert rides the same endpoint
  const toast = useToast();
  const [moq, setMoq] = useState<string | null>(null);
  const shown = moq ?? (product.moq ? String(product.moq) : "");

  const commit = () => {
    if (moq === null) return;
    const parsed = Math.max(1, Math.round(Number(moq) || 0));
    setMoq(null);
    if (!Number(moq) || parsed === product.moq) return;
    add.mutate(
      { vendorId: vendor.id, productId: product.product_id, moq: parsed },
      { onError: (e) => toast.error(e instanceof Error ? e.message : "Could not save MOQ") },
    );
  };

  return (
    <tr className="border-b border-outline-variant/50 last:border-b-0">
      <td className="max-w-96 px-3.5 py-2">
        <div className="truncate font-medium">
          {product.name}
          {!product.is_active && (
            <Badge tone="neutral"> inactive</Badge>
          )}
        </div>
        <div className="text-[12px] text-on-surface-variant">
          {product.global_sku}
          {product.category ? ` · ${product.category}` : ""}
        </div>
      </td>
      <td className="px-3.5 py-2">
        <input
          type="number"
          min={1}
          value={shown}
          placeholder="—"
          onChange={(e) => setMoq(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
          aria-label={`MOQ for ${product.global_sku}`}
          className="w-24 rounded-(--radius-sm) border border-outline-variant bg-field px-2 py-1.5 text-right tabular-nums"
        />
      </td>
      <td className="px-3.5 py-2 text-right">
        <Button variant="ghost" size="sm" onClick={onRemove}>
          remove
        </Button>
      </td>
    </tr>
  );
}

function VendorDialog({ vendor, onClose }: { vendor: VendorOut | null; onClose: () => void }) {
  const save = useSaveVendor();
  const toast = useToast();
  const [form, setForm] = useState({
    name: vendor?.name ?? "",
    kind: vendor?.kind ?? "us",
    contact_name: vendor?.contact_name ?? "",
    contact_email: vendor?.contact_email ?? "",
    cc_emails: vendor?.cc_emails ?? "",
    notes: vendor?.notes ?? "",
    active: vendor?.active ?? true,
  });
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <Dialog
      open
      onClose={onClose}
      title={vendor ? `Edit ${vendor.name}` : "New vendor"}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!form.name.trim()}
            loading={save.isPending}
            onClick={() =>
              save.mutate(
                { id: vendor?.id, ...form, kind: form.kind as VendorOut["kind"] },
                {
                  onSuccess: () => {
                    toast.success("Vendor saved.");
                    onClose();
                  },
                  onError: (e) => toast.error(e instanceof Error ? e.message : "Save failed"),
                },
              )
            }
          >
            Save
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        <Field label="Name">
          <Input value={form.name} onChange={set("name")} placeholder=" " />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Kind">
            <Select value={form.kind} onChange={set("kind")}>
              <option value="us">US (domestic)</option>
              <option value="india">India</option>
              <option value="canada">Canada</option>
              <option value="other">Other</option>
            </Select>
          </Field>
          <Field label="Contact name" help="Order emails open with “Dear {name},”">
            <Input value={form.contact_name} onChange={set("contact_name")} placeholder=" " />
          </Field>
        </div>
        <Field label="Order email (To)">
          <Input value={form.contact_email} onChange={set("contact_email")} placeholder=" " />
        </Field>
        <Field label="CC" help="Comma-separated.">
          <Input value={form.cc_emails} onChange={set("cc_emails")} placeholder=" " />
        </Field>
        <Field label="Notes">
          <Textarea rows={2} value={form.notes} onChange={set("notes")} placeholder=" " />
        </Field>
      </div>
    </Dialog>
  );
}
