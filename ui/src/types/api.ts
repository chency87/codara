export interface ApiPageMeta {
  cursor?: string | null;
  total_count?: number;
}

export interface ApiEnvelope<T> {
  data: T;
  meta?: {
    page?: ApiPageMeta;
  };
}

export interface HealthStatusPayload {
  status?: string;
}

export interface AccountRecord {
  account_id: string;
  provider: string;
  credential_id?: string | null;
  label?: string | null;
  cli_name?: string | null;
  auth_index?: string | null;
  auth_type?: string | null;
  allocation?: string | null;
  cli_primary?: boolean;
  status?: string | null;
  usage_source?: string | null;
  usage_observed?: boolean;
  hourly_used_pct?: number | null;
  weekly_used_pct?: number | null;
  hourly_left_pct?: number | null;
  weekly_left_pct?: number | null;
  hourly_reset_at?: string | null;
  weekly_reset_at?: string | null;
  hourly_reset_after_seconds?: number | null;
  weekly_reset_after_seconds?: number | null;
  hourly_limit?: number | null;
  weekly_limit?: number | null;
  hourly_left?: number | null;
  weekly_left?: number | null;
  remaining_compute_hours?: number | null;
  compute_hours_left?: number | null;
  credits_unlimited?: boolean | null;
  credits_has_credits?: boolean | null;
  credits_overage_limit_reached?: boolean | null;
  plan_type?: string | null;
  access_token_expires_at?: string | null;
  rate_limit_reached?: boolean | null;
  rate_limit_allowed?: boolean | null;
  last_seen_at?: string | null;
  last_used_at?: string | null;
  cooldown_until?: string | null;
  approx_local_messages_min?: number | null;
  approx_local_messages_max?: number | null;
  approx_cloud_messages_min?: number | null;
  approx_cloud_messages_max?: number | null;
}

export interface AuditLogRecord {
  audit_id: string;
  actor: string;
  action: string;
  target_type: string;
  target_id: string;
  before_state?: string | null;
  after_state?: string | null;
  timestamp: number;
}

export interface RuntimeLogRecord {
  timestamp: string;
  level: string;
  logger: string;
  message: string;
  trace_id?: string | null;
  span_id?: string | null;
  parent_span_id?: string | null;
  request_id?: string | null;
  component?: string | null;
  event_name?: string | null;
  attributes?: Record<string, any> | null;
  exception?: string | null;
}

export interface RuntimeComponent {
  status?: string;
  latency_ms?: number | null;
}

export interface ProviderHealthRecord {
  provider: string;
  status?: string;
  latency_ms?: number | null;
  active_sessions?: number;
  accounts_total?: number;
  accounts_available?: number;
  accounts_in_cooldown?: number;
  accounts_expired?: number;
  cli_primary_accounts?: number;
  usage_observed_accounts?: number;
  total_tokens?: number;
  last_seen_at?: string | null;
  runtime_available?: boolean;
  runtime_detail?: string | null;
  models_status?: string | null;
  models_source?: string | null;
  default_model?: string | null;
  model_count?: number;
  checked_at?: string | null;
}

export interface OverviewSummary {
  active_sessions?: number;
  dirty_sessions?: number;
  sessions_total?: number;
  accounts_available?: number;
  cooldown_accounts?: number;
  expired_accounts?: number;
  active_users?: number;
  active_keys?: number;
  users_total?: number;
  total_tokens_30d?: number;
  total_requests_30d?: number;
}

export interface OverviewRuntime {
  workspaces_root?: string;
  max_concurrency?: number;
  session_ttl_hours?: number;
  compression_threshold?: number | null;
  codex_usage_endpoints?: string[];
}

export interface OverviewPayload {
  summary?: OverviewSummary;
  health?: {
    status?: string;
    checked_at?: string | null;
    components?: Record<string, RuntimeComponent>;
  };
  providers?: ProviderHealthRecord[];
  recent_audit?: AuditLogRecord[];
  runtime?: OverviewRuntime;
}

export interface ProviderModelsRecord {
  provider: string;
  default_model?: string | null;
  models: string[];
  source?: string | null;
  status?: string | null;
  runtime_available?: boolean;
  detail?: string | null;
  cached?: boolean;
}

export interface ChatResponseChoice {
  finish_reason?: string | null;
  message?: {
    content?: string | Array<string | { text?: string; content?: string }>;
  };
}

export interface ChatResponsePayload {
  choices?: ChatResponseChoice[];
  extensions?: {
    diff?: string | null;
    client_session_id?: string | null;
    bound_user_display_name?: string | null;
    reported_context_tokens?: number | null;
  };
}

export interface SessionDetail {
  client_session_id: string;
  provider: string;
  backend_id?: string | null;
  user_display_name?: string | null;
  user_email?: string | null;
  api_key_label?: string | null;
  api_key_prefix?: string | null;
}

export interface SessionTurn {
  turn_id: string;
  output_tokens?: number;
  diff?: string | null;
  timestamp: number;
  finish_reason?: string | null;
}

export interface SessionListItem {
  client_session_id: string;
  updated_at: number;
  provider: string;
  user_display_name?: string | null;
  user_email?: string | null;
  api_key_label?: string | null;
  cwd_path: string;
  status: string;
}

export interface UsageTimeseriesPoint {
  period: string;
  input_tokens?: number;
  output_tokens?: number;
  cache_hit_tokens?: number;
  request_count?: number;
}

export interface TopUserRecord {
  user_id: string;
  display_name?: string | null;
  email?: string | null;
  total_tokens?: number;
  request_count?: number;
  cache_hit_tokens?: number;
}

export interface UsageSummaryPayload {
  provider_totals?: Array<{
    provider: string;
    active_sessions?: number;
    total_tokens?: number;
    accounts?: number;
  }>;
  top_users?: TopUserRecord[];
}

export interface UserSummary {
  user_id: string;
  email: string;
  display_name: string;
  active_keys: number;
  active_sessions: number;
  total_tokens_30d?: number;
  status: string;
}

export interface ApiKeyRecord {
  key_id: string;
  key_prefix: string;
  label?: string | null;
  status: string;
}

export interface UserSessionRecord {
  client_session_id: string;
  provider: string;
  api_key_label?: string | null;
  cwd_path?: string | null;
  status: string;
}

export interface UserActivityRecord {
  turn_id: string;
  provider: string;
  client_session_id: string;
  output_tokens?: number;
  input_tokens?: number;
  api_key_label?: string | null;
  finish_reason?: string | null;
  timestamp?: string | null;
  cwd_path?: string | null;
}

export interface WorkspaceResetRecord {
  reset_id: string;
  triggered_by: string;
  sessions_wiped: number;
  reset_at?: string | null;
}

export interface UserDetailPayload {
  user_id: string;
  email: string;
  display_name: string;
  workspace_path: string;
  active_keys: number;
  max_concurrency: number;
  total_tokens_30d?: number;
  total_input_tokens_30d: number;
  total_output_tokens_30d: number;
  total_cache_hit_tokens_30d: number;
  api_keys: ApiKeyRecord[];
  sessions: UserSessionRecord[];
  recent_activity: UserActivityRecord[];
  resets: WorkspaceResetRecord[];
}

export interface CreateUserResponse {
  user_id: string;
  display_name: string;
  api_key: {
    raw_key: string;
  };
}

export interface RotateUserKeyResponse extends ApiKeyRecord {
  raw_key: string;
}

export interface WorkspaceGitRecord {
  is_git_repo: boolean;
  branch?: string | null;
  head_commit?: string | null;
  short_commit?: string | null;
  head_summary?: string | null;
  head_committed_at?: string | null;
  remote_url?: string | null;
  dirty?: boolean;
}

export interface WorkspaceUserBinding {
  user_id: string;
  display_name: string;
  email: string;
  status: string;
  workspace_path: string;
  owner: boolean;
  active_sessions?: number;
  active_keys?: number;
}

export interface WorkspaceSessionBinding {
  client_session_id: string;
  backend_id?: string | null;
  provider: string;
  account_id?: string | null;
  user_id?: string | null;
  user_display_name?: string | null;
  user_email?: string | null;
  api_key_id?: string | null;
  api_key_label?: string | null;
  api_key_prefix?: string | null;
  cwd_path: string;
  prefix_hash?: string | null;
  status: string;
  created_at: number;
  updated_at: number;
  expires_at: number;
}

export interface WorkspaceRecord {
  workspace_id: string;
  name: string;
  path: string;
  relative_path?: string | null;
  exists: boolean;
  scope: string;
  git: WorkspaceGitRecord;
  bound_sessions_count: number;
  bound_users_count: number;
  owners: WorkspaceUserBinding[];
}

export interface WorkspaceDetailPayload extends WorkspaceRecord {
  users: WorkspaceUserBinding[];
  sessions: WorkspaceSessionBinding[];
}

export interface TraceEvent {
  event_id: string;
  trace_id: string;
  span_id?: string | null;
  parent_span_id?: string | null;
  kind: string;
  name: string;
  component?: string | null;
  level?: string | null;
  status?: string | null;
  request_id?: string | null;
  started_at: number;
  ended_at?: number | null;
  duration_ms?: number | null;
  attributes?: Record<string, any> | null;
}

export interface TraceRecord {
  trace_id: string;
  span_id: string;
  name: string;
  component: string;
  level: string;
  status: string;
  request_id: string;
  started_at: number;
  ended_at: number;
  duration_ms: number;
  attributes: Record<string, any>;
}
