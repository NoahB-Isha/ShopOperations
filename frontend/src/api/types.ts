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
  /** Odoo-tag-declared procurement origin ("" when untagged) */
  sourcing: "" | "domestic" | "india";
  is_stock_tracked: boolean;
  is_active: boolean;
  case_size: number;
  dept_orderable: boolean;
  restock_exclude: boolean;
  blacklisted: boolean;
  tags: TagOut[];
  stock: Record<string, number>;
  odoo_url: string | null;
}

export interface StockHistoryPointOut {
  day: string; // YYYY-MM-DD, oldest → newest; the last point is live
  total: number;
  bwhse: number;
  floor: number;
  staging: number;
  staging2: number;
  source: "sync" | "reconstructed" | "live";
}

export interface StockHistoryOut {
  points: StockHistoryPointOut[];
  first_covered: string | null;
  covered_days: number;
  reconstructed_days: number;
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

export interface BlacklistSweepOut {
  no_stock_history: number;
  usa_items: number;
  total: number;
  applied: boolean;
  sample: string[];
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
  barcode: string;
  name: string;
  category: string;
  qty_on_the_way: number;
  floor_qty: number;
  bwhse_qty: number;
  requests: { id: number; display_name: string; status: TransferStatus; qty: number }[];
  // transfers made DIRECTLY in Odoo (drafts included), via the transfers sync
  odoo_pickings: { picking_name: string; state: string; qty: number; expected_date: string | null }[];
}

export interface Staging2Item {
  product_id: number;
  sku: string;
  barcode: string;
  name: string;
  qty: number;
}

export interface PalletOut {
  id: number;
  status: "open" | "validated" | "cancelled";
  picking_status: "none" | "created" | "simulated" | "failed";
  picking_name: string;
  picking_url: string;
  picking_error: string;
  line_count: number;
  total_units: number;
  created_at: string;
  validated_at: string | null;
}

export interface Staging2Out {
  items: Staging2Item[];
  total_units: number;
  source: "live" | "snapshot" | "unmapped";
  note: string;
  pallets: PalletOut[];
}

export interface OosMarkOut {
  id: number;
  note: string;
  created_by: string;
  created_at: string;
  qty_removed: number;
  picking: {
    status: "none" | "created" | "simulated" | "failed";
    reference: string;
    error: string;
    picking_id: number | null;
    picking_name: string;
    url: string;
  };
}

export interface OosItemOut {
  product_id: number;
  sku: string;
  barcode: string;
  name: string;
  category: string;
  floor_qty: number;
  bwhse_qty: number;
  incoming_label: string;
  mark: OosMarkOut | null;
}

// ------------------------------------------------------- purchasing (phase 4)
export type PurchaseOrderStatus = "draft" | "placed" | "closed" | "cancelled";
export type PurchaseOrderType = "import" | "domestic";
export type OrderDestination = "III" | "CAN";

export interface PurchaseOrderSummaryOut {
  id: number;
  name: string;
  reference: string;
  order_type: PurchaseOrderType;
  status: PurchaseOrderStatus;
  destination: OrderDestination;
  vendor_id: number | null;
  vendor_name: string | null;
  snapshot_source: string;
  created_at: string;
  placed_at: string | null;
  line_count: number;
  ordering_line_count: number;
  sea_units: number;
  air_units: number;
  pending_proposals: number;
}

/** The engine's frozen Suggestion — everything the review table renders. */
export interface OrderingSuggestion {
  name: string;
  us_sku: string;
  category: string;
  avg_monthly_sales: number;
  units_sold: number;
  months_active: number;
  forecast_monthly: number[];
  forecast_mean: number;
  baseline_monthly_sales: number;
  forecast_method: string;
  forecast_confidence: "high" | "medium" | "low";
  diverges_from_baseline: boolean;
  on_hand: number;
  current_moh: number;
  incoming_units_by_month: number[];
  projected_moh: number[];
  projected_moh_m4: number;
  projected_moh_m6: number;
  projected_moh_with_order: number[];
  target_moh: number;
  case_size: number;
  suggested_sea_qty: number;
  suggested_air_qty: number;
  suggested_sea_round: number;
  suggested_air_round: number;
  baseline_sea_round: number;
  baseline_air_round: number;
  unit_cost: number;
  retail_price: number;
  margin: number;
  profit_lost_by_air: number;
  air_split_reason: string;
  flags: string[];
  notes: string[];
}

export interface PurchaseOrderLineOut {
  id: number;
  product_id: number | null;
  global_sku: string;
  line_status: "active" | "discontinued" | "substituted";
  substitute_sku: string;
  suggested_sea_qty: number;
  suggested_air_qty: number;
  baseline_sea_qty: number;
  baseline_air_qty: number;
  origin_sea_qty: number;
  origin_air_qty: number;
  final_sea_qty: number;
  final_air_qty: number;
  target_moh_used: number;
  case_size: number;
  suggestion: Partial<OrderingSuggestion>;
}

export interface OrderLegOut {
  id: number;
  label: string;
  method: "sea" | "air";
  status: "planned" | "shipped" | "arrived" | "cancelled";
  eta: string | null;
  line_quantities: Record<string, number>;
}

export type OrderEventKind =
  | "status"
  | "note"
  | "qty_change"
  | "substitution"
  | "discontinued"
  | "method_change"
  | "split"
  | "availability"
  | "email"
  | "attachment";

export interface OrderEventOut {
  id: number;
  kind: OrderEventKind;
  status: string;
  note: string;
  payload: Record<string, unknown>;
  actor_label: string;
  line_id: number | null;
  line_sku: string | null;
  source_message_id: number | null;
  source_quote: string;
  confidence: number | null;
  created_at: string;
}

export interface OrderProposalOut {
  id: number;
  order_id: number;
  message_id: number | null;
  line_id: number | null;
  line_sku: string | null;
  kind: OrderEventKind;
  payload: Record<string, unknown>;
  quote: string;
  confidence: number;
  parsed_by: string;
  status: "pending" | "confirmed" | "rejected";
  created_at: string;
}

export interface OrderEmailOut {
  id: number;
  direction: "in" | "out";
  sender: string;
  recipients: string;
  subject: string;
  body: string;
  status: string;
  occurred_at: string;
}

export interface OrderAttachmentOut {
  id: number;
  source: "export" | "upload" | "email";
  filename: string;
  content_type: string;
  size_bytes: number;
  note: string;
  message_id: number | null;
  created_at: string;
}

export interface PurchaseOrderDetailOut {
  order: PurchaseOrderSummaryOut;
  rules: Record<string, unknown>;
  notes: string;
  snapshot_at: string | null;
  email_gate_reason: string;
  lines: PurchaseOrderLineOut[];
  legs: OrderLegOut[];
}

export interface OrderTimelineOut {
  order: PurchaseOrderSummaryOut;
  events: OrderEventOut[];
  proposals: OrderProposalOut[];
  emails: OrderEmailOut[];
  attachments: OrderAttachmentOut[];
  legs: OrderLegOut[];
}

export interface VendorOut {
  id: number;
  name: string;
  kind: "india" | "us" | "canada" | "other";
  contact_name: string;
  contact_email: string;
  cc_emails: string;
  notes: string;
  active: boolean;
  product_count: number;
}

export interface VendorSuggestionItem extends OrderingSuggestion {
  global_sku: string;
}

export interface VendorSuggestionsOut {
  vendor: { id: number; name: string; contact_email: string };
  items: VendorSuggestionItem[];
}

export interface OrderingRulesOut {
  effective: Record<string, unknown>;
  overrides: Record<string, unknown>;
}

export interface OrderingEmailSettings {
  india_to: string[];
  cc: string[];
}

export interface AnalogyOut {
  id: number;
  product_id: number;
  product_sku: string;
  product_name: string;
  analog_product_id: number | null;
  analog_sku: string | null;
  analog_name: string | null;
  monthly_estimate: number | null;
  rationale: string;
  source: string;
  status: "active" | "graduated" | "dismissed";
}

export interface AnalogSuggestionOut {
  analog_product_id: number;
  analog_sku: string;
  analog_name: string;
  rationale: string;
  source: string;
}

export interface CatalogImportResultOut {
  catalog: OrderListOut;
  matched: number;
  skipped: string[];
  unmatched_rows: string[];
  total_rows: number;
}

export interface ProductListMetaOut {
  filename: string;
  uploaded_at: string;
  matched: number;
  total_rows: number;
  unmatched_rows: string[];
}

export interface VendorProductOut {
  product_id: number;
  global_sku: string;
  name: string;
  category: string;
  moq: number | null;
  is_active: boolean;
}

// -------------------------------------------------- phase 5: reporting
export interface ChannelSummaryOut {
  channel: string;
  label: string;
  units: number;
  revenue: number;
  prior_revenue: number;
  share: number;
  delta_pct: number | null;
}

export interface BreakdownRowOut {
  key: string;
  label: string;
  units: number;
  revenue: number;
  estimated_share: number;
  share: number;
  prior_units: number;
  prior_revenue: number;
  delta_pct: number | null;
  sku?: string;
  category?: string;
}

export interface SalesOverviewOut {
  period: { key: string; label: string; months: string[] };
  scope: string;
  orders: OrdersSummaryOut;
  generated_at: string;
  totals: {
    units: number;
    revenue: number;
    prior_units: number;
    prior_revenue: number;
    revenue_delta_pct: number | null;
    units_delta_pct: number | null;
    estimated_share: number;
    has_legacy_channel_rows: boolean;
  };
  channels: ChannelSummaryOut[];
  series: { month: string; channel: string; units: number; revenue: number }[];
  top_categories: BreakdownRowOut[];
  top_products: BreakdownRowOut[];
  centers: BreakdownRowOut[];
}

export interface BreakdownOut {
  period: { key: string; label: string };
  dim: string;
  rows: BreakdownRowOut[];
}

export interface NarrativeOut {
  headline: string;
  bullets: string[];
  actions: string[];
  source: string;
  generated: boolean;
  generated_at: string;
  period: string;
}

export interface QaOut {
  answer: string;
  source: string;
  generated: boolean;
  generated_at: string;
}

// ------------------------------------------------ phase 5: time machine
export interface TimeMachineBoundsOut {
  today: string;
  min_date: string;
  max_date: string;
  history_days: string[];
  horizon_months: number;
}

export interface TimeMachineItemOut {
  product_id: number;
  sku: string;
  barcode: string;
  name: string;
  category: string;
  total_qty: number;
  bwhse_qty: number | null;
  floor_qty: number | null;
  staging_qty: number | null;
  incoming_included: number;
  forecast_method: string;
  forecast_confidence: string;
}

export interface TimeMachineViewOut {
  mode: "past" | "today" | "future";
  requested_date: string;
  effective_date: string;
  confidence: {
    level: "high" | "medium" | "low" | "none";
    note: string;
    gap_days?: number | null;
    month_index?: number;
    stock_synced_at?: string | null;
  };
  items: TimeMachineItemOut[];
}

// ------------------------------------------------ phase 5: availability
export interface AvailabilityItemOut {
  product_id: number;
  sku: string;
  barcode: string;
  name: string;
  category: string;
  bwhse_qty: number;
  floor_qty: number;
  staging_qty: number;
  total_qty: number;
  incoming_qty: number;
  incoming_expected: string | null;
  incoming_label: string;
  last_in_stock_on: string | null;
  low_count_caveat: boolean;
}

export interface AvailabilityMetaOut {
  freshness: { stock: string | null; incoming: string | null };
}

// ------------------------------------------------------- notices inbox
export interface NoticeOut {
  id: number;
  title: string;
  body: string;
  author: string;
  created_at: string;
  read: boolean;
}

export interface InboxOut {
  unread: number;
  items: NoticeOut[];
}

export interface OrdersMonthOut {
  month: string;
  orders: number;
  amount: number;
  aov: number | null;
  known_share: number | null;
  customers: number;
  new_customers: number;
  returning_customers: number;
}

export interface OrdersSummaryOut {
  series: OrdersMonthOut[];
  totals: {
    orders: number;
    amount: number;
    aov: number | null;
    prior_orders: number;
    prior_aov: number | null;
    orders_delta_pct: number | null;
    aov_delta_pct: number | null;
    new_customers: number;
    returning_share_last_month: number | null;
    returning_share_month: string | null;
    known_customer_share: number | null;
  };
  caveat: string;
}
