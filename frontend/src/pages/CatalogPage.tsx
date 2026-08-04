import { usePersistedState } from "../persist";
import { useEffect, useMemo, useState } from "react";
import type { ProductOut, TagOut } from "../api/types";
import { useCreateManualProduct, useFacets, useProducts } from "../api/hooks";
import { useAuth } from "../auth/AuthContext";
import {
  Badge,
  Button,
  ContextMenu,
  DataTable,
  Dialog,
  Fab,
  Field,
  Input,
  PageHeader,
  Pagination,
  Select,
  toneForLabel,
  useContextMenu,
  useRowSelection,
  useToast,
} from "../design";
import type { Column } from "../design";
import { productCode } from "./shared/OpsBits";
import { TAG_LABELS, TAG_TONES } from "./shared/tags";
import { BulkProductDrawer, ProductDrawer } from "./ProductDrawer";
import { useSillyLabel } from "../silly";

/* ---- variant grouping: rows whose names are ≥70% similar collapse into one
   expandable group (only meaningful in name order, where variants sit
   together). Similarity = Sørensen–Dice over character bigrams. ---- */
const GROUP_SIMILARITY = 0.7;

function bigrams(text: string): Set<string> {
  const s = text.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  const out = new Set<string>();
  for (let i = 0; i < s.length - 1; i++) out.add(s.slice(i, i + 2));
  return out;
}

function nameSimilarity(a: string, b: string): number {
  const ba = bigrams(a);
  const bb = bigrams(b);
  if (ba.size === 0 || bb.size === 0) return 0;
  let shared = 0;
  for (const g of ba) if (bb.has(g)) shared++;
  return (2 * shared) / (ba.size + bb.size);
}

function groupLabel(names: string[]): string {
  let prefix = names[0];
  for (const name of names.slice(1)) {
    let i = 0;
    while (i < prefix.length && i < name.length && prefix[i] === name[i]) i++;
    prefix = prefix.slice(0, i);
  }
  const clean = prefix.replace(/[\s—–\-·(,/]+$/g, "").trim();
  return clean.length >= 4 ? clean : names[0];
}

type CatalogRow =
  | { kind: "product"; p: ProductOut; inGroup?: boolean }
  | { kind: "group"; key: string; label: string; members: ProductOut[]; expanded: boolean };

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

function sumStock(members: ProductOut[], key: "bwhse" | "floor"): number | undefined {
  const tracked = members.filter((m) => m.is_stock_tracked);
  if (tracked.length === 0) return undefined;
  return tracked.reduce((n, m) => n + (m.stock[key] ?? 0), 0);
}

function priceRange(members: ProductOut[]): string {
  const prices = members.map((m) => m.retail_price);
  const lo = Math.min(...prices);
  const hi = Math.max(...prices);
  return lo === hi ? `$${lo.toFixed(2)}` : `$${lo.toFixed(2)}–$${hi.toFixed(2)}`;
}

function unionTags(members: ProductOut[]): TagOut[] {
  const seen = new Map<string, TagOut>();
  for (const m of members) for (const t of m.tags) if (!seen.has(t.tag)) seen.set(t.tag, t);
  return [...seen.values()];
}

function TagChips({ tags }: { tags: TagOut[] }) {
  const shown = tags.slice(0, 3);
  return (
    <span className="flex flex-wrap gap-1">
      {shown.map((t) => (
        <Badge key={t.tag} tone={TAG_TONES[t.tag] ?? "neutral"}>
          {TAG_LABELS[t.tag] ?? t.tag}
          {t.expires_on ? ` ${t.expires_on}` : ""}
        </Badge>
      ))}
      {tags.length > shown.length && <Badge tone="outline">+{tags.length - shown.length}</Badge>}
    </span>
  );
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

  const [search, setSearch] = usePersistedState("catalog.search", "");
  const s = useSillyLabel();
  const [category, setCategory] = usePersistedState("catalog.category", "");
  const [tag, setTag] = usePersistedState("catalog.tag", "");
  const [page, setPage] = useState(1);
  const [sort, setSort] = usePersistedState<{ key: string; dir: "asc" | "desc" }>("catalog.sort", { key: "name", dir: "asc" });
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

  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [bulkOpen, setBulkOpen] = useState(false);
  const rows = useMemo<CatalogRow[]>(() => {
    const items = data?.items ?? [];
    if (sort.key !== "name") return items.map((p) => ({ kind: "product" as const, p }));
    const clusters: ProductOut[][] = [];
    for (const p of items) {
      const current = clusters[clusters.length - 1];
      if (current && nameSimilarity(current[0].name, p.name) >= GROUP_SIMILARITY) {
        current.push(p);
      } else {
        clusters.push([p]);
      }
    }
    const out: CatalogRow[] = [];
    for (const cluster of clusters) {
      if (cluster.length === 1) {
        out.push({ kind: "product", p: cluster[0] });
        continue;
      }
      const key = `group-${cluster[0].id}`;
      const expanded = expandedGroups.has(key);
      out.push({
        kind: "group",
        key,
        label: groupLabel(cluster.map((m) => m.name)),
        members: cluster,
        expanded,
      });
      if (expanded) out.push(...cluster.map((p) => ({ kind: "product" as const, p, inGroup: true })));
    }
    return out;
  }, [data, sort.key, expandedGroups]);

  // Premiere-style multi-select over the visible product rows (groups
  // expand on plain click and stay out of selection)
  const visibleProductIds = useMemo(
    () => rows.filter((r) => r.kind === "product").map((r) => (r as { p: ProductOut }).p.id),
    [rows],
  );
  const selection = useRowSelection(visibleProductIds);
  const menu = useContextMenu();
  const selectedProducts = useMemo(() => {
    const byId = new Map((data?.items ?? []).map((p) => [p.id, p]));
    return [...selection.selected].map((id) => byId.get(id)).filter((p): p is ProductOut => !!p);
  }, [data, selection.selected]);
  // a new page/filter shows different rows — stale selections would be invisible
  useEffect(() => {
    selection.clear();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch, category, tag, page]);

  const rowMenu = (r: CatalogRow, e: React.MouseEvent) => {
    if (r.kind !== "product") return;
    const ids = selection.forContext(r.p.id);
    if (!isAdmin) return; // bulk editing is the admin's tool
    menu.open(e, [
      {
        label: `Edit ${ids.size} together…`,
        onSelect: () => setBulkOpen(true),
      },
      { label: "Clear selection", onSelect: () => selection.clear() },
    ]);
  };

  const columns = useMemo<Column<CatalogRow>[]>(
    () => [
      {
        key: "sku",
        header: "Barcode",
        width: "150px",
        sortable: true,
        render: (r) =>
          r.kind === "group" ? (
            <span aria-hidden className="text-[13px] text-ink-soft">
              {r.expanded ? "▾" : "▸"}
            </span>
          ) : (
            <span className="font-mono text-[12.5px] text-ink-soft">
              {r.inGroup && <span aria-hidden className="mr-1 text-ink-faint">└</span>}
              {productCode(r.p.barcode, r.p.global_sku)}
            </span>
          ),
      },
      {
        key: "name",
        header: "Product",
        sortable: true,
        render: (r) =>
          r.kind === "group" ? (
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate font-semibold">{r.label}</span>
              <Badge tone="secondary">{r.members.length} variants</Badge>
            </div>
          ) : (
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate font-medium">{r.p.name}</span>
              {r.p.source === "manual" && (
                <Badge tone="outline" title="App-only item — not tracked in Odoo">
                  untracked
                </Badge>
              )}
              {!r.p.is_active && <Badge tone="danger">archived</Badge>}
            </div>
          ),
      },
      { key: "category", header: "Category", sortable: true, hideBelow: "md",
        render: (r) => {
          const category = r.kind === "group" ? r.members[0].category : r.p.category;
          return category ? <Badge tone={toneForLabel(category)}>{category}</Badge> : null;
        } },
      {
        key: "bwhse",
        header: "Bwhse",
        align: "right",
        hideBelow: "sm",
        render: (r) =>
          r.kind === "group" ? (
            <Qty value={sumStock(r.members, "bwhse")} />
          ) : (
            <Qty value={r.p.is_stock_tracked ? (r.p.stock.bwhse ?? 0) : undefined} />
          ),
      },
      {
        key: "floor",
        header: "Floor",
        align: "right",
        hideBelow: "sm",
        render: (r) =>
          r.kind === "group" ? (
            <Qty value={sumStock(r.members, "floor")} />
          ) : (
            <Qty value={r.p.is_stock_tracked ? (r.p.stock.floor ?? 0) : undefined} />
          ),
      },
      {
        key: "price",
        header: "Price",
        align: "right",
        sortable: true,
        render: (r) =>
          r.kind === "group" ? (
            <span className="tabular-nums text-ink-soft">{priceRange(r.members)}</span>
          ) : (
            <span className="tabular-nums">${r.p.retail_price.toFixed(2)}</span>
          ),
      },
      {
        key: "tags",
        header: "Tags",
        hideBelow: "lg",
        render: (r) => <TagChips tags={r.kind === "group" ? unionTags(r.members) : r.p.tags} />,
      },
    ],
    [],
  );

  return (
    <>
      <PageHeader
        title="All SKUs"
        subtitle={
          facets
            ? `${facets.total_active.toLocaleString()} active products · search by name, SKU, or barcode`
            : "Loading…"
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2.5">
        <Input
          placeholder={s("Search products…")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs [--control-radius:9999px]"
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

      {selection.selected.size > 1 && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-full bg-secondary-container/60 px-4 py-2">
          <span className="text-[13px] font-semibold text-on-secondary-container">
            {selection.selected.size} selected
          </span>
          {isAdmin && (
            <Button size="sm" variant="secondary" onClick={() => setBulkOpen(true)}>
              Edit together
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => selection.clear()}>
            Clear
          </Button>
          <span className="ml-auto hidden text-[12px] text-on-secondary-container/80 sm:block">
            shift-click: range · {navigator.platform.includes("Mac") ? "⌘" : "ctrl"}-click: toggle
          </span>
        </div>
      )}

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => (r.kind === "group" ? r.key : `p${r.p.id}`)}
        loading={isLoading}
        rowClassName={(r) =>
          r.kind === "product" && selection.selected.has(r.p.id)
            ? "bg-secondary-container/40 hover:bg-secondary-container/50"
            : ""
        }
        onRowContextMenu={rowMenu}
        onRowClick={(r, e) => {
          if (r.kind === "group") {
            setExpandedGroups((prev) => {
              const next = new Set(prev);
              if (next.has(r.key)) next.delete(r.key);
              else next.add(r.key);
              return next;
            });
          } else if (e.shiftKey || e.metaKey || e.ctrlKey) {
            selection.click(r.p.id, e); // build the selection, no drawer
          } else {
            selection.click(r.p.id, e); // plain click anchors the range…
            setSelected(r.p); // …and inspects, as always
          }
        }}
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

      {isAdmin && (
        <>
          {/* room to scroll the pagination clear of the floating FAB */}
          <div className="h-24" aria-hidden />
          <Fab
            label="New item"
            onClick={() => setNewOpen(true)}
            className="fixed right-6 bottom-6 z-30"
            title="Add a non-Odoo item (water, cookies — dept-orderable, no stock tracking)"
          />
        </>
      )}

      <ProductDrawer product={selected} onClose={() => setSelected(null)} isAdmin={isAdmin} />
      <ContextMenu menu={menu.menu} onClose={menu.close} />
      {bulkOpen && selectedProducts.length > 0 && (
        <BulkProductDrawer products={selectedProducts} onClose={() => setBulkOpen(false)} />
      )}
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
