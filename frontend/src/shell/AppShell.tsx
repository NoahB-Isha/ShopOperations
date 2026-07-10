import { useState } from "react";
import type { ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useHealth } from "../api/hooks";
import { navForRoles } from "../nav";
import type { NavItem } from "../nav";
import { StatusDot } from "../design";

/** Eight-petal flower — the brand mark. It twirls when you say hello. */
export function FlowerMark({ size = 34 }: { size?: number }) {
  return (
    <span
      className="twirl-on-hover grid shrink-0 place-items-center rounded-full text-primary"
      style={{ width: size, height: size }}
      aria-hidden
    >
      <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        {[0, 45, 90, 135].map((a) => (
          <ellipse key={a} cx="12" cy="12" rx="10.5" ry="3.6" transform={`rotate(${a} 12 12)`} />
        ))}
        <circle cx="12" cy="12" r="2.4" fill="var(--color-tertiary-container)" />
      </svg>
    </span>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-1">
      <FlowerMark />
      <div className="leading-tight">
        <div className="display text-[15px]">Isha Life</div>
        <div className="label-m">Shop Ops</div>
      </div>
    </div>
  );
}

/** M3 navigation drawer items: full-width pills, secondary-container when active. */
function NavList({ items, onNavigate }: { items: NavItem[]; onNavigate?: () => void }) {
  return (
    <nav className="mt-5 flex flex-col gap-1" aria-label="Main">
      {items.map((item) => (
        <NavLink
          key={item.path}
          to={item.path}
          onClick={onNavigate}
          className={({ isActive }) =>
            `state-layer flex h-11 items-center gap-3 rounded-full px-4 text-sm transition-colors
             ${
               isActive
                 ? "bg-secondary-container font-semibold text-on-secondary-container"
                 : "font-medium text-on-surface-variant"
             }`
          }
        >
          {item.icon}
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}

/** M3 bottom navigation bar — phones, roles with few destinations. */
function BottomNav({ items }: { items: NavItem[] }) {
  return (
    <nav
      aria-label="Main"
      className="fixed inset-x-0 bottom-0 z-30 flex justify-around gap-1 bg-surface-container
        px-2 pt-2 pb-[max(0.75rem,env(safe-area-inset-bottom))] shadow-[0_-1px_0_var(--color-outline-variant)]
        md:hidden"
    >
      {items.map((item) => (
        <NavLink
          key={item.path}
          to={item.path}
          className="group flex min-w-16 flex-col items-center gap-1 text-[11px] font-medium"
        >
          {({ isActive }) => (
            <>
              <span
                className={`grid h-8 w-16 place-items-center rounded-full transition-all
                  duration-200 ease-(--ease-emphasized)
                  ${
                    isActive
                      ? "bg-secondary-container text-on-secondary-container"
                      : "text-on-surface-variant group-hover:bg-on-surface/5"
                  }`}
              >
                {item.icon}
              </span>
              <span className={isActive ? "font-semibold text-on-surface" : "text-on-surface-variant"}>
                {item.label}
              </span>
            </>
          )}
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
  return (
    <span className="inline-flex items-center rounded-(--radius-sm) border border-outline-variant
      bg-surface-container-low px-2.5 py-1">
      <StatusDot ok={ok} warn={!ok && !data.odoo_auth_failed} label={label} />
    </span>
  );
}

export function AppShell({ title, children }: { title: string; children: ReactNode }) {
  const { user, roles, signOut } = useAuth();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);

  const items = navForRoles(roles);
  // M3: bottom bar handles up to 5 destinations; busier roles get the drawer.
  const bottomBar = items.length <= 5;

  const logout = () => {
    signOut();
    navigate("/login");
  };

  return (
    <div className="min-h-dvh md:grid md:grid-cols-[248px_1fr]">
      {/* desktop: standard navigation drawer */}
      <aside className="hidden bg-surface-container-low px-3 py-6 md:block">
        <Brand />
        <NavList items={items} />
      </aside>

      {/* mobile top app bar */}
      <div className="sticky top-0 z-30 flex items-center justify-between bg-surface-container-low
        px-4 py-3 md:hidden">
        <Brand />
        {bottomBar ? (
          <button
            aria-label="Sign out"
            onClick={logout}
            className="state-layer grid h-10 w-10 place-items-center rounded-full text-on-surface-variant"
          >
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
              <path
                d="M7 3H4.5A1.5 1.5 0 0 0 3 4.5v9A1.5 1.5 0 0 0 4.5 15H7M12 12.5 15.5 9 12 5.5M15 9H7"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        ) : (
          <button
            aria-label="Menu"
            onClick={() => setMenuOpen((v) => !v)}
            className="state-layer grid h-10 w-10 place-items-center rounded-full text-on-surface-variant"
          >
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
              {menuOpen ? (
                <path d="M4 4l10 10M14 4L4 14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              ) : (
                <path d="M2.5 5h13M2.5 9h13M2.5 13h13" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              )}
            </svg>
          </button>
        )}
      </div>
      {menuOpen && !bottomBar && (
        <div className="bg-surface-container-low px-4 pb-4 shadow-(--shadow-e1) md:hidden">
          <NavList items={items} onNavigate={() => setMenuOpen(false)} />
          <button
            onClick={logout}
            className="state-layer mt-3 w-full rounded-full border border-outline-variant px-4
              py-2.5 text-left text-sm text-on-surface-variant"
          >
            Sign out {user?.display_name ? `(${user.display_name})` : ""}
          </button>
        </div>
      )}

      <div className="min-w-0">
        {/* desktop top app bar */}
        <header className="sticky top-0 z-20 hidden items-center justify-between bg-surface/85
          px-8 py-3.5 backdrop-blur md:flex">
          <div className="title-l text-on-surface">{title}</div>
          <div className="flex items-center gap-4">
            <HealthChip />
            <div className="flex items-center gap-2.5">
              <span className="grid h-8 w-8 place-items-center rounded-full bg-tertiary-container
                text-[12.5px] font-bold text-on-tertiary-container">
                {(user?.display_name || user?.email || "?").slice(0, 1).toUpperCase()}
              </span>
              <span className="max-w-40 truncate text-[13px] text-on-surface-variant">
                {user?.display_name || user?.email}
              </span>
              <button
                onClick={logout}
                className="text-[13px] font-medium text-primary underline-offset-2 hover:underline"
              >
                Sign out
              </button>
            </div>
          </div>
        </header>
        <main
          className={`mx-auto w-full max-w-6xl px-4 py-6 md:px-8 md:py-8
            ${bottomBar ? "pb-28 md:pb-8" : ""}`}
        >
          {children}
        </main>
      </div>

      {bottomBar && <BottomNav items={items} />}
    </div>
  );
}
