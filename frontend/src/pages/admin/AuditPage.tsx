import { useAudit } from "../../api/hooks";
import { Badge, Card, EmptyState, PageHeader, Spinner } from "../../design";

const REASON_LABELS: Record<string, string> = {
  requested: "requested",
  kill_switch: "kill switch",
  feature_flag: "flag off",
  fixture_mode: "fixture mode",
};

export function AuditPage() {
  const { data, isLoading } = useAudit();

  return (
    <>
      <PageHeader
        title="Odoo write audit"
        subtitle="Every write the app attempted — live, dry-run, or failed. Because the Odoo account is shared with a human, this log (plus the ILAPP- prefix) is the source of truth for what the app did."
      />
      {isLoading ? (
        <div className="grid place-items-center py-20"><Spinner size={22} /></div>
      ) : !data?.length ? (
        <EmptyState
          title="No writes yet"
          hint="Dry-run the canary from the Status page to see the audit trail in action."
        />
      ) : (
        <div className="flex flex-col gap-2.5">
          {data.map((row) => (
            <Card key={row.id} className="p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-[13px] font-semibold">{row.operation}</span>
                {row.dry_run ? (
                  <Badge tone="gold">dry-run · {REASON_LABELS[row.dry_run_reason] ?? row.dry_run_reason}</Badge>
                ) : row.success ? (
                  <Badge tone="forest">written</Badge>
                ) : (
                  <Badge tone="danger">failed</Badge>
                )}
                {row.reference && (
                  <span className="font-mono text-[12px] text-ink-faint">{row.reference}</span>
                )}
                <span className="ml-auto text-[12.5px] text-ink-faint">
                  {new Date(row.created_at).toLocaleString()} · {row.duration_ms}ms
                  {row.odoo_record_ids.length > 0 && ` · ${row.odoo_model} #${row.odoo_record_ids.join(", #")}`}
                </span>
              </div>
              {row.error && (
                <div className="mt-2 rounded bg-danger-tint px-3 py-1.5 text-[13px] text-danger">
                  {row.error}
                </div>
              )}
              <details className="mt-2">
                <summary className="cursor-pointer text-[12.5px] text-ink-faint hover:text-copper-deep">
                  payload
                </summary>
                <pre className="mt-1.5 overflow-x-auto rounded bg-raised p-3 font-mono text-[11.5px] leading-4 text-ink-soft">
{JSON.stringify(row.request_payload, null, 2)}
                </pre>
              </details>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
