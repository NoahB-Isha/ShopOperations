/* The half-built transfer request, following you around.

   A draft survives navigation (it lives in transferDraft.ts), but until now
   nothing said so — you could add three items from the restock list, walk to
   another page, and have no way back except remembering the route. This is
   that way back: a small floating pill with the item count that opens the
   request page.

   It hides itself on the request page (you're already there) and can be
   dismissed for the session — dismissing only hides the pill, never the
   draft, and adding another item brings it back. */
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useDraftLines } from "../transferDraft";
import { fmtQty } from "../pages/shared/OpsBits";

const NEW_REQUEST_PATH = "/transfer-requests/new";

export function TransferDraftBubble() {
  const lines = useDraftLines();
  const location = useLocation();
  const navigate = useNavigate();
  const { roles } = useAuth();
  const canRequest = roles.has("shoppe_floor") || roles.has("admin");

  // dismissal is per draft-size: put something else on the list and the pill
  // comes back rather than staying hidden on stale state
  const [dismissedAt, setDismissedAt] = useState<number | null>(null);
  useEffect(() => {
    if (dismissedAt !== null && lines.length !== dismissedAt) setDismissedAt(null);
  }, [lines.length, dismissedAt]);

  if (!canRequest || lines.length === 0) return null;
  if (location.pathname === NEW_REQUEST_PATH) return null;
  if (dismissedAt !== null) return null;

  const units = lines.reduce((sum, l) => sum + l.qty, 0);

  return (
    // on phones it rides above the snackbar row (which itself clears the
    // bottom navigation bar) so a toast and the pill never sit on each other
    <div
      className="fixed right-4 bottom-[calc(10rem+env(safe-area-inset-bottom))]
        z-40 flex items-center gap-1 md:bottom-6"
    >
      <button
        onClick={() => navigate(NEW_REQUEST_PATH)}
        className="animate-pop-in state-layer flex items-center gap-2.5 rounded-full
          bg-primary-container py-2.5 pl-4 pr-3.5 text-on-primary-container
          shadow-(--shadow-e2) transition-transform duration-200 ease-(--ease-spring)
          hover:scale-[1.03]"
      >
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden>
          <path
            d="M2.5 4.5h2l1.7 7.2a1 1 0 0 0 1 .8h5.4a1 1 0 0 0 1-.77l1.1-4.23H5.2"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <circle cx="7.5" cy="15" r="1" fill="currentColor" />
          <circle cx="12.5" cy="15" r="1" fill="currentColor" />
        </svg>
        <span className="text-left text-[13px] leading-tight font-semibold">
          Transfer request
          <span className="block text-[11.5px] font-medium opacity-80">
            {lines.length} item{lines.length === 1 ? "" : "s"} · {fmtQty(units)} units
          </span>
        </span>
      </button>
      <button
        aria-label="Hide the transfer request draft"
        title="Hide — the draft is kept"
        onClick={() => setDismissedAt(lines.length)}
        className="state-layer grid h-7 w-7 place-items-center rounded-full
          bg-surface-container-high text-on-surface-variant shadow-(--shadow-e1)"
      >
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden>
          <path d="M2.5 2.5l7 7M9.5 2.5l-7 7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
      </button>
    </div>
  );
}
