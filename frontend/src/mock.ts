import type { AnalyticsSummary, Execution, Folder, Node, Provider, Rule, RuleInput, Session } from './types'

const NOW = '2026-10-29T08:00:00Z'
const NODE_ID = '11111111-1111-4111-8111-111111111111'
const FOLDER_ID = '22222222-2222-4222-8222-222222222222'
const RULE_ID = '33333333-3333-4333-8333-333333333333'

const nodes: Node[] = [{
  id: NODE_ID, display_name: 'Demo server A', enabled: true, syncthing_device_id: 'DEMO-A', api_status: 'ok',
  last_checked_at: NOW, last_seen_at: NOW, is_stale: false, created_at: NOW, updated_at: NOW,
}]
const folders: Folder[] = [{
  id: FOLDER_ID, node_id: NODE_ID, syncthing_folder_id: 'shared', label: 'Рабочие документы', present: true,
  actual_paused: null, config_observed_at: null, runtime_state: null, runtime_observed_at: null,
  need_bytes: null, pull_errors: null, desired_paused: true, active_rule_id: RULE_ID, is_stale: true,
}]
const rules: Rule[] = [{
  id: RULE_ID, folder_id: FOLDER_ID, timezone: 'Europe/Moscow', days_of_week: [1, 2, 3, 4, 5],
  start_time: '09:00', end_time: '18:00', enabled: true, version: 2, created_at: NOW, updated_at: NOW,
}]
const executions: Execution[] = [{
  id: '44444444-4444-4444-8444-444444444444', node_id: NODE_ID, folder_id: FOLDER_ID, source: 'rule',
  rule_id: RULE_ID, rule_version: 2, desired_paused: true, status: 'unknown', observed_paused: null,
  restart_required: null, created_at: '2026-10-29T07:42:00Z', completed_at: '2026-10-29T07:42:08Z', error_code: 'verification_unavailable',
}]

const clone = <T>(value: T): T => structuredClone(value)
const delay = (ms = 120) => new Promise<void>((resolve) => setTimeout(resolve, ms))

export class MockProvider implements Provider {
  readonly mode = 'mock' as const

  async login(login: string): Promise<Session> {
    await delay()
    if (!login.trim()) throw new Error('Введите логин')
    return { user: { id: '55555555-5555-4555-8555-555555555555', login }, csrf_token: 'mock-token-in-memory', expires_at: '2026-10-29T16:00:00Z' }
  }
  async logout() { await delay(40) }
  async listNodes() { await delay(); return clone(nodes) }
  async listFolders(nodeId?: string) { await delay(); return clone(nodeId ? folders.filter((folder) => folder.node_id === nodeId) : folders) }
  async listRules() { await delay(); return clone(rules) }
  async listExecutions() { await delay(); return clone(executions) }
  async analytics(): Promise<AnalyticsSummary> {
    await delay()
    return { from: '2026-10-28T08:00:00Z', to: NOW, commands: { applied: 7, failed: 1, unknown: 2, cancelled: 0, pending_created: 1, success_ratio: 0.875 }, api_checks: { successful_checks: 22, total_checks: 24, success_ratio: 0.9167 } }
  }
  async createRule(input: RuleInput) {
    await delay()
    const rule: Rule = { ...input, id: crypto.randomUUID(), version: 1, created_at: NOW, updated_at: NOW }
    rules.push(rule)
    return clone(rule)
  }
  async setRuleEnabled(rule: Rule, enabled: boolean) {
    await delay()
    const current = rules.find((item) => item.id === rule.id)
    if (!current || current.version !== rule.version) throw new Error('Правило уже изменено')
    current.enabled = enabled
    current.version += 1
    current.updated_at = NOW
    return clone(current)
  }
  async resume(folderId: string) {
    await delay()
    const execution: Execution = { id: crypto.randomUUID(), node_id: NODE_ID, folder_id: folderId, source: 'manual', rule_id: null, rule_version: null, desired_paused: false, status: 'pending', observed_paused: null, restart_required: null, created_at: NOW, completed_at: null, error_code: null }
    executions.unshift(execution)
    return clone(execution)
  }
}
