import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { LoginPage } from "./auth/LoginPage";
import { Spinner, ToastProvider } from "./design";
import { homeForRoles } from "./nav";
import { AppShell } from "./shell/AppShell";
import { CatalogPage } from "./pages/CatalogPage";
import { MyCentersPage } from "./pages/MyCentersPage";
import { PaletteLabPage } from "./pages/PaletteLabPage";
import { SettingsPage } from "./pages/SettingsPage";
import { StyleguidePage } from "./pages/StyleguidePage";
import { AuditPage } from "./pages/admin/AuditPage";
import { CentersPage } from "./pages/admin/CentersPage";
import { StatusPage } from "./pages/admin/StatusPage";
import { UsersPage } from "./pages/admin/UsersPage";
import { CoordinatorListsPage } from "./pages/orders/CoordinatorListsPage";
import { OrderDetailPage } from "./pages/orders/OrderDetailPage";
import { OrderHistoryPage } from "./pages/orders/OrderHistoryPage";
import { OrderListEditorPage } from "./pages/orders/OrderListEditorPage";
import { OrderListsPage } from "./pages/orders/OrderListsPage";
import { PendingOrdersPage } from "./pages/orders/PendingOrdersPage";
import { PlaceOrderPage } from "./pages/orders/PlaceOrderPage";
import { PurchaseOrderPage } from "./pages/purchasing/PurchaseOrderPage";
import { PurchasingPage } from "./pages/purchasing/PurchasingPage";
import { VendorsPage } from "./pages/purchasing/VendorsPage";
import { ReportsPage } from "./pages/reports/ReportsPage";
import { TimeMachinePage } from "./pages/reports/TimeMachinePage";
import { OutOfStockPage } from "./pages/restock/OutOfStockPage";
import { RestockPage } from "./pages/restock/RestockPage";
import { ComingSoonPage } from "./pages/transfers/ComingSoonPage";
import { NewTransferRequestPage } from "./pages/transfers/NewTransferRequestPage";
import { TransferRequestDetailPage } from "./pages/transfers/TransferRequestDetailPage";
import { TransferRequestsPage } from "./pages/transfers/TransferRequestsPage";
import { AdjustmentsPage } from "./pages/warehouse/AdjustmentsPage";
import { IncomingPage } from "./pages/warehouse/IncomingPage";

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
                <Protected title="All SKUs"><CatalogPage /></Protected>
              } />
              <Route path="/settings" element={
                <Protected title="Settings"><SettingsPage /></Protected>
              } />
              {/* design pages: out of the nav, linked from admin Settings */}
              <Route path="/styleguide" element={
                <Protected title="Styleguide" roles={["admin"]}><StyleguidePage /></Protected>
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
                <Protected title="Catalogs" roles={["admin"]}><OrderListsPage /></Protected>
              } />
              <Route path="/orders/:id" element={
                <Protected title="Catalog" roles={["admin"]}><OrderListEditorPage /></Protected>
              } />
              <Route path="/purchasing" element={
                <Protected title="Purchasing" roles={["admin"]}><PurchasingPage /></Protected>
              } />
              <Route path="/purchasing/vendors" element={
                <Protected title="Vendors" roles={["admin"]}><VendorsPage /></Protected>
              } />
              <Route path="/purchasing/:id" element={
                <Protected title="Purchase order" roles={["admin"]}><PurchaseOrderPage /></Protected>
              } />
              <Route path="/reports" element={
                <Protected title="Sales" roles={["admin"]}><ReportsPage /></Protected>
              } />
              <Route path="/time-machine" element={
                <Protected title="Time machine" roles={["admin", "warehouse"]}>
                  <TimeMachinePage />
                </Protected>
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
              <Route path="/my-order-lists" element={
                <Protected title="Catalogs" roles={["zone_coordinator", "dept_liaison"]}>
                  <CoordinatorListsPage />
                </Protected>
              } />

              {/* orderers */}
              <Route path="/place-order" element={
                <Protected title="Place an order" roles={["center_orderer", "dept_orderer", "zone_coordinator", "dept_liaison"]}>
                  <PlaceOrderPage />
                </Protected>
              } />
              <Route path="/order-history" element={
                <Protected
                  title="Order history"
                  roles={["center_orderer", "dept_orderer", "zone_coordinator", "dept_liaison"]}
                >
                  <OrderHistoryPage />
                </Protected>
              } />
              <Route path="/order/:id" element={
                <Protected
                  title="Order"
                  roles={["center_orderer", "dept_orderer", "zone_coordinator", "dept_liaison"]}
                >
                  <OrderDetailPage />
                </Protected>
              } />

              {/* warehouse */}
              <Route path="/incoming" element={
                <Protected title="Incoming" roles={["warehouse"]}>
                  <IncomingPage />
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

              {/* shoppe floor (+ rotating) + warehouse shared flow */}
              <Route path="/restock" element={
                <Protected title="Restock" roles={["shoppe_floor", "floor_rotating", "warehouse"]}>
                  <RestockPage />
                </Protected>
              } />
              <Route path="/transfer-requests" element={
                <Protected title="Transfer requests" roles={["shoppe_floor", "floor_rotating", "warehouse"]}>
                  <TransferRequestsPage />
                </Protected>
              } />
              {/* creating requests is shoppe_floor only — rotating can't */}
              <Route path="/transfer-requests/new" element={
                <Protected title="Request stock" roles={["shoppe_floor"]}>
                  <NewTransferRequestPage />
                </Protected>
              } />
              <Route path="/coming-soon" element={
                <Protected title="Coming soon" roles={["shoppe_floor", "floor_rotating", "warehouse"]}>
                  <ComingSoonPage />
                </Protected>
              } />
              <Route path="/out-of-stock" element={
                <Protected title="Out of stock" roles={["shoppe_floor", "floor_rotating", "warehouse"]}>
                  <OutOfStockPage />
                </Protected>
              } />
              <Route path="/transfer-requests/:id" element={
                <Protected title="Transfer request" roles={["shoppe_floor", "floor_rotating", "warehouse"]}>
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
