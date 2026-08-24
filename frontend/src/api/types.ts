export interface RoleOut {
  role: string;
  zone_id: number | null;
  zone_name: string | null;
  /** field | departments — the "center" vs "department" wording follows the
   *  review zone, not the role (the dept roles merged 2026-08-13). */
  zone_kind: string | null;
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
  /** OAuth providers to offer (supabase mode only) — e.g. ["google"]. */
  oauth_providers: string[];
  /** Whether to also offer the email/SMS one-time-code form. */
  otp_enabled: boolean;
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
  available_in_pos?: boolean;
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
  /** Product families derived from real barcodes (CX, IN, JW…). */
  barcode_prefixes?: string[];
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
  /** map position from the backend gazetteer; null = nothing honest to place */
  latitude: number | null;
  longitude: number | null;
  /** last COMPLETE month vs the one before; null = never seen by the rollup */
  sales_units: number | null;
  sales_amount: number | null;
  sales_prev_units: number | null;
  sales_month: string;
  sales_prev_month: string;
  /** display names only — contact details live on the detail endpoint */
  reviewers: string[];
  requesters: string[];
  /** the link this center's printable QR poster encodes */
  order_url: string;
}

export interface CenterPerson {
  name: string;
  email: string;
  phone: string;
  note: string;
  is_app_user: boolean;
}

export interface CenterStockLine {
  sku: string;
  barcode: string;
  name: string;
  qty: number;
}

export interface CenterDetailOut {
  id: number;
  name: string;
  zone_name: string | null;
  reviewers: CenterPerson[];
  requesters: CenterPerson[];
  contacts: CenterPerson[];
  stock: CenterStockLine[];
  stock_total: number;
  /** ok = a live Odoo read; unmapped / unavailable explain themselves */
  stock_status: "ok" | "unmapped" | "unavailable";
  stock_note: string;
  /** what the printable QR poster encodes — the order form, this center picked */
  order_url: string;
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
  barcode: string;
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
  barcode: string;
  name: string;
  category: string;
  qty_requested: number;
  qty_sent: number | null;
  qty_counted: number | null;
  delta: number | null;
  floor_qty: number;
  bwhse_qty: number;
  /** the warehouse's own words when this line didn't come in full */
  reasons: DiscrepancyReason[];
  reason_labels: string[];
  reason_note: string;
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
  delivery: DeliveryRefOut | null;
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
  delivery: DeliveryRefOut | null;
}

export interface AdjustmentOut {
  id: number;
  request_id: number | null;
  delivery_id: number | null;
  delivery_name: string;
  product_id: number;
  sku: string;
  barcode: string;
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
  barcode: string;
  name: string;
  category: string;
  qty: number;
  flagged_on: string;
  /** bwhse_qty isn't shown on the row (restocking the shelf is a floor job) —
   *  it rides along so a "request more" swipe can build an honest draft. */
  floor_qty: number;
  bwhse_qty: number;
  checked: boolean;
  snoozed: boolean;
  /** Aisle label from the barcode prefix (IN → Incense), falling back to the
   *  Odoo category. See backend restock/grouping.py — CA never names a group. */
  group: string;
  /** units sold on the shop floor in the popularity window */
  popularity: number;
  /** the whole group's units, which is what orders the groups */
  group_popularity: number;
}

export interface RestockBackItem {
  product_id: number;
  sku: string;
  barcode: string;
  name: string;
  category: string;
  floor_qty: number;
  bwhse_qty: number;
  avg_daily: number;
  days_of_cover: number | null;
  suggested_qty: number;
  checked: boolean;
  group: string;
  popularity: number;
  group_popularity: number;
}

/** A Floor Team ask — a person's "we need more of this", waiting for the
 *  Inventory Flow Manager on the Suggested items page. */
export interface FloorRequestOut {
  id: number;
  product_id: number;
  sku: string;
  barcode: string;
  name: string;
  category: string;
  qty: number;
  note: string;
  status: "open" | "picked_up" | "dismissed";
  requested_by: string;
  created_at: string;
  resolved_by: string;
  resolved_at: string | null;
  floor_qty: number;
  bwhse_qty: number;
}

export interface RestockOut {
  floor: RestockFloorItem[];
  back: RestockBackItem[];
  meta: {
    today: string;
    folded_through: string | null;
    sales_synced_at: string | null;
    stock_synced_at?: string | null;
    /** days an unchecked line stays on the list (0 = forever) */
    line_max_age_days?: number;
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
  barcode: string;
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
  barcode: string;
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
  /** can THIS user decide it — the approvals board shows nothing else */
  can_decide: boolean;
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

export type DeliveryStatus = "open" | "validated" | "counting" | "counted" | "cancelled";

export type DiscrepancyReason = "no_stock" | "full_case" | "another_transfer" | "other";

/** The received transfer a request rode to the floor. */
export interface DeliveryRefOut {
  id: number;
  status: DeliveryStatus;
  picking_name: string;
  picking_url: string;
  declared_at: string | null;
  validated_at: string | null;
  /** how many requests shared the pallet */
  request_count: number;
}

export interface DeliveryRequestOut {
  id: number;
  display_name: string;
  status: TransferStatus;
  created_by: string;
  line_count: number;
}

export interface DeliveryDiscrepancyOut {
  product_id: number;
  sku: string;
  barcode: string;
  name: string;
  qty_requested: number;
  qty_sent: number;
  delta: number;
  reasons: DiscrepancyReason[];
  reason_labels: string[];
  note: string;
}

export interface DeliveryItemOut {
  product_id: number;
  sku: string;
  name: string;
  qty: number;
}

export interface DeliveryOut {
  id: number;
  status: DeliveryStatus;
  picking_status: OdooOutcome;
  odoo_picking_id: number | null;
  picking_name: string;
  picking_url: string;
  picking_error: string;
  item_count: number;
  total_units: number;
  created_at: string;
  validated_at: string | null;
  declared_at: string | null;
  declared_by: string;
  note: string;
  /** it moved real stock and nobody has said whose */
  needs_details: boolean;
  requests: DeliveryRequestOut[];
  discrepancies: DeliveryDiscrepancyOut[];
  items: DeliveryItemOut[];
  count: OdooRefOut;
}

export interface DeliveryCandidateOut {
  odoo_picking_id: number;
  name: string;
  state: string;
  date: string;
  item_count: number;
  total_units: number;
  already_declared: boolean;
  declared_pallet_id: number | null;
  from_staging2: boolean;
}

export interface DeliveryCandidatesOut {
  candidates: DeliveryCandidateOut[];
  note: string;
}

export interface DeliverySuggestionOut {
  request_id: number;
  display_name: string;
  status: TransferStatus;
  created_by: string;
  created_at: string;
  line_count: number;
  matched_items: number;
  total_requested: number;
  reason: string;
  /** there's real evidence — the form shows it up front */
  suggested: boolean;
  /** evidence strong enough to arrive ticked */
  auto_select: boolean;
}

export interface DeliveryReviewRowOut {
  product_id: number;
  sku: string;
  barcode: string;
  name: string;
  qty_requested: number;
  qty_sent: number;
  delta: number;
  requested_by: string[];
  reasons: DiscrepancyReason[];
  note: string;
}

export interface DeliveryExtraRowOut {
  product_id: number;
  sku: string;
  barcode: string;
  name: string;
  qty_sent: number;
}

export interface DeliveryPreviewOut {
  picking: DeliveryCandidateOut | null;
  suggestions: DeliverySuggestionOut[];
  review: DeliveryReviewRowOut[];
  extras: DeliveryExtraRowOut[];
  threshold: number;
  reason_options: { value: DiscrepancyReason; label: string }[];
  note: string;
}

export interface DeclareDeliveryIn {
  odoo_picking_id: number;
  request_ids: number[];
  reasons: { product_id: number; reasons: DiscrepancyReason[]; note: string }[];
  note: string;
}

export interface PalletOut {
  id: number;
  status: DeliveryStatus;
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
  retail_price: number;
  /* unit_cost / margin / profit_lost_by_air are stripped server-side
     (ordering/router.public_suggestion) — cost is not shown in the app. */
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

/* The time-machine PAGE was removed (2026-08-11). Its endpoints stay live —
   the response shapes live in the backend schemas and in git history; there
   is no frontend consumer to type any more. */

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

/** One-time: the pre-delivery-form requests stranded in the old count flow. */
export interface ReleaseStaleRow {
  request_id: number;
  display_name: string;
  was_status: string;
  line_count: number;
  total_requested: number;
  count_picking_name: string;
  count_picking_url: string;
  count_state: string;
  action: string; // would_release | released | already_counted
  detail: string;
}

export interface ReleaseStaleOut {
  applied: boolean;
  released: number;
  skipped: number;
  note: string;
  rows: ReleaseStaleRow[];
  cancel_in_odoo: { picking_name: string; url: string; state: string; request: string }[];
}

/** One-time: clear the testing rubble and restart the delivery flow. */
export interface ResetFlowOut {
  applied: boolean;
  keep_hours: number;
  cutoff: string;
  requests_cleared: number;
  requests_kept: number;
  pallets_cleared: number;
  events_cleared: number;
  adjustments_cleared: number;
  drafts_removed: string[];
  already_gone: string[];
  leftovers: {
    picking_name: string;
    url: string;
    state: string;
    belonged_to: string;
    reason: string;
  }[];
  kept: string[];
  discover_from: string;
  note: string;
}

/** All the places one item physically sits (live Odoo quant read). */
export interface ItemLocation {
  location: string;
  short: string;
  area: string;
  qty: number;
}

export interface ItemLocations {
  product_id: number;
  name: string;
  barcode: string;
  source: "live" | "snapshot" | "unavailable" | "untracked";
  note: string;
  total: number;
  locations: ItemLocation[];
  buckets: Record<string, number>;
}

export interface FloorCountOut {
  product_id: number;
  floor_qty_before: number;
  counted_qty: number;
  delta: number;
  direction: "add" | "reduce" | "none";
  status: "created" | "simulated" | "failed" | "none";
  reference: string;
  picking_name: string;
  url: string;
  error: string;
  note: string;
}

// ---------------------------------------------------------- inventory counting
export interface CountLocationOut {
  key: string;
  label: string;
  odoo_id: number | null;
  note: string;
}

export interface CountLocationsOut {
  locations: CountLocationOut[];
  default: string;
  can_review: boolean;
}

export interface CountEntryOut {
  attempt: number;
  counted_qty: number;
  odoo_qty: number;
  odoo_qty_source: string;
  delta: number;
  counted_by: string;
  reason: string;
  created_at: string;
}

export interface CountItemEventOut {
  kind: string;
  note: string;
  actor: string;
  created_at: string;
}

export type CountItemStatus = "pending" | "recount_requested" | "approved" | "rejected";

export interface CountItemOut {
  id: number;
  count_id: number;
  product_id: number;
  sku: string;
  barcode: string;
  name: string;
  status: CountItemStatus;
  location_key: string;
  counted_by: string;
  recount_assignee: string;
  recount_assignee_id: number | null;
  reviewed_by: string;
  reviewed_at: string | null;
  attempts: number;
  counted_qty: number | null;
  odoo_qty: number | null;
  delta: number | null;
  applied_qty: number | null;
  picking_status: string;
  picking_name: string;
  picking_url: string;
  picking_error: string;
  entries: CountEntryOut[];
  events: CountItemEventOut[];
  submitted_at: string;
}

export type CountStatus =
  | "pending"
  | "partially_reviewed"
  | "recount_required"
  | "completed";

export interface CountOut {
  id: number;
  display_name: string;
  location_key: string;
  location_label: string;
  status: CountStatus;
  counted_by: string;
  note: string;
  submitted_at: string;
  items: CountItemOut[];
  events: CountItemEventOut[];
}

export interface CountSummaryOut {
  id: number;
  display_name: string;
  location_key: string;
  location_label: string;
  status: CountStatus;
  counted_by: string;
  submitted_at: string;
  item_count: number;
  pending_items: number;
  recount_items: number;
}

export interface CountAssigneeOut {
  id: number;
  name: string;
  roles: string[];
}

/** Months of cover to order — the one number a buyer thinks in. */
export interface OrderingCoverageOut {
  months: number | null; // null = targets differ per category
  default_target_moh: number;
  category_target_moh: Record<string, number>;
  expiry_max_target_moh: number;
  air_only_min_moh: number;
  bulk_cycle_target_moh: number;
  sea_lead_months: number;
  air_lead_months: number;
}
