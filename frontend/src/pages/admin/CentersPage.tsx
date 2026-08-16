import { usePersistedState } from "../../persist";
import { useMemo, useRef, useState } from "react";
import { useCenters, useImportRosterFile, useZones } from "../../api/hooks";
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
import { useSillyLabel } from "../../silly";
import { Icons } from "../../nav";
import { CenterEditDialog } from "./CenterEditDialog";
import { CentersMap } from "./CentersMap";
import { zoneColors, zoneSwatch } from "./centerSignals";

/** Who to talk to about this center, at a glance: the zone's Order Reviewer
 *  approves its orders, the Order Requesters place them, and the roster names
 *  are the people on the ground who may have no app login at all. */
function People({ center }: { center: CenterOut }) {
  const roster = center.contacts.map((c) => c.name).filter(Boolean);
  const rows: [React.ReactNode, string, string[]][] = [
    [Icons.clipboard, "Reviewer", center.reviewers],
    [Icons.bag, "Requester", center.requesters],
    [Icons.users, "Roster", roster],
  ];
  const shown = rows.filter(([, , names]) => names.length > 0);
  if (shown.length === 0) {
    return <span className="text-[13px] text-ink-faint">nobody assigned</span>;
  }
  return (
    <div className="flex flex-col gap-0.5 text-[12.5px]">
      {shown.map(([icon, label, names]) => (
        <span key={label} className="flex items-center gap-1.5 text-ink-soft" title={names.join(", ")}>
          <span className="scale-75 opacity-70" aria-hidden>
            {icon}
          </span>
          <span className="sr-only">{label}: </span>
          <span className="truncate">
            {names[0]}
            {names.length > 1 && (
              <span className="text-ink-faint"> +{names.length - 1}</span>
            )}
          </span>
        </span>
      ))}
    </div>
  );
}

/** Bulk roster import, deliberately quiet and deliberately last. */
function RosterImport({
  importing,
  onImport,
}: {
  importing: boolean;
  onImport: (file: File) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <div className="mt-10 flex flex-col items-center gap-1.5 border-t border-line pt-6 pb-2 text-center">
      <input
        ref={input}
        type="file"
        accept=".xlsx,.xlsm,.csv"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onImport(file);
          e.target.value = ""; // let the same file be picked twice
        }}
      />
      <Button
        variant="ghost"
        size="sm"
        loading={importing}
        icon={Icons.upload}
        onClick={() => input.current?.click()}
      >
        Import from .xlsx or .csv
      </Button>
      <p className="text-[12px] text-ink-faint">
        Adds and updates centers in bulk. Existing centers keep the edits made here.
      </p>
    </div>
  );
}

const REASON_LABELS: Record<string, string> = {
  ambiguous_active: "active status unclear",
  no_zone: "no review zone assigned",
  no_reachable_contact: "no email or phone on file",
  contact_missing_email: "contact missing email",
  contact_missing_email_and_phone: "a contact has no email/phone",
  temporary: "marked temporary",
};

export function CentersPage() {
  const [zoneId, setZoneId] = useState("");
  const [filter, setFilter] = usePersistedState("centers.filter", "");
  const s = useSillyLabel();
  const [onlyFollowup, setOnlyFollowup] = useState(false);
  const { data: zones } = useZones();
  const { data: centers, isLoading } = useCenters(
    zoneId ? { zone_id: Number(zoneId) } : {},
  );
  const importer = useImportRosterFile();
  const toast = useToast();
  const [report, setReport] = useState<ImportReportOut | null>(null);
  const [editing, setEditing] = useState<CenterOut | null>(null);
  // Selecting on the map scrolls the list to the same center, so the two views
  // are one view: pick a dot, read its row.
  const [mapSelection, setMapSelection] = useState<number | null>(null);

  const rows = useMemo(
    () => (centers ?? []).filter((c) => !onlyFollowup || c.needs_followup),
    [centers, onlyFollowup],
  );

  const zoneHues = useMemo(() => zoneColors((centers ?? []).map((c) => c.zone_name)), [centers]);

  const columns = useMemo<Column<CenterOut>[]>(
    () => [
      {
        key: "name",
        header: "Center",
        sortable: true,
        render: (c) => (
          <div className="flex min-w-0 flex-col gap-0.5">
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
            <span className="flex items-center gap-1 text-[12px] text-ink-faint">
              <span className="scale-75 opacity-70">{Icons.mapPin}</span>
              {[c.city, c.state].filter(Boolean).join(", ") || "no location on file"}
            </span>
          </div>
        ),
      },
      {
        key: "zone_name",
        header: "Review zone",
        sortable: true,
        value: (c) => c.zone_name ?? "",
        render: (c) => {
          const swatch = zoneSwatch(c.zone_name, zoneHues);
          return c.zone_name ? (
            <span className="flex items-center gap-1.5">
              {/* the same hue the dot wears on the map above */}
              <span
                aria-hidden
                className="h-2.5 w-2.5 shrink-0 rounded-full"
                style={
                  swatch.hollow
                    ? { boxShadow: `inset 0 0 0 1.5px ${swatch.color}` }
                    : { background: swatch.color }
                }
              />
              {c.zone_name}
            </span>
          ) : (
            <span className="text-ink-faint">unassigned</span>
          );
        },
      },
      {
        key: "people",
        header: "Who's involved",
        hideBelow: "md",
        value: (c) => [...c.reviewers, ...c.requesters].join(" "),
        render: (c) => <People center={c} />,
      },
      {
        key: "active",
        header: "Running",
        value: (c) => (c.is_active ? 1 : 0),
        sortable: true,
        render: (c) =>
          c.is_active ? (
            <Badge tone="forest">active</Badge>
          ) : (
            <Badge tone="neutral" title={c.activity_raw ? `roster says: ${c.activity_raw}` : undefined}>
              dormant
            </Badge>
          ),
      },
      {
        key: "terminal",
        header: "Stripe terminal",
        hideBelow: "lg",
        value: (c) => c.stripe_terminal_name,
        render: (c) =>
          c.stripe_terminal_name ? (
            <span className="flex items-center gap-1.5 font-mono text-[12px] text-ink-soft">
              <span className="scale-75 opacity-70">{Icons.card}</span>
              {c.stripe_terminal_name}
            </span>
          ) : (
            <span className="text-ink-faint">—</span>
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
      {
        key: "edit",
        header: "",
        render: (c) => (
          <Button
            variant="ghost"
            size="sm"
            onClick={(e) => {
              e.stopPropagation();
              setEditing(c);
            }}
          >
            Edit
          </Button>
        ),
      },
    ],
    [zoneHues],
  );

  return (
    <>
      <PageHeader title="Centers" />

      {/* Desktop only, deliberately: this is the stand-back view of a
          continent. On a phone the list below is the better tool and the only
          one rendered.

          Selecting a dot used to scroll the list into view, which read as the
          page lurching away from the map you just clicked. The panel opens on
          the map; nothing moves. */}
      <div className="-mt-4 hidden lg:block">
        <CentersMap centers={rows} selectedId={mapSelection} onSelect={setMapSelection} />
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2.5">
        <Input
          placeholder={s("Filter centers…")}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="max-w-60"
        />
        <div className="w-52">
          <Select value={zoneId} onChange={(e) => setZoneId(e.target.value)}>
            <option value="">All review zones</option>
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

      <CenterEditDialog center={editing} onClose={() => setEditing(null)} />

      {/* The roster lives in this app now — editing happens above, row by row.
          A spreadsheet is something you bring TO it, which is why this sits at
          the bottom rather than in the header. */}
      <RosterImport
        importing={importer.isPending}
        onImport={(file) =>
          importer.mutate(
            { file, apply: true },
            {
              onSuccess: (r) => {
                setReport(r);
                toast.success(
                  `Roster imported: ${r.centers_created} new, ${r.centers_updated} updated.`,
                );
              },
              onError: (e) => toast.error(e.message),
            },
          )
        }
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
