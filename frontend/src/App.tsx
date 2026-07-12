import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { LoginPage } from "./auth/LoginPage";
import { Spinner, ToastProvider } from "./design";
import { homeForRoles } from "./nav";
import { AppShell } from "./shell/AppShell";
import { CatalogPage } from "./pages/CatalogPage";
import { ComingSoon } from "./pages/ComingSoon";
import { MyCentersPage } from "./pages/MyCentersPage";
import { PaletteLabPage } from "./pages/PaletteLabPage";
import { StyleguidePage } from "./pages/StyleguidePage";
import { AuditPage } from "./pages/admin/AuditPage";
import { CentersPage } from "./pages/admin/CentersPage";
import { StatusPage } from "./pages/admin/StatusPage";
import { UsersPage } from "./pages/admin/UsersPage";
import { OrderListEditorPage } from "./pages/orders/OrderListEditorPage";
import { OrderListsPage } from "./pages/orders/OrderListsPage";
import { PendingOrdersPage } from "./pages/orders/PendingOrdersPage";
import { RestockPage } from "./pages/restock/RestockPage";
import { NewTransferRequestPage } from "./pages/transfers/NewTransferRequestPage";
import { TransferRequestDetailPage } from "./pages/transfers/TransferRequestDetailPage";
import { TransferRequestsPage } from "./pages/transfers/TransferRequestsPage";
import { AdjustmentsPage } from "./pages/warehouse/AdjustmentsPage";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

function Protected({
  title,
  roles: allowed,
  children,
}: {
  title: string;
  roles?: string[];
  children: ReactNode;
}) {
  const { user, loading, roles } = useAuth();
  const location = useLocation();
  if (loading) {
    return (
      <div className="grid min-h-dvh place-items-center">
        <Spinner size={24} />
      </div>
    );
  }
  if (!user) return <Navigate to="/login" state={{ from: location }} replace />;
  if (allowed && !allowed.some((r) => roles.has(r)) && !roles.has("admin")) {
    return <Navigate to={homeForRoles(roles)} replace />;
  }
  return <AppShell title={title}>{children}</AppShell>;
}

function Home() {
  const { user, loading, roles } = useAuth();
  if (loading) {
    return (
      <div className="grid min-h-dvh place-items-center">
        <Spinner size={24} />
      </div>
    );
  }
  return <Navigate to={user ? homeForRoles(roles) : "/login"} replace />;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <AuthProvider>
          <BrowserRouter>
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/login" element={<LoginPage />} />

              <Route path="/catalog" element={
                <Protected title="Catalog"><CatalogPage /></Protected>
              } />
              <Route path="/styleguide" element={
                <Protected title="Styleguide"><StyleguidePage /></Protected>
              } />
              <Route path="/palette-lab" element={
                <Protected title="Palette lab" roles={["admin"]}><PaletteLabPage /></Protected>
              } />

              {/* admin */}
              <Route path="/status" element={
                <Protected title="Status" roles={["admin"]}><StatusPage /></Protected>
              } />
              <Route path="/users" element={
                <Protected title="Users" roles={["admin"]}><UsersPage /></Protected>
              } />
              <Route path="/centers" element={
                <Protected title="Centers" roles={["admin"]}><CentersPage /></Protected>
              } />
              <Route path="/audit" element={
                <Protected title="Audit log" roles={["admin"]}><AuditPage /></Protected>
              } />
              <Route path="/orders" element={
                <Protected title="Order lists" roles={["admin"]}><OrderListsPage /></Protected>
              } />
              <Route path="/orders/:id" element={
                <Protected title="Order list" roles={["admin"]}><OrderListEditorPage /></Protected>
              } />

              {/* coordinator / liaison */}
              <Route path="/my-centers" element={
                <Protected title="My centers" roles={["zone_coordinator", "dept_liaison"]}>
                  <MyCentersPage />
                </Protected>
              } />
              <Route path="/pending-orders" element={
                <Protected title="Pending orders" roles={["zone_coordinator", "dept_liaison"]}>
                  <PendingOrdersPage />
                </Protected>
              } />

              {/* orderers */}
              <Route path="/place-order" element={
                <Protected title="Place an order" roles={["center_orderer", "dept_orderer"]}>
                  <ComingSoon what="Ordering" phase="Phase 3" />
                </Protected>
              } />
              <Route path="/order-history" element={
                <Protected
                  title="Order history"
                  roles={["center_orderer", "dept_orderer", "zone_coordinator", "dept_liaison"]}
                >
                  <ComingSoon what="Order history" phase="Phase 3" />
                </Protected>
              } />

              {/* warehouse */}
              <Route path="/incoming" element={
                <Protected title="Incoming" roles={["warehouse"]}>
                  <ComingSoon what="Incoming shipments" phase="Phase 2b" />
                </Protected>
              } />
              <Route path="/transfers" element={
                <Protected title="Transfers" roles={["warehouse"]}>
                  <TransferRequestsPage />
                </Protected>
              } />
              <Route path="/adjustments" element={
                <Protected title="Adjustments" roles={["warehouse"]}>
                  <AdjustmentsPage />
                </Protected>
              } />

              {/* shoppe floor + warehouse shared flow */}
              <Route path="/restock" element={
                <Protected title="Restock" roles={["shoppe_floor", "warehouse"]}>
                  <RestockPage />
                </Protected>
              } />
              <Route path="/transfer-requests" element={
                <Protected title="Transfer requests" roles={["shoppe_floor", "warehouse"]}>
                  <TransferRequestsPage />
                </Protected>
              } />
              <Route path="/transfer-requests/new" element={
                <Protected title="Request stock" roles={["shoppe_floor"]}>
                  <NewTransferRequestPage />
                </Protected>
              } />
              <Route path="/transfer-requests/:id" element={
                <Protected title="Transfer request" roles={["shoppe_floor", "warehouse"]}>
                  <TransferRequestDetailPage />
                </Protected>
              } />

              <Route path="*" element={<Home />} />
            </Routes>
          </BrowserRouter>
        </AuthProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}
