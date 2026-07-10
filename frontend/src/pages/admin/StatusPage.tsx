import { useState } from "react";
import { useAdminStatus, useCanary, useSetFlag, useTriggerSync } from "../../api/hooks";
import type { CanaryResult, DomainSync } from "../../api/types";
import {
  Badge,
  Button,
  Card,
  Dialog,
  PageHeader,
  Spinner,
  Stat,
  Toggle,
  useToast,
} from "../../design";

function age(seconds: number | null): string {
  if (seconds === null) return "never";
  if (seconds < 90) return `${Math.round(seconds)}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  return `${(seconds / 3600).toFixed(1)}h ago`;
}

function SyncCard({ domain, d }: { domain: string; d: DomainSync }) {
  const trigger = useTriggerSync();
  const toast = useToast();
  return (
    <Card className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="label-caps">{domain}</span>
        {d.stale ? <Badge tone="gold">stale</Badge> : <Badge tone="forest">fresh</Badge>}
      </div>
      <div className="display text-lg">{age(d.age_seconds)}</div>
      <div className="text-[12.5px] text-ink-faint">
        every {d.interval_minutes >= 60 ? `${d.interval_minutes / 60}h` : `${d.interval_minutes}m`}
        {typeof d.extra.last_window === "string" && ` · ${d.extra.last_window}`}
      </div>
      {d.last_error && d.last_error !== "never synced" && (
        <div className="rounded bg-danger-tint px-2 py-1 text-[12px] text-danger">{d.last_error}</div>
      )}
      <Button
        variant="secondary"
        size="sm"
        loading={trigger.isPending}
        onClick={() =>
          trigger.mutate(domain, {
            onSuccess: () => toast.success(`${domain} synced.`),
            onError: (e) => toast.error(e.message),
          })
        }
      >
        Sync now
      </Button>
    </Card>
  );
}

function CanaryCard() {
  const canary = useCanary();
  const toast = useToast();
  const [result, setResult] = useState<CanaryResult | null>(null);
  const [confirmLive, setConfirmLive] = useState(false);

  const run = (dry: boolean) =>
    canary.mutate(dry, {
      onSuccess: (r) => {
        setResult(r);
        setConfirmLive(false);
        if (r.ok) toast.success(dry ? "Canary dry-run rendered." : "Canary passed end to end.");
        else toast.error("Canary stopped — see the step log.");
      },
      onError: (e) => toast.error(e.message),
    });

  return (
    <Card>
      <div className="mb-1 flex items-center justify-between">
        <h3 className="display text-[16px]">Write canary — internal transfer</h3>
        <Badge tone="outline">create → verify → unlink</Badge>
      </div>
      <p className="mb-3 text-[13px] leading-5 text-ink-faint">
        Proves the first write operation against Odoo before its feature flag ever turns on:
        creates one clearly-marked <span className="font-mono">APP-TEST-</span> draft, reads it
        back, checks the deep link, then removes it. Run it live once real credentials are in
        place (the checklist's early-canary step).
      </p>
      <div className="flex gap-2">
        <Button variant="secondary" size="sm" loading={canary.isPending} onClick={() => run(true)}>
          Dry run
        </Button>
        <Button size="sm" onClick={() => setConfirmLive(true)}>
          Run live canary
        </Button>
      </div>

      {result && (
        <div className="mt-4 flex flex-col gap-1.5 border-t border-line pt-3">
          {result.steps.map((s) => (
            <div key={s.name} className="flex items-start gap-2 text-[13px]">
              <span className={s.ok ? "text-forest" : "text-danger"}>{s.ok ? "✓" : "✗"}</span>
              <div>
                <span className="font-medium">{s.name}</span>
                {s.detail && <span className="text-ink-faint"> — {s.detail}</span>}
              </div>
            </div>
          ))}
          {result.deep_link && (
            <a href={result.deep_link} target="_blank" rel="noreferrer"
              className="mt-1 text-[13px] font-medium text-copper-deep hover:underline">
              Odoo record link ↗
            </a>
          )}
        </div>
      )}

      <Dialog
        open={confirmLive}
        onClose={() => setConfirmLive(false)}
        title="Run the live canary?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmLive(false)}>Cancel</Button>
            <Button loading={canary.isPending} onClick={() => run(false)}>
              Yes — create & remove the test draft
            </Button>
          </>
        }
      >
        <p className="text-sm leading-6 text-ink-soft">
          This writes one draft internal transfer to Odoo (reference{" "}
          <span className="font-mono text-[12.5px]">APP-TEST-…</span>, one line, quantity 1),
          verifies it, and unlinks it. Drafts move no stock. Requires{" "}
          <span className="font-mono text-[12.5px]">ODOO_WRITES_ENABLED=true</span>; in fixture
          mode it exercises the simulator instead.
        </p>
      </Dialog>
    </Card>
  );
}

export function StatusPage() {
  const { data, isLoading } = useAdminStatus();
  const setFlag = useSetFlag();
  const toast = useToast();

  if (isLoading || !data) {
    return <div className="grid place-items-center py-24"><Spinner size={24} /></div>;
  }

  return (
    <>
      <PageHeader
        title="Status"
        subtitle="Sync freshness, write safety, and the paper trail — the honest view."
      />

      {data.odoo_auth_failed && (
        <div className="mb-5 rounded-(--radius-md) border border-danger/40 bg-danger-tint px-4 py-3
          text-sm font-medium text-danger">
          Odoo authentication is failing — sync is stalled. The shared account's password may have
          changed or 2FA was enabled. Fix the credentials, then “Sync now”.
        </div>
      )}

      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat
          label="Odoo connection"
          value={data.odoo_mode === "live" ? "Live" : "Fixture"}
          hint={data.odoo_mode === "live" ? data.odoo_base_url : "no credentials — simulator data"}
          tone={data.odoo_mode === "live" ? "good" : "default"}
        />
        <Stat
          label="Writes"
          value={data.writes_enabled ? "Enabled" : "Kill switch OFF"}
          hint={data.writes_enabled ? "gated per-operation by flags" : "every write renders a dry-run"}
          tone={data.writes_enabled ? "warn" : "good"}
        />
        <Stat label="Auth mode" value={data.auth_mode === "dev" ? "Dev codes" : "Supabase OTP"} />
        <Stat
          label="Overall"
          value={data.status === "ok" ? "Healthy" : data.status}
          tone={data.status === "ok" ? "good" : "bad"}
        />
      </div>

      <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {Object.entries(data.sync).map(([domain, d]) => (
          <SyncCard key={domain} domain={domain} d={d} />
        ))}
      </div>

      <div className="mb-6 grid gap-4 lg:grid-cols-2">
        <CanaryCard />
        <Card>
          <h3 className="display mb-1 text-[16px]">Feature flags</h3>
          <p className="mb-3 text-[13px] text-ink-faint">
            Write operations ship OFF and graduate only after their canary passes.
          </p>
          <div className="flex flex-col gap-3">
            {data.flags.map((f) => (
              <div key={f.key} className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate font-mono text-[12.5px]">{f.key}</div>
                  <div className="truncate text-[12.5px] text-ink-faint">{f.description}</div>
                </div>
                <Toggle
                  checked={f.enabled}
                  onChange={(v) =>
                    setFlag.mutate(
                      { key: f.key, enabled: v },
                      {
                        onSuccess: () =>
                          toast.info(`${f.key} ${v ? "enabled" : "disabled"}.`),
                        onError: (e) => toast.error(e.message),
                      },
                    )
                  }
                />
              </div>
            ))}
            {data.flags.length === 0 && (
              <div className="text-[13px] text-ink-faint">No flags yet — run `make seed`.</div>
            )}
          </div>
        </Card>
      </div>

      <Card pad={false}>
        <div className="border-b border-line px-5 py-3.5">
          <h3 className="display text-[16px]">Recent sync runs</h3>
        </div>
        <div className="max-h-80 overflow-y-auto">
          <table className="w-full text-[13px]">
            <tbody>
              {data.recent_runs.map((r) => (
                <tr key={r.id} className="border-b border-line/60 last:border-0">
                  <td className="px-5 py-2 font-medium">{r.domain}</td>
                  <td className="px-2 py-2">
                    {r.status === "success" ? (
                      <Badge tone="forest">success</Badge>
                    ) : r.status === "failure" ? (
                      <Badge tone="danger">failure</Badge>
                    ) : (
                      <Badge>running</Badge>
                    )}
                  </td>
                  <td className="px-2 py-2 text-right tabular-nums">{r.rows} rows</td>
                  <td className="px-2 py-2 text-ink-faint">{r.trigger} · {r.source}</td>
                  <td className="px-2 py-2 text-ink-faint">
                    {r.started_at ? new Date(r.started_at).toLocaleString() : ""}
                  </td>
                  <td className="max-w-60 truncate px-5 py-2 text-danger" title={r.error}>
                    {r.error}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
