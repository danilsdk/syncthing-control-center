import type {
  AnalyticsSummary,
  EventQuery,
  Execution,
  ExecutionQuery,
  Folder,
  Node,
  Page,
  Provider,
  Rule,
  RuleInput,
  Session,
  SyncEvent,
} from "./types";
import { ProviderError } from "./types";
const nodeId = "11111111-1111-4111-8111-111111111111",
  folderId = "22222222-2222-4222-8222-222222222222",
  ruleId = "33333333-3333-4333-8333-333333333333";
const clone = <T>(value: T): T => structuredClone(value);
export class MockProvider implements Provider {
  readonly mode = "mock" as const;
  private session: Session | null = null;
  private now = new Date().toISOString();
  private nodes: Node[] = [
    {
      id: nodeId,
      display_name: "Демонстрационный узел A",
      enabled: true,
      syncthing_device_id: "DEMO-A",
      api_status: "ok",
      last_checked_at: this.now,
      last_seen_at: this.now,
      is_stale: false,
      created_at: this.now,
      updated_at: this.now,
    },
  ];
  private folders: Folder[] = [
    {
      id: folderId,
      node_id: nodeId,
      syncthing_folder_id: "shared",
      label: "Рабочие документы",
      present: true,
      actual_paused: null,
      config_observed_at: null,
      runtime_state: null,
      runtime_observed_at: null,
      need_bytes: null,
      pull_errors: null,
      desired_paused: true,
      active_rule_id: ruleId,
      is_stale: true,
    },
  ];
  private rules: Rule[] = [
    {
      id: ruleId,
      folder_id: folderId,
      timezone: "Europe/Moscow",
      days_of_week: [1, 2, 3, 4, 5],
      start_time: "09:00",
      end_time: "18:00",
      enabled: true,
      version: 2,
      created_at: this.now,
      updated_at: this.now,
    },
  ];
  private executions: Execution[] = [
    {
      id: "44444444-4444-4444-8444-444444444444",
      node_id: nodeId,
      folder_id: folderId,
      source: "rule",
      rule_id: ruleId,
      rule_version: 2,
      desired_paused: true,
      status: "unknown",
      observed_paused: null,
      restart_required: null,
      created_at: this.now,
      completed_at: this.now,
      verified_at: null,
      error_code: "verification_unavailable",
    },
  ];
  private events: SyncEvent[] = [
    {
      id: "66666666-6666-4666-8666-666666666666",
      node_id: nodeId,
      folder_id: folderId,
      kind: "source",
      event_type: "ItemFinished",
      occurred_at: this.now,
      received_at: this.now,
      payload_summary: {},
    },
  ];
  async currentSession() {
    if (!this.session)
      throw new ProviderError("Нужен вход", "authentication_required", 401);
    return clone(this.session);
  }
  async login(login: string): Promise<Session> {
    if (!login.trim()) throw new ProviderError("Введите логин");
    this.session = {
      user: { id: "55555555-5555-4555-8555-555555555555", login },
      csrf_token: "mock-token-in-memory",
      expires_at: new Date(Date.now() + 28_800_000).toISOString(),
    };
    return clone(this.session);
  }
  async logout() {
    this.session = null;
  }
  async listNodes() {
    return clone(this.nodes);
  }
  async listFolders(id: string) {
    return clone(this.folders.filter((folder) => folder.node_id === id));
  }
  async listRules() {
    return clone(this.rules);
  }
  async listExecutions(query: ExecutionQuery = {}): Promise<Page<Execution>> {
    const { limit = 20, offset = 0 } = query;
    const items = this.executions.filter(
      (item) =>
        (!query.status || item.status === query.status) &&
        (!query.node_id || item.node_id === query.node_id) &&
        (!query.from || item.created_at >= query.from) &&
        (!query.to || item.created_at < query.to),
    );
    return clone({
      items: items.slice(offset, offset + limit),
      total: items.length,
      limit,
      offset,
    });
  }
  async getExecution(id: string) {
    const item = this.executions.find((value) => value.id === id);
    if (!item) throw new ProviderError("Попытка не найдена");
    return clone(item);
  }
  async listEvents(query: EventQuery = {}): Promise<Page<SyncEvent>> {
    const { limit = 20, offset = 0 } = query;
    const items = this.events.filter(
      (item) =>
        (!query.node_id || item.node_id === query.node_id) &&
        (!query.folder_id || item.folder_id === query.folder_id) &&
        (!query.from || item.received_at >= query.from) &&
        (!query.to || item.received_at < query.to),
    );
    return clone({
      items: items.slice(offset, offset + limit),
      total: items.length,
      limit,
      offset,
    });
  }
  async analytics(): Promise<AnalyticsSummary> {
    return {
      from: new Date(Date.now() - 86_400_000).toISOString(),
      to: this.now,
      commands: {
        applied: 0,
        failed: 0,
        unknown: 1,
        cancelled: 0,
        pending_created: 0,
        success_ratio: null,
      },
      api_checks: {
        successful_checks: 0,
        total_checks: 0,
        success_ratio: null,
      },
    };
  }
  private syncFolder(id: string) {
    const folder = this.folders.find((item) => item.id === id),
      rule = this.rules.find((item) => item.folder_id === id && item.enabled);
    if (folder) {
      folder.active_rule_id = rule?.id ?? null;
      folder.desired_paused = rule ? true : null;
    }
  }
  private assertActive(input: RuleInput, except?: string) {
    if (
      input.enabled &&
      this.rules.some(
        (rule) =>
          rule.folder_id === input.folder_id &&
          rule.enabled &&
          rule.id !== except,
      )
    )
      throw new ProviderError(
        "У папки уже есть активное правило.",
        "active_rule_conflict",
        409,
      );
  }
  async createRule(input: RuleInput) {
    this.assertActive(input);
    const rule = {
      ...clone(input),
      id: crypto.randomUUID(),
      version: 1,
      created_at: this.now,
      updated_at: this.now,
    };
    this.rules.push(rule);
    this.syncFolder(rule.folder_id);
    return clone(rule);
  }
  async updateRule(rule: Rule, input: RuleInput) {
    const current = this.rules.find((item) => item.id === rule.id);
    if (!current || current.version !== rule.version)
      throw new ProviderError("Правило уже изменено.", "version_conflict", 409);
    this.assertActive(input, rule.id);
    Object.assign(current, clone(input), {
      version: current.version + 1,
      updated_at: this.now,
    });
    this.syncFolder(current.folder_id);
    return clone(current);
  }
  setRuleEnabled(rule: Rule, enabled: boolean) {
    return this.updateRule(rule, { ...rule, enabled });
  }
  async deleteRule(rule: Rule) {
    const current = this.rules.find((item) => item.id === rule.id);
    if (!current || current.version !== rule.version)
      throw new ProviderError("Правило уже изменено.", "version_conflict", 409);
    this.rules = this.rules.filter((item) => item.id !== rule.id);
    this.syncFolder(rule.folder_id);
  }
  async resume(id: string) {
    if (this.rules.some((rule) => rule.folder_id === id && rule.enabled))
      throw new ProviderError(
        "Папкой управляет активное правило.",
        "operation_in_progress",
        409,
      );
    const execution: Execution = {
      id: crypto.randomUUID(),
      node_id: nodeId,
      folder_id: id,
      source: "manual",
      rule_id: null,
      rule_version: null,
      desired_paused: false,
      status: "pending",
      observed_paused: null,
      restart_required: null,
      created_at: this.now,
      completed_at: null,
      error_code: null,
    };
    this.executions.unshift(execution);
    return clone(execution);
  }
}
