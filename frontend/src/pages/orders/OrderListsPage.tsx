/* Admin: curated catalogs people order FROM (no quantities). Grant them to
   zones here; coordinators decide which centers see them. New catalogs can be
   born from ANY spreadsheet with a list of items (names/SKUs/barcodes). */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCreateOrderList, useImportOrderList, useOrderLists } from "../../api/hooks";
import type { CatalogImportResultOut, OrderListSummaryOut } from "../../api/types";
import {
  Badge,
  Button,
  DataTable,
  Dialog,
  EmptyState,
  Field,
  Input,
  PageHeader,
  Textarea,
  Toggle,
  useToast,
} from "../../design";
import type { Column } from "../../design";
import { fmtWhen } from "../shared/OpsBits";

export function OrderListsPage() {
  const [showArchived, setShowArchived] = useState(false);
  const { data, isLoading } = useOrderLists(showArchived);
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();

  const columns: Column<OrderListSummaryOut>[] = [
    {
      key: "name",
      header: "Catalog",
      sortable: true,
      render: (r) => (
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate font-medium">{r.name}</span>
            {r.is_archived && <Badge tone="neutral">archived</Badge>}
          </div>
          <div className="text-[12px] text-on-surface-variant">
            {r.line_count} product{r.line_count === 1 ? "" : "s"}
            {r.stale_line_count > 0 && (
              <span className="ml-1.5 text-warn">
                · {r.stale_line_count} inactive — prune
              </span>
            )}
          </div>
        </div>
      ),
    },
    {
      key: "zone_names",
      header: "Granted to zones",
      hideBelow: "sm",
      value: (r) => r.zone_names.join(", "),
      render: (r) =>
        r.zone_names.length ? (
          <span className="flex flex-wrap gap-1">
            {r.zone_names.map((z) => (
              <Badge key={z} tone="secondary">
                {z}
              </Badge>
            ))}
          </span>
        ) : (
          <span className="text-on-surface-variant">not granted yet</span>
        ),
    },
    {
      key: "center_count",
      header: "Centers",
      align: "right",
      hideBelow: "md",
      sortable: true,
      render: (r) => (
        <span className="tabular-nums text-on-surface-variant">
          {r.center_count || "—"}
        </span>
      ),
    },
    {
      key: "updated_at",
      header: "Updated",
      sortable: true,
      align: "right",
      hideBelow: "md",
      value: (r) => r.updated_at,
      render: (r) => <span className="text-on-surface-variant">{fmtWhen(r.updated_at)}</span>,
    },
  ];

  return (
    <>
      <PageHeader
        title="Catalogs"
        subtitle="Safe menus of currently-active products. Zones get catalogs from you; coordinators open them to their centers; centers order from them."
        actions={
          <>
            <Toggle
              checked={showArchived}
              onChange={setShowArchived}
              label="Archived"
            />
            <Button onClick={() => setCreating(true)} data-testid="new-catalog">
              New Catalog
            </Button>
          </>
        }
      />

      <DataTable
        columns={columns}
        rows={data ?? []}
        rowKey={(r) => r.id}
        loading={isLoading}
        onRowClick={(r) => navigate(`/orders/${r.id}`)}
        empty={
          <EmptyState
            title="No catalogs yet"
            hint="Start with a “Center starter kit” — the safe default menu for any pop-up."
            action={<Button onClick={() => setCreating(true)}>New Catalog</Button>}
          />
        }
      />

      <CreateDialog open={creating} onClose={() => setCreating(false)} />
    </>
  );
}

function CreateDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<CatalogImportResultOut | null>(null);
  const create = useCreateOrderList();
  const importList = useImportOrderList();
  const toast = useToast();
  const navigate = useNavigate();
  const busy = create.isPending || importList.isPending;

  const submit = () => {
    if (file) {
      importList.mutate(
        { name: name.trim(), notes: notes.trim(), file },
        {
          onSuccess: (res) => {
            onClose();
            if (res.skipped.length || res.unmatched_rows.length) {
              setResult(res); // show what didn't make it before moving on
            } else {
              toast.success(`Catalog created — all ${res.matched} items matched.`);
              navigate(`/orders/${res.catalog.id}`);
            }
          },
          onError: (e) => toast.error(e.message),
        },
      );
    } else {
      create.mutate(
        { name: name.trim(), notes: notes.trim() },
        {
          onSuccess: (ol) => {
            toast.success("Catalog created — add products.");
            onClose();
            navigate(`/orders/${(ol as { id: number }).id}`);
          },
          onError: (e) => toast.error(e.message),
        },
      );
    }
  };

  return (
    <>
      <Dialog
        open={open}
        onClose={onClose}
        title="New Catalog"
        footer={
          <>
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button onClick={submit} disabled={name.trim().length < 2} loading={busy}>
              {file ? "Create from file" : "Create"}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-4">
          <Field label="Name" help="e.g. “Center starter kit” — coordinators and centers see this.">
            <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          </Field>
          <Field label="Notes (optional)">
            <Textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
          </Field>
          <label className="block cursor-pointer rounded-(--radius-md) border border-dashed border-outline-variant p-4 text-center text-[13.5px] text-on-surface-variant transition-colors hover:border-primary">
            <input
              type="file"
              accept=".csv,.tsv,.xlsx,.xlsm"
              className="hidden"
              data-testid="catalog-file"
              aria-label="Spreadsheet to import"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            {file ? (
              <span className="font-semibold text-on-surface">
                {file.name}{" "}
                <button
                  className="ml-1 text-primary"
                  onClick={(e) => {
                    e.preventDefault();
                    setFile(null);
                  }}
                >
                  remove
                </button>
              </span>
            ) : (
              <span>
                Optional: start from a spreadsheet (CSV or Excel) with product names, SKUs or
                barcodes — any combination. Quantities and other columns are ignored.
              </span>
            )}
          </label>
        </div>
      </Dialog>

      {result && (
        <Dialog
          open
          onClose={() => setResult(null)}
          title={`${result.matched} of ${result.total_rows} rows matched`}
          footer={
            <Button
              onClick={() => {
                const id = result.catalog.id;
                setResult(null);
                navigate(`/orders/${id}`);
              }}
            >
              Open catalog
            </Button>
          }
        >
          <div className="grid gap-3 text-[13.5px]">
            {result.skipped.length > 0 && (
              <div>
                <div className="label-m mb-1 text-on-surface-variant">
                  Matched but can’t be ordered ({result.skipped.length})
                </div>
                <ul className="list-disc pl-5 text-on-surface-variant">
                  {result.skipped.slice(0, 8).map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ul>
              </div>
            )}
            {result.unmatched_rows.length > 0 && (
              <div>
                <div className="label-m mb-1 text-on-surface-variant">
                  Rows nobody recognized ({result.unmatched_rows.length}) — add these by hand
                </div>
                <ul className="list-disc pl-5 text-on-surface-variant">
                  {result.unmatched_rows.slice(0, 8).map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                  {result.unmatched_rows.length > 8 && (
                    <li>…and {result.unmatched_rows.length - 8} more</li>
                  )}
                </ul>
              </div>
            )}
          </div>
        </Dialog>
      )}
    </>
  );
}
