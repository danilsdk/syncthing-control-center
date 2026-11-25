import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Badge,
  ResourceNotice,
  StateCard,
  formatTime,
  Pagination,
  useResource,
  statusLabel,
} from "./ui";
import {
  PageLink,
  useRoute,
  useQueryUpdate,
  BackToFolders,
} from "./navigation";
import type { Execution, SyncEvent, ExecutionQuery, Page } from "./types";
import type { WorkspaceData } from "./folders";
import { eventName } from "./event-history";
import { useSettings, folderName, attemptPath } from "./identity";
import { Icon } from "./icons";

function Dates({
  from,
  to,
  apply,
}: {
  from: string;
  to: string;
  apply: (from: string, to: string) => void;
}) {
  const [a, setA] = useState(from.slice(0, 16)),
    [b, setB] = useState(to.slice(0, 16)),
    [error, setError] = useState("");
  return (
    <form
      className="period-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (a && b && a >= b)
          return setError("Начало периода должно быть раньше конца.");
        setError("");
        apply(a ? a + ":00Z" : "", b ? b + ":00Z" : "");
      }}
    >
      <label>
        С (UTC)
        <input
          type="datetime-local"
          value={a}
          onChange={(e) => setA(e.target.value)}
        />
      </label>
      <label>
        До (UTC, не включая)
        <input
          type="datetime-local"
          value={b}
          onChange={(e) => setB(e.target.value)}
        />
      </label>
      <button className="btn">Применить период</button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
export function History({
  provider,
  nodes,
  folders,
  revision,
  onFailure,
  onRefresh,
}: WorkspaceData) {
  const { id, search } = useRoute(),
    events = id === "events",
    update = useQueryUpdate(),
    prefs = useSettings(),
    [poll, setPoll] = useState(0);
  const query = useMemo(() => {
    const q = new URLSearchParams(search);
    const x: ExecutionQuery = {
      limit: 20,
      offset: Math.max(0, Number(q.get("offset")) || 0),
    };
    for (const k of ["from", "to", "node_id", "folder_id"] as const)
      if (q.get(k)) x[k] = q.get(k)!;
    if (!events && q.get("status"))
      x.status = q.get("status") as ExecutionQuery["status"];
    return x;
  }, [search, events]);
  const load = useCallback(
    (): Promise<Page<Execution | SyncEvent>> =>
      events ? provider.listEvents(query) : provider.listExecutions(query),
    [provider, events, query],
  );
  const resource = useResource(load, revision + poll, onFailure),
    page = resource.data;
  useEffect(() => {
    if (!page?.items.some((a) => "status" in a && a.status === "pending"))
      return;
    const t = setTimeout(() => setPoll((x) => x + 1), 3000);
    return () => clearTimeout(t);
  }, [page]);
  return (
    <>
      <BackToFolders />
      <div className="tabs" aria-label="Раздел истории">
        {[
          ["commands", "Команды"],
          ["events", "События Syncthing"],
        ].map(([key, label]) => (
          <PageLink
            key={key}
            className="tab"
            aria-current={
              (events ? key === "events" : key === "commands")
                ? "page"
                : undefined
            }
            to={
              "/history/" +
              key +
              (search
                ? "?" +
                  new URLSearchParams(
                    Object.entries(query)
                      .filter(
                        ([k]) => !["status", "offset", "limit"].includes(k),
                      )
                      .map(([k, v]) => [k, String(v)]),
                  )
                : "")
            }
          >
            {label}
          </PageLink>
        ))}
      </div>
      <section className="panel audit-controls">
        <div className="filter-line">
          <label>
            Узел
            <select
              value={query.node_id ?? ""}
              onChange={(e) =>
                update({ node_id: e.target.value, folder_id: "", offset: 0 })
              }
            >
              <option value="">Все узлы</option>
              {nodes.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.display_name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Папка
            <select
              value={query.folder_id ?? ""}
              onChange={(e) => update({ folder_id: e.target.value, offset: 0 })}
            >
              <option value="">Все папки</option>
              {folders
                .filter((f) => !query.node_id || f.node_id === query.node_id)
                .map((f) => (
                  <option key={f.id} value={f.id}>
                    {folderName(f, prefs)} ·{" "}
                    {nodes.find((n) => n.id === f.node_id)?.display_name}
                  </option>
                ))}
            </select>
          </label>
          {!events && (
            <label>
              Результат
              <select
                value={query.status ?? ""}
                onChange={(e) => update({ status: e.target.value, offset: 0 })}
              >
                <option value="">Все результаты</option>
                {["pending", "applied", "failed", "unknown", "cancelled"].map(
                  (s) => (
                    <option key={s} value={s}>
                      {statusLabel(s)}
                    </option>
                  ),
                )}
              </select>
            </label>
          )}
        </div>
        <Dates
          key={[query.from, query.to].join()}
          from={query.from ?? ""}
          to={query.to ?? ""}
          apply={(from, to) => update({ from, to, offset: 0 })}
        />
      </section>
      <ResourceNotice resource={resource} onRetry={onRefresh} />
      {page && (
        <section className="panel history-panel">
          {page.items.length ? (
            <div
              className="history-table"
              aria-label={
                events
                  ? "Прокручиваемый журнал событий"
                  : "Прокручиваемый журнал команд"
              }
            >
              <table className="record-table">
                <caption className="only-reader">
                  {events ? "События Syncthing" : "Попытки команд"}
                </caption>
                <thead>
                  <tr>
                    <th>Время, UTC</th>
                    <th>{events ? "Событие" : "Команда"}</th>
                    <th>Папка и узел</th>
                    <th>{events ? "Тип" : "Результат"}</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((a) => {
                    const command = "status" in a,
                      f = folders.find((f) => f.id === a.folder_id),
                      n = nodes.find((n) => n.id === a.node_id);
                    return (
                      <tr key={a.id}>
                        <td className="mono">
                          {formatTime(
                            command
                              ? a.created_at
                              : (a.occurred_at ?? a.received_at),
                            "medium",
                          )}
                        </td>
                        <td>
                          <PageLink
                            to={
                              command
                                ? attemptPath(a.id)
                                : "/history/events/" + encodeURIComponent(a.id)
                            }
                          >
                            {command
                              ? a.source === "rule"
                                ? "По расписанию"
                                : "Вручную"
                              : eventName(a.event_type)}{" "}
                            <Icon name="diagonal" />
                          </PageLink>
                          {command ? (
                            <small>
                              Цель:{" "}
                              {a.desired_paused
                                ? "приостановить"
                                : "возобновить"}
                            </small>
                          ) : a.kind === "gap" ? (
                            <small>
                              Пропущены события {a.payload_summary.from_id}–
                              {a.payload_summary.to_id}
                            </small>
                          ) : (
                            a.payload_summary.state && (
                              <small>{a.payload_summary.state}</small>
                            )
                          )}
                        </td>
                        <td>
                          {f ? folderName(f, prefs) : (a.folder_id ?? "—")}
                          <small>{n?.display_name ?? a.node_id}</small>
                        </td>
                        <td>
                          {command ? (
                            <>
                              <Badge value={a.status} />
                              <small>
                                {a.error_code ??
                                  (a.verified_at
                                    ? formatTime(a.verified_at)
                                    : "Нет наблюдения")}
                              </small>
                            </>
                          ) : (
                            a.event_type
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <StateCard
              title={events ? "Событий пока нет" : "Нет попыток за этот период"}
            />
          )}
          <Pagination page={page} onChange={(offset) => update({ offset })} />
        </section>
      )}
    </>
  );
}
export function Analytics({
  nodes,
  provider,
  revision,
  onFailure,
  onRefresh,
}: WorkspaceData) {
  const { query } = useRoute(),
    update = useQueryUpdate(),
    from = query.get("from") ?? "",
    to = query.get("to") ?? "",
    node = query.get("node_id") ?? "";
  const period = query.get("period") ?? "24h";
  const load = useCallback(() => {
    const end = new Date();
    const span = period === "30d" ? 30 : period === "7d" ? 7 : 1;
    const start = new Date(end.getTime() - span * 86400000);
    return provider.analytics({
      from: from || start.toISOString(),
      to: to || end.toISOString(),
      node_id: node,
    });
  }, [provider, from, to, node, period]);
  const resource = useResource(load, revision, onFailure),
    s = resource.data;
  function indicator(value: number | null) {
    return (
      <>
        <div className="metric">
          {value === null ? "Нет данных" : Math.round(value * 100) + "%"}
        </div>
        <div
          className={"ratio " + (value === null ? "no-denominator" : "")}
          aria-hidden="true"
        >
          <i style={{ width: (value ?? 0) * 100 + "%" }} />
        </div>
      </>
    );
  }
  return (
    <>
      <div className="bar-head">
        <div className="tabs period-tabs" aria-label="Период">
          {[
            ["24h", "24 часа"],
            ["7d", "7 дней"],
            ["30d", "30 дней"],
          ].map(([id, label]) => (
            <button
              key={id}
              className="tab"
              aria-pressed={period === id}
              onClick={() =>
                update({ period: id, from: undefined, to: undefined })
              }
            >
              {label}
            </button>
          ))}
        </div>
        {s && (
          <span className="period-caption mono">
            {formatTime(s.from)} — {formatTime(s.to)}
          </span>
        )}
      </div>
      <details
        className="range-details"
        open={period === "custom" || undefined}
      >
        <summary>
          Период и узел <Icon name="down" />
        </summary>
        <div className="panel audit-controls">
          <label>
            Узел
            <select
              value={node}
              onChange={(e) => update({ node_id: e.target.value })}
            >
              <option value="">Все узлы</option>
              {nodes.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.display_name}
                </option>
              ))}
            </select>
          </label>
          <Dates
            key={from + to}
            from={from}
            to={to}
            apply={(a, b) => update({ from: a, to: b, period: "custom" })}
          />
        </div>
      </details>
      <ResourceNotice resource={resource} onRetry={onRefresh} />
      {s && (
        <>
          <div className="metric-grid">
            <section className="panel metric-card">
              <div className="panel-label">
                <Icon name="check" />
                Подтверждение команд
              </div>
              {indicator(s.commands.success_ratio)}
              <p>
                {s.commands.applied} из {s.commands.applied + s.commands.failed}{" "}
                попыток с определённым исходом
              </p>
              <dl className="facts">
                {(
                  [
                    "applied",
                    "failed",
                    "unknown",
                    "cancelled",
                    "pending",
                  ] as const
                ).map((k) => (
                  <div key={k}>
                    <dt>{statusLabel(k)}</dt>
                    <dd>
                      {k === "pending"
                        ? s.commands.pending_created
                        : s.commands[k]}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
            <section className="panel metric-card">
              <div className="panel-label">
                <Icon name="pulse" />
                Наблюдаемая доступность
              </div>
              {indicator(s.api_checks.success_ratio)}
              <p>
                {s.api_checks.successful_checks} из {s.api_checks.total_checks}{" "}
                проверок API
              </p>
              <dl className="facts">
                <div>
                  <dt>Успешные проверки</dt>
                  <dd>{s.api_checks.successful_checks}</dd>
                </div>
                <div>
                  <dt>Неуспешные проверки</dt>
                  <dd>
                    {s.api_checks.total_checks - s.api_checks.successful_checks}
                  </dd>
                </div>
              </dl>
            </section>
          </div>
          <details className="explain">
            <summary>
              Как считаются показатели <Icon name="down" />
            </summary>
            <div className="explain-body">
              <p>
                Для команд учитываются подтверждённые и неудачные попытки по
                времени завершения. Ожидающие учитываются по времени создания.
              </p>
              <p>
                Доступность — доля успешных проверок узла за выбранный период.
                При нулевом знаменателе показано «Нет данных».
              </p>
            </div>
          </details>
        </>
      )}
    </>
  );
}
