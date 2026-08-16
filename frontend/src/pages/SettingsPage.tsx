/* Settings — every role lands here from the top-bar gear.

   Your own account comes FIRST (Noah, 2026-08-16): the page opens on who you
   are signed in as. Then appearance — theme and palette — for everyone.
   Admins additionally get the app-wide blacklist and the pages that left the
   nav to live here: Users, Dev Tools (the old Status page), and the design
   pages. */
import { useState } from "react";
import { Link } from "react-router-dom";
import { useBlacklistSweep, usePatchProduct, useProducts } from "../api/hooks";
import type { BlacklistSweepOut, ProductOut } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Badge, Button, Card, Dialog, EmptyState, Input, PageHeader, Spinner, Toggle, useToast } from "../design";
import { Icons } from "../nav";
import {
  PALETTES,
  THEME_MODES,
  currentPalette,
  currentThemeMode,
  setPalette,
  setThemeMode,
} from "../theme";
import type { ThemeMode } from "../theme";
import { setSillyMode, useSillyLabel, useSillyMode } from "../silly";

function AppearanceCard() {
  const [active, setActive] = useState(currentPalette);
  const [mode, setMode] = useState<ThemeMode>(currentThemeMode);
  const silly = useSillyMode();
  const s = useSillyLabel();
  return (
    <Card>
      <h2 className="display mb-1 text-[16px]">{s("Appearance")}</h2>
      <p className="mb-3 text-[13px] text-on-surface-variant">
        Light or dark, and which palette light mode wears. This device only.
      </p>

      {/* Dark used to follow the device with no way out: a phone in dark mode
          meant no palette choice at all. */}
      <div className="mb-4 grid grid-cols-3 gap-2">
        {THEME_MODES.map((m) => (
          <button
            key={m.id}
            onClick={() => {
              setThemeMode(m.id);
              setMode(m.id);
            }}
            aria-pressed={mode === m.id}
            title={m.hint}
            className={`state-layer rounded-(--radius-md) border px-3 py-2 text-[13px]
              transition-all duration-200 ease-(--ease-spring)
              ${
                mode === m.id
                  ? "border-primary bg-secondary-container font-semibold text-on-secondary-container"
                  : "border-outline-variant font-medium text-on-surface-variant hover:-translate-y-0.5"
              }`}
          >
            {m.label}
          </button>
        ))}
      </div>

      <h3 className="mb-2 text-[14px] font-semibold">Light palette</h3>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {PALETTES.map((p) => (
          <button
            key={p.id}
            onClick={() => {
              setPalette(p.id);
              setActive(p.id);
            }}
            aria-pressed={active === p.id}
            className={`state-layer flex items-center gap-2.5 rounded-(--radius-md) border px-3 py-2.5
              text-left text-[13px] transition-all duration-200 ease-(--ease-spring)
              ${
                active === p.id
                  ? "border-primary bg-secondary-container font-semibold text-on-secondary-container"
                  : "border-outline-variant font-medium text-on-surface-variant hover:-translate-y-0.5"
              }`}
          >
            <span
              className="h-4 w-4 shrink-0 rounded-full"
              style={{ backgroundColor: p.dot }}
              aria-hidden
            />
            {p.label}
          </button>
        ))}
      </div>

      <div className="mt-4 flex items-start justify-between gap-4 border-t border-outline-variant/60 pt-3">
        <div>
          <h3 className="text-[14px] font-semibold">
            Silly mode {silly && <span aria-hidden>🕶️</span>}
          </h3>
          <p className="text-[12.5px] leading-4.5 text-on-surface-variant">
            Menus and page titles get their street names — Purchasing becomes “Get the goods”,
            Users become “Peeps”, Reports become 🤑🤑🤑. Purely cosmetic, this device only;
            every number stays serious.
          </p>
        </div>
        <Toggle checked={silly} onChange={setSillyMode} />
      </div>
    </Card>
  );
}

function AccountCard() {
  const { user, roles } = useAuth();
  const s = useSillyLabel();
  return (
    <Card>
      <h2 className="display mb-1 text-[16px]">{s("Account")}</h2>
      <div className="flex flex-col gap-1.5 text-[13.5px]">
        <div>
          <span className="text-on-surface-variant">Signed in as </span>
          <span className="font-semibold">{user?.display_name || user?.email}</span>
          {user?.display_name && user?.email && (
            <span className="text-on-surface-variant"> · {user.email}</span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-on-surface-variant">Roles:</span>
          {[...roles].map((r) => (
            <Badge key={r} tone={r === "admin" ? "copper" : "neutral"}>
              {r.replace(/_/g, " ")}
            </Badge>
          ))}
        </div>
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------- blacklist */

function BlacklistRow({
  p,
  action,
  onAction,
  busy,
}: {
  p: ProductOut;
  action: string;
  onAction: () => void;
  busy: boolean;
}) {
  return (
    <li className="flex items-center justify-between gap-3 rounded-(--radius-md) bg-surface-container-low px-3 py-2">
      <div className="min-w-0">
        <div className="truncate text-[13.5px] font-medium">{p.name}</div>
        <div className="text-[11.5px] text-on-surface-variant">
          <span className="font-mono">{p.odoo_internal_ref || p.global_sku}</span>
          {p.category && <> · {p.category}</>}
        </div>
      </div>
      <Button variant="ghost" size="sm" disabled={busy} onClick={onAction}>
        {action}
      </Button>
    </li>
  );
}

/** One-click cleanup: preview, then blacklist every never-stocked item and
 *  every "-USA" duplicate. Re-runnable as new junk syncs in from Odoo. */
function SweepBlock() {
  const toast = useToast();
  const sweep = useBlacklistSweep();
  const [preview, setPreview] = useState<BlacklistSweepOut | null>(null);

  const run = (apply: boolean) =>
    sweep.mutate(apply, {
      onSuccess: (out) => {
        if (!apply) {
          setPreview(out);
          return;
        }
        setPreview(null);
        toast.success(
          out.total
            ? `${out.total} item(s) blacklisted — they're gone from the whole app.`
            : "Nothing left to sweep.",
        );
      },
      onError: (e) => toast.error(e.message),
    });

  const s = useSillyLabel();
  return (
    <div className="mt-4 border-t border-outline-variant/60 pt-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="label-m text-on-surface-variant">{s("Cleanup sweep")}</h3>
          <p className="text-[12.5px] text-on-surface-variant">
            Finds items that have never had stock <b>and never sold</b> (except IL-Service)
            plus “- USA” duplicate entries. Preview first — nothing is hidden until you
            confirm.
          </p>
        </div>
        <Button variant="outlined" size="sm" loading={sweep.isPending && !preview} onClick={() => run(false)}>
          Preview sweep…
        </Button>
      </div>
      <Dialog
        open={preview !== null}
        onClose={() => setPreview(null)}
        title="Blacklist sweep"
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setPreview(null)}>
              Cancel
            </Button>
            <Button
              disabled={!preview?.total || sweep.isPending}
              loading={sweep.isPending}
              onClick={() => run(true)}
            >
              Blacklist {preview?.total ?? 0} item(s)
            </Button>
          </div>
        }
      >
        {preview && (
          <div className="flex flex-col gap-3 text-[13.5px]">
            <div className="flex flex-wrap gap-2">
              <Badge tone="gold">{preview.no_stock_history} never stocked</Badge>
              <Badge tone="gold">{preview.usa_items} “- USA” items</Badge>
              <Badge tone="outline">{preview.total} total (overlap deduped)</Badge>
            </div>
            {preview.total === 0 ? (
              <p className="text-on-surface-variant">Nothing matches the sweep rules — all clean.</p>
            ) : (
              <>
                <p className="text-on-surface-variant">
                  First {Math.min(preview.sample.length, 15)} of {preview.total}:
                </p>
                <ul className="max-h-56 list-disc overflow-y-auto pl-5 text-[13px] text-on-surface">
                  {preview.sample.map((name, i) => (
                    <li key={`${i}-${name}`}>{name}</li>
                  ))}
                </ul>
                <p className="text-[12.5px] text-on-surface-variant">
                  Everything stays restorable from the blacklist below. Odoo is untouched.
                </p>
              </>
            )}
          </div>
        )}
      </Dialog>
    </div>
  );
}

function BlacklistCard() {
  const toast = useToast();
  const patch = usePatchProduct();
  const [search, setSearch] = useState("");
  const blacklisted = useProducts({
    search: "",
    category: "",
    tag: "",
    page: 1,
    sort: "name",
    dir: "asc",
    include_inactive: true,
    blacklisted: true,
  });
  const candidates = useProducts({
    search,
    category: "",
    tag: "",
    page: 1,
    sort: "name",
    dir: "asc",
    include_inactive: true,
  });

  // the search stays open after adding so several matches can be
  // blacklisted in a row — the added item just drops out of the results
  const setFlag = (p: ProductOut, value: boolean) =>
    patch.mutate(
      { id: p.id, blacklisted: value },
      {
        onSuccess: () =>
          toast.success(
            value
              ? `“${p.name}” is hidden app-wide — lists, reports, ordering, everything.`
              : `“${p.name}” is visible again.`,
          ),
        onError: (e) => toast.error(e.message),
      },
    );

  const rows = blacklisted.data?.items ?? [];
  const matches = (candidates.data?.items ?? []).slice(0, 8);
  const s = useSillyLabel();

  return (
    <Card>
      <h2 className="display mb-1 text-[16px]">{s("Product blacklist")}</h2>
      <p className="mb-3 text-[13px] leading-5 text-on-surface-variant">
        Blacklisted items disappear from the whole app — catalogs, restock and OOS lists,
        ordering, reports, the time machine. Use it for stale Odoo entries and items that
        aren't shop operations. Odoo itself is untouched.
      </p>

      <Input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder={s("Search items to blacklist…")}
        aria-label="Search items to blacklist"
        className="mb-2 w-full"
      />
      {search.trim() && (
        <ul className="mb-4 flex flex-col gap-1.5">
          {candidates.isLoading ? (
            <li className="grid place-items-center py-4">
              <Spinner size={18} />
            </li>
          ) : matches.length === 0 ? (
            <li className="py-2 text-center text-[13px] text-on-surface-variant">
              Nothing matches “{search.trim()}”.
            </li>
          ) : (
            matches.map((p) => (
              <BlacklistRow
                key={p.id}
                p={p}
                action="Blacklist"
                busy={patch.isPending}
                onAction={() => setFlag(p, true)}
              />
            ))
          )}
        </ul>
      )}

      <h3 className="label-m mb-2 text-on-surface-variant">
        Currently blacklisted{blacklisted.data ? ` (${blacklisted.data.total})` : ""}
      </h3>
      {blacklisted.isLoading ? (
        <div className="grid place-items-center py-6">
          <Spinner size={18} />
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          title="Nothing blacklisted"
          hint="Search above and hide the items that don't belong in shop operations."
        />
      ) : (
        <ul className="flex max-h-80 flex-col gap-1.5 overflow-y-auto">
          {rows.map((p) => (
            <BlacklistRow
              key={p.id}
              p={p}
              action="Restore"
              busy={patch.isPending}
              onAction={() => setFlag(p, false)}
            />
          ))}
        </ul>
      )}
      <SweepBlock />
    </Card>
  );
}

/** The admin pages that left the main nav. They are still full pages at their
 *  own routes — this is just where you reach them from now. */
function AdminPagesCard() {
  const s = useSillyLabel();
  const links: { to: string; label: string; hint: string; icon: React.ReactNode }[] = [
    {
      to: "/users",
      label: s("Users"),
      hint: "Invite people, set roles, deactivate",
      icon: Icons.users,
    },
    {
      to: "/status",
      label: s("Dev Tools"),
      hint: "Syncs, feature flags, audit log, Odoo health",
      icon: Icons.pulse,
    },
    { to: "/styleguide", label: "Styleguide", hint: "Every component, one page", icon: Icons.palette },
    { to: "/palette-lab", label: "Palette lab", hint: "The full token set", icon: Icons.palette },
  ];
  return (
    <Card>
      <h2 className="display mb-1 text-[16px]">Admin pages</h2>
      <p className="mb-3 text-[13px] text-on-surface-variant">
        Everything that doesn't belong in the daily menu.
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        {links.map((l) => (
          <Link
            key={l.to}
            to={l.to}
            className="state-layer flex items-center gap-3 rounded-(--radius-md) border
              border-outline-variant px-3 py-2.5 transition-transform duration-200
              ease-(--ease-spring) hover:-translate-y-0.5"
          >
            <span className="text-on-surface-variant">{l.icon}</span>
            <span className="min-w-0">
              <span className="block text-[13.5px] font-semibold text-on-surface">{l.label}</span>
              <span className="block text-[12px] text-on-surface-variant">{l.hint}</span>
            </span>
          </Link>
        ))}
      </div>
    </Card>
  );
}

export function SettingsPage() {
  const { roles } = useAuth();
  const isAdmin = roles.has("admin");
  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-4">
      <PageHeader
        title="Settings"
        subtitle={
          isAdmin
            ? "Your account and appearance; users, dev tools and the blacklist for the whole app."
            : "Your account, and how the app looks on this device."
        }
      />
      <AccountCard />
      <AppearanceCard />
      {isAdmin && (
        <>
          <AdminPagesCard />
          <BlacklistCard />
        </>
      )}
    </div>
  );
}
