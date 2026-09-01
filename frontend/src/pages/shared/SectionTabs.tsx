/* URL-driven section tabs — the TransfersPage pattern, shared: two sibling
   routes presented as ONE nav destination. Each page renders the same bar and
   the active tab lives in the URL, so deep links, the bottom bar, and
   back/forward all agree. With fewer than two visible tabs the bar renders
   nothing (a lone tab is just the page). */
import { useNavigate } from "react-router-dom";

export interface SectionTab {
  path: string;
  label: string;
}

export function SectionTabs({ tabs, active }: { tabs: SectionTab[]; active: string }) {
  const navigate = useNavigate();
  if (tabs.length < 2) return null;
  return (
    <div
      className="mb-5 grid gap-1.5 rounded-full bg-surface-container p-1.5"
      style={{ gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` }}
    >
      {tabs.map((t) => (
        <button
          key={t.path}
          onClick={() => navigate(t.path, { replace: true })}
          aria-current={t.path === active}
          className={`state-layer flex items-center justify-center gap-2 rounded-full px-4 py-2.5
            text-sm font-semibold transition-colors ${
              t.path === active ? "bg-primary text-on-primary" : "text-on-surface-variant"
            }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
