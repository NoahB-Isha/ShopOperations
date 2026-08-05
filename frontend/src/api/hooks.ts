import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiDownload, apiUpload } from "./client";
import type {
  AdjustmentOut,
  AuditRow,
  BlacklistSweepOut,
  CanaryResult,
  CenterOut,
  FacetsOut,
  HealthOut,
  ImportReportOut,
  ProductListOut,
  ProductOut,
  StockHistoryOut,
  OrderListOut,
  OrderListSummaryOut,
  RestockOut,
  StatusOut,
  TagOut,
  TransferRequestOut,
  TransferSummaryOut,
  UserOut,
  ZoneOut,
  AnalogSuggestionOut,
  AnalogyOut,
  OrderingEmailSettings,
  OrderingRulesOut,
  OrderTimelineOut,
  PurchaseOrderDetailOut,
  PurchaseOrderLineOut,
  PurchaseOrderSummaryOut,
  VendorOut,
  VendorSuggestionsOut,
  CatalogImportResultOut,
  ProductListMetaOut,
  VendorProductOut,
  AvailabilityItemOut,
  AvailabilityMetaOut,
  BreakdownOut,
  InboxOut,
  NoticeOut,
  NarrativeOut,
  QaOut,
  SalesOverviewOut,
  TimeMachineBoundsOut,
  TimeMachineViewOut,
} from "./types";

// ------------------------------------------------------------------ catalog
export interface ProductQuery {
  search: string;
  category: string;
  tag: string;
  page: number;
  sort: string;
  dir: "asc" | "desc";
  include_inactive?: boolean;
  blacklisted?: boolean;
}

export function useProducts(q: ProductQuery) {
  return useQuery({
    queryKey: ["products", q],
    queryFn: () => api<ProductListOut>("/products", { params: { ...q, page_size: 50 } }),
    placeholderData: keepPreviousData,
    staleTime: 15_000,
  });
}

export function useFacets() {
  return useQuery({
    queryKey: ["facets"],
    queryFn: () => api<FacetsOut>("/products/facets"),
    staleTime: 5 * 60_000,
  });
}

export function useProductStockHistory(productId: number | null, days: number) {
  return useQuery({
    queryKey: ["product-stock-history", productId, days],
    enabled: productId !== null,
    queryFn: () =>
      api<StockHistoryOut>(`/products/${productId}/stock-history`, { params: { days } }),
    staleTime: 60_000,
  });
}

export function useSaveTags() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, tags }: { id: number; tags: TagOut[] }) =>
      api<ProductOut>(`/products/${id}/tags`, { method: "PUT", body: { tags } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["products"] }),
  });
}

export function usePatchProduct() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: number } & Record<string, unknown>) =>
      api<ProductOut>(`/products/${id}`, { method: "PATCH", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["products"] }),
  });
}

export function useCreateManualProduct() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; global_sku?: string; category?: string; retail_price?: number }) =>
      api<ProductOut>("/products", { method: "POST", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["products"] }),
  });
}

export function useBlacklistSweep() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (apply: boolean) =>
      api<BlacklistSweepOut>("/products/blacklist/sweep", { method: "POST", body: { apply } }),
    onSuccess: (_out, apply) => {
      if (apply) qc.invalidateQueries({ queryKey: ["products"] });
    },
  });
}

// ------------------------------------------------------------------ centers
export function useZones() {
  return useQuery({ queryKey: ["zones"], queryFn: () => api<ZoneOut[]>("/zones") });
}

export function useCenters(params: { zone_id?: number; q?: string } = {}) {
  return useQuery({
    queryKey: ["centers", params],
    queryFn: () => api<CenterOut[]>("/centers", { params }),
    placeholderData: keepPreviousData,
  });
}

// ------------------------------------------------------------------ admin
export function useUsers() {
  return useQuery({ queryKey: ["users"], queryFn: () => api<UserOut[]>("/admin/users") });
}

export function useInviteUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: unknown) => api<UserOut>("/admin/users", { method: "POST", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });
}

export function useUpdateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: number } & Record<string, unknown>) =>
      api<UserOut>(`/admin/users/${id}`, { method: "PATCH", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });
}

export function useHealth() {
  return useQuery({
    // /health is the public liveness probe (status + db only); the sync/staleness
    // and Odoo-posture payload lives behind a session at /health/detail.
    queryKey: ["health"],
    queryFn: () => api<HealthOut>("/health/detail"),
    refetchInterval: 60_000,
  });
}

export function useAdminStatus() {
  return useQuery({
    queryKey: ["admin-status"],
    queryFn: () => api<StatusOut>("/admin/status"),
    refetchInterval: 30_000,
  });
}

export function useTriggerSync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (domain: string) => api<unknown>(`/admin/sync/${domain}`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-status"] });
      qc.invalidateQueries({ queryKey: ["health"] });
      qc.invalidateQueries({ queryKey: ["products"] });
    },
  });
}

export function useSetFlag() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ key, enabled }: { key: string; enabled: boolean }) =>
      api<unknown>(`/admin/flags/${key}`, { method: "PUT", body: { enabled } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-status"] }),
  });
}

export function useAudit() {
  return useQuery({
    queryKey: ["audit"],
    queryFn: () => api<AuditRow[]>("/admin/audit", { params: { limit: 100 } }),
  });
}

export function useCanary() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (dry_run: boolean) =>
      api<CanaryResult>("/admin/odoo/canary/create-internal-transfer", {
        method: "POST",
        body: { dry_run },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["audit"] }),
  });
}

export function useImportCoordinators() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (apply: boolean) =>
      api<ImportReportOut>("/admin/import/coordinators", { method: "POST", body: { apply } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["centers"] });
      qc.invalidateQueries({ queryKey: ["zones"] });
      qc.invalidateQueries({ queryKey: ["users"] });
    },
  });
}

// -------------------------------------------------------------- order lists

export function useOrderLists(includeArchived = false) {
  return useQuery({
    queryKey: ["order-lists", includeArchived],
    queryFn: () =>
      api<OrderListSummaryOut[]>("/order-lists", {
        params: { include_archived: includeArchived },
      }),
  });
}

export function useOrderList(id: number | null) {
  return useQuery({
    queryKey: ["order-list", id],
    queryFn: () => api<OrderListOut>(`/order-lists/${id}`),
    enabled: id !== null,
  });
}

function useOrderListMutation<TArgs>(fn: (args: TArgs) => Promise<unknown>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["order-lists"] });
      qc.invalidateQueries({ queryKey: ["order-list"] });
    },
  });
}

export function useCreateOrderList() {
  return useOrderListMutation((body: { name: string; notes?: string }) =>
    api<OrderListOut>("/order-lists", { method: "POST", body }),
  );
}

export function usePatchOrderList() {
  return useOrderListMutation(
    ({ id, ...body }: { id: number; name?: string; notes?: string; is_archived?: boolean }) =>
      api<OrderListOut>(`/order-lists/${id}`, { method: "PATCH", body }),
  );
}

export function usePutOrderListLines() {
  return useOrderListMutation(({ id, product_ids }: { id: number; product_ids: number[] }) =>
    api<OrderListOut>(`/order-lists/${id}/lines`, { method: "PUT", body: { product_ids } }),
  );
}

export function useCloneOrderList() {
  return useOrderListMutation((id: number) =>
    api<OrderListOut>(`/order-lists/${id}/clone`, { method: "POST" }),
  );
}

export function useDeleteOrderList() {
  return useOrderListMutation((id: number) =>
    api<void>(`/order-lists/${id}`, { method: "DELETE" }),
  );
}

export function useSetOrderListZones() {
  return useOrderListMutation(({ id, zone_ids }: { id: number; zone_ids: number[] }) =>
    api<OrderListOut>(`/order-lists/${id}/zones`, { method: "PUT", body: { zone_ids } }),
  );
}

export function useSetOrderListCenters() {
  return useOrderListMutation(({ id, center_ids }: { id: number; center_ids: number[] }) =>
    api<OrderListOut>(`/order-lists/${id}/centers`, { method: "PUT", body: { center_ids } }),
  );
}

// ---------------------------------------------------------------- transfers
// the board polls like a food-POS screen: state changes appear on their own
const BOARD_POLL_MS = 4000;

export function useTransferRequests(status = "") {
  return useQuery({
    queryKey: ["transfer-requests", status],
    queryFn: () => api<TransferSummaryOut[]>("/transfer-requests", { params: { status } }),
    refetchInterval: BOARD_POLL_MS,
  });
}

export function useTransferRequest(id: number | null) {
  return useQuery({
    queryKey: ["transfer-request", id],
    queryFn: () => api<TransferRequestOut>(`/transfer-requests/${id}`),
    enabled: id !== null,
    refetchInterval: BOARD_POLL_MS,
  });
}

function useTransferMutation<TArgs>(fn: (args: TArgs) => Promise<unknown>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["transfer-requests"] });
      qc.invalidateQueries({ queryKey: ["transfer-request"] });
      qc.invalidateQueries({ queryKey: ["adjustments"] });
    },
  });
}

export function useCreateTransferRequest() {
  return useTransferMutation(
    (body: { notes?: string; lines: { product_id: number; qty: number }[] }) =>
      api<TransferRequestOut>("/transfer-requests", { method: "POST", body }),
  );
}

export function useReplaceTransferLines() {
  return useTransferMutation(
    ({ id, lines, note }: { id: number; lines: { product_id: number; qty: number }[]; note?: string }) =>
      api<TransferRequestOut>(`/transfer-requests/${id}/lines`, {
        method: "PUT",
        body: { lines, note },
      }),
  );
}

export function useTransferAction(
  action: "ack" | "sent" | "prepare-count" | "mark-done" | "cancel" | "note",
) {
  return useTransferMutation(({ id, note }: { id: number; note?: string }) =>
    api<TransferRequestOut>(`/transfer-requests/${id}/${action}`, {
      method: "POST",
      body: { note: note ?? "" },
    }),
  );
}

export function useAdjustments(status = "open") {
  return useQuery({
    queryKey: ["adjustments", status],
    queryFn: () => api<AdjustmentOut[]>("/adjustments", { params: { status } }),
    refetchInterval: 15_000,
  });
}

export function useResolveAdjustment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action, note }: { id: number; action: "resolved" | "dismissed"; note?: string }) =>
      api<AdjustmentOut>(`/adjustments/${id}/resolve`, {
        method: "POST",
        body: { action, note: note ?? "" },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["adjustments"] }),
  });
}

// ------------------------------------------------------------------ restock
export function useRestock() {
  return useQuery({
    queryKey: ["restock"],
    queryFn: () => api<RestockOut>("/restock"),
    refetchInterval: 5 * 60_000, // refreshed on each sync; keep the phone view current
  });
}

export function useCheckRestock() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { list: "floor"; line_id: number; checked: boolean } | { list: "back"; product_id: number; checked: boolean }) =>
      args.list === "floor"
        ? api(`/restock/floor/${args.line_id}/check`, { method: "POST", body: { checked: args.checked } })
        : api(`/restock/back/${args.product_id}/check`, { method: "POST", body: { checked: args.checked } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["restock"] }),
  });
}

// ------------------------------------------------------------ center orders
import type {
  CenterOrderOut,
  CenterOrderSummaryOut,
  OrderCatalogOut,
  OrderContextCenter,
  ReasonPreviewOut,
} from "./types";

export function useOrderContext() {
  return useQuery({
    queryKey: ["order-context"],
    queryFn: () => api<OrderContextCenter[]>("/center-orders/context"),
    staleTime: 5 * 60_000,
  });
}

export function useOrderCatalog(centerId: number | null) {
  return useQuery({
    queryKey: ["order-catalog", centerId],
    queryFn: () => api<OrderCatalogOut>("/center-orders/catalog", { params: { center_id: centerId ?? undefined } }),
    enabled: centerId !== null,
    staleTime: 60_000,
  });
}

export function usePreviewReasonability() {
  return useMutation({
    mutationFn: (body: { center_id: number; lines: { product_id: number; qty: number }[] }) =>
      api<ReasonPreviewOut>("/center-orders/preview", { method: "POST", body }),
  });
}

export function useCenterOrders(params: { status?: string; center_id?: number; mine?: boolean } = {}) {
  return useQuery({
    queryKey: ["center-orders", params],
    queryFn: () => api<CenterOrderSummaryOut[]>("/center-orders", { params }),
    refetchInterval: BOARD_POLL_MS, // the board is also the SHIPPED listener
  });
}

export function useCenterOrder(id: number | null) {
  return useQuery({
    queryKey: ["center-order", id],
    queryFn: () => api<CenterOrderOut>(`/center-orders/${id}`),
    enabled: id !== null,
    refetchInterval: BOARD_POLL_MS,
  });
}

function useCenterOrderMutation<TArgs>(fn: (args: TArgs) => Promise<unknown>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["center-orders"] });
      qc.invalidateQueries({ queryKey: ["center-order"] });
    },
  });
}

export function usePlaceCenterOrder() {
  return useCenterOrderMutation(
    (body: {
      center_id: number;
      notes?: string;
      duplicate_of_id?: number | null;
      lines: { product_id: number; qty: number }[];
    }) => api<CenterOrderOut>("/center-orders", { method: "POST", body }),
  );
}

export function useApproveCenterOrder() {
  return useCenterOrderMutation(
    ({ id, note, lines }: { id: number; note?: string; lines?: { product_id: number; qty: number }[] }) =>
      api<CenterOrderOut>(`/center-orders/${id}/approve`, { method: "POST", body: { note: note ?? "", lines } }),
  );
}

export function useRejectCenterOrder() {
  return useCenterOrderMutation(({ id, note }: { id: number; note: string }) =>
    api<CenterOrderOut>(`/center-orders/${id}/reject`, { method: "POST", body: { note } }),
  );
}

export function useCancelCenterOrder() {
  return useCenterOrderMutation(({ id, note }: { id: number; note?: string }) =>
    api<CenterOrderOut>(`/center-orders/${id}/cancel`, { method: "POST", body: { note: note ?? "" } }),
  );
}

export function useAdjustCenterOrderLines() {
  return useCenterOrderMutation(
    ({ id, lines }: { id: number; lines: { product_id: number; qty: number }[] }) =>
      api<CenterOrderOut>(`/center-orders/${id}/lines`, { method: "PUT", body: lines }),
  );
}

export function useResetFloorRestock() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api<{ lines_cleared: number; accumulators_zeroed: number }>("/restock/floor/reset", {
        method: "POST",
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["restock"] }),
  });
}

export function useComingSoon() {
  return useQuery({
    queryKey: ["coming-soon"],
    queryFn: () => api<import("./types").ComingSoonItem[]>("/transfer-requests/coming-soon"),
    refetchInterval: BOARD_POLL_MS,
  });
}

// ------------------------------------------------- staging2 pallet flow
export function useStaging2() {
  return useQuery({
    queryKey: ["staging2"],
    queryFn: () => api<import("./types").Staging2Out>("/transfer-requests/staging2"),
    // the GET doubles as the pallet-validation listener — keep it warm
    refetchInterval: 10_000,
  });
}

export function useSendPallet() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api<import("./types").Staging2Out>("/transfer-requests/staging2/send-all", {
        method: "POST",
      }),
    onSuccess: (out) => {
      qc.setQueryData(["staging2"], out);
      qc.invalidateQueries({ queryKey: ["transfer-requests"] });
    },
  });
}

// -------------------------------------------------------------- floor OOS
export function useOosList() {
  return useQuery({
    queryKey: ["oos"],
    queryFn: () => api<import("./types").OosItemOut[]>("/oos"),
    refetchInterval: 15_000,
  });
}

export function useMarkOos() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { product_id: number; note?: string }) =>
      api<import("./types").OosItemOut>("/oos", { method: "POST", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["oos"] }),
  });
}

export function useUnmarkOos() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (markId: number) => api<void>(`/oos/${markId}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["oos"] }),
  });
}

export interface OosRestockResult {
  floor_qty_before: number;
  adjustment: {
    direction: "add" | "reduce";
    qty: number;
    status: "created" | "simulated" | "failed";
    reference: string;
    picking_name: string;
    url: string;
    error: string;
  } | null;
}

export function useRestockOosMark() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ markId, counted_qty }: { markId: number; counted_qty: number | null }) =>
      api<OosRestockResult>(`/oos/${markId}/restock`, {
        method: "POST",
        body: { counted_qty },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["oos"] }),
  });
}

// ------------------------------------------------------- purchasing (phase 4)
export function usePurchaseOrders(status = "") {
  return useQuery({
    queryKey: ["purchase-orders", status],
    queryFn: () => api<PurchaseOrderSummaryOut[]>("/ordering/orders", { params: { status } }),
    refetchInterval: BOARD_POLL_MS * 3,
  });
}

export function usePurchaseOrder(id: number | null) {
  return useQuery({
    queryKey: ["purchase-order", id],
    queryFn: () => api<PurchaseOrderDetailOut>(`/ordering/orders/${id}`),
    enabled: id !== null,
  });
}

/** The timeline poll — the listener for replies/proposals while tracking. */
export function useOrderTimeline(id: number | null, active: boolean) {
  return useQuery({
    queryKey: ["purchase-order-timeline", id],
    queryFn: () => api<OrderTimelineOut>(`/ordering/orders/${id}/timeline`),
    enabled: id !== null,
    refetchInterval: active ? BOARD_POLL_MS : false,
  });
}

function usePurchasingMutation<TArgs, TOut = unknown>(fn: (args: TArgs) => Promise<TOut>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["purchase-orders"] });
      qc.invalidateQueries({ queryKey: ["purchase-order"] });
      qc.invalidateQueries({ queryKey: ["purchase-order-timeline"] });
    },
  });
}

export function useCreatePurchaseOrder() {
  return usePurchasingMutation(
    (body: { name: string; destination?: string; notes?: string }) =>
      api<PurchaseOrderDetailOut>("/ordering/orders", { method: "POST", body }),
  );
}

export function useCreatePurchaseOrderUpload() {
  return usePurchasingMutation(
    ({ file, name, destination }: { file: File; name: string; destination?: string }) => {
      const form = new FormData();
      form.set("file", file);
      form.set("name", name);
      if (destination) form.set("destination", destination);
      return apiUpload<PurchaseOrderDetailOut>("/ordering/orders/upload", form);
    },
  );
}

export function useOverrideOrderLine() {
  return usePurchasingMutation(
    ({ orderId, lineId, ...body }: { orderId: number; lineId: number; final_sea_qty?: number; final_air_qty?: number }) =>
      api<PurchaseOrderLineOut>(`/ordering/orders/${orderId}/lines/${lineId}`, {
        method: "PATCH",
        body,
      }),
  );
}

export function usePlacePurchaseOrder() {
  return usePurchasingMutation((orderId: number) =>
    api<PurchaseOrderDetailOut>(`/ordering/orders/${orderId}/place`, { method: "POST" }),
  );
}

export function usePurchaseOrderAction(action: "cancel" | "close") {
  return usePurchasingMutation(({ orderId, note }: { orderId: number; note?: string }) =>
    api<PurchaseOrderSummaryOut>(`/ordering/orders/${orderId}/${action}`, {
      method: "POST",
      body: { note: note ?? "" },
    }),
  );
}

export function useIngestOrderEmail() {
  return usePurchasingMutation(
    ({ orderId, ...body }: { orderId: number; sender?: string; subject?: string; body: string }) =>
      api<OrderTimelineOut>(`/ordering/orders/${orderId}/ingest-email`, { method: "POST", body }),
  );
}

export function useAddOrderEvent() {
  return usePurchasingMutation(
    ({ orderId, ...body }: {
      orderId: number;
      kind: string;
      line_id?: number | null;
      payload?: Record<string, unknown>;
      note?: string;
    }) => api<OrderTimelineOut>(`/ordering/orders/${orderId}/events`, { method: "POST", body }),
  );
}

export function useDecideProposal() {
  return usePurchasingMutation(
    ({ proposalId, ...body }: {
      proposalId: number;
      accept: boolean;
      payload?: Record<string, unknown>;
      line_id?: number | null;
      note?: string;
    }) => api<OrderTimelineOut>(`/ordering/proposals/${proposalId}/decide`, { method: "POST", body }),
  );
}

export function useUploadOrderAttachment() {
  return usePurchasingMutation(
    ({ orderId, file, note }: { orderId: number; file: File; note?: string }) => {
      const form = new FormData();
      form.set("file", file);
      if (note) form.set("note", note);
      return apiUpload<OrderTimelineOut>(`/ordering/orders/${orderId}/attachments`, form);
    },
  );
}

export function downloadOrderExport(orderId: number, fmt: "csv" | "xlsx", name: string) {
  return apiDownload(`/ordering/orders/${orderId}/export.${fmt}`, `${name} ORDER LIST.${fmt}`);
}

export function downloadOrderAttachment(orderId: number, attachmentId: number, filename: string) {
  return apiDownload(`/ordering/orders/${orderId}/attachments/${attachmentId}/download`, filename);
}

// ---- vendors
export function useVendors() {
  return useQuery({
    queryKey: ["vendors"],
    queryFn: () => api<VendorOut[]>("/ordering/vendors"),
  });
}

export function useSaveVendor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id?: number } & Omit<VendorOut, "id" | "product_count">) =>
      id
        ? api<VendorOut>(`/ordering/vendors/${id}`, { method: "PATCH", body })
        : api<VendorOut>("/ordering/vendors", { method: "POST", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["vendors"] }),
  });
}

export function useVendorSuggestions(vendorId: number | null) {
  return useQuery({
    queryKey: ["vendor-suggestions", vendorId],
    queryFn: () => api<VendorSuggestionsOut>(`/ordering/vendors/${vendorId}/suggestions`),
    enabled: vendorId !== null,
  });
}

export function useCreateVendorOrder() {
  return usePurchasingMutation(
    ({ vendorId, ...body }: { vendorId: number; quantities: Record<string, number>; name?: string; destination?: string; send?: boolean }) =>
      api<PurchaseOrderDetailOut>(`/ordering/vendors/${vendorId}/orders`, { method: "POST", body }),
  );
}

// ---- rules / email settings / analogies
export function useOrderingRules() {
  return useQuery({
    queryKey: ["ordering-rules"],
    queryFn: () => api<OrderingRulesOut>("/ordering/rules"),
  });
}

export function useSaveOrderingRules() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (overrides: Record<string, unknown>) =>
      api<OrderingRulesOut>("/ordering/rules", { method: "PUT", body: overrides }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ordering-rules"] }),
  });
}

export function useOrderingEmailSettings() {
  return useQuery({
    queryKey: ["ordering-email-settings"],
    queryFn: () => api<OrderingEmailSettings>("/ordering/email-settings"),
  });
}

export function useSaveOrderingEmailSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: OrderingEmailSettings) =>
      api<OrderingEmailSettings>("/ordering/email-settings", { method: "PUT", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ordering-email-settings"] }),
  });
}

export function useAnalogies() {
  return useQuery({
    queryKey: ["analogies"],
    queryFn: () => api<AnalogyOut[]>("/ordering/analogies"),
  });
}

export function useSuggestAnalogy() {
  return useMutation({
    mutationFn: (productId: number) =>
      api<AnalogSuggestionOut>("/ordering/analogies/suggest", {
        method: "POST",
        body: { product_id: productId },
      }),
  });
}

export function useCreateAnalogy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      product_id: number;
      analog_product_id?: number | null;
      monthly_estimate?: number | null;
      rationale?: string;
      source?: string;
    }) => api<AnalogyOut>("/ordering/analogies", { method: "POST", body }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["analogies"] });
      qc.invalidateQueries({ queryKey: ["purchase-order"] });
    },
  });
}

export function useDismissAnalogy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api<void>(`/ordering/analogies/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["analogies"] }),
  });
}

// ---- catalogs import (spreadsheet -> catalog)
export function useImportOrderList() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, notes, file }: { name: string; notes?: string; file: File }) => {
      const form = new FormData();
      form.set("name", name);
      if (notes) form.set("notes", notes);
      form.set("file", file);
      return apiUpload<CatalogImportResultOut>("/order-lists/import", form);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["order-lists"] }),
  });
}

// ---- India product list (scopes import-order generation)
export function useIndiaProductList() {
  return useQuery({
    queryKey: ["india-product-list"],
    queryFn: () => api<ProductListMetaOut | null>("/ordering/product-list"),
  });
}

export function useUploadIndiaProductList() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.set("file", file);
      return apiUpload<ProductListMetaOut>("/ordering/product-list", form, "PUT");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["india-product-list"] }),
  });
}

export function useDeleteIndiaProductList() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<void>("/ordering/product-list", { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["india-product-list"] }),
  });
}

export function downloadIndiaProductList(filename: string) {
  return apiDownload("/ordering/product-list/download", filename);
}

// ---- vendor product roster
export function useVendorProducts(vendorId: number | null) {
  return useQuery({
    queryKey: ["vendor-products", vendorId],
    queryFn: () => api<VendorProductOut[]>(`/ordering/vendors/${vendorId}/products`),
    enabled: vendorId !== null,
  });
}

export function useAddVendorProduct() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ vendorId, productId, moq }: { vendorId: number; productId: number; moq?: number }) =>
      api<VendorProductOut[]>(`/ordering/vendors/${vendorId}/products`, {
        method: "POST",
        body: { product_id: productId, moq },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["vendor-products"] });
      qc.invalidateQueries({ queryKey: ["vendor-suggestions"] });
      qc.invalidateQueries({ queryKey: ["vendors"] });
    },
  });
}

export function useRemoveVendorProduct() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ vendorId, productId }: { vendorId: number; productId: number }) =>
      api<VendorProductOut[]>(`/ordering/vendors/${vendorId}/products/${productId}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["vendor-products"] });
      qc.invalidateQueries({ queryKey: ["vendor-suggestions"] });
      qc.invalidateQueries({ queryKey: ["vendors"] });
    },
  });
}

// ------------------------------------------------- phase 5: reporting
export function useSalesOverview(period: string, scope = "all") {
  return useQuery({
    queryKey: ["reports-sales", period, scope],
    queryFn: () => api<SalesOverviewOut>("/reports/sales", { params: { period, scope } }),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

export function useBreakdown(period: string, dim: string, scope = "all") {
  return useQuery({
    queryKey: ["reports-breakdown", period, dim, scope],
    queryFn: () =>
      api<BreakdownOut>("/reports/breakdown", { params: { period, dim, scope, limit: 500 } }),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

export function useNarrative(period: string) {
  return useQuery({
    queryKey: ["reports-narrative", period],
    queryFn: () => api<NarrativeOut>("/reports/narrative", { params: { period } }),
    staleTime: 5 * 60_000,
  });
}

export function useRefreshNarrative() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (period: string) =>
      api<NarrativeOut>("/reports/narrative", { params: { period, refresh: true } }),
    onSuccess: (data, period) => qc.setQueryData(["reports-narrative", period], data),
  });
}

export function useAskQuestion() {
  return useMutation({
    mutationFn: (body: { question: string; period: string }) =>
      api<QaOut>("/reports/qa", { method: "POST", body }),
  });
}

// ---------------------------------------------- phase 5: time machine
export function useTimeMachineBounds() {
  return useQuery({
    queryKey: ["time-machine-bounds"],
    queryFn: () => api<TimeMachineBoundsOut>("/time-machine/bounds"),
    staleTime: 5 * 60_000,
  });
}

export function useTimeMachine(date: string | null, category: string) {
  return useQuery({
    queryKey: ["time-machine", date, category],
    queryFn: () =>
      api<TimeMachineViewOut>("/time-machine", {
        params: { date: date!, ...(category ? { category } : {}) },
      }),
    enabled: date !== null,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
}

// ---------------------------------------------- phase 5: availability
export function useAvailabilityMeta() {
  return useQuery({
    queryKey: ["availability-meta"],
    queryFn: () => api<AvailabilityMetaOut>("/availability/meta"),
    staleTime: 5 * 60_000,
  });
}

export function useAvailabilityOos(scope: string, enabled = true, includeNeverStocked = false) {
  return useQuery({
    queryKey: ["availability-oos", scope, includeNeverStocked],
    queryFn: () =>
      api<AvailabilityItemOut[]>("/availability/oos", {
        params: { scope, ...(includeNeverStocked ? { include_never_stocked: true } : {}) },
      }),
    enabled,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

export function useAvailabilityComingSoon(within_days: number | null) {
  return useQuery({
    queryKey: ["availability-coming-soon", within_days],
    queryFn: () =>
      api<AvailabilityItemOut[]>("/availability/coming-soon", {
        params: { ...(within_days ? { within_days } : {}) },
      }),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

// ------------------------------------------------------- notices inbox
export function useNotices() {
  return useQuery({
    queryKey: ["notices"],
    queryFn: () => api<InboxOut>("/notices"),
    staleTime: 30_000,
    refetchInterval: 90_000, // the unread badge stays roughly current
  });
}

export function useMarkNoticesRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<InboxOut>("/notices/read", { method: "POST" }),
    onSuccess: (inbox) => qc.setQueryData(["notices"], inbox),
  });
}

export function usePostNotice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { title: string; body: string }) =>
      api<NoticeOut>("/notices", { method: "POST", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notices"] }),
  });
}

export function useDeleteNotice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api<void>(`/notices/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notices"] }),
  });
}

export function useStartHistoryBackfill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (weeks?: number) =>
      api<{ queued: number; requested_weeks: number; note: string }>(
        "/admin/time-machine/backfill",
        { method: "POST", body: weeks ? { weeks } : {} },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["time-machine-bounds"] }),
  });
}

export function useRebuildSalesHistory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api<{ status: string; rows: number; error: string }>("/admin/sync/sales/rebuild", {
        method: "POST",
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-status"] });
      qc.invalidateQueries({ queryKey: ["reports-sales"] });
      qc.invalidateQueries({ queryKey: ["reports-breakdown"] });
    },
  });
}
