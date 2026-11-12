import type {
  AnalyticsSummary,
  Execution,
  ExecutionQuery,
  Folder,
  Node,
  Page,
  Provider,
  Rule,
  RuleInput,
  Session,
} from "./types";
import { ProviderError } from "./types";

const messages: Record<string, string> = {
  invalid_credentials: "Неверный логин или пароль.",
  authentication_required: "Сессия завершена. Войдите снова.",
  auth_not_configured: "Администратор ещё не настроен на сервере.",
  csrf_invalid: "Сессия изменилась. Войдите снова перед сохранением.",
  origin_forbidden: "Адрес интерфейса не разрешён сервером.",
  login_rate_limited: "Слишком много попыток входа. Повторите через минуту.",
  version_conflict: "Правило уже изменено. Перечитайте данные перед повтором.",
  active_rule_conflict: "У папки уже есть активное правило.",
  operation_in_progress: "Для этой папки уже выполняется операция.",
  node_disabled: "Узел отключён от управления.",
  invalid_timezone: "Укажите существующий часовой пояс IANA.",
  invalid_window: "Начало и конец окна должны различаться.",
  invalid_period: "Начало периода должно быть раньше конца.",
  validation_error: "Проверьте значения полей формы.",
  service_unavailable: "Сервер временно недоступен. Повторите запрос позже.",
};

export class ApiProvider implements Provider {
  readonly mode = "api" as const;
  private csrfToken: string | null = null;
  private resumeKeys = new Map<string, string>();

  private async request<T>(
    path: string,
    options: RequestInit = {},
  ): Promise<T> {
    const headers = new Headers(options.headers);
    if (options.body) headers.set("Content-Type", "application/json");
    if (options.method && options.method !== "GET" && this.csrfToken)
      headers.set("X-CSRF-Token", this.csrfToken);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20_000);
    try {
      const response = await fetch(path, {
        ...options,
        credentials: "same-origin",
        headers,
        signal: controller.signal,
      });
      if (response.status === 204) return undefined as T;
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        if (response.status === 401) {
          this.csrfToken = null;
          this.resumeKeys.clear();
        }
        const code = payload?.error?.code ?? "request_failed";
        const fallback =
          response.status === 404
            ? "Этот раздел API ещё не подключён на сервере."
            : "Не удалось выполнить запрос. Повторите позже.";
        throw new ProviderError(
          messages[code] ?? fallback,
          code,
          response.status,
        );
      }
      if (!payload || typeof payload !== "object")
        throw new ProviderError(
          "Сервер вернул некорректный ответ.",
          "invalid_response",
        );
      return payload as T;
    } catch (error) {
      if (isProviderError(error)) throw error;
      throw new ProviderError(
        "Нет ответа сервера. Проверьте подключение и повторите запрос.",
        "network_error",
      );
    } finally {
      clearTimeout(timeout);
    }
  }
  async currentSession() {
    const session = await this.request<Session>("/api/auth/me");
    this.csrfToken = session.csrf_token;
    return session;
  }
  async login(login: string, password: string) {
    const session = await this.request<Session>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ login, password }),
    });
    this.csrfToken = session.csrf_token;
    return session;
  }
  async logout() {
    await this.request<void>("/api/auth/logout", { method: "POST" });
    this.csrfToken = null;
    this.resumeKeys.clear();
  }
  private async all<T>(path: string): Promise<T[]> {
    const items: T[] = [];
    for (let offset = 0; ; offset += 100) {
      const page = await this.request<Page<T>>(
        `${path}?limit=100&offset=${offset}`,
      );
      if (!Array.isArray(page.items) || !Number.isInteger(page.total))
        throw new ProviderError(
          "Некорректная страница данных.",
          "invalid_response",
        );
      items.push(...page.items);
      if (!page.items.length || offset + page.items.length >= page.total)
        return items;
    }
  }
  listNodes() {
    return this.all<Node>("/api/nodes");
  }
  listFolders(nodeId: string) {
    return this.all<Folder>(`/api/nodes/${encodeURIComponent(nodeId)}/folders`);
  }
  listRules() {
    return this.all<Rule>("/api/rules");
  }
  listExecutions(query: ExecutionQuery = {}) {
    const params = new URLSearchParams({ limit: "20", offset: "0" });
    for (const [key, value] of Object.entries(query))
      if (value !== undefined && value !== "") params.set(key, String(value));
    return this.request<Page<Execution>>(`/api/executions?${params}`);
  }
  getExecution(id: string) {
    return this.request<Execution>(
      `/api/executions/${encodeURIComponent(id)}`,
    );
  }
  createRule(input: RuleInput) {
    return this.request<Rule>("/api/rules", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }
  setRuleEnabled(rule: Rule, enabled: boolean) {
    return this.request<Rule>(`/api/rules/${rule.id}`, {
      method: "PATCH",
      body: JSON.stringify({ expected_version: rule.version, enabled }),
    });
  }
  updateRule(rule: Rule, input: RuleInput) {
    const { timezone, days_of_week, start_time, end_time, enabled } = input;
    return this.request<Rule>(`/api/rules/${rule.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        timezone,
        days_of_week,
        start_time,
        end_time,
        enabled,
        expected_version: rule.version,
      }),
    });
  }
  deleteRule(rule: Rule) {
    return this.request<void>(
      `/api/rules/${rule.id}?expected_version=${rule.version}`,
      { method: "DELETE" },
    );
  }
  analytics() {
    const to = new Date();
    const from = new Date(to.getTime() - 86_400_000);
    return this.request<AnalyticsSummary>(
      `/api/analytics/summary?from=${encodeURIComponent(from.toISOString())}&to=${encodeURIComponent(to.toISOString())}`,
    );
  }
  async resume(folderId: string) {
    const key = this.resumeKeys.get(folderId) ?? crypto.randomUUID();
    this.resumeKeys.set(folderId, key);
    const execution = await this.request<Execution>("/api/executions/resume", {
      method: "POST",
      body: JSON.stringify({ folder_id: folderId, idempotency_key: key }),
    });
    if (
      typeof execution?.id !== "string" ||
      !execution.id ||
      execution.folder_id !== folderId
    ) {
      throw new ProviderError(
        "Сервер вернул некорректную попытку.",
        "invalid_response",
      );
    }
    // The key is only needed until the POST identifies its execution.
    if (this.resumeKeys.get(folderId) === key) this.resumeKeys.delete(folderId);
    return execution;
  }
}
export function isProviderError(error: unknown): error is ProviderError {
  return error instanceof ProviderError;
}
