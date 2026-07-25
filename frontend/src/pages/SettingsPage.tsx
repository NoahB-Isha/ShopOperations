/* Settings — every role lands here from the top-bar gear.

   Appearance (the palette picker that used to be the Themes menu/page) is
   for everyone; admins additionally manage the app-wide product blacklist
   and reach the design pages (Styleguide, Palette lab) that left the nav. */
import { useState } from "react";
import { Link } from "react-router-dom";
import { usePatchProduct, useProducts } from "../api/hooks";
import type { ProductOut } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Badge, Button, Card, EmptyState, Input, PageHeader, Spinner, useToast } from "../design";
import { PALETTES, currentPalette, setPalette } from "../theme";

function AppearanceCard() {
  const [active, setActive] = useState(currentPalette);
  return (
    <Card>
      <h2 className="display mb-1 text-[16px]">Appearance</h2>
      <p className="mb-3 text-[13px] text-on-surface-variant">
        Pick a light-mode palette. Dark mode is automatic and follows your system.
      </p>
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
    </Card>
  );
}

function AccountCard() {
  const { user, roles } = useAuth();
  return (
    <Card>
      <h2 className="display mb-1 text-[16px]">Account</h2>
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

  const setFlag = (p: ProductOut, value: boolean) =>
    patch.mutate(
      { id: p.id, blacklisted: value },
      {
        onSuccess: () => {
          toast.success(
            value
              ? `“${p.name}” is hidden app-wide — lists, reports, ordering, everything.`
              : `“${p.name}” is visible again.`,
          );
          if (value) setSearch("");
        },
        onError: (e) => toast.error(e.message),
      },
    );

  const rows = blacklisted.data?.items ?? [];
  const matches = (candidates.data?.items ?? []).slice(0, 8);

  return (
    <Card>
      <h2 className="display mb-1 text-[16px]">Product blacklist</h2>
      <p className="mb-3 text-[13px] leading-5 text-on-surface-variant">
        Blacklisted items disappear from the whole app — catalogs, restock and OOS lists,
        ordering, reports, the time machine. Use it for stale Odoo entries and items that
        aren't shop operations. Odoo itself is untouched.
      </p>

      <Input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search items to blacklist…"
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
    </Card>
  );
}

function DesignCard() {
  return (
    <Card>
      <h2 className="display mb-1 text-[16px]">Design pages</h2>
      <p className="mb-3 text-[13px] text-on-surface-variant">
        The component styleguide and the full palette lab left the menu — they live here now.
      </p>
      <div className="flex flex-wrap gap-2">
        <Link to="/styleguide">
          <Button variant="outlined" size="sm">
            Open styleguide →
          </Button>
        </Link>
        <Link to="/palette-lab">
          <Button variant="outlined" size="sm">
            Open palette lab →
          </Button>
        </Link>
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
            ? "Appearance for you; the blacklist and design pages for the whole app."
            : "How the app looks on this device."
        }
      />
      <AppearanceCard />
      <AccountCard />
      {isAdmin && (
        <>
          <BlacklistCard />
          <DesignCard />
        </>
      )}
    </div>
  );
}
