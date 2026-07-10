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
