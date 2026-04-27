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
  attributes?: Record<string, unknown> | null;
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
  active_users?: number;
  active_keys?: number;
  users_total?: number;
}

export interface OverviewRuntime {
  workspaces_root?: string;
  max_concurrency?: number;
  session_ttl_hours?: number;
  compression_threshold?: number | null;
}

export interface VersionInfo {
  name?: string;
  version?: string;
  release_check?: {
    enabled?: boolean;
    repository?: string | null;
    current_version?: string;
    latest_version?: string | null;
    update_available?: boolean;
    status?: string;
    release_url?: string | null;
    error?: string | null;
    cached?: boolean;
  };
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
  version?: VersionInfo;
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

export interface CliRunMeta {
  run_id: string;
  provider: string;
  session_id: string;
  cwd?: string | null;
  command?: string[] | null;
  provider_model?: string | null;
  attempt?: string | null;
  status?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  exit_code?: number | null;
  trace_id?: string | null;
  request_id?: string | null;
  error?: string | null;
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
  diff?: string | null;
  timestamp: number;
  finish_reason?: string | null;
}

export interface TurnResultRecord {
  output: string;
  backend_id: string;
  finish_reason: string;
  modified_files?: string[];
  diff?: string | null;
  actions?: Array<Record<string, unknown>>;
  dirty?: boolean;
  is_retry?: boolean;
}

export interface TaskRecord {
  task_id: string;
  session_id: string;
  workspace_id: string;
  user_id: string;
  prompt: string;
  status: string;
  result?: TurnResultRecord | null;
  created_at: number;
  updated_at: number;
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

export interface UserSummary {
  user_id: string;
  email: string;
  display_name: string;
  active_keys: number;
  active_sessions: number;
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

export interface ChannelLinkTokenResponse {
  token_id: string;
  raw_token: string;
  user_id: string;
  channel: string;
  bot_name: string;
  created_by: string;
  created_at: string;
  expires_at: string;
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
  attributes?: Record<string, unknown> | null;
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
  attributes: Record<string, unknown>;
  events?: TraceEvent[];
}

export interface ObservabilityPruneResult {
  runtime_logs: {
    retention_days: number;
    files_deleted: number;
    files_rewritten: number;
    records_deleted: number;
  };
  traces: {
    retention_days: number;
    files_deleted: number;
    files_rewritten: number;
    records_deleted: number;
  };
}
