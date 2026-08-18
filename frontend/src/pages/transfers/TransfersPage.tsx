/* Transfers — one page, two halves, phone-first like the restock list:

     New transfer  (the default — building one is the common act)
     Past transfers (the live board of everything already placed)

   The tab lives in the URL (/transfer-requests/new · /past) so the floating
   draft bubble knows when you've arrived at the draft and can burst into it,
   and so a prefill link ("duplicate", "new transfer from these items") lands
   on the right half.

   Roles that can't create requests — warehouse, rotating floor volunteers —
   get the board with no tabs at all; there's nothing for them to switch to. */
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import { PageHeader } from "../../design";
import { NewTransferPanel } from "./NewTransferRequestPage";
import { PastTransfersPanel } from "./TransferRequestsPage";

type Tab = "new" | "past";

export function TransfersPage() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { roles } = useAuth();
  // creating is shoppe_floor's (and admin's) — the route already enforces it
  const canCreate = roles.has("shoppe_floor") || roles.has("admin");

  const tab: Tab = !canCreate || pathname.endsWith("/past") ? "past" : "new";
  const go = (next: Tab) =>
    navigate(next === "new" ? "/transfer-requests/new" : "/transfer-requests/past", {
      replace: true,
    });

  return (
    <div className={tab === "new" ? "mx-auto max-w-2xl" : undefined}>
      <PageHeader
        title="Transfers"
        subtitle={
          tab === "new"
            ? "From the Blue Warehouse to the Shoppe floor — sending it renders the draft transfer in Odoo immediately."
            : "Floor asks, the warehouse pulls it in Odoo, and it closes when the pallet reaches the floor — live board, no refreshing."
        }
      />

      {canCreate && (
        <div className="mb-5 grid grid-cols-2 gap-1.5 rounded-full bg-surface-container p-1.5">
          {(
            [
              ["new", "New transfer"],
              ["past", "Past transfers"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => go(key)}
              aria-current={tab === key}
              className={`state-layer flex items-center justify-center gap-2 rounded-full px-4 py-2.5
                text-sm font-semibold transition-colors ${
                  tab === key ? "bg-primary text-on-primary" : "text-on-surface-variant"
                }`}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      {tab === "new" ? <NewTransferPanel /> : <PastTransfersPanel />}
    </div>
  );
}
