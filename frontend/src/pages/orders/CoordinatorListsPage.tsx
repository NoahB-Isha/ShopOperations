/* Zone coordinator: the catalogs the office granted to your zone. You decide
   which of YOUR centers can order from each — that's what the phase-3 order
   form will show them. */
import { useState } from "react";
import {
  useCenters,
  useOrderList,
  useOrderLists,
  useSetOrderListCenters,
} from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import type { OrderListOut, OrderListSummaryOut } from "../../api/types";
import {
  Badge,
  Button,
  Card,
  Dialog,
  EmptyState,
  PageHeader,
  Spinner,
  useToast,
} from "../../design";
import { fmtWhen } from "../shared/OpsBits";

export function CoordinatorListsPage() {
  const { data, isLoading } = useOrderLists();
  const [openId, setOpenId] = useState<number | null>(null);

  return (
    <>
      <PageHeader
        title="Order lists"
        subtitle="Catalogs the office granted to your zone. Open each one to the centers that should order from it."
      />
      {isLoading ? (
        <div className="grid place-items-center py-24">
          <Spinner size={24} />
        </div>
      ) : (data ?? []).length === 0 ? (
        <EmptyState
          title="No lists granted yet"
          hint="When the office grants a catalog to your zone, it lands here."
        />
      ) : (
        <div className="stagger-children grid grid-cols-1 gap-3 md:grid-cols-2">
          {(data ?? []).map((ol) => (
            <ListCard key={ol.id} summary={ol} onOpen={() => setOpenId(ol.id)} />
          ))}
        </div>
      )}
      <CentersDialog id={openId} onClose={() => setOpenId(null)} />
    </>
  );
}

function ListCard({
  summary,
  onOpen,
}: {
  summary: OrderListSummaryOut;
  onOpen: () => void;
}) {
  return (
    <Card className="flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[16px] font-semibold">{summary.name}</div>
          <div className="mt-0.5 text-[13px] text-on-surface-variant">
            {summary.line_count} product{summary.line_count === 1 ? "" : "s"} · updated{" "}
            {fmtWhen(summary.updated_at)}
          </div>
        </div>
        {summary.center_count > 0 ? (
          <Badge tone="forest">
            {summary.center_count} center{summary.center_count === 1 ? "" : "s"}
          </Badge>
        ) : (
          <Badge tone="outline">no centers yet</Badge>
        )}
      </div>
      <div className="mt-1">
        <Button size="sm" onClick={onOpen}>
          Choose centers
        </Button>
      </div>
    </Card>
  );
}

function CentersDialog({ id, onClose }: { id: number | null; onClose: () => void }) {
  const { data: ol } = useOrderList(id);
  if (id === null) return null;
  return (
    <Dialog open onClose={onClose} title={ol?.name ?? "Order list"} wide>
      {!ol ? (
        <div className="grid place-items-center py-16">
          <Spinner size={22} />
        </div>
      ) : (
        <DialogBody ol={ol} />
      )}
    </Dialog>
  );
}

function DialogBody({ ol }: { ol: OrderListOut }) {
  const { user } = useAuth();
  const myZoneIds = new Set(
    (user?.roles ?? [])
      .filter((r) => (r.role === "zone_coordinator" || r.role === "dept_liaison") && r.zone_id)
      .map((r) => r.zone_id as number),
  );
  const grantedToMe = ol.zones.filter((z) => myZoneIds.has(z.zone_id)).map((z) => z.zone_id);
  const { data: centers } = useCenters(
    grantedToMe.length === 1 ? { zone_id: grantedToMe[0] } : {},
  );
  const setCenters = useSetOrderListCenters();
  const toast = useToast();

  const myCenters = (centers ?? []).filter(
    (c) => c.is_active && c.zone_id !== null && myZoneIds.has(c.zone_id) &&
      grantedToMe.includes(c.zone_id),
  );
  const grantedCenterIds = new Set(ol.centers.map((c) => c.center_id));

  const toggle = (centerId: number) => {
    // send only MY centers' selection; other zones' grants are preserved server-side
    const mineSelected = myCenters
      .filter((c) => (c.id === centerId ? !grantedCenterIds.has(c.id) : grantedCenterIds.has(c.id)))
      .map((c) => c.id);
    setCenters.mutate(
      { id: ol.id, center_ids: mineSelected },
      {
        onSuccess: () => toast.success("Centers updated."),
        onError: (e) => toast.error(e.message),
      },
    );
  };

  return (
    <div className="flex flex-col gap-4">
      {ol.notes && (
        <p className="rounded-(--radius-md) bg-surface-container px-3.5 py-2.5 text-sm whitespace-pre-wrap">
          {ol.notes}
        </p>
      )}
      <div>
        <div className="label-caps mb-2">Your centers</div>
        {myCenters.length === 0 ? (
          <p className="text-sm text-on-surface-variant">No active centers in your zone.</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {myCenters.map((c) => {
              const on = grantedCenterIds.has(c.id);
              return (
                <button
                  key={c.id}
                  onClick={() => toggle(c.id)}
                  disabled={setCenters.isPending}
                  aria-pressed={on}
                  className={`state-layer rounded-full px-3.5 py-1.5 text-[13px] font-semibold ${
                    on
                      ? "bg-secondary-container text-on-secondary-container"
                      : "border border-outline-variant text-on-surface-variant"
                  }`}
                >
                  {on ? "✓ " : ""}
                  {c.name}
                </button>
              );
            })}
          </div>
        )}
      </div>
      <div>
        <div className="label-caps mb-2">
          Products ({ol.lines.length}
          {ol.stale_line_count > 0 ? ` · ${ol.stale_line_count} inactive` : ""})
        </div>
        <ul className="max-h-64 overflow-y-auto rounded-(--radius-md) bg-surface-container-low">
          {ol.lines.map((line) => (
            <li
              key={line.id}
              className="flex items-center justify-between gap-3 border-b
                border-outline-variant/50 px-3.5 py-2 text-sm last:border-b-0"
            >
              <span className="min-w-0">
                <span className="block truncate font-medium">{line.name}</span>
                <span className="font-mono text-[11.5px] text-on-surface-variant">
                  {line.sku}
                </span>
              </span>
              {!line.is_active && <Badge tone="gold">inactive</Badge>}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
