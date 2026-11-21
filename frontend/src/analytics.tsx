import { useCallback, useState, type FormEvent } from "react";
import type { AnalyticsQuery, Node, Provider } from "./types";
import {
  formatTime,
  PanelHeader,
  ResourceNotice,
  useResource,
  type OnFailure,
} from "./ui";

const DAY_MS = 86_400_000;
const percentage = new Intl.NumberFormat("ru-RU", {
  style: "percent",
  maximumFractionDigits: 1,
});
const formatRatio = (ratio: number | null) =>
  ratio === null ? "Нет данных" : percentage.format(ratio);

function last24Hours(): AnalyticsQuery {
  const to = Math.floor(Date.now() / 60_000) * 60_000 + 60_000;
  return {
    from: new Date(to - DAY_MS).toISOString(),
    to: new Date(to).toISOString(),
  };
}

export function Analytics({
  provider,
  nodes,
  revision,
  onFailure,
}: {
  provider: Provider;
  nodes: Node[];
  revision: number;
  onFailure: OnFailure;
}) {
  const [query, setQuery] = useState(last24Hours);
  const [from, setFrom] = useState(() => query.from.slice(0, 16));
  const [to, setTo] = useState(() => query.to.slice(0, 16));
  const [nodeId, setNodeId] = useState("");
  const [source, setSource] = useState<AnalyticsQuery["source"]>();
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const load = useCallback(() => provider.analytics(query), [provider, query]);
  const resource = useResource(load, revision + retry, onFailure);
  const summary = resource.data;

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fromUtc = `${from}:00Z`;
    const toUtc = `${to}:00Z`;
    const duration = Date.parse(toUtc) - Date.parse(fromUtc);
    if (!(duration > 0 && duration <= 31 * DAY_MS)) {
      setError(
        "Начало должно быть раньше конца. Максимальный период — 31 сутки.",
      );
      return;
    }
    setError("");
    setQuery({
      from: fromUtc,
      to: toUtc,
      node_id: nodeId || undefined,
      source,
    });
  }

  function resetPeriod() {
    const period = last24Hours();
    setFrom(period.from.slice(0, 16));
    setTo(period.to.slice(0, 16));
    setError("");
    setQuery({ ...period, node_id: nodeId || undefined, source });
  }

  return (
    <>
      <section className="hero-row compact">
        <h2>Показатели синхронизации</h2>
      </section>
      <form className="history-filters form-stack" onSubmit={applyFilters}>
        <label>
          С (UTC)
          <input
            type="datetime-local"
            value={from}
            required
            onChange={(event) => setFrom(event.target.value)}
          />
        </label>
        <label>
          До (UTC, не включая)
          <input
            type="datetime-local"
            value={to}
            required
            onChange={(event) => setTo(event.target.value)}
          />
        </label>
        <label>
          Узел
          <select
            value={nodeId}
            onChange={(event) => setNodeId(event.target.value)}
          >
            <option value="">Все узлы</option>
            {nodes.map((node) => (
              <option key={node.id} value={node.id}>
                {node.display_name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Источник команд
          <select
            value={source ?? ""}
            onChange={(event) =>
              setSource(
                (event.target.value || undefined) as AnalyticsQuery["source"],
              )
            }
          >
            <option value="">Все команды</option>
            <option value="rule">По расписанию</option>
            <option value="manual">Ручные</option>
          </select>
        </label>
        <div className="analytics-actions">
          <button className="button button-primary" type="submit">
            Применить
          </button>
          <button
            className="button button-secondary"
            type="button"
            onClick={resetPeriod}
          >
            Последние 24 часа
          </button>
        </div>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </form>
      <ResourceNotice
        resource={resource}
        onRetry={() => setRetry((value) => value + 1)}
      />
      {summary && (
        <>
          <p className="muted analytics-period">
            {formatTime(summary.from)} — {formatTime(summary.to)}
          </p>
          <div className="analytics-grid">
            <section
              className="analytics-card"
              aria-label="Доля подтверждённых команд"
            >
              <span>Доля подтверждённых команд</span>
              <strong>{formatRatio(summary.commands.success_ratio)}</strong>
              <small>
                {summary.commands.applied} подтверждено ·{" "}
                {summary.commands.failed} отказов
              </small>
              <small>
                {summary.commands.unknown} неизвестно ·{" "}
                {summary.commands.cancelled} отменено ·{" "}
                {summary.commands.pending_created} в ожидании
              </small>
            </section>
            <section
              className="analytics-card"
              aria-label="Доля успешных проверок API"
            >
              <span>Доля успешных проверок API</span>
              <strong>{formatRatio(summary.api_checks.success_ratio)}</strong>
              <small>
                {summary.api_checks.successful_checks} успешных из{" "}
                {summary.api_checks.total_checks} проверок
              </small>
            </section>
          </div>
          <section className="panel history-panel analytics-daily spaced">
            <PanelHeader title="По дням (UTC)" />
            <div className="history-table analytics-table">
              <table aria-label="Дневные показатели UTC">
                <thead>
                  <tr>
                    <th scope="col">Дата</th>
                    <th scope="col">Команды</th>
                    <th scope="col">Проверки API</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.daily.map(({ date, commands, api_checks }) => (
                    <tr key={date}>
                      <th scope="row">
                        <time dateTime={date}>{date}</time>
                      </th>
                      <td>
                        <strong>{formatRatio(commands.success_ratio)}</strong>
                        <small>
                          {commands.applied} из{" "}
                          {commands.applied + commands.failed}
                        </small>
                      </td>
                      <td>
                        <strong>{formatRatio(api_checks.success_ratio)}</strong>
                        <small>
                          {api_checks.successful_checks} из{" "}
                          {api_checks.total_checks}
                        </small>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </>
  );
}
