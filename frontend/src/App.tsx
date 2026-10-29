import { useCallback, useEffect, useMemo, useState } from 'react'
import { MockProvider } from './mock'
import { ApiProvider, isProviderError } from './provider'
import type { AnalyticsSummary, Execution, Folder, Node, Provider, Rule, RuleInput, Session } from './types'

type View = 'overview' | 'folders' | 'rules' | 'history' | 'analytics'

const navItems: { id: View; label: string; icon: string }[] = [
  { id: 'overview', label: 'Обзор', icon: '◒' },
  { id: 'folders', label: 'Папки', icon: '▣' },
  { id: 'rules', label: 'Правила', icon: '◫' },
  { id: 'history', label: 'История', icon: '◷' },
  { id: 'analytics', label: 'Аналитика', icon: '↗' },
]

const mode: 'mock' | 'api' = import.meta.env.VITE_DATA_MODE === 'mock' ? 'mock' : 'api'

function formatTime(value: string | null) {
  if (!value) return 'Нет наблюдения'
  return new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

function statusLabel(value: string) {
  const labels: Record<string, string> = { ok: 'Доступен', unknown: 'Неизвестно', unreachable: 'Недоступен', auth_error: 'Ошибка доступа', invalid_response: 'Некорректный ответ', identity_mismatch: 'Смена идентичности', applied: 'Подтверждено', failed: 'Не применено', pending: 'Ожидает', cancelled: 'Отменено' }
  return labels[value] ?? value
}

function tone(value: string) {
  if (['ok', 'applied'].includes(value)) return 'success'
  if (['unknown', 'pending'].includes(value)) return 'warning'
  if (['unreachable', 'auth_error', 'invalid_response', 'identity_mismatch', 'failed'].includes(value)) return 'danger'
  return 'neutral'
}

function ErrorNotice({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return <div className="notice notice-error" role="alert"><span className="notice-mark">!</span><div><strong>Не удалось получить данные</strong><p>{message}</p>{onRetry && <button className="button button-small" onClick={onRetry}>Повторить</button>}</div></div>
}

function StateCard({ title, children, kind = 'loading' }: { title: string; children: React.ReactNode; kind?: 'loading' | 'empty' | 'error' }) {
  return <div className={`state-card state-${kind}`}><div className="state-icon">{kind === 'loading' ? '…' : kind === 'empty' ? '∅' : '!'}</div><strong>{title}</strong><p>{children}</p></div>
}

function Login({ provider, onLogin }: { provider: Provider; onLogin: (session: Session) => void }) {
  const [login, setLogin] = useState(mode === 'mock' ? 'admin' : '')
  const [password, setPassword] = useState(mode === 'mock' ? 'demo-password' : '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true); setError('')
    try { onLogin(await provider.login(login, password)) } catch (reason) { setError(reason instanceof Error ? reason.message : 'Проверьте подключение к серверу') } finally { setBusy(false) }
  }

  return <main className="login-page"><div className="login-art"><div className="brand-mark large">S</div><span className="eyebrow">CONTROL CENTER / 01</span><h1>Синхронизация,<br /><em>которой можно доверять.</em></h1><p>Единая панель для расписаний, подтверждённых команд и состояния папок Syncthing.</p><div className="login-orbit"><i /><i /><i /></div></div><section className="login-panel"><div className="brand-row"><div className="brand-mark">S</div><span>Syncthing<br /><b>Control Center</b></span></div><div className="login-copy"><span className="eyebrow">Добро пожаловать</span><h2>Войти в систему</h2><p>Используйте учётную запись администратора, чтобы продолжить.</p></div><form onSubmit={submit} className="form-stack"><label>Логин<input autoComplete="username" value={login} onChange={(event) => setLogin(event.target.value)} placeholder="admin" required /></label><label>Пароль<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="••••••••" required /></label>{error && <div className="form-error">{error}</div>}<button className="button button-primary button-wide" disabled={busy}>{busy ? 'Проверяем…' : 'Войти'} <span>→</span></button></form><div className="login-foot"><span className={`mode-dot ${mode}`} />{mode === 'mock' ? 'Демонстрационный режим' : 'Защищённое подключение'}</div></section></main>
}

function Badge({ value, label = statusLabel(value) }: { value: string; label?: string }) {
  return <span className={`badge badge-${tone(value)}`}><i />{label}</span>
}

function App() {
  const provider = useMemo<Provider>(() => mode === 'mock' ? new MockProvider() : new ApiProvider(), [])
  const [session, setSession] = useState<Session | null>(null)
  const [view, setView] = useState<View>('overview')
  const [nodes, setNodes] = useState<Node[]>([])
  const [folders, setFolders] = useState<Folder[]>([])
  const [rules, setRules] = useState<Rule[]>([])
  const [executions, setExecutions] = useState<Execution[]>([])
  const [analytics, setAnalytics] = useState<AnalyticsSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [nextNodes, nextRules, nextExecutions, nextAnalytics] = await Promise.all([provider.listNodes(), provider.listRules(), provider.listExecutions(), provider.analytics()])
      const nextFolders = (await Promise.all(nextNodes.map((node) => provider.listFolders(node.id)))).flat()
      setNodes(nextNodes); setFolders(nextFolders); setRules(nextRules); setExecutions(nextExecutions); setAnalytics(nextAnalytics); setLastUpdated(new Date().toISOString())
    } catch (reason) {
      if (isProviderError(reason) && reason.status === 401) {
        setSession(null); setNodes([]); setFolders([]); setRules([]); setExecutions([]); setAnalytics(null); setLastUpdated(null)
      } else {
        setError(reason instanceof Error ? reason.message : 'Сервис недоступен')
      }
    } finally { setLoading(false) }
  }, [provider])

  useEffect(() => { if (session) void refresh() }, [session, refresh])

  if (!session) return <Login provider={provider} onLogin={setSession} />

  const logout = async () => { await provider.logout().catch(() => undefined); setSession(null); setNodes([]); setFolders([]); setRules([]); setExecutions([]) }
  const folderById = new Map(folders.map((folder) => [folder.id, folder]))
  const nodeById = new Map(nodes.map((node) => [node.id, node]))
  const activeRules = rules.filter((rule) => rule.enabled).length
  const unknownFolders = folders.filter((folder) => folder.is_stale || folder.actual_paused === null).length

  return <div className="app-shell"><aside className="sidebar"><div className="brand-row app-brand"><div className="brand-mark">S</div><span>Syncthing<br /><b>Control Center</b></span></div><div className="workspace-label">WORKSPACE <span>DEMO</span></div><nav>{navItems.map((item) => <button key={item.id} className={view === item.id ? 'nav-item active' : 'nav-item'} onClick={() => setView(item.id)}><span>{item.icon}</span>{item.label}{item.id === 'history' && executions.some((execution) => execution.status === 'unknown') && <i className="nav-alert" />}</button>)}</nav><div className="sidebar-bottom"><div className="connection-state"><span className="pulse" /><div><small>Подключение</small><strong>{mode === 'mock' ? 'Mock API' : 'Backend API'}</strong></div></div><div className="user-chip"><span className="avatar">{session.user.login.slice(0, 1).toUpperCase()}</span><div><strong>{session.user.login}</strong><small>Администратор</small></div><button aria-label="Выйти" onClick={logout}>↗</button></div></div></aside><main className="main-content"><header className="topbar"><div><span className="breadcrumb">Control Center <b>/</b> {navItems.find((item) => item.id === view)?.label}</span><h1>{navItems.find((item) => item.id === view)?.label}</h1></div><div className="top-actions"><span className={`mode-pill ${mode}`}><i />{mode === 'mock' ? 'MOCK MODE' : 'LIVE API'}</span><button className="icon-button" aria-label="Обновить" onClick={() => void refresh()} disabled={loading}>{loading ? '…' : '↻'}</button><button className="avatar avatar-top">{session.user.login.slice(0, 1).toUpperCase()}</button></div></header>{mode === 'mock' && <div className="mock-banner"><span>Демонстрационные данные</span> Интерфейс работает изолированно от Syncthing. Результаты mock-режима не подтверждают готовность интеграции.</div>}{error && <ErrorNotice message={error} onRetry={() => void refresh()} />}{loading && !nodes.length ? <StateCard title="Синхронизируем состояние">Получаем узлы, папки и историю…</StateCard> : <div className="view-content">{view === 'overview' && <Overview nodes={nodes} folders={folders} rules={rules} executions={executions} unknownFolders={unknownFolders} onNavigate={setView} nodeById={nodeById} folderById={folderById} />}{view === 'folders' && <Folders folders={folders} nodes={nodes} rules={rules} provider={provider} onRefresh={refresh} />}{view === 'rules' && <Rules folders={folders} rules={rules} provider={provider} onRefresh={refresh} />}{view === 'history' && <History executions={executions} nodeById={nodeById} folderById={folderById} />}{view === 'analytics' && <Analytics summary={analytics} />}</div>}<footer className="footer"><span>Последняя синхронизация интерфейса: {lastUpdated ? formatTime(lastUpdated) : '—'}</span><span>UTC · Данные хранятся на сервере</span></footer></main></div>
}

function Overview({ nodes, folders, rules, executions, unknownFolders, onNavigate, nodeById, folderById }: { nodes: Node[]; folders: Folder[]; rules: Rule[]; executions: Execution[]; unknownFolders: number; onNavigate: (view: View) => void; nodeById: Map<string, Node>; folderById: Map<string, Folder> }) {
  const latest = executions[0]
  return <><section className="hero-row"><div><span className="eyebrow">Состояние системы</span><h2>Добрый день, администратор <span>✦</span></h2><p className="muted">Здесь собраны актуальные наблюдения по вашим узлам и папкам.</p></div><button className="button button-secondary" onClick={() => onNavigate('rules')}>Настроить правило <span>→</span></button></section><section className="metric-grid"><Metric label="Узлы онлайн" value={`${nodes.filter((node) => node.api_status === 'ok').length}/${nodes.length}`} detail="Доступность API" icon="◉" tone="green" /><Metric label="Папки под контролем" value={folders.length} detail={`${unknownFolders ? `${unknownFolders} требуют внимания` : 'Все данные свежие'}`} icon="▣" tone={unknownFolders ? 'amber' : 'blue'} /><Metric label="Активные правила" value={rules.filter((rule) => rule.enabled).length} detail="По расписанию" icon="◫" tone="purple" /><Metric label="Подтверждение команд" value={latest ? statusLabel(latest.status) : 'Нет данных'} detail={latest ? formatTime(latest.created_at) : 'История пуста'} icon="✓" tone={latest?.status === 'applied' ? 'green' : 'amber'} /></section><div className="two-column"><section className="panel"><PanelHeader title="Узлы Syncthing" action="Все узлы" onClick={() => onNavigate('folders')} /><div className="node-list">{nodes.length ? nodes.map((node) => <div className="node-row" key={node.id}><div className="node-symbol">{node.display_name.slice(-1)}</div><div className="row-main"><strong>{node.display_name}</strong><span>{node.syncthing_device_id ?? 'Device ID не подтверждён'}</span></div><Badge value={node.api_status} /><span className="row-time">{formatTime(node.last_checked_at)}</span></div>) : <StateCard title="Нет подключённых узлов" kind="empty">Добавьте узел через backend, чтобы увидеть его здесь.</StateCard>}</div></section><section className="panel"><PanelHeader title="Последняя попытка" action="История" onClick={() => onNavigate('history')} />{latest ? <div className="execution-highlight"><div className="execution-icon">{latest.status === 'unknown' ? '?' : '✓'}</div><div className="row-main"><strong>{latest.source === 'rule' ? 'Команда по расписанию' : 'Ручное возобновление'}</strong><span>{folderById.get(latest.folder_id)?.label ?? 'Папка'} · {nodeById.get(latest.node_id)?.display_name ?? 'Узел'}</span></div><Badge value={latest.status} /></div> : <StateCard title="История пока пуста" kind="empty">Первая попытка появится после запуска исполнителя.</StateCard>}<div className="panel-divider" /><div className="small-summary"><span>Активные правила</span><strong>{rules.filter((rule) => rule.enabled).length}</strong><span>Папки с неизвестным состоянием</span><strong className={unknownFolders ? 'text-amber' : ''}>{unknownFolders}</strong></div></section></div></>
}

function Metric({ label, value, detail, icon, tone: color }: { label: string; value: string | number; detail: string; icon: string; tone: string }) { return <div className="metric-card"><div className={`metric-icon ${color}`}>{icon}</div><div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div></div> }
function PanelHeader({ title, action, onClick }: { title: string; action: string; onClick?: () => void }) { return <div className="panel-header"><h3>{title}</h3>{onClick && <button className="text-button" onClick={onClick}>{action} →</button>}</div> }

function Folders({ folders, nodes, rules, provider, onRefresh }: { folders: Folder[]; nodes: Node[]; rules: Rule[]; provider: Provider; onRefresh: () => Promise<void> }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const doResume = async (folder: Folder) => { setBusy(folder.id); setMessage(''); try { await provider.resume(folder.id); setMessage('Команда принята. Ожидайте подтверждения исполнителя.'); await onRefresh() } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Команда не принята') } finally { setBusy(null) } }
 return <><section className="hero-row compact"><div><span className="eyebrow">Наблюдаемые объекты</span><h2>Папки и их состояние</h2><p className="muted">Зелёный статус появляется только после подтверждённого наблюдения.</p></div><div className="filter-chip">Все узлы <span>⌄</span></div></section>{message && <div className="notice notice-info">{message}</div>}<section className="folder-grid">{folders.length ? folders.map((folder) => { const node = nodes.find((item) => item.id === folder.node_id); const rule = rules.find((item) => item.id === folder.active_rule_id); const stale = folder.is_stale || folder.actual_paused === null; return <article className="folder-card" key={folder.id}><div className="card-top"><div className="folder-icon">▦</div><Badge value={stale ? 'unknown' : folder.actual_paused ? 'pending' : 'ok'} label={stale ? 'Неизвестно' : folder.actual_paused ? 'Приостановлена' : 'Активна'} /></div><h3>{folder.label}</h3><span className="folder-id">{folder.syncthing_folder_id} · {node?.display_name ?? 'Неизвестный узел'}</span><div className="state-line"><span>Фактическое состояние</span><strong className={stale ? 'text-amber' : ''}>{stale ? 'Нет свежего наблюдения' : folder.actual_paused ? 'Paused' : 'Running'}</strong></div><div className="state-line"><span>Желаемое состояние</span><strong>{rule ? `${rule.enabled ? 'По расписанию' : 'Правило выключено'}` : 'Нет активного правила'}</strong></div><div className="card-foot"><span>{formatTime(folder.config_observed_at)}</span>{!rule && !stale && folder.actual_paused === true ? <button className="button button-small" onClick={() => void doResume(folder)} disabled={busy === folder.id}>{busy === folder.id ? 'Отправка…' : 'Возобновить'}</button> : <span className="lock-note">{rule ? 'Управляется правилом' : stale ? 'Нужно свежее наблюдение' : 'Действий нет'}</span>}</div></article> }) : <StateCard title="Папок пока нет" kind="empty">Когда A2 зарегистрирует узел, папки появятся здесь.</StateCard>}</section></>
}

function Rules({ folders, rules, provider, onRefresh }: { folders: Folder[]; rules: Rule[]; provider: Provider; onRefresh: () => Promise<void> }) {
  const [form, setForm] = useState<RuleInput>({ folder_id: folders[0]?.id ?? '', timezone: 'Europe/Moscow', days_of_week: [1, 2, 3, 4, 5], start_time: '09:00', end_time: '18:00', enabled: true })
  const [busy, setBusy] = useState(false); const [error, setError] = useState('')
  const create = async (event: React.FormEvent) => { event.preventDefault(); setBusy(true); setError(''); try { await provider.createRule(form); await onRefresh() } catch (reason) { setError(reason instanceof Error ? reason.message : 'Правило не создано') } finally { setBusy(false) } }
  const toggle = async (rule: Rule) => { setBusy(true); setError(''); try { await provider.setRuleEnabled(rule, !rule.enabled); await onRefresh() } catch (reason) { setError(reason instanceof Error ? reason.message : 'Версия правила устарела') } finally { setBusy(false) } }
  return <><section className="hero-row compact"><div><span className="eyebrow">Автоматизация</span><h2>Правила синхронизации</h2><p className="muted">Один активный интервал на папку. Отключение правила не возобновляет папку автоматически.</p></div></section>{error && <ErrorNotice message={error} />}<div className="two-column rules-layout"><section className="panel"><PanelHeader title="Активные правила" action="" />{rules.length ? <div className="rule-list">{rules.map((rule) => <div className="rule-row" key={rule.id}><div className={`rule-switch ${rule.enabled ? 'on' : ''}`}><button onClick={() => void toggle(rule)} disabled={busy} aria-label={rule.enabled ? 'Отключить правило' : 'Включить правило'}><span /></button></div><div className="row-main"><strong>{folders.find((folder) => folder.id === rule.folder_id)?.label ?? 'Неизвестная папка'}</strong><span>{rule.days_of_week.join(' · ')} день недели · {rule.start_time}—{rule.end_time} · {rule.timezone}</span></div><span className="version">v{rule.version}</span><Badge value={rule.enabled ? 'ok' : 'unknown'} label={rule.enabled ? 'Активно' : 'Выключено'} /></div>)}</div> : <StateCard title="Правил пока нет" kind="empty">Создайте первое правило справа.</StateCard>}</section><section className="panel form-panel"><PanelHeader title="Новое правило" action="" /><form onSubmit={create} className="form-stack"><label>Папка<select value={form.folder_id} onChange={(event) => setForm({ ...form, folder_id: event.target.value })} required><option value="">Выберите папку</option>{folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.label}</option>)}</select></label><div className="form-grid"><label>Начало<input type="time" value={form.start_time} onChange={(event) => setForm({ ...form, start_time: event.target.value })} /></label><label>Конец<input type="time" value={form.end_time} onChange={(event) => setForm({ ...form, end_time: event.target.value })} /></label></div><label>Часовой пояс<select value={form.timezone} onChange={(event) => setForm({ ...form, timezone: event.target.value })}><option>Europe/Moscow</option><option>UTC</option><option>Europe/Berlin</option></select></label><div><span className="field-label">Дни недели</span><div className="day-picker">{['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].map((day, index) => <button type="button" className={form.days_of_week.includes(index + 1) ? 'selected' : ''} key={day} onClick={() => setForm({ ...form, days_of_week: form.days_of_week.includes(index + 1) ? form.days_of_week.filter((item) => item !== index + 1) : [...form.days_of_week, index + 1].sort() })}>{day}</button>)}</div></div><button className="button button-primary button-wide" disabled={busy || !form.folder_id}>{busy ? 'Сохраняем…' : 'Создать правило'} <span>→</span></button></form></section></div></>
}

function History({ executions, nodeById, folderById }: { executions: Execution[]; nodeById: Map<string, Node>; folderById: Map<string, Folder> }) { return <><section className="hero-row compact"><div><span className="eyebrow">Аудит команд</span><h2>История выполнения</h2><p className="muted">HTTP-ответ сам по себе не считается подтверждением применения.</p></div><div className="filter-chip">Последние 30 дней <span>⌄</span></div></section><section className="panel history-panel">{executions.length ? <div className="history-table"><div className="history-head"><span>Команда</span><span>Папка и узел</span><span>Создана</span><span>Результат</span></div>{executions.map((execution) => <div className="history-row" key={execution.id}><div className="command-cell"><span className={`command-icon ${execution.status}`}>{execution.source === 'manual' ? '↗' : '◫'}</span><div><strong>{execution.source === 'manual' ? 'Ручное возобновление' : 'Правило расписания'}</strong><small>{execution.id.slice(0, 8)}…</small></div></div><div><strong>{folderById.get(execution.folder_id)?.label ?? 'Неизвестная папка'}</strong><small>{nodeById.get(execution.node_id)?.display_name ?? 'Неизвестный узел'}</small></div><span className="history-time">{formatTime(execution.created_at)}</span><div><Badge value={execution.status} />{execution.error_code && <small className="error-code">{execution.error_code}</small>}</div></div>)}</div> : <StateCard title="История пуста" kind="empty">Подтверждённые и неподтверждённые попытки появятся после запуска правил.</StateCard>}</section></> }

function Analytics({ summary }: { summary: AnalyticsSummary | null }) { if (!summary) return <StateCard title="Нет данных аналитики" kind="empty">Выберите период после появления наблюдений.</StateCard>; const ratio = summary.commands.success_ratio === null ? '—' : `${Math.round(summary.commands.success_ratio * 100)}%`; const apiRatio = summary.api_checks.success_ratio === null ? '—' : `${Math.round(summary.api_checks.success_ratio * 100)}%`; return <><section className="hero-row compact"><div><span className="eyebrow">Контроль качества</span><h2>Аналитика</h2><p className="muted">Период {formatTime(summary.from)} — {formatTime(summary.to)} · UTC</p></div><div className="filter-chip">Последние 24 часа <span>⌄</span></div></section><section className="analytics-grid"><div className="analytics-card accent-blue"><span>Подтверждение команд</span><strong>{ratio}</strong><small>{summary.commands.applied} применено · {summary.commands.failed} отказов · {summary.commands.unknown} неизвестно</small><div className="bar"><i style={{ width: `${summary.commands.success_ratio ? summary.commands.success_ratio * 100 : 0}%` }} /></div></div><div className="analytics-card accent-green"><span>Доступность API</span><strong>{apiRatio}</strong><small>{summary.api_checks.successful_checks} успешных из {summary.api_checks.total_checks} проверок</small><div className="bar"><i style={{ width: `${summary.api_checks.success_ratio ? summary.api_checks.success_ratio * 100 : 0}%` }} /></div></div></section><section className="panel insight-panel"><div className="insight-icon">✦</div><div><strong>Неизвестные состояния видны отдельно</strong><p>Система не засчитывает пропущенные наблюдения как ошибку или подтверждённый успех. Для точного вывода откройте историю команд.</p></div></section></> }

export default App
