import { useState } from "react";
import type { ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useHealth } from "../api/hooks";
import { navForRoles } from "../nav";
import { StatusDot } from "../design";

function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-1">
      <span className="grid h-8 w-8 place-items-center rounded-full bg-copper">
        <span className="h-2.5 w-2.5 rounded-full bg-surface" />
      </span>
      <div className="leading-tight">
        <div className="display text-[15px]">Isha Life</div>
        <div className="label-caps">Shop Ops</div>
      </div>
    </div>
  );
}

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  const { roles } = useAuth();
  return (
    <nav className="mt-6 flex flex-col gap-0.5" aria-label="Main">
      {navForRoles(roles).map((item) => (
        <NavLink
          key={item.path}
          to={item.path}
          onClick={onNavigate}
          className={({ isActive }) =>
            `flex items-center gap-2.5 rounded-(--radius-sm) px-3 py-2 text-sm transition-colors
             ${isActive
               ? "bg-copper-tint font-medium text-copper-deep"
               : "text-ink-soft hover:bg-raised hover:text-ink"}`
          }
        >
          {item.icon}
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}

function HealthChip() {
  const { data } = useHealth();
  if (!data) return null;
  const ok = data.status === "ok";
  const label =
    data.status === "ok"
      ? `Synced · ${data.odoo_mode === "fixture" ? "fixture data" : "live Odoo"}`
      : data.odoo_auth_failed
        ? "Odoo auth failing!"
        : "Sync stale";
  return <StatusDot ok={ok} warn={!ok && !data.odoo_auth_failed} label={label} />;
}

export function AppShell({ title, children }: { title: string; children: ReactNode }) {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);

  const logout = () => {
    signOut();
    navigate("/login");
  };

  return (
    <div className="min-h-dvh md:grid md:grid-cols-[232px_1fr]">
      {/* desktop sidebar */}
      <aside className="hidden border-r border-line bg-surface px-3 py-5 md:block">
        <Brand />
        <NavList />
      </aside>

      {/* mobile top bar */}
      <div className="sticky top-0 z-30 flex items-center justify-between border-b border-line
        bg-surface px-4 py-3 md:hidden">
        <Brand />
        <button
          aria-label="Menu"
          onClick={() => setMenuOpen((v) => !v)}
          className="rounded p-2 text-ink-soft hover:bg-raised"
        >
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
            {menuOpen ? (
              <path d="M4 4l10 10M14 4L4 14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            ) : (
              <path d="M2.5 5h13M2.5 9h13M2.5 13h13" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            )}
          </svg>
        </button>
      </div>
      {menuOpen && (
        <div className="border-b border-line bg-surface px-4 pb-4 md:hidden">
          <NavList onNavigate={() => setMenuOpen(false)} />
          <button
            onClick={logout}
            className="mt-3 w-full rounded-(--radius-sm) border border-line px-3 py-2 text-left text-sm text-ink-soft"
          >
            Sign out {user?.display_name ? `(${user.display_name})` : ""}
          </button>
        </div>
      )}

      <div className="min-w-0">
        {/* desktop topbar */}
        <header className="sticky top-0 z-20 hidden items-center justify-between border-b
          border-line bg-canvas/90 px-8 py-3.5 backdrop-blur md:flex">
          <div className="display text-[15px] text-ink-soft">{title}</div>
          <div className="flex items-center gap-5">
            <HealthChip />
            <div className="flex items-center gap-2.5">
              <span className="grid h-7 w-7 place-items-center rounded-full bg-forest-tint
                text-[12px] font-semibold text-forest-deep">
                {(user?.display_name || user?.email || "?").slice(0, 1).toUpperCase()}
              </span>
              <span className="max-w-40 truncate text-[13px] text-ink-soft">
                {user?.display_name || user?.email}
              </span>
              <button
                onClick={logout}
                className="text-[13px] text-ink-faint underline-offset-2 hover:text-copper-deep hover:underline"
              >
                Sign out
              </button>
            </div>
          </div>
        </header>
        <main className="mx-auto w-full max-w-6xl px-4 py-6 md:px-8 md:py-8">{children}</main>
      </div>
    </div>
  );
}
