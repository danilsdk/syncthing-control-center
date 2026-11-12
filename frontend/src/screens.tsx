import { useCallback, useEffect, useRef, useState } from "react";
import type { View } from "./App";
import type {
  Execution,
  ExecutionQuery,
  Folder,
  Node,
  Provider,
  Rule,
  RuleInput,
} from "./types";
import {
  Badge,
  ErrorNotice,
  formatTime,
  PanelHeader,
  ResourceNotice,
  StateCard,
  statusLabel,
  useResource,
} from "./ui";
import type { OnFailure } from "./ui";

export function Overview({
  nodes,
  folders,
  rules,
  latest,
  onNavigate,
}: {
  nodes?: Node[];
  folders?: Folder[];
  rules?: Rule[];
  latest?: Execution;
  onNavigate: (view: View) => void;
}) {
  const uncertain = folders?.filter(
    (folder) => folder.is_stale || folder.actual_paused === null,
  ).length;
  return (
    <>
      <section className="hero-row">
        <div>
          <span className="eyebrow">Состояние системы</span>
          <h2>Синхронизация под наблюдением</h2>
          <p className="muted">
            Конфигурация папок и подтверждение команд показаны отдельно.
          </p>
        </div>
        <button
          className="button button-secondary"
          onClick={() => onNavigate("rules")}
        >
          Настроить правило →
        </button>
      </section>
      <section className="metric-grid" aria-label="Сводка">
        <Metric
          label="Узлы доступны"
          value={
            nodes
              ? `${nodes.filter((node) => node.enabled && node.api_status === "ok" && !node.is_stale).length}/${nodes.length}`
              : "—"
          }
          detail="По свежим наблюдениям"
        />
        <Metric
          label="Папки"
          value={folders?.length ?? "—"}
          detail={
            uncertain === undefined
              ? "Нет полного наблюдения"
              : `${uncertain} без свежих данных`
          }
        />
        <Metric
          label="Активные правила"
          value={rules?.filter((rule) => rule.enabled).length ?? "—"}
          detail="Сохранённые расписания"
        />
        <Metric
          label="Последняя команда"
          value={latest ? statusLabel(latest.status) : "—"}
          detail={
            latest ? formatTime(latest.created_at) : "Нет загруженных попыток"
          }
        />
      </section>
      <div className="two-column">
        <section className="panel">
          <PanelHeader title="Узлы Syncthing" />
          {nodes?.length === 0 && (
            <StateCard title="Нет подключённых узлов">
              Зарегистрируйте подключения через API реестра.
            </StateCard>
          )}
          {nodes?.map((node) => (
            <article className="node-row" key={node.id}>
              <div className="node-symbol" aria-hidden="true">
                S
              </div>
              <div className="row-main">
                <strong>{node.display_name}</strong>
                <span title={node.syncthing_device_id ?? ""}>
                  {node.syncthing_device_id ?? "Device ID не подтверждён"}
                </span>
                <small>{formatTime(node.last_seen_at)}</small>
              </div>
              <Badge
                value={
                  !node.enabled
                    ? "disabled"
                    : node.api_status === "ok" && node.is_stale
                      ? "stale"
                      : node.api_status
                }
              />
            </article>
          ))}
        </section>
        <section className="panel">
          <PanelHeader title="Последняя попытка" />
          {latest ? (
            <>
              <Badge value={latest.status} />
              <p className="muted">
                {latest.source === "rule"
                  ? "По расписанию"
                  : "Ручное возобновление"}{" "}
                · цель:{" "}
                {latest.desired_paused ? "приостановить" : "возобновить"}
              </p>
              <p className="muted">
                Проверка: {formatTime(latest.verified_at)}
              </p>
              {latest.error_code && (
                <p className="error-code">{latest.error_code}</p>
              )}
            </>
          ) : (
            <StateCard title="Нет загруженных попыток">
              История появится после работы исполнителя.
            </StateCard>
          )}
          <button
            className="text-button spaced"
            onClick={() => onNavigate("history")}
          >
            Открыть историю →
          </button>
        </section>
      </div>
    </>
  );
}
function Metric({
  label,
  value,
  detail,
}: {
  label: string;
  value: string | number;
  detail: string;
}) {
  return (
    <div className="metric-card">
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{detail}</small>
      </div>
    </div>
  );
}
type Shared = {
  folders: Folder[];
  nodes: Node[];
  rules?: Rule[];
  ready: boolean;
  provider: Provider;
  onRefresh: () => void;
  onFailure: OnFailure;
};

export function Folders({
  folders,
  nodes,
  rules,
  ready,
  provider,
  onRefresh,
  onFailure,
}: Shared) {
  const [nodeFilter, setNodeFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState<{
    id: string;
    folderId: string;
    result?: Execution;
  } | null>(null);
  const result = attempt?.result;
  const status = result?.status ?? "pending";
  useEffect(() => {
    if (!attempt || status !== "pending") return;
    let current = true;
    const timer = setTimeout(() => {
      provider
        .getExecution(attempt.id)
        .then((value) => {
          if (!current) return;
          setAttempt({ ...attempt, result: value });
          setError("");
          if (value.status !== "pending") onRefresh();
        })
        .catch((reason) => {
          if (!current) return;
          onFailure(reason);
          setError(
            reason instanceof Error
              ? reason.message
              : "Проверка результата недоступна.",
          );
        });
    }, 1500);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [attempt, status, provider, onFailure, onRefresh]);
  const resume = async (folder: Folder) => {
    setBusy(true);
    setError("");
    try {
      const accepted = await provider.resume(folder.id);
      setAttempt({ id: accepted.id, folderId: folder.id });
      onRefresh();
    } catch (reason) {
      onFailure(reason);
      setError(
        reason instanceof Error
          ? reason.message
          : "Исход запроса неизвестен. Повторите с тем же намерением.",
      );
    } finally {
      setBusy(false);
    }
  };
  const items = folders.filter(
    (folder) => !nodeFilter || folder.node_id === nodeFilter,
  );
  return (
    <>
      <section className="hero-row compact">
        <div>
          <span className="eyebrow">Наблюдаемые объекты</span>
          <h2>Папки и их состояние</h2>
          <p className="muted">
            Отключённое расписание сохраняет текущую паузу папки.
          </p>
        </div>
        <label className="filter-label">
          Узел
          <select
            value={nodeFilter}
            onChange={(event) => setNodeFilter(event.target.value)}
          >
            <option value="">Все узлы</option>
            {nodes.map((node) => (
              <option key={node.id} value={node.id}>
                {node.display_name}
              </option>
            ))}
          </select>
        </label>
      </section>
      {error && (
        <ErrorNotice
          message={error}
          onRetry={() => {
            if (attempt && status === "pending") setAttempt({ ...attempt });
            onRefresh();
          }}
        />
      )}
      {attempt && (
        <div
          className="notice notice-info"
          role="status"
          aria-label="Результат команды"
        >
          <div>
            <Badge value={status} />
            <p>
              {status === "pending"
                ? "Намерение сохранено. Ожидаем отдельной проверки исполнителя."
                : `Результат попытки: ${statusLabel(status)}. Проверено: ${formatTime(result?.verified_at)}`}
            </p>
            {result?.error_code && <p>{result.error_code}</p>}
          </div>
        </div>
      )}
      {ready && items.length === 0 && (
        <StateCard title="Папок пока нет">
          Для этого узла нет сохранённых папок.
        </StateCard>
      )}
      <section className="folder-grid">
        {items.map((folder) => {
          const node = nodes.find((item) => item.id === folder.node_id);
          const stale =
            folder.is_stale ||
            folder.actual_paused === null ||
            !folder.config_observed_at ||
            !folder.present ||
            !node?.enabled;
          const active =
            !!folder.active_rule_id ||
            !!rules?.some(
              (rule) => rule.folder_id === folder.id && rule.enabled,
            );
          const canResume =
            ready &&
            !!rules &&
            !stale &&
            !active &&
            folder.actual_paused === true;
          return (
            <article className="folder-card" key={folder.id}>
              <div className="card-top">
                <span className="folder-icon" aria-hidden="true">
                  ▦
                </span>
                <Badge
                  value={
                    stale ? "unknown" : folder.actual_paused ? "pending" : "ok"
                  }
                  label={
                    !folder.present
                      ? "Отсутствует у источника"
                      : stale
                        ? "Нет свежих данных"
                        : folder.actual_paused
                          ? "Приостановлена"
                          : "Возобновлена"
                  }
                />
              </div>
              <h3>{folder.label || folder.syncthing_folder_id}</h3>
              <span className="folder-id">
                {folder.syncthing_folder_id} ·{" "}
                {node?.display_name ?? folder.node_id}
              </span>
              <div className="state-line">
                <span>Фактическая пауза</span>
                <strong>
                  {folder.actual_paused === null
                    ? "Неизвестна"
                    : folder.actual_paused
                      ? "Включена"
                      : "Выключена"}
                  {stale && folder.actual_paused !== null ? " · устарело" : ""}
                </strong>
              </div>
              <div className="state-line">
                <span>Цель расписания</span>
                <strong>
                  {folder.desired_paused === null
                    ? active
                      ? "Неизвестна"
                      : "Нет активного правила"
                    : folder.desired_paused
                      ? "Приостановить"
                      : "Возобновить"}
                </strong>
              </div>
              <div className="state-line">
                <span>Состояние синхронизации</span>
                <strong>{folder.runtime_state ?? "Неизвестно"}</strong>
              </div>
              <p className="muted runtime-time">
                Runtime: {formatTime(folder.runtime_observed_at)}
              </p>
              <div className="card-foot">
                <span>
                  Конфигурация: {formatTime(folder.config_observed_at)}
                </span>
                {canResume ? (
                  <button
                    className="button button-small"
                    onClick={() => void resume(folder)}
                    disabled={
                      busy ||
                      (attempt?.folderId === folder.id && status === "pending")
                    }
                  >
                    Возобновить
                  </button>
                ) : (
                  <span className="lock-note">
                    {active
                      ? "Управляется правилом"
                      : stale
                        ? "Нужно свежее наблюдение"
                        : !rules
                          ? "Правила недоступны"
                          : "Действий нет"}
                  </span>
                )}
              </div>
            </article>
          );
        })}
      </section>
    </>
  );
}
const weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const initialRule = (): RuleInput => ({
  folder_id: "",
  timezone: "Europe/Moscow",
  days_of_week: [1, 2, 3, 4, 5],
  start_time: "09:00",
  end_time: "18:00",
  enabled: true,
});
export function Rules({
  folders,
  nodes,
  rules,
  ready,
  provider,
  onRefresh,
  onFailure,
}: Shared) {
  const [form, setForm] = useState(initialRule);
  const [editing, setEditing] = useState<Rule | null>(null);
  const [deleting, setDeleting] = useState<Rule | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const folderLabel = (id: string) => {
    const folder = folders.find((item) => item.id === id);
    return folder
      ? `${folder.label || folder.syncthing_folder_id} · ${nodes.find((node) => node.id === folder.node_id)?.display_name ?? folder.node_id}`
      : id;
  };
  const action = async (operation: () => Promise<unknown>, success: string) => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await operation();
      setMessage(success);
      onRefresh();
      return true;
    } catch (reason) {
      onFailure(reason);
      setError(
        reason instanceof Error ? reason.message : "Изменение не подтверждено.",
      );
      return false;
    } finally {
      setBusy(false);
    }
  };
  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!form.days_of_week.length) {
      setError("Выберите хотя бы один день недели.");
      return;
    }
    if (form.start_time === form.end_time) {
      setError("Начало и конец окна должны различаться.");
      return;
    }
    try {
      new Intl.DateTimeFormat("ru", { timeZone: form.timezone });
    } catch {
      setError("Укажите существующий часовой пояс IANA.");
      return;
    }
    if (
      await action(
        () =>
          editing
            ? provider.updateRule(editing, form)
            : provider.createRule(form),
        "Расписание сохранено. Применение проверяется отдельно в истории.",
      )
    ) {
      setEditing(null);
      setForm(initialRule());
    }
  };
  return (
    <>
      <section className="hero-row compact">
        <div>
          <span className="eyebrow">Автоматизация</span>
          <h2>Правила синхронизации</h2>
          <p className="muted">
            Внутри окна синхронизация разрешена. Вне окна — пауза. Отключение
            правила не возобновляет папку.
          </p>
        </div>
      </section>
      {error && <ErrorNotice message={error} onRetry={onRefresh} />}
      {message && (
        <div className="notice notice-info" role="status">
          {message}
        </div>
      )}
      <div className="two-column rules-layout">
        <section className="panel">
          <PanelHeader title="Сохранённые правила" />
          {rules?.length === 0 && (
            <StateCard title="Правил пока нет">
              Выберите папку и создайте расписание.
            </StateCard>
          )}
          {rules?.map((rule) => (
            <article className="rule-entry" key={rule.id}>
              <div className="rule-row">
                <div className="row-main">
                  <strong>{folderLabel(rule.folder_id)}</strong>
                  <span>
                    {rule.days_of_week
                      .map((day) => weekdays[day - 1])
                      .join(", ")}{" "}
                    · {rule.start_time}—{rule.end_time}
                  </span>
                  <small>
                    {rule.timezone} · версия {rule.version}
                  </small>
                </div>
                <Badge
                  value={rule.enabled ? "ok" : "disabled"}
                  label={rule.enabled ? "Включено" : "Выключено"}
                />
              </div>
              <div className="rule-actions">
                <button
                  className="button button-small"
                  disabled={busy}
                  onClick={() =>
                    void action(
                      () => provider.setRuleEnabled(rule, !rule.enabled),
                      rule.enabled
                        ? "Правило отключено. Пауза папки сохранена."
                        : "Правило включено. Ожидайте проверки исполнителя.",
                    )
                  }
                >
                  {rule.enabled ? "Отключить правило" : "Включить правило"}
                </button>
                <button
                  className="text-button"
                  disabled={busy}
                  onClick={() => {
                    setEditing(rule);
                    setForm({ ...rule });
                    setError("");
                  }}
                >
                  Изменить
                </button>
                <button
                  className="text-button"
                  disabled={busy}
                  onClick={() => setDeleting(rule)}
                >
                  Удалить
                </button>
              </div>
            </article>
          ))}
        </section>
        <section className="panel form-panel">
          <PanelHeader
            title={
              editing
                ? `Изменить правило · v${editing.version}`
                : "Новое правило"
            }
          />
          <form onSubmit={save} className="form-stack">
            <label>
              Папка
              <select
                value={form.folder_id}
                disabled={!!editing}
                onChange={(event) =>
                  setForm({ ...form, folder_id: event.target.value })
                }
                required
              >
                <option value="">Выберите папку</option>
                {folders
                  .filter(
                    (folder) =>
                      folder.present || folder.id === editing?.folder_id,
                  )
                  .map((folder) => (
                    <option key={folder.id} value={folder.id}>
                      {folderLabel(folder.id)}
                    </option>
                  ))}
              </select>
            </label>
            <div className="form-grid">
              <label>
                Начало
                <input
                  type="time"
                  required
                  value={form.start_time}
                  onChange={(event) =>
                    setForm({ ...form, start_time: event.target.value })
                  }
                />
              </label>
              <label>
                Конец
                <input
                  type="time"
                  required
                  value={form.end_time}
                  onChange={(event) =>
                    setForm({ ...form, end_time: event.target.value })
                  }
                />
              </label>
            </div>
            <p className="muted">
              Если конец раньше начала, окно продолжается на следующий день.
            </p>
            <label>
              Часовой пояс
              <input
                list="timezones"
                required
                value={form.timezone}
                onChange={(event) =>
                  setForm({ ...form, timezone: event.target.value })
                }
                placeholder="Europe/Moscow"
              />
            </label>
            <datalist id="timezones">
              <option>Europe/Moscow</option>
              <option>UTC</option>
              <option>Europe/Berlin</option>
            </datalist>
            <fieldset className="day-field">
              <legend>Дни начала окна</legend>
              <div className="day-picker">
                {weekdays.map((day, index) => (
                  <button
                    type="button"
                    aria-pressed={form.days_of_week.includes(index + 1)}
                    className={
                      form.days_of_week.includes(index + 1) ? "selected" : ""
                    }
                    key={day}
                    onClick={() =>
                      setForm({
                        ...form,
                        days_of_week: form.days_of_week.includes(index + 1)
                          ? form.days_of_week.filter(
                              (item) => item !== index + 1,
                            )
                          : [...form.days_of_week, index + 1].sort(),
                      })
                    }
                  >
                    {day}
                  </button>
                ))}
              </div>
            </fieldset>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(event) =>
                  setForm({ ...form, enabled: event.target.checked })
                }
              />
              Расписание включено
            </label>
            <button
              className="button button-primary button-wide"
              disabled={busy || !ready || !rules || !form.folder_id}
            >
              {busy
                ? "Сохраняем…"
                : editing
                  ? "Сохранить изменения"
                  : "Создать правило"}
            </button>
            {editing && (
              <button
                type="button"
                className="button button-secondary"
                onClick={() => {
                  setEditing(null);
                  setForm(initialRule());
                }}
              >
                Отменить редактирование
              </button>
            )}
          </form>
        </section>
      </div>
      {deleting && (
        <ConfirmDialog onCancel={() => setDeleting(null)}>
          <h3 id="delete-heading">Удалить правило?</h3>
          <p className="muted">
            История сохранится. Текущая пауза папки не изменится.
          </p>
          <div className="rule-actions">
            <button
              className="button button-secondary"
              autoFocus
              disabled={busy}
              onClick={() => setDeleting(null)}
            >
              Отмена
            </button>
            <button
              className="button button-primary"
              disabled={busy}
              onClick={async () => {
                if (
                  await action(
                    () => provider.deleteRule(deleting),
                    "Правило удалено. История сохранена.",
                  )
                )
                  setDeleting(null);
              }}
            >
              Удалить правило
            </button>
          </div>
        </ConfirmDialog>
      )}
    </>
  );
}
function ConfirmDialog({
  children,
  onCancel,
}: {
  children: React.ReactNode;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return (
    <dialog
      className="panel confirm-dialog"
      ref={ref}
      aria-labelledby="delete-heading"
      onCancel={(event) => {
        event.preventDefault();
        onCancel();
      }}
    >
      {children}
    </dialog>
  );
}

export function History({
  provider,
  nodes,
  folders,
  revision,
  onFailure,
}: {
  provider: Provider;
  nodes: Node[];
  folders: Folder[];
  revision: number;
  onFailure: OnFailure;
}) {
  const [query, setQuery] = useState<ExecutionQuery>({ limit: 20, offset: 0 });
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [error, setError] = useState("");
  const [poll, setPoll] = useState(0);
  const load = useCallback(
    () => provider.listExecutions(query),
    [provider, query],
  );
  const resource = useResource(load, revision + poll, onFailure);
  useEffect(() => {
    if (!resource.data?.items.some((item) => item.status === "pending")) return;
    const timer = setTimeout(() => setPoll((value) => value + 1), 3000);
    return () => clearTimeout(timer);
  }, [resource.data]);
  const filter = (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    if (from && to && from >= to) {
      setError("Начало периода должно быть раньше конца.");
      return;
    }
    setQuery({
      ...query,
      offset: 0,
      from: from ? `${from}:00Z` : undefined,
      to: to ? `${to}:00Z` : undefined,
    });
  };
  const page = resource.data;
  return (
    <>
      <section className="hero-row compact">
        <div>
          <span className="eyebrow">Аудит команд</span>
          <h2>История выполнения</h2>
          <p className="muted">
            Успешная отправка и подтверждённое применение — разные этапы.
          </p>
        </div>
      </section>
      <form className="history-filters form-stack" onSubmit={filter}>
        <label>
          С (UTC)
          <input
            type="datetime-local"
            value={from}
            onChange={(event) => setFrom(event.target.value)}
          />
        </label>
        <label>
          До (UTC, не включая)
          <input
            type="datetime-local"
            value={to}
            onChange={(event) => setTo(event.target.value)}
          />
        </label>
        <label>
          Результат
          <select
            value={query.status ?? ""}
            onChange={(event) =>
              setQuery({
                ...query,
                offset: 0,
                status: event.target.value as ExecutionQuery["status"],
              })
            }
          >
            <option value="">Все результаты</option>
            {["pending", "applied", "failed", "unknown", "cancelled"].map(
              (status) => (
                <option key={status} value={status}>
                  {statusLabel(status)}
                </option>
              ),
            )}
          </select>
        </label>
        <label>
          Узел
          <select
            value={query.node_id ?? ""}
            onChange={(event) =>
              setQuery({ ...query, offset: 0, node_id: event.target.value })
            }
          >
            <option value="">Все узлы</option>
            {nodes.map((node) => (
              <option key={node.id} value={node.id}>
                {node.display_name}
              </option>
            ))}
          </select>
        </label>
        <button className="button button-secondary">Применить период</button>
      </form>
      {error && <ErrorNotice message={error} />}
      <ResourceNotice
        resource={resource}
        onRetry={() => setPoll((value) => value + 1)}
      />
      {page && (
        <section className="panel history-panel">
          {page.items.length ? (
            <div
              className="history-table"
              tabIndex={0}
              aria-label="Прокручиваемая история"
            >
              <table>
                <caption className="sr-only">Попытки применения команд</caption>
                <thead>
                  <tr>
                    <th scope="col">Команда</th>
                    <th scope="col">Папка и узел</th>
                    <th scope="col">Создана / проверена (UTC)</th>
                    <th scope="col">Результат</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((execution) => (
                    <tr key={execution.id}>
                      <td>
                        <strong>
                          {execution.source === "manual"
                            ? "Ручное возобновление"
                            : "Правило расписания"}
                        </strong>
                        <small>
                          {execution.desired_paused
                            ? "Цель: пауза"
                            : "Цель: возобновить"}
                        </small>
                        <small>{execution.id.slice(0, 8)}</small>
                      </td>
                      <td>
                        <strong>
                          {folders.find(
                            (folder) => folder.id === execution.folder_id,
                          )?.label ?? execution.folder_id}
                        </strong>
                        <small>
                          {nodes.find((node) => node.id === execution.node_id)
                            ?.display_name ?? execution.node_id}
                        </small>
                      </td>
                      <td>
                        {formatTime(execution.created_at)}
                        <small>
                          Проверка: {formatTime(execution.verified_at)}
                        </small>
                      </td>
                      <td>
                        <Badge value={execution.status} />
                        {execution.error_code && (
                          <small className="error-code">
                            {execution.error_code}
                          </small>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <StateCard title="Нет попыток за этот период">
              Измените фильтры или дождитесь работы исполнителя.
            </StateCard>
          )}
          <div className="pagination">
            <button
              className="button button-small"
              disabled={!page.offset}
              onClick={() =>
                setQuery({
                  ...query,
                  offset: Math.max(0, page.offset - page.limit),
                })
              }
            >
              Назад
            </button>
            <span>
              {page.total
                ? `${Math.min(page.offset + 1, page.total)}–${Math.min(page.offset + page.items.length, page.total)}`
                : "0"}{" "}
              из {page.total}
            </span>
            <button
              className="button button-small"
              disabled={page.offset + page.items.length >= page.total}
              onClick={() =>
                setQuery({ ...query, offset: page.offset + page.limit })
              }
            >
              Далее
            </button>
          </div>
        </section>
      )}
    </>
  );
}
export function Analytics({
  provider,
  revision,
  onFailure,
}: {
  provider: Provider;
  revision: number;
  onFailure: OnFailure;
}) {
  const load = useCallback(() => provider.analytics(), [provider]);
  const [retry, setRetry] = useState(0);
  const resource = useResource(load, revision + retry, onFailure);
  const summary = resource.data;
  return (
    <>
      <section className="hero-row compact">
        <div>
          <span className="eyebrow">Контроль качества</span>
          <h2>Аналитика за 24 часа</h2>
          <p className="muted">
            Показатели появятся после подключения API аналитики в B4.
          </p>
        </div>
      </section>
      <ResourceNotice
        resource={resource}
        onRetry={() => setRetry((value) => value + 1)}
      />
      {summary && (
        <>
          <p className="muted">
            {formatTime(summary.from)} — {formatTime(summary.to)}
          </p>
          <div className="analytics-grid">
            <div className="analytics-card">
              <span>Подтверждение команд</span>
              <strong>
                {summary.commands.success_ratio === null
                  ? "Нет данных"
                  : `${Math.round(summary.commands.success_ratio * 100)}%`}
              </strong>
              <small>
                {summary.commands.applied} применено · {summary.commands.failed}{" "}
                отказов · {summary.commands.unknown} неизвестно
              </small>
            </div>
            <div className="analytics-card">
              <span>Наблюдаемая доступность</span>
              <strong>
                {summary.api_checks.success_ratio === null
                  ? "Нет данных"
                  : `${Math.round(summary.api_checks.success_ratio * 100)}%`}
              </strong>
              <small>
                {summary.api_checks.successful_checks} успешных из{" "}
                {summary.api_checks.total_checks} проверок
              </small>
            </div>
          </div>
        </>
      )}
    </>
  );
}
