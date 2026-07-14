export interface RoleOut {
  role: string;
  zone_id: number | null;
  zone_name: string | null;
  center_id: number | null;
  center_name: string | null;
}

export interface UserOut {
  id: number;
  email: string | null;
  phone: string | null;
  display_name: string;
  is_active: boolean;
  roles: RoleOut[];
}

export interface AuthConfig {
  mode: "dev" | "supabase";
  supabase_url: string;
  supabase_anon_key: string;
}

export interface SessionOut {
  token: string;
  user: UserOut;
}

export interface TagOut {
  tag: string;
  expires_on: string | null;
}

export interface ProductOut {
  id: number;
  global_sku: string;
  us_sku: string;
  odoo_internal_ref: string;
  barcode: string;
  name: string;
  category: string;
  cost: number;
  retail_price: number;
  source: "odoo" | "manual";
  is_stock_tracked: boolean;
  is_active: boolean;
  case_size: number;
  dept_orderable: boolean;
  restock_exclude: boolean;
  tags: TagOut[];
  stock: Record<string, number>;
  odoo_url: string | null;
}

export interface ProductListOut {
  items: ProductOut[];
  total: number;
  page: number;
  page_size: number;
}

export interface FacetsOut {
  categories: string[];
  tags: string[];
  total_active: number;
}

export interface ZoneOut {
  id: number;
  name: string;
  kind: string;
  center_count: number;
}

export interface ContactOut {
  name: string;
  email: string;
  phone: string;
  role_note: string;
}

export interface CenterOut {
  id: number;
  name: string;
  city: string;
  state: string;
  region: string;
  country: string;
  zone_id: number | null;
  zone_name: string | null;
  is_active: boolean;
  activity_raw: string;
  stripe_terminal_name: string;
  needs_followup: boolean;
  followup_reasons: string[];
  shared_product_group: string | null;
  notes: string;
  contacts: ContactOut[];
  odoo_location_id: number | null;
  odoo_location_name: string;
}

export interface DomainSync {
  last_success_at: string | null;
  last_attempt_at: string | null;
  age_seconds: number | null;
  stale: boolean;
  interval_minutes: number;
  last_error: string;
  auth_failed: boolean;
  extra: Record<string, unknown>;
}

export interface HealthOut {
  status: "ok" | "degraded" | "down";
  db: boolean;
  odoo_mode: "live" | "fixture";
  writes_enabled: boolean;
  odoo_auth_failed: boolean;
  sync: Record<string, DomainSync>;
}

export interface SyncRunOut {
  id: number;
  domain: string;
  trigger: string;
  status: string;
  rows: number;
  source: string;
  started_at: string | null;
  finished_at: string | null;
  error: string;
}

export interface FlagOut {
  key: string;
  enabled: boolean;
  description: string;
}

export interface StatusOut extends HealthOut {
  auth_mode: string;
  odoo_base_url: string | null;
  recent_runs: SyncRunOut[];
  flags: FlagOut[];
  notifications?: NotificationsStatusOut; // phase 3 — optional for older backends
}

export interface AuditRow {
  id: number;
  created_at: string;
  actor_user_id: number | null;
  operation: string;
  reference: string;
  dry_run: boolean;
  dry_run_reason: string;
  success: boolean;
  odoo_model: string;
  odoo_record_ids: number[];
  request_payload: Record<string, unknown>;
  response: Record<string, unknown>;
  error: string;
  duration_ms: number;
}

export interface CanaryStep {
  name: string;
  ok: boolean;
  detail: string;
}

export interface CanaryResult {
  operation: string;
  mode: string;
  dry_run: boolean;
  steps: CanaryStep[];
  ok: boolean;
  reference: string;
  deep_link: string;
  payload?: Record<string, unknown>;
}

export interface ImportReportOut {
  sheets_processed: string[];
  sheets_skipped: string[];
  centers_parsed: number;
  centers_created: number;
  centers_updated: number;
  zones_created: string[];
  contacts: number;
  users_created: number;
  shared_groups: Record<string, string[]>;
  followups: { center: string; reasons: string[] }[];
  warnings: string[];
  applied: boolean;
  followup_count: number;
}

// ---------------------------------------------------------------- phase 2
export type OdooOutcome = "none" | "created" | "simulated" | "failed";

// order lists are CATALOGS people order from — no quantities
export interface OrderLineOut {
  id: number;
  product_id: number;
  sku: string;
  name: string;
  category: string;
  is_active: boolean;
  retail_price: number;
  bwhse_qty: number;
}

export interface OrderListOut {
  id: number;
  name: string;
  notes: string;
  is_archived: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
  cloned_from_id: number | null;
  lines: OrderLineOut[];
  zones: { zone_id: number; zone_name: string }[];
  centers: { center_id: number; center_name: string; zone_id: number | null }[];
  stale_line_count: number;
}

export interface OrderListSummaryOut {
  id: number;
  name: string;
  is_archived: boolean;
  line_count: number;
  stale_line_count: number;
  zone_names: string[];
  center_count: number;
  updated_at: string;
}

export type TransferStatus =
  | "requested"
  | "working_on_it"
  | "sent"
  | "counting"
  | "done"
  | "cancelled";

export interface TransferLineOut {
  id: number;
  product_id: number;
  sku: string;
  name: string;
  category: string;
  qty_requested: number;
  qty_sent: number | null;
  qty_counted: number | null;
  delta: number | null;
  floor_qty: number;
  bwhse_qty: number;
}

export interface TransferEventOut {
  id: number;
  kind: "status" | "note" | "lines_edited" | "odoo" | "discrepancy";
  status: string;
  note: string;
  actor: string;
  created_at: string;
}

export interface OdooRefOut {
  status: OdooOutcome;
  reference: string;
  error: string;
  picking_id: number | null;
  picking_name: string;
  url: string;
  barcode_url: string;
}

export interface TransferActionsOut {
  can_edit_lines: boolean;
  can_ack: boolean;
  can_mark_sent: boolean;
  can_prepare_count: boolean;
  can_mark_done: boolean;
  can_cancel: boolean;
}

export interface TransferRequestOut {
  id: number;
  display_name: string;
  status: TransferStatus;
  notes: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  placement: OdooRefOut;
  count: OdooRefOut;
  lines: TransferLineOut[];
  events: TransferEventOut[];
  actions: TransferActionsOut;
}

export interface TransferSummaryOut {
  id: number;
  display_name: string;
  status: TransferStatus;
  created_by: string;
  created_at: string;
  updated_at: string;
  line_count: number;
  total_requested: number;
  open_adjustments: number;
  picking_status: OdooOutcome;
  count_status: OdooOutcome;
}

export interface AdjustmentOut {
  id: number;
  request_id: number | null;
  product_id: number;
  sku: string;
  name: string;
  qty_expected: number;
  qty_counted: number;
  delta: number;
  status: "open" | "resolved" | "dismissed";
  note: string;
  resolution_note: string;
  resolved_by: string;
  created_at: string;
  resolved_at: string | null;
}

export interface RestockFloorItem {
  line_id: number;
  product_id: number;
  sku: string;
  name: string;
  category: string;
  qty: number;
  flagged_on: string;
  floor_qty: number;
  bwhse_qty: number;
  checked: boolean;
}

export interface RestockBackItem {
  product_id: number;
  sku: string;
  name: string;
  category: string;
  floor_qty: number;
  bwhse_qty: number;
  avg_daily: number;
  days_of_cover: number | null;
  suggested_qty: number;
  checked: boolean;
}

export interface RestockOut {
  floor: RestockFloorItem[];
  back: RestockBackItem[];
  meta: {
    today: string;
    folded_through: string | null;
    sales_synced_at: string | null;
    floor_threshold: number;
    low_cover_days: number;
    target_cover_days: number;
    avg_window_days: number;
    last_reset_at?: string | null;
    last_reset_by?: string;
  };
}

// ------------------------------------------------------------ center orders
export type CenterOrderStatus = "pending" | "approved" | "shipped" | "rejected" | "cancelled";
export type ReasonLevel = "" | "ok" | "info" | "warn";

export interface AvailabilityOut {
  status: "in" | "low" | "out" | "untracked";
  qty: number | null;
  low_count_caveat: boolean;
  incoming_qty: number;
  incoming_expected: string | null;
  incoming_label: string;
}

export interface OrderContextCenter {
  id: number;
  name: string;
  zone_name: string;
  zone_kind: "field" | "departments";
  item_count: number;
}

export interface CatalogItemOut {
  product_id: number;
  sku: string;
  name: string;
  category: string;
  retail_price: number;
  case_size: number;
  untracked: boolean;
  from_lists: string[];
  availability: AvailabilityOut;
}

export interface OrderCatalogOut {
  center: { id: number; name: string; zone_name: string; zone_kind: string };
  source_key: "bwhse" | "floor";
  items: CatalogItemOut[];
}

export interface ReasonBadge {
  code: string;
  level: "info" | "warn";
  text: string;
}

export interface ReasonPreviewOut {
  level: ReasonLevel;
  summary: string;
  source: string;
  order_badges: ReasonBadge[];
  lines: Record<string, ReasonBadge[]>;
}

export interface CenterOrderLineOut {
  id: number;
  product_id: number;
  sku: string;
  name: string;
  category: string;
  qty_requested: number;
  qty_approved: number | null;
  qty_final: number;
  unit_price: number;
  untracked: boolean;
  badges: ReasonBadge[];
  availability: AvailabilityOut | null;
}

export interface CenterOrderEventOut {
  id: number;
  kind: string;
  status: string;
  note: string;
  actor: string;
  created_at: string;
}

export interface CenterOrderActions {
  can_approve: boolean;
  can_reject: boolean;
  can_adjust: boolean;
  can_cancel: boolean;
}

export interface CenterOrderOut {
  id: number;
  display_name: string;
  status: CenterOrderStatus;
  notes: string;
  center: { id: number; name: string; zone_name: string; zone_kind: string };
  created_by: string;
  created_at: string;
  updated_at: string;
  decided_by: string;
  decided_at: string | null;
  decision_note: string;
  duplicate_of_id: number | null;
  source_location_key: string;
  reasonability: {
    level?: ReasonLevel;
    summary?: string;
    source?: string;
    order_badges?: ReasonBadge[];
    lines?: Record<string, ReasonBadge[]>;
  };
  reasonability_level: ReasonLevel;
  placement: {
    status: "none" | "created" | "simulated" | "failed";
    reference: string;
    error: string;
    picking_id: number | null;
    picking_name: string;
    url: string;
  };
  totals: { items: number; units: number; value: number };
  lines: CenterOrderLineOut[];
  events: CenterOrderEventOut[];
  actions: CenterOrderActions;
}

export interface CenterOrderSummaryOut {
  id: number;
  display_name: string;
  status: CenterOrderStatus;
  center_id: number;
  center_name: string;
  zone_name: string;
  created_by: string;
  created_at: string;
  decided_at: string | null;
  line_count: number;
  total_units: number;
  total_value: number;
  reasonability_level: ReasonLevel;
  picking_status: "none" | "created" | "simulated" | "failed";
  odoo_picking_name: string;
}

export interface NotifyChannelOut {
  configured: boolean;
  live: boolean;
  gate: string | null;
  connected: boolean;
  detail: string;
  last_ok_at: string | null;
  checked_at: string | null;
  consecutive_failures: number;
  last_error: string;
}

export interface NotificationsStatusOut {
  enabled: boolean;
  whatsapp: NotifyChannelOut;
  email: NotifyChannelOut;
  has_pending: boolean;
}

export interface ComingSoonItem {
  product_id: number;
  sku: string;
  name: string;
  category: string;
  qty_on_the_way: number;
  floor_qty: number;
  bwhse_qty: number;
  requests: { id: number; display_name: string; status: TransferStatus; qty: number }[];
}
