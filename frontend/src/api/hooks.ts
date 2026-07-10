import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  AuditRow,
  CanaryResult,
  CenterOut,
  FacetsOut,
  HealthOut,
  ImportReportOut,
  ProductListOut,
  ProductOut,
  StatusOut,
  TagOut,
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
