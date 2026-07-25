import { useMemo, useState } from "react";
import { useCenters, useImportCoordinators, useZones } from "../../api/hooks";
import type { CenterOut, ImportReportOut } from "../../api/types";
import {
  Badge,
  Button,
  DataTable,
  Dialog,
  Input,
  PageHeader,
  Select,
  useToast,
} from "../../design";
import type { Column } from "../../design";

const REASON_LABELS: Record<string, string> = {
  ambiguous_active: "active status unclear",
  no_zone: "no zone assigned",
  no_reachable_contact: "no email or phone on file",
  contact_missing_email: "contact missing email",
  contact_missing_email_and_phone: "a contact has no email/phone",
  temporary: "marked temporary",
};

export function CentersPage() {
  const [zoneId, setZoneId] = useState("");
  const [filter, setFilter] = useState("");
  const [onlyFollowup, setOnlyFollowup] = useState(false);
  const { data: zones } = useZones();
  const { data: centers, isLoading } = useCenters(
    zoneId ? { zone_id: Number(zoneId) } : {},
  );
  const importer = useImportCoordinators();
  const toast = useToast();
  const [report, setReport] = useState<ImportReportOut | null>(null);

  const rows = useMemo(
    () => (centers ?? []).filter((c) => !onlyFollowup || c.needs_followup),
    [centers, onlyFollowup],
  );

  const columns = useMemo<Column<CenterOut>[]>(
    () => [
      {
        key: "name",
        header: "Center",
        sortable: true,
        render: (c) => (
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="font-medium">{c.name}</span>
            {c.shared_product_group && (
              <Badge tone="copper" title={`Shares a product set (${c.shared_product_group})`}>
                shared set
              </Badge>
            )}
            {/* phones hide the Follow-up column — keep the signal as a dot */}
            {c.needs_followup && (
              <span
                aria-label="Needs follow-up"
                title={c.followup_reasons.map((r) => REASON_LABELS[r] ?? r).join(", ")}
                className="h-2 w-2 shrink-0 rounded-full bg-gold sm:hidden"
              />
            )}
          </div>
        ),
      },
      { key: "state", header: "State", hideBelow: "md", sortable: true,
        render: (c) => <span className="text-ink-soft">{c.state}</span> },
      { key: "zone_name", header: "Zone", sortable: true,
        value: (c) => c.zone_name ?? "",
        render: (c) => c.zone_name ?? <span className="text-ink-faint">unassigned</span> },
      {
        key: "active",
        header: "Active",
        value: (c) => (c.is_active ? 1 : 0),
        sortable: true,
        render: (c) =>
          c.is_active ? (
            <Badge tone="forest">yes</Badge>
          ) : (
            <Badge tone="neutral" title={c.activity_raw ? `sheet says: ${c.activity_raw}` : undefined}>
              no
            </Badge>
          ),
      },
      {
        key: "terminal",
        header: "Stripe terminal",
        hideBelow: "lg",
        value: (c) => c.stripe_terminal_name,
        render: (c) => (
          <span className="font-mono text-[12px] text-ink-soft">{c.stripe_terminal_name || "—"}</span>
        ),
      },
      {
        key: "followup",
        header: "Follow-up",
        hideBelow: "sm",
        render: (c) =>
          c.needs_followup ? (
            // bounded width: long reason badges wrap instead of stretching
            // the table into horizontal scroll
            <span className="flex max-w-56 flex-wrap gap-1">
              {c.followup_reasons.map((r) => (
                <Badge key={r} tone="gold" title={REASON_LABELS[r] ?? r}>
                  {REASON_LABELS[r] ?? r}
                </Badge>
              ))}
            </span>
          ) : (
            <span className="text-ink-faint">—</span>
          ),
      },
    ],
    [],
  );

  const followupCount = (centers ?? []).filter((c) => c.needs_followup).length;

  return (
    <>
      <PageHeader
        title="Centers"
        subtitle={`${centers?.length ?? 0} centers across ${zones?.length ?? 0} zones · ${followupCount} flagged for follow-up`}
        actions={
          <Button
            variant="secondary"
            loading={importer.isPending}
            onClick={() =>
              importer.mutate(true, {
                onSuccess: (r) => {
                  setReport(r);
                  toast.success(
                    `Roster re-imported: ${r.centers_created} new, ${r.centers_updated} updated.`,
                  );
                },
                onError: (e) => toast.error(e.message),
              })
            }
          >
            Re-import roster
          </Button>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2.5">
        <Input
          placeholder="Filter centers…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="max-w-60"
        />
        <div className="w-52">
          <Select value={zoneId} onChange={(e) => setZoneId(e.target.value)}>
            <option value="">All zones</option>
            {zones?.map((z) => (
              <option key={z.id} value={z.id}>
                {z.name} ({z.center_count})
              </option>
            ))}
          </Select>
        </div>
        <label className="flex cursor-pointer items-center gap-2 text-sm text-ink-soft">
          <input
            type="checkbox"
            checked={onlyFollowup}
            onChange={(e) => setOnlyFollowup(e.target.checked)}
            className="accent-copper"
          />
          Needs follow-up only
        </label>
      </div>

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(c) => c.id}
        loading={isLoading}
        filterText={filter}
        footer={<span>{rows.length} shown</span>}
      />

      <Dialog
        open={report !== null}
        onClose={() => setReport(null)}
        title="Import report"
        wide
        footer={<Button onClick={() => setReport(null)}>Done</Button>}
      >
        {report && (
          <div className="flex flex-col gap-3 text-sm">
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="rounded bg-raised p-2">
                <div className="display text-xl">{report.centers_created}</div>
                <div className="label-caps">created</div>
              </div>
              <div className="rounded bg-raised p-2">
                <div className="display text-xl">{report.centers_updated}</div>
                <div className="label-caps">updated</div>
              </div>
              <div className="rounded bg-raised p-2">
                <div className="display text-xl">{report.users_created}</div>
                <div className="label-caps">users created</div>
              </div>
            </div>
            <div className="text-[13px] text-ink-faint">
              Sheets read: {report.sheets_processed.join(", ")} · skipped legacy:{" "}
              {report.sheets_skipped.join(", ") || "none"}
            </div>
            {Object.keys(report.shared_groups).length > 0 && (
              <div>
                <div className="label-caps mb-1">Shared product sets</div>
                {Object.entries(report.shared_groups).map(([g, names]) => (
                  <div key={g} className="text-[13px] text-ink-soft">{names.join(" ↔ ")}</div>
                ))}
              </div>
            )}
            <div>
              <div className="label-caps mb-1">Needs follow-up ({report.followups.length})</div>
              <div className="max-h-48 overflow-y-auto rounded border border-line">
                {report.followups.map((f) => (
                  <div key={f.center} className="flex justify-between gap-3 border-b border-line/60
                    px-3 py-1.5 text-[13px] last:border-0">
                    <span className="font-medium">{f.center}</span>
                    <span className="text-right text-ink-faint">
                      {f.reasons.map((r) => REASON_LABELS[r] ?? r).join(", ")}
                    </span>
                  </div>
                ))}
              </div>
            </div>
            {report.warnings.length > 0 && (
              <div className="rounded bg-gold-tint px-3 py-2 text-[13px] text-gold">
                {report.warnings.map((w) => (
                  <div key={w}>{w}</div>
                ))}
              </div>
            )}
          </div>
        )}
      </Dialog>
    </>
  );
}
