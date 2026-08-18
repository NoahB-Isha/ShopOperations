import { useState } from "react";
import { Link } from "react-router-dom";
import {
  useAdminStatus,
  useCanary,
  useRebuildSalesHistory,
  useReleaseStaleCounts,
  useResetTransferFlow,
  useSetFlag,
  useTriggerSync,
} from "../../api/hooks";
import type {
  CanaryResult,
  DomainSync,
  NotificationsStatusOut,
  NotifyChannelOut,
  ReleaseStaleOut,
  ResetFlowOut,
} from "../../api/types";
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
      {domain === "sales" && <RebuildSalesButton />}
    </Card>
  );
}

function RebuildSalesButton() {
  const rebuild = useRebuildSalesHistory();
  const toast = useToast();
  return (
    <Button
      variant="ghost"
      size="sm"
      loading={rebuild.isPending}
      onClick={() => {
        if (
          !window.confirm(
            "Re-pull the FULL sales window from Odoo (the one deliberate heavy query)? " +
              "This fills channel splits, revenue amounts, and order/customer metrics " +
              "for months synced before those existed.",
          )
        )
          return;
        rebuild.mutate(undefined, {
          onSuccess: (r) =>
            r.status === "success"
              ? toast.success(`Sales history rebuilt — ${r.rows.toLocaleString()} monthly rows.`)
              : toast.error(r.error || "Rebuild failed — see sync status."),
          onError: (e) => toast.error(e.message),
        });
      }}
    >
      Rebuild history…
    </Button>
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

function ReleaseStaleCountsCard() {
  /* One-time (2026-08-18): requests that were mid-flight when the delivery
     form landed sit in `counting` with their own count transfer, which isn't
     a status the form can link — so they'd wait forever while their stock
     rides the warehouse's next pallet. Preview, then apply. */
  const release = useReleaseStaleCounts();
  const toast = useToast();
  const [result, setResult] = useState<ReleaseStaleOut | null>(null);

  const run = (apply: boolean) =>
    release.mutate(apply, {
      onSuccess: (r) => {
        setResult(r);
        if (!apply) return;
        toast.success(
          r.released > 0
            ? `${r.released} request(s) handed back to the delivery form.`
            : "Nothing needed releasing.",
        );
      },
      onError: (e) => toast.error(e.message),
    });

  return (
    <Card>
      <div className="mb-1 flex items-center justify-between">
        <h3 className="display text-[16px]">Stranded transfer requests</h3>
        <Badge tone="outline">one-time</Badge>
      </div>
      <p className="mb-3 text-[13px] leading-5 text-ink-faint">
        Requests that were already waiting on their own count transfer when the delivery form
        landed can't be picked up by it — their stock is really sitting in Staging 2, riding the
        next pallet. This hands them back to <b>Staged</b> so the form offers them, and forgets
        the per-request count (a pallet gets one count for everything on it). A count Odoo says
        was validated is left alone.
      </p>
      <div className="flex gap-2">
        <Button
          variant="secondary"
          size="sm"
          loading={release.isPending}
          onClick={() => run(false)}
        >
          Preview
        </Button>
        <Button
          size="sm"
          disabled={!result || result.applied || result.released === 0}
          loading={release.isPending}
          onClick={() => run(true)}
        >
          {result && result.released > 0
            ? `Release ${result.released} request${result.released === 1 ? "" : "s"}`
            : "Release"}
        </Button>
      </div>

      {result && (
        <div className="mt-4 flex flex-col gap-2 border-t border-line pt-3">
          <p className="text-[13px] font-medium">{result.note}</p>
          {result.rows.map((r) => (
            <div key={r.request_id} className="text-[13px]">
              <span className="font-mono font-semibold">{r.display_name}</span>{" "}
              <span className="text-ink-faint">
                · {r.line_count} item(s) · {r.action === "already_counted" ? "left alone" : "release"}
              </span>
              <div className="text-[12.5px] leading-4 text-ink-faint">{r.detail}</div>
            </div>
          ))}
          {result.cancel_in_odoo.length > 0 && (
            <div className="mt-1 rounded-(--radius-md) bg-warn-container px-3 py-2">
              <div className="text-[13px] font-semibold">Cancel these in Odoo</div>
              <p className="mb-1 text-[12.5px] leading-4">
                The app doesn't touch them. Left open, the floor could scan one of these AND the
                pallet's count — the same units twice.
              </p>
              {result.cancel_in_odoo.map((c) => (
                <a
                  key={c.picking_name}
                  href={c.url}
                  target="_blank"
                  rel="noreferrer"
                  className="block font-mono text-[12.5px] font-medium text-copper-deep hover:underline"
                >
                  {c.picking_name} ({c.state}) ↗
                </a>
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function ResetFlowCard() {
  /* One-time (2026-08-18): two weeks of testing left a full board and fifteen
     undeclared pallets. Preview, then apply. Deliberately NOT a one-click
     button — it deletes rows, so the preview is the confirmation step, and
     the second click needs a typed word. */
  const reset = useResetTransferFlow();
  const toast = useToast();
  const [result, setResult] = useState<ResetFlowOut | null>(null);
  const [confirm, setConfirm] = useState("");
  const KEEP_HOURS = 24;

  const run = (apply: boolean) =>
    reset.mutate(
      { apply, keep_hours: KEEP_HOURS },
      {
        onSuccess: (r) => {
          setResult(r);
          if (apply) {
            setConfirm("");
            toast.success(
              `Cleared ${r.requests_cleared} request(s) and ${r.pallets_cleared} pallet(s).`,
            );
          }
        },
        onError: (e) => toast.error(e.message),
      },
    );

  const previewed = result && !result.applied;
  const nothingToDo =
    result !== null && result.requests_cleared === 0 && result.pallets_cleared === 0;

  return (
    <Card>
      <div className="mb-1 flex items-center justify-between">
        <h3 className="display text-[16px]">Reset the transfer flow</h3>
        <Badge tone="outline">one-time · deletes</Badge>
      </div>
      <p className="mb-3 text-[13px] leading-5 text-ink-faint">
        Clears the testing rubble so the real process starts from a known point: every transfer
        request older than {KEEP_HOURS}h and <b>all</b> pallet records go, along with their
        events and adjustments. Anything requested in the last {KEEP_HOURS}h is kept. The app
        removes only its <b>own still-draft</b> pickings from Odoo — validated ones, and anything
        a human made, are listed for you instead. The next pallet validated in Odoo is the first
        one the app will see.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="secondary" size="sm" loading={reset.isPending} onClick={() => run(false)}>
          Preview
        </Button>
        {previewed && !nothingToDo && (
          <>
            <input
              className="m3-control w-32 rounded-(--radius-sm) border border-outline-variant bg-field px-2 py-1 text-[13px]"
              placeholder="type CLEAR"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              aria-label="Type CLEAR to confirm"
            />
            <Button
              variant="danger"
              size="sm"
              disabled={confirm.trim().toUpperCase() !== "CLEAR"}
              loading={reset.isPending}
              onClick={() => run(true)}
            >
              Clear {result.requests_cleared} request(s) · {result.pallets_cleared} pallet(s)
            </Button>
          </>
        )}
      </div>

      {result && (
        <div className="mt-4 flex flex-col gap-2 border-t border-line pt-3 text-[13px]">
          <p className="font-medium">{result.note}</p>
          {result.kept.length > 0 && (
            <p className="text-ink-faint">
              Keeping: <span className="font-mono">{result.kept.join(", ")}</span>
            </p>
          )}
          {result.drafts_removed.length > 0 && (
            <p className="text-ink-faint">
              App drafts {result.applied ? "removed from" : "to remove from"} Odoo:{" "}
              <span className="font-mono">{result.drafts_removed.join(", ")}</span>
            </p>
          )}
          {result.already_gone.length > 0 && (
            <p className="text-ink-faint">
              Already gone from Odoo (nothing to do):{" "}
              <span className="font-mono">{result.already_gone.join(", ")}</span>
            </p>
          )}
          {result.leftovers.length > 0 && (
            <div className="rounded-(--radius-md) bg-warn-container px-3 py-2">
              <div className="text-[13px] font-semibold">Left in Odoo for you</div>
              <p className="mb-1 text-[12.5px] leading-4">
                The app never touches a picking it didn't create, or one that already moved stock.
              </p>
              {result.leftovers.map((l) => (
                <div key={l.picking_name} className="mb-1">
                  <a
                    href={l.url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-[12.5px] font-medium text-copper-deep hover:underline"
                  >
                    {l.picking_name} ({l.state}) ↗
                  </a>
                  <div className="text-[12px] leading-4 text-ink-faint">
                    {l.belonged_to} — {l.reason}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function ChannelRow({ name, c }: { name: string; c: NotifyChannelOut }) {
  const chip = !c.configured ? (
    // WhatsApp is deliberately paused for now — don't dress "off" as a fault
    <Badge tone="neutral">{name === "whatsapp" ? "on hold" : "not configured"}</Badge>
  ) : !c.live ? (
    <Badge tone="gold" title={c.gate ?? undefined}>simulating · {c.gate}</Badge>
  ) : c.connected ? (
    <Badge tone="forest">connected</Badge>
  ) : (
    <Badge tone="danger" title={c.last_error}>down · falls back</Badge>
  );
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="min-w-0">
        <div className="text-[13.5px] font-semibold capitalize">{name}</div>
        <div className="truncate text-[12px] text-ink-faint">
          {!c.configured && name === "whatsapp"
            ? "paused for now — email carries notifications"
            : c.detail || (c.configured ? "ok" : "set SMTP_HOST")}
          {c.consecutive_failures > 0 && ` · ${c.consecutive_failures} failure(s) in a row`}
        </div>
      </div>
      {chip}
    </div>
  );
}

/** WhatsApp bridge + email fallback health — unofficial bridges drop sessions,
 *  so this is watched, not assumed. */
function NotificationsCard({ n }: { n: NotificationsStatusOut }) {
  return (
    <Card>
      <div className="mb-1 flex items-center justify-between">
        <h3 className="display text-[16px]">Notifications</h3>
        {!n.enabled ? (
          <Badge tone="gold">kill switch OFF</Badge>
        ) : n.has_pending ? (
          <Badge tone="gold">retrying sends</Badge>
        ) : (
          <Badge tone="forest">idle</Badge>
        )}
      </div>
      <p className="mb-3 text-[13px] text-ink-faint">
        WhatsApp is on hold for now — email is the delivery channel. Gated sends are
        recorded as simulated; nothing silently pretends to deliver.
      </p>
      <div className="flex flex-col gap-3">
        <ChannelRow name="whatsapp" c={n.whatsapp} />
        <ChannelRow name="email" c={n.email} />
      </div>
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
        title="Dev Tools"
        subtitle="Sync freshness, write safety, and the paper trail — the honest view."
        actions={
          <Link to="/audit">
            <Button variant="outlined">Audit log →</Button>
          </Link>
        }
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

      <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {Object.entries(data.sync).map(([domain, d]) => (
          <SyncCard key={domain} domain={domain} d={d} />
        ))}
      </div>

      <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <CanaryCard />
        <ReleaseStaleCountsCard />
        <ResetFlowCard />
        {data.notifications && <NotificationsCard n={data.notifications} />}
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
