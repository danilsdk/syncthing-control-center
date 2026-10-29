import type { AnalyticsSummary, Execution, Folder, Node, Page, Provider, ProviderError, Rule, RuleInput, Session } from './types'
import { ProviderError as ProviderErrorClass } from './types'

const API_ORIGIN = (import.meta.env.VITE_API_ORIGIN as string | undefined)?.replace(/\/$/, '') ?? ''

type RequestOptions = RequestInit & { csrf?: boolean }

export class ApiProvider implements Provider {
  readonly mode = 'api' as const
  private csrfToken: string | null = null

  private async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const headers = new Headers(options.headers)
    if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
    if (options.csrf && this.csrfToken) headers.set('X-CSRF-Token', this.csrfToken)
    const response = await fetch(`${API_ORIGIN}${path}`, { ...options, credentials: 'include', headers })
    if (response.status === 204) return undefined as T
    const payload = await response.json().catch(() => ({})) as Record<string, unknown>
    if (!response.ok) {
      const detail = payload.error as { code?: string; message?: string } | undefined
      if (response.status === 401) this.csrfToken = null
      throw new ProviderErrorClass(detail?.message ?? 'Сервис недоступен', detail?.code, response.status)
    }
    return payload as T
  }

  async login(login: string, password: string): Promise<Session> {
    const session = await this.request<Session>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ login, password }),
      csrf: false,
    })
    this.csrfToken = session.csrf_token
    return session
  }

  async logout() {
    await this.request<void>('/api/auth/logout', { method: 'POST', csrf: true })
    this.csrfToken = null
  }

  private async page<T>(path: string): Promise<T[]> {
    const response = await this.request<Page<T>>(`${path}${path.includes('?') ? '&' : '?'}limit=100&offset=0`)
    return response.items
  }

  listNodes() { return this.page<Node>('/api/nodes') }
  listFolders(nodeId?: string) { return this.page<Folder>(nodeId ? `/api/nodes/${nodeId}/folders` : '/api/folders') }
  listRules() { return this.page<Rule>('/api/rules') }
  listExecutions() { return this.page<Execution>('/api/executions') }

  createRule(input: RuleInput) {
    return this.request<Rule>('/api/rules', { method: 'POST', body: JSON.stringify(input), csrf: true })
  }

  setRuleEnabled(rule: Rule, enabled: boolean) {
    return this.request<Rule>(`/api/rules/${rule.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ expected_version: rule.version, enabled }),
      csrf: true,
    })
  }

  analytics() {
    const to = new Date()
    const from = new Date(to.getTime() - 24 * 60 * 60 * 1000)
    return this.request<AnalyticsSummary>(`/api/analytics/summary?from=${encodeURIComponent(from.toISOString())}&to=${encodeURIComponent(to.toISOString())}`)
  }

  resume(folderId: string) {
    return this.request<Execution>('/api/executions/resume', {
      method: 'POST',
      body: JSON.stringify({ folder_id: folderId, idempotency_key: crypto.randomUUID() }),
      csrf: true,
    })
  }
}

export function isProviderError(error: unknown): error is ProviderError {
  return error instanceof ProviderErrorClass
}
