import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  AdjustmentOut,
  AuditRow,
  CanaryResult,
  CenterOut,
  FacetsOut,
  HealthOut,
  ImportReportOut,
  ProductListOut,
  ProductOut,
  OrderListOut,
  OrderListSummaryOut,
  RestockOut,
  StatusOut,
  TagOut,
  TransferRequestOut,
  TransferSummaryOut,
  UserOut,
  ZoneOut,
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
    queryKey: ["health"],
    queryFn: () => api<HealthOut>("/health"),
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
