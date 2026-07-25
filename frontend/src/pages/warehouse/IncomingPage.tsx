/** Incoming shipments — every pending inbound move from the snapshot,
 * soonest first. (This list lived on the removed Availability page.) */
import { useState } from "react";
import { useAvailabilityComingSoon, useAvailabilityMeta } from "../../api/hooks";
import type { AvailabilityItemOut } from "../../api/types";
import { DataTable, PageHeader, Select } from "../../design";
import type { Column } from "../../design";
import { LowCountHint, fmtWhen } from "../shared/OpsBits";

export function IncomingPage() {
  const [withinDays, setWithinDays] = useState<number | null>(null);
  const [filter, setFilter] = useState("");
  const meta = useAvailabilityMeta();
  const coming = useAvailabilityComingSoon(withinDays);

  const columns: Column<AvailabilityItemOut>[] = [
    {
      key: "name",
      header: "Product",
      value: (r) => `${r.name} ${r.sku}`,
      sortable: true,
      render: (r) => (
        <div>
          <div className="text-on-surface">{r.name}</div>
          <div className="text-[11px] text-on-surface-variant">{r.sku}</div>
        </div>
      ),
    },
    { key: "category", header: "Category", value: (r) => r.category, sortable: true, hideBelow: "md" },
    {
      key: "qty",
      header: "On the way",
      align: "right",
      value: (r) => r.incoming_qty,
      sortable: true,
      render: (r) => <span className="font-semibold">{r.incoming_qty.toLocaleString()}</span>,
    },
    {
      key: "eta",
      header: "Expected",
      value: (r) => r.incoming_expected ?? "9999",
      sortable: true,
      render: (r) => (
        <div>
          <div className="text-on-surface">{r.incoming_label}</div>
          {r.incoming_expected && (
            <div className="text-[11px] text-on-surface-variant">{r.incoming_expected}</div>
          )}
        </div>
      ),
    },
    {
      key: "onhand",
      header: "On hand now",
      align: "right",
      value: (r) => r.total_qty,
      sortable: true,
      render: (r) => (
        <span>
          {r.total_qty.toLocaleString()}
          {r.low_count_caveat && <LowCountHint qty={r.total_qty} />}
        </span>
      ),
      hideBelow: "sm",
    },
  ];

  const freshness = meta.data?.freshness;

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Incoming"
        subtitle="Pending inbound shipments, soonest first — straight from the stock snapshot."
      />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Select
          value={withinDays ?? ""}
          onChange={(e) => setWithinDays(e.target.value ? Number(e.target.value) : null)}
          aria-label="Arrival window"
        >
          <option value="">Any arrival date</option>
          <option value="30">Next 30 days</option>
          <option value="60">Next 60 days</option>
          <option value="90">Next 90 days</option>
        </Select>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Search…"
          className="m3-control w-52"
          aria-label="Search"
        />
        {freshness && (
          <span className="ml-auto text-[12px] text-on-surface-variant">
            incoming synced {freshness.incoming ? fmtWhen(freshness.incoming) : "never"}
          </span>
        )}
      </div>
      <div style={{ opacity: coming.isFetching && !coming.isLoading ? 0.7 : 1 }}>
        <DataTable
          columns={columns}
          rows={coming.data ?? []}
          rowKey={(r) => r.product_id}
          filterText={filter}
          loading={coming.isLoading}
          empty="No pending inbound shipments."
        />
      </div>
    </div>
  );
}
