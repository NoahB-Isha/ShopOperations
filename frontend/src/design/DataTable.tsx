import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { EmptyState } from "./EmptyState";
import { Button } from "./Button";

export interface Column<T> {
  key: string;
  header: ReactNode;
  render?: (row: T) => ReactNode;
  /** value used for client-side sorting/filtering; defaults to row[key] */
  value?: (row: T) => string | number | null | undefined;
  sortable?: boolean;
  align?: "left" | "right";
  width?: string;
  /** hide below this breakpoint for phone layouts */
  hideBelow?: "sm" | "md" | "lg";
}

const hideClass = { sm: "hidden sm:table-cell", md: "hidden md:table-cell", lg: "hidden lg:table-cell" };

export interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  onRowClick?: (row: T, e: React.MouseEvent) => void;
  /** right-click hook for selection/context menus */
  onRowContextMenu?: (row: T, e: React.MouseEvent) => void;
  /** extra classes per row (e.g. selection tint) */
  rowClassName?: (row: T) => string;
  /** client-side text filter across all column values */
  filterText?: string;
  loading?: boolean;
  empty?: ReactNode;
  /** server-driven mode: parent owns sorting */
  sort?: { key: string; dir: "asc" | "desc" };
  onSortChange?: (key: string, dir: "asc" | "desc") => void;
  footer?: ReactNode;
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  onRowContextMenu,
  rowClassName,
  filterText = "",
  loading = false,
  empty,
  sort,
  onSortChange,
  footer,
}: DataTableProps<T>) {
  const [localSort, setLocalSort] = useState<{ key: string; dir: "asc" | "desc" } | null>(null);
  const activeSort = sort ?? localSort;

  const colValue = (col: Column<T>, row: T): string | number => {
    const v = col.value ? col.value(row) : (row as Record<string, unknown>)[col.key];
    return typeof v === "number" ? v : String(v ?? "");
  };

  const visible = useMemo(() => {
    let out = rows;
    if (filterText.trim()) {
      const needle = filterText.trim().toLowerCase();
      out = out.filter((r) =>
        columns.some((c) => String(colValue(c, r)).toLowerCase().includes(needle)),
      );
    }
    if (!sort && localSort) {
      const col = columns.find((c) => c.key === localSort.key);
      if (col) {
        out = [...out].sort((a, b) => {
          const va = colValue(col, a);
          const vb = colValue(col, b);
          const cmp =
            typeof va === "number" && typeof vb === "number"
              ? va - vb
              : String(va).localeCompare(String(vb));
          return localSort.dir === "asc" ? cmp : -cmp;
        });
      }
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, filterText, localSort, sort, columns]);

  const toggleSort = (col: Column<T>) => {
    if (!col.sortable) return;
    const dir =
      activeSort?.key === col.key && activeSort.dir === "asc" ? "desc" : "asc";
    if (onSortChange) onSortChange(col.key, dir);
    else setLocalSort({ key: col.key, dir });
  };

  return (
    <div className="overflow-hidden rounded-(--radius-lg) bg-surface-container-low">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="bg-surface-container">
              {columns.map((col) => (
                <th
                  key={col.key}
                  style={col.width ? { width: col.width } : undefined}
                  className={`label-m px-3.5 py-3 text-left select-none
                    ${col.align === "right" ? "text-right" : ""}
                    ${col.hideBelow ? hideClass[col.hideBelow] : ""}
                    ${col.sortable ? "cursor-pointer transition-colors hover:text-primary" : ""}`}
                  onClick={() => toggleSort(col)}
                  aria-sort={
                    activeSort?.key === col.key
                      ? activeSort.dir === "asc" ? "ascending" : "descending"
                      : undefined
                  }
                >
                  <span className="inline-flex items-center gap-1">
                    {col.header}
                    {activeSort?.key === col.key && (
                      <span aria-hidden className="animate-pop text-primary">
                        {activeSort.dir === "asc" ? "↑" : "↓"}
                      </span>
                    )}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 8 }).map((_, i) => (
                  <tr key={i} className="border-b border-outline-variant/50">
                    {columns.map((c) => (
                      <td key={c.key} className={`px-3.5 py-3 ${c.hideBelow ? hideClass[c.hideBelow] : ""}`}>
                        <div className="h-3.5 w-4/5 animate-pulse rounded-full bg-outline-variant/60" />
                      </td>
                    ))}
                  </tr>
                ))
              : visible.map((row) => (
                  <tr
                    key={rowKey(row)}
                    onClick={onRowClick ? (e) => onRowClick(row, e) : undefined}
                    onContextMenu={onRowContextMenu ? (e) => onRowContextMenu(row, e) : undefined}
                    // shift-click selection must not smear a text selection
                    onMouseDown={(e) => e.shiftKey && e.preventDefault()}
                    className={`border-b border-outline-variant/50 transition-colors last:border-b-0
                      ${onRowClick ? "cursor-pointer hover:bg-primary/8" : "hover:bg-on-surface/4"}
                      ${rowClassName ? rowClassName(row) : ""}`}
                  >
                    {columns.map((col) => (
                      <td
                        key={col.key}
                        className={`px-3.5 py-2.5 align-middle
                          ${col.align === "right" ? "text-right tabular-nums" : ""}
                          ${col.hideBelow ? hideClass[col.hideBelow] : ""}`}
                      >
                        {col.render
                          ? col.render(row)
                          : String((row as Record<string, unknown>)[col.key] ?? "")}
                      </td>
                    ))}
                  </tr>
                ))}
          </tbody>
        </table>
        {!loading && visible.length === 0 && (
          <div className="p-6">
            {empty ?? <EmptyState title="Nothing here" hint="No rows match." />}
          </div>
        )}
      </div>
      {footer && (
        <div className="flex items-center justify-between bg-surface-container px-3.5 py-2 text-[13px] text-on-surface-variant">
          {footer}
        </div>
      )}
    </div>
  );
}

export function Pagination({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (p: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <>
      <span>
        {from}–{to} of {total.toLocaleString()}
      </span>
      <span className="flex items-center gap-1">
        <Button variant="ghost" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          ← Prev
        </Button>
        <span className="px-1 tabular-nums">
          {page} / {pages}
        </span>
        <Button variant="ghost" size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Next →
        </Button>
      </span>
    </>
  );
}
