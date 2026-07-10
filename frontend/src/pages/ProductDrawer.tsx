import { useEffect, useState } from "react";
import type { ProductOut, TagOut } from "../api/types";
import { usePatchProduct, useSaveTags } from "../api/hooks";
import { Badge, Button, Drawer, Field, Input, Toggle, useToast } from "../design";

const ALL_TAGS: { tag: string; label: string }[] = [
  { tag: "air_only", label: "Air only" },
  { tag: "sea_only", label: "Sea only" },
  { tag: "gold", label: "Gold" },
  { tag: "silver", label: "Silver" },
  { tag: "bloom", label: "Bloom" },
  { tag: "camphor", label: "Camphor" },
  { tag: "toothpaste", label: "Toothpaste" },
  { tag: "expires", label: "Expires (date)" },
];

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-line/60 py-2 text-sm last:border-0">
      <span className="label-caps shrink-0">{label}</span>
      <span className="min-w-0 text-right">{children}</span>
    </div>
  );
}

export function ProductDrawer({
  product,
  onClose,
  isAdmin,
}: {
  product: ProductOut | null;
  onClose: () => void;
  isAdmin: boolean;
}) {
  const toast = useToast();
  const saveTags = useSaveTags();
  const patch = usePatchProduct();

  const [tags, setTags] = useState<Record<string, string | true>>({});
  const [caseSize, setCaseSize] = useState("1");
  const [deptOrderable, setDeptOrderable] = useState(false);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!product) return;
    const t: Record<string, string | true> = {};
    for (const tag of product.tags) t[tag.tag] = tag.expires_on ?? true;
    setTags(t);
    setCaseSize(String(product.case_size));
    setDeptOrderable(product.dept_orderable);
    setDirty(false);
  }, [product]);

  if (!product) return null;

  const stock = product.stock;
  const lowNote =
    product.is_stock_tracked &&
    Object.values(stock).some((v) => v > 0 && v <= 3);

  const toggleTag = (tag: string) => {
    setDirty(true);
    setTags((prev) => {
      const next = { ...prev };
      if (tag in next) delete next[tag];
      else next[tag] = tag === "expires" ? "" : true;
      // air/sea are mutually exclusive
      if (tag === "air_only") delete next.sea_only;
      if (tag === "sea_only") delete next.air_only;
      return next;
    });
  };

  const save = async () => {
    const tagList: TagOut[] = Object.entries(tags).map(([tag, v]) => ({
      tag,
      expires_on: tag === "expires" ? (typeof v === "string" && v ? v : null) : null,
    }));
    if (tagList.some((t) => t.tag === "expires" && !t.expires_on)) {
      toast.error("The Expires tag needs a date.");
      return;
    }
    try {
      await saveTags.mutateAsync({ id: product.id, tags: tagList });
      await patch.mutateAsync({
        id: product.id,
        case_size: Number(caseSize) || 1,
        dept_orderable: deptOrderable,
      });
      toast.success("Saved.");
      setDirty(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed.");
    }
  };

  return (
    <Drawer
      open
      onClose={onClose}
      title={product.name}
      footer={
        isAdmin ? (
          <>
            <Button variant="ghost" onClick={onClose}>Close</Button>
            <Button onClick={save} loading={saveTags.isPending || patch.isPending} disabled={!dirty}>
              Save changes
            </Button>
          </>
        ) : undefined
      }
    >
      <div className="flex flex-col gap-5">
        <div>
          <Row label="Global SKU">
            <span className="font-mono text-[13px]">{product.global_sku}</span>
          </Row>
          {product.us_sku !== product.global_sku && (
            <Row label="US SKU"><span className="font-mono text-[13px]">{product.us_sku}</span></Row>
          )}
          {product.barcode && (
            <Row label="Barcode"><span className="font-mono text-[13px]">{product.barcode}</span></Row>
          )}
          <Row label="Category">{product.category || "—"}</Row>
          <Row label="Price">${product.retail_price.toFixed(2)}</Row>
          <Row label="Cost">${product.cost.toFixed(2)}</Row>
          <Row label="Source">
            {product.source === "odoo" ? (
              <Badge tone="forest">Odoo-synced</Badge>
            ) : (
              <Badge tone="outline">app-only · untracked</Badge>
            )}
          </Row>
        </div>

        {product.is_stock_tracked && (
          <div>
            <div className="label-caps mb-2">On hand</div>
            <div className="grid grid-cols-3 gap-2">
              {(["bwhse", "floor", "staging"] as const).map((k) => (
                <div key={k} className="rounded-(--radius-sm) border border-line bg-raised/60 p-2.5 text-center">
                  <div className="label-caps">{k}</div>
                  <div className="display mt-0.5 text-xl tabular-nums">{stock[k] ?? 0}</div>
                </div>
              ))}
            </div>
            {lowNote && (
              <p className="mt-2 text-[12.5px] leading-4.5 text-gold">
                ⚠ Low counts are often wrong at this scale — verify physically before promising
                stock.
              </p>
            )}
          </div>
        )}

        {isAdmin && (
          <>
            <div>
              <div className="label-caps mb-2">App tags</div>
              <div className="grid grid-cols-2 gap-1.5">
                {ALL_TAGS.map(({ tag, label }) => (
                  <label
                    key={tag}
                    className={`flex cursor-pointer items-center gap-2 rounded-(--radius-sm) border
                      px-2.5 py-1.5 text-[13px] transition-colors
                      ${tag in tags
                        ? "border-copper bg-copper-tint text-copper-deep"
                        : "border-line text-ink-soft hover:border-line-strong"}`}
                  >
                    <input
                      type="checkbox"
                      className="sr-only"
                      checked={tag in tags}
                      onChange={() => toggleTag(tag)}
                    />
                    {label}
                  </label>
                ))}
              </div>
              {"expires" in tags && (
                <div className="mt-2">
                  <Input
                    type="date"
                    value={typeof tags.expires === "string" ? tags.expires : ""}
                    onChange={(e) => {
                      setDirty(true);
                      setTags((prev) => ({ ...prev, expires: e.target.value }));
                    }}
                  />
                </div>
              )}
            </div>

            <div className="grid grid-cols-2 items-end gap-3">
              <Field label="Case size" help="Units per case for ordering math">
                <Input
                  value={caseSize}
                  inputMode="numeric"
                  onChange={(e) => {
                    setDirty(true);
                    setCaseSize(e.target.value.replace(/\D/g, ""));
                  }}
                />
              </Field>
              <div className="pb-1">
                <Toggle
                  checked={deptOrderable}
                  onChange={(v) => {
                    setDirty(true);
                    setDeptOrderable(v);
                  }}
                  label="Dept-orderable"
                />
              </div>
            </div>
          </>
        )}

        {product.odoo_url && (
          <a
            href={product.odoo_url}
            target="_blank"
            rel="noreferrer"
            className="text-[13px] font-medium text-copper-deep underline-offset-2 hover:underline"
          >
            Open in Odoo ↗
          </a>
        )}
      </div>
    </Drawer>
  );
}
