import { useEffect, useMemo, useState } from "react";
import type { ProductOut } from "../api/types";
import { useCreateManualProduct, useFacets, useProducts } from "../api/hooks";
import { useAuth } from "../auth/AuthContext";
import {
  Badge,
  Button,
  DataTable,
  Dialog,
  Field,
  Input,
  PageHeader,
  Pagination,
  Select,
  useToast,
} from "../design";
import type { Column } from "../design";
import { ProductDrawer } from "./ProductDrawer";

const TAG_LABELS: Record<string, string> = {
  air_only: "Air only",
  sea_only: "Sea only",
  gold: "Gold",
  silver: "Silver",
  bloom: "Bloom",
  camphor: "Camphor",
  toothpaste: "Toothpaste",
  expires: "Expires",
};

const TAG_TONES: Record<string, "copper" | "forest" | "gold" | "danger" | "neutral"> = {
  air_only: "copper",
  sea_only: "forest",
  gold: "gold",
  silver: "neutral",
  bloom: "forest",
  camphor: "neutral",
  toothpaste: "neutral",
  expires: "danger",
};

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

function Qty({ value }: { value: number | undefined }) {
  if (value === undefined) return <span className="text-ink-faint">—</span>;
  const low = value > 0 && value <= 3;
  return (
    <span
      className={`tabular-nums ${value === 0 ? "text-ink-faint" : low ? "text-gold" : ""}`}
      title={low ? "Low counts are often wrong — verify physically" : undefined}
    >
      {value}
      {low && " ⚠"}
    </span>
  );
}

export function CatalogPage() {
  const { roles } = useAuth();
  const isAdmin = roles.has("admin");
  const toast = useToast();

  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [tag, setTag] = useState("");
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" }>({ key: "name", dir: "asc" });
  const [selected, setSelected] = useState<ProductOut | null>(null);
  const [newOpen, setNewOpen] = useState(false);

  const debouncedSearch = useDebounced(search, 200);
  useEffect(() => setPage(1), [debouncedSearch, category, tag]);

  const { data, isLoading, isFetching } = useProducts({
    search: debouncedSearch,
    category,
    tag,
    page,
    sort: sort.key,
    dir: sort.dir,
  });
  const { data: facets } = useFacets();

  const columns = useMemo<Column<ProductOut>[]>(
    () => [
      {
        key: "sku",
        header: "SKU",
        width: "150px",
        sortable: true,
        render: (p) => (
          <span className="font-mono text-[12.5px] text-ink-soft">{p.global_sku}</span>
        ),
      },
      {
        key: "name",
        header: "Product",
        sortable: true,
        render: (p) => (
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate font-medium">{p.name}</span>
            {p.source === "manual" && (
              <Badge tone="outline" title="App-only item — not tracked in Odoo">
                untracked
              </Badge>
            )}
            {!p.is_active && <Badge tone="danger">archived</Badge>}
          </div>
        ),
      },
      { key: "category", header: "Category", sortable: true, hideBelow: "md",
        render: (p) => <span className="text-ink-soft">{p.category}</span> },
      {
        key: "bwhse",
        header: "Bwhse",
        align: "right",
        hideBelow: "sm",
        value: (p) => p.stock.bwhse ?? -1,
        render: (p) => <Qty value={p.is_stock_tracked ? (p.stock.bwhse ?? 0) : undefined} />,
      },
      {
        key: "floor",
        header: "Floor",
        align: "right",
        hideBelow: "sm",
        value: (p) => p.stock.floor ?? -1,
        render: (p) => <Qty value={p.is_stock_tracked ? (p.stock.floor ?? 0) : undefined} />,
      },
      {
        key: "price",
        header: "Price",
        align: "right",
        sortable: true,
        render: (p) => <span className="tabular-nums">${p.retail_price.toFixed(2)}</span>,
      },
      {
        key: "tags",
        header: "Tags",
        hideBelow: "lg",
        render: (p) => (
          <span className="flex flex-wrap gap-1">
            {p.tags.map((t) => (
              <Badge key={t.tag} tone={TAG_TONES[t.tag] ?? "neutral"}>
                {TAG_LABELS[t.tag] ?? t.tag}
                {t.expires_on ? ` ${t.expires_on}` : ""}
              </Badge>
            ))}
          </span>
        ),
      },
    ],
    [],
  );

  return (
    <>
      <PageHeader
        title="Catalog"
        subtitle={
          facets
            ? `${facets.total_active.toLocaleString()} active products · search by name, SKU, or barcode`
            : "Loading…"
        }
        actions={
          isAdmin && (
            <Button variant="secondary" size="sm" onClick={() => setNewOpen(true)}>
              + Non-Odoo item
            </Button>
          )
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2.5">
        <Input
          placeholder="Search products…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs"
          aria-label="Search products"
        />
        <div className="w-52">
          <Select value={category} onChange={(e) => setCategory(e.target.value)}
            aria-label="Filter by category">
            <option value="">All categories</option>
            {facets?.categories.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </Select>
        </div>
        <div className="w-40">
          <Select value={tag} onChange={(e) => setTag(e.target.value)} aria-label="Filter by tag">
            <option value="">All tags</option>
            {facets?.tags.map((t) => (
              <option key={t} value={t}>{TAG_LABELS[t] ?? t}</option>
            ))}
          </Select>
        </div>
        {isFetching && !isLoading && <span className="text-[13px] text-ink-faint">refreshing…</span>}
      </div>

      <DataTable
        columns={columns}
        rows={data?.items ?? []}
        rowKey={(p) => p.id}
        loading={isLoading}
        onRowClick={setSelected}
        sort={sort}
        onSortChange={(key, dir) => setSort({ key: key === "sku" ? "sku" : key, dir })}
        empty={
          <div className="text-center text-sm text-ink-faint">
            No products match “{debouncedSearch}”.
          </div>
        }
        footer={
          <Pagination
            page={page}
            pageSize={data?.page_size ?? 50}
            total={data?.total ?? 0}
            onPage={setPage}
          />
        }
      />

      <ProductDrawer product={selected} onClose={() => setSelected(null)} isAdmin={isAdmin} />
      <NewItemDialog
        open={newOpen}
        onClose={() => setNewOpen(false)}
        onCreated={(name) => toast.success(`“${name}” added to the catalog.`)}
      />
    </>
  );
}

function NewItemDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (name: string) => void;
}) {
  const create = useCreateManualProduct();
  const [name, setName] = useState("");
  const [sku, setSku] = useState("");
  const [price, setPrice] = useState("");
  const [error, setError] = useState("");

  const submit = () => {
    setError("");
    create.mutate(
      { name, global_sku: sku || undefined, retail_price: price ? Number(price) : 0 },
      {
        onSuccess: (p) => {
          onCreated(p.name);
          setName(""); setSku(""); setPrice("");
          onClose();
        },
        onError: (e) => setError(e.message),
      },
    );
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="New non-Odoo item"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} loading={create.isPending} disabled={name.trim().length < 2}>
            Add item
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-[13px] text-ink-faint">
          For things departments order that Odoo doesn't track — water, cookies, supplies.
          No stock counts, no transfers; it simply becomes orderable.
        </p>
        <Field label="Name" error={error}>
          <Input value={name} onChange={(e) => setName(e.target.value)}
            placeholder="Spring Water — 24-Pack" autoFocus />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="SKU" help="Blank = auto (MAN-…)">
            <Input value={sku} onChange={(e) => setSku(e.target.value)} placeholder="MAN-0001" />
          </Field>
          <Field label="Price">
            <Input value={price} onChange={(e) => setPrice(e.target.value)} placeholder="6.50"
              inputMode="decimal" />
          </Field>
        </div>
      </div>
    </Dialog>
  );
}
