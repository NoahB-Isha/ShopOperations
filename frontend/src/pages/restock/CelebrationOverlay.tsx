/* The full-screen restock celebration — an aisle finished, or the whole list.
   Decoration with the house manners:

   - pointer-events-none all the way down: the floor keeps ticking, every tap
     lands on the list underneath. Nothing about this overlay is blocking.
   - the page dismisses it on a TIMER (CHEER_MS), never animationend — a
     backgrounded tab never delivers that event and `both` fill would then
     outrank every later paint.
   - reduced motion gets the announcement without the theatrics: static veil
     and headline, same timer.
   - `key` restarts the CSS animations when a new cheer replaces a running
     one (finishing two aisles in quick succession). */

export interface Cheer {
  key: number;
  title: string;
  subtitle?: string;
}

/** Keep equal to the --animate-cheer-* durations in tokens.css. */
export const CHEER_MS = 1800;

export function CelebrationOverlay({ cheer }: { cheer: Cheer | null }) {
  if (!cheer) return null;
  return (
    <div
      key={cheer.key}
      aria-live="polite"
      className="pointer-events-none fixed inset-0 z-[70] grid place-items-center"
    >
      <div className="absolute inset-0 animate-cheer-veil bg-surface/75 backdrop-blur-[3px] motion-reduce:animate-none" />
      <div className="relative animate-cheer-pop px-6 text-center motion-reduce:animate-none">
        <div className="display-l text-primary">{cheer.title}</div>
        {cheer.subtitle && (
          <div className="mt-2 text-[15px] font-medium text-on-surface-variant">
            {cheer.subtitle}
          </div>
        )}
      </div>
    </div>
  );
}
