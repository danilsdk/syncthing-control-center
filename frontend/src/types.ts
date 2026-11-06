export type DataMode = "mock" | "api";

export type ApiStatus =
  | "unknown"
  | "ok"
  | "unreachable"
  | "auth_error"
  | "invalid_response"
  | "identity_mismatch";
export type ExecutionStatus =
  | "pending"
  | "applied"
  | "failed"
  | "unknown"
  | "cancelled";

export interface Session {
  user: { id: string; login: string };
  csrf_token: string;
  expires_at: string;
}

export interface Node {
  id: string;
  display_name: string;
  enabled: boolean;
  syncthing_device_id: string | null;
  api_status: ApiStatus;
  last_checked_at: string | null;
  last_seen_at: string | null;
  is_stale: boolean;
  created_at: string;
  updated_at: string;
}

export interface Folder {
  id: string;
  node_id: string;
  syncthing_folder_id: string;
  label: string;
  present: boolean;
  actual_paused: boolean | null;
  config_observed_at: string | null;
  runtime_state: string | null;
  runtime_observed_at: string | null;
  need_bytes: number | null;
  pull_errors: number | null;
  desired_paused: boolean | null;
  active_rule_id: string | null;
  is_stale: boolean;
}

export interface Rule {
  id: string;
  folder_id: string;
  timezone: string;
  days_of_week: number[];
  start_time: string;
  end_time: string;
  enabled: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface Execution {
  id: string;
  node_id: string;
  folder_id: string;
  source: "rule" | "manual";
  rule_id: string | null;
  rule_version: number | null;
  desired_paused: boolean;
  status: ExecutionStatus;
  observed_paused: boolean | null;
  restart_required: boolean | null;
  created_at: string;
  sent_at?: string | null;
  verified_at?: string | null;
  completed_at: string | null;
  error_code: string | null;
}

export interface Metrics {
  applied: number;
  failed: number;
  unknown: number;
  cancelled: number;
  pending_created: number;
  success_ratio: number | null;
}

export interface AnalyticsSummary {
  from: string;
  to: string;
  commands: Metrics;
  api_checks: {
    successful_checks: number;
    total_checks: number;
    success_ratio: number | null;
  };
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface RuleInput {
  folder_id: string;
  timezone: string;
  days_of_week: number[];
  start_time: string;
  end_time: string;
  enabled: boolean;
}

export interface Provider {
  readonly mode: DataMode;
  currentSession(): Promise<Session>;
  login(login: string, password: string): Promise<Session>;
  logout(): Promise<void>;
  listNodes(): Promise<Node[]>;
  listFolders(nodeId: string): Promise<Folder[]>;
  listRules(): Promise<Rule[]>;
  createRule(input: RuleInput): Promise<Rule>;
  setRuleEnabled(rule: Rule, enabled: boolean): Promise<Rule>;
  updateRule(rule: Rule, input: RuleInput): Promise<Rule>;
  deleteRule(rule: Rule): Promise<void>;
  listExecutions(query?: ExecutionQuery): Promise<Page<Execution>>;
  getExecution(id: string): Promise<Execution>;
  analytics(): Promise<AnalyticsSummary>;
  resume(folderId: string): Promise<Execution>;
}

export class ProviderError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(message: string, code = "request_failed", status = 0) {
    super(message);
    this.name = "ProviderError";
    this.code = code;
    this.status = status;
  }
}

export interface ExecutionQuery {
  limit?: number;
  offset?: number;
  from?: string;
  to?: string;
  status?: ExecutionStatus | "";
  node_id?: string;
}
