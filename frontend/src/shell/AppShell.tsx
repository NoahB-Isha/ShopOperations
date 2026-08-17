import { useRef, useState } from "react";
import type { ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useFloorRequests, useHealth } from "../api/hooks";
import { Icons, navForRoles } from "../nav";
import type { NavItem } from "../nav";
import { StatusDot } from "../design";
import { ScanButton, ScanSheetLazy } from "../scan/ScanButton";
import { useSillyLabel } from "../silly";
import { InboxMenu } from "./InboxMenu";
import { TransferDraftBubble } from "./TransferDraftBubble";

/** The Isha Life "iL" emblem — the provided brand PNG, served from /public
 *  (all-white variant in dark mode). Bounces when you say hello. */
export function ILMark({ size = 36 }: { size?: number }) {
  const width = Math.round(size * (353 / 400));
  return (
    <picture className="bounce-on-hover grid shrink-0 place-items-center" aria-hidden>
      <source srcSet="/il-mark-dark.png" media="(prefers-color-scheme: dark)" />
      <img
        src="/il-mark.png"
        alt=""
        width={width}
        height={size}
        style={{ width, height: size }}
        draggable={false}
      />
    </picture>
  );
}

function Brand() {
  // "Isha Life" is the org and stays; the app name is fair game
  const s = useSillyLabel();
  return (
    <div className="flex items-center gap-2.5 px-1">
      <ILMark />
      <div className="leading-tight">
        <div className="display text-[15px]">Isha Life</div>
        <div className="label-m">{s("Shop Ops")}</div>
      </div>
    </div>
  );
}

/** A quiet "someone is waiting on you" mark. Not a count — the page says how
 *  many; the dot only says "look here". */
function NavDot() {
  return (
    <span
      aria-label="new"
      className="ml-auto h-2 w-2 shrink-0 rounded-full bg-error"
    />
  );
}

/** M3 navigation drawer items: full-width pills, secondary-container when active. */
function NavList({
  items,
  dotted,
  onNavigate,
}: {
  items: NavItem[];
  dotted?: Set<string>;
  onNavigate?: () => void;
}) {
  const s = useSillyLabel();
  return (
    <nav className="stagger-children mt-5 flex flex-col gap-1" aria-label="Main">
      {items.map((item) => (
        <NavLink
          key={item.path}
          to={item.path}
          onClick={onNavigate}
          className={({ isActive }) =>
            `state-layer flex h-11 items-center gap-3 rounded-full px-4 text-sm
             transition-[transform,background-color,color] duration-200 ease-(--ease-spring)
             ${
               isActive
                 ? "bg-secondary-container font-semibold text-on-secondary-container"
                 : "font-medium text-on-surface-variant hover:translate-x-1"
             }`
          }
        >
          {item.icon}
          {s(item.label)}
          {dotted?.has(item.path) && <NavDot />}
        </NavLink>
      ))}
    </nav>
  );
}

/** One destination in the bottom bar. */
function BottomNavLink({
  item,
  dotted,
}: {
  item: NavItem;
  dotted?: Set<string>;
}) {
  const s = useSillyLabel();
  return (
    <NavLink
      to={item.path}
      className="group flex min-w-0 flex-1 flex-col items-center gap-1 text-[11px] font-medium"
    >
          {({ isActive }) => (
            <>
              <span
                className={`relative grid h-8 w-16 place-items-center rounded-full transition-all
                  duration-300 ease-(--ease-spring)
                  ${
                    isActive
                      ? "scale-110 bg-secondary-container text-on-secondary-container"
                      : "text-on-surface-variant group-hover:scale-105 group-hover:bg-on-surface/5"
                  }`}
              >
                {item.icon}
                {dotted?.has(item.path) && (
                  <span
                    aria-label="new"
                    className="absolute top-1 right-4 h-2 w-2 rounded-full bg-error"
                  />
                )}
              </span>
              <span
                className={`w-full truncate text-center ${
                  isActive ? "font-semibold text-on-surface" : "text-on-surface-variant"
                }`}
              >
                {s(item.short ?? item.label)}
              </span>
            </>
          )}
    </NavLink>
  );
}

/** M3 bottom navigation bar — EVERY role gets one on a phone now.
 *
 *  Five slots. A role with more destinations keeps the first four and the
 *  fifth becomes "More", which opens the rest in a sheet — the hamburger
 *  drawer this replaced put the whole menu behind a corner tap on exactly the
 *  roles with the most to reach. The scanner rides in the bar as its own
 *  destination (Noah, 2026-08-16) rather than hiding in the top bar.
 *
 *  Search is the exception: it leaves the row for a round FAB docked into the
 *  bar and breaking out above it (Noah, 2026-08-17). Finding a product is the
 *  thing people do most on a phone, and the bar's own slots are all the same
 *  size, so it could never look like the primary action while it sat in one.
 *  It reserves a slot's worth of width rather than floating over an icon —
 *  a 56px circle on top of a tappable label is a mis-tap waiting to happen. */
const BOTTOM_SLOTS = 5;
/** The one destination that becomes the FAB. */
const SEARCH_PATH = "/catalog";

function BottomNav({
  items,
  dotted,
  onScan,
}: {
  items: NavItem[];
  dotted?: Set<string>;
  onScan: () => void;
}) {
  const s = useSillyLabel();
  const [moreOpen, setMoreOpen] = useState(false);
  // Roles without an inventory search (Order Reviewer, Order Requester) get no
  // FAB — there is nothing for it to open.
  const search = items.find((i) => i.path === SEARCH_PATH);
  const rest = items.filter((i) => i.path !== SEARCH_PATH);
  // Scan is PINNED: it was asked for as a menu item, so it keeps its slot
  // rather than being the first thing the overflow swallows. The FAB's spacer
  // counts as a slot because it takes the same width.
  const cells = BOTTOM_SLOTS - (search ? 1 : 0);
  const overflowing = rest.length + 1 > cells;
  const keep = overflowing ? cells - 2 : rest.length; // leave room for Scan (+ More)
  const primary = rest.slice(0, keep);
  const overflow = rest.slice(keep);
  const overflowDotted = overflow.some((i) => dotted?.has(i.path));

  return (
    <>
      {moreOpen && (
        <div
          className="animate-fade-in fixed inset-0 z-30 bg-scrim/35 md:hidden"
          onClick={() => setMoreOpen(false)}
        >
          <div
            className="animate-rise-in absolute inset-x-0 bottom-0 rounded-t-(--radius-xl)
              bg-surface-container px-3 pt-3 pb-[max(5.5rem,calc(env(safe-area-inset-bottom)+5rem))]"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mx-auto mb-3 h-1 w-9 rounded-full bg-outline-variant" aria-hidden />
            <NavList items={overflow} dotted={dotted} onNavigate={() => setMoreOpen(false)} />
          </div>
        </div>
      )}
      <nav
        aria-label="Main"
        className="fixed inset-x-0 bottom-0 z-40 flex items-stretch gap-1 bg-surface-container
          px-1 pt-2 pb-[max(0.75rem,env(safe-area-inset-bottom))]
          shadow-[0_-1px_0_var(--color-outline-variant)] md:hidden"
      >
        {search && (
          <>
            {/* reserves the circle's footprint so no label sits under it */}
            <div className="w-[4.5rem] shrink-0" aria-hidden />
            <SearchFab item={search} />
          </>
        )}
        {primary.map((item) => (
          <BottomNavLink key={item.path} item={item} dotted={dotted} />
        ))}
        <BottomAction label={s("Scan")} icon={Icons.scan} onClick={onScan} />
        {overflowing && (
          <BottomAction
            label="More"
            icon={Icons.more}
            dot={overflowDotted}
            active={moreOpen}
            onClick={() => setMoreOpen((v) => !v)}
          />
        )}
      </nav>
    </>
  );
}

/** The docked search FAB: brand orange, breaking out of the top of the bar.
 *
 *  Wears `on-primary` (deep umber) rather than white — that is the token that
 *  actually has contrast on this orange, and it keeps the button reading as the
 *  same brand action as every other primary control. */
function SearchFab({ item }: { item: NavItem }) {
  const s = useSillyLabel();
  return (
    <NavLink
      to={item.path}
      aria-label={s(item.label)}
      title={s(item.label)}
      className="absolute -top-5 left-3 grid h-14 w-14 place-items-center rounded-full
        bg-primary text-on-primary shadow-(--shadow-e3)
        transition-transform duration-300 ease-(--ease-spring)
        hover:-translate-y-0.5 hover:scale-105 active:translate-y-0 active:scale-95"
    >
      {({ isActive }) => (
        <>
          {/* a ring instead of a fill change: the FAB must stay brand orange */}
          {isActive && (
            <span
              aria-hidden
              className="absolute inset-0 rounded-full ring-3 ring-on-surface/25"
            />
          )}
          <span className="scale-150" aria-hidden>
            {Icons.search}
          </span>
        </>
      )}
    </NavLink>
  );
}

/** A bottom-bar slot that runs an action instead of navigating. */
function BottomAction({
  label,
  icon,
  onClick,
  active = false,
  dot = false,
}: {
  label: string;
  icon: ReactNode;
  onClick: () => void;
  active?: boolean;
  dot?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className="group flex min-w-0 flex-1 flex-col items-center gap-1 text-[11px] font-medium"
    >
      <span
        className={`relative grid h-8 w-16 place-items-center rounded-full transition-all
          duration-300 ease-(--ease-spring)
          ${
            active
              ? "scale-110 bg-secondary-container text-on-secondary-container"
              : "text-on-surface-variant group-hover:scale-105 group-hover:bg-on-surface/5"
          }`}
      >
        {icon}
        {dot && (
          <span aria-label="new" className="absolute top-1 right-4 h-2 w-2 rounded-full bg-error" />
        )}
      </span>
      <span className="w-full truncate text-center text-on-surface-variant">{label}</span>
    </button>
  );
}

/** Top-bar link to the Settings page (palette picker lives there now). */
function SettingsButton() {
  return (
    <NavLink
      to="/settings"
      aria-label="Settings"
      title="Settings"
      className={({ isActive }) =>
        `state-layer grid h-10 w-10 place-items-center rounded-full
         ${isActive ? "bg-secondary-container text-on-secondary-container" : "text-on-surface-variant"}`
      }
    >
      {Icons.gear}
    </NavLink>
  );
}

function HealthChip() {
  const { data } = useHealth();
  // only the healthy labels have dictionary entries — "Sync stale" and
  // "Odoo auth failing!" pass through s() unchanged, staying serious
  const s = useSillyLabel();
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
      <StatusDot ok={ok} warn={!ok && !data.odoo_auth_failed} label={s(label)} />
    </span>
  );
}


export function AppShell({ title, children }: { title: string; children: ReactNode }) {
  const { user, roles, isDepartments, signOut } = useAuth();
  const navigate = useNavigate();
  const rootRef = useRef<HTMLDivElement>(null);
  const s = useSillyLabel();

  const items = navForRoles(roles, { departments: isDepartments });
  // a red dot on Suggested items while the floor team is waiting on someone
  const canReview = roles.has("shoppe_floor") || roles.has("admin");
  const openAsks = useFloorRequests({ enabled: canReview });
  const dotted = new Set(
    canReview && (openAsks.data?.length ?? 0) > 0 ? ["/suggested-items"] : [],
  );
  // the one route whose name depends on the review zone rather than the role
  const shownTitle = isDepartments && title === "My centers" ? "My departments" : title;
  // Every role gets the bottom bar now; the fifth slot becomes "More" when a
  // role has more destinations than fit (it used to be a hamburger drawer,
  // which hid the whole menu from exactly the busiest roles).
  const [scanOpen, setScanOpen] = useState(false);

  const logout = () => {
    signOut();
    navigate("/login");
  };

  return (
    <div ref={rootRef} className="min-h-dvh md:grid md:grid-cols-[248px_1fr]">
      {/* desktop: standard navigation drawer */}
      <aside className="hidden bg-surface-container-low px-3 py-6 md:block">
        <Brand />
        <NavList items={items} dotted={dotted} />
      </aside>

      {/* Mobile top app bar. The brand lock-up is gone from here (Noah,
          2026-08-16) — on a 375px screen it spent a third of the bar telling
          you which app you already opened. The page title takes its place.
          Standalone (home-screen) mode draws under the status bar, so the top
          edge honors the safe-area inset. */}
      <div className="sticky top-0 z-30 flex items-center justify-between gap-2 bg-surface-container-low
        px-4 pb-3 pt-[max(0.75rem,env(safe-area-inset-top))] md:hidden">
        <div className="min-w-0" />
        <div className="flex shrink-0 items-center gap-1">
          <InboxMenu />
          <SettingsButton />
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
        </div>
      </div>

      <div className="min-w-0">
        {/* desktop top app bar */}
        <header className="sticky top-0 z-20 hidden items-center justify-between bg-surface/85
          px-8 py-3.5 backdrop-blur md:flex">
          <div className="title-l text-on-surface">{s(shownTitle)}</div>
          <div className="flex items-center gap-4">
            <HealthChip />
            <div className="flex items-center gap-1">
              <ScanButton />
              <InboxMenu />
              <SettingsButton />
            </div>
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
                {s("Sign out")}
              </button>
            </div>
          </div>
        </header>
        {/* pb-28: the bottom bar is fixed, so the last row of every page
            would otherwise sit under it */}
        <main className="stagger-children mx-auto w-full max-w-6xl px-4 py-6 pb-28 md:px-8 md:py-8 md:pb-8">
          {children}
        </main>
      </div>

      <BottomNav items={items} dotted={dotted} onScan={() => setScanOpen(true)} />
      {scanOpen && <ScanSheetLazy onClose={() => setScanOpen(false)} />}

      {/* a half-built transfer request follows you between pages */}
      <TransferDraftBubble />
    </div>
  );
}
