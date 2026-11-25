import { useCallback, useState } from "react";
import type { Folder, Node, Provider, Rule } from "./types";
import {
  Badge,
  formatTime,
  ResourceNotice,
  StateCard,
  useResource,
  type OnFailure,
} from "./ui";
import { PageLink } from "./navigation";
import {
  useSettings,
  folderName,
  folderPath,
  FolderIcon,
  observation,
  isFresh,
  rulePath,
  attemptPath,
} from "./identity";
import { Icon } from "./icons";
import { eventName } from "./event-history";

export function PaperFront() {
  const papers = (
    <div className="paper-set" aria-hidden="true">
      <div className="paper sheet-back" />
      <div className="paper sheet-mid">
        <span />
      </div>
      <div className="paper sheet-front">
        <span />
      </div>
    </div>
  );
  return (
    <>
      {papers}
      <div className="front-rim" aria-hidden="true" />
      <div className="front-glass" aria-hidden="true">
        {papers}
      </div>
    </>
  );
}
export const days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
export function dayNames(list: number[]) {
  return list.join(",") === "1,2,3,4,5"
    ? "Пн–Пт"
    : list.map((x) => days[x - 1]).join(", ");
}
export function desired(value: boolean | null) {
  return value === null ? "Неизвестно" : value ? "Пауза" : "Разрешена";
}
export function Overview({
  nodes,
  folders,
  rules,
  provider,
  revision,
  onFailure,
  onRefresh,
  ready,
}: {
  nodes: Node[];
  folders: Folder[];
  rules?: Rule[];
  provider: Provider;
  revision: number;
  onFailure: OnFailure;
  onRefresh: () => void;
  ready: boolean;
}) {
  const prefs = useSettings(),
    f = folders.find((x) => x.id === prefs.featured) ?? folders[0],
    n = f ? nodes.find((x) => x.id === f.node_id) : nodes[0],
    r = rules?.find((x) => x.folder_id === f?.id && x.enabled);
  const [filter, setFilter] = useState("all");
  const commands = useResource(
    useCallback(() => provider.listExecutions({ limit: 3 }), [provider]),
    revision,
    onFailure,
  );
  const events = useResource(
    useCallback(() => provider.listEvents({ limit: 3 }), [provider]),
    revision,
    onFailure,
  );
  const records = [
    ...(commands.data?.items ?? []).map((a) => ({
      type: "command",
      id: a.id,
      at: a.created_at,
      folder: a.folder_id,
      title:
        (a.desired_paused ? "Приостановить" : "Возобновить") +
        (a.source === "rule" ? " по расписанию" : " вручную"),
      status: a.status,
      path: attemptPath(a.id),
    })),
    ...(events.data?.items ?? []).map((a) => ({
      type: "event",
      id: a.id,
      at: a.occurred_at ?? a.received_at,
      folder: a.folder_id,
      title: eventName(a.event_type),
      status: null,
      path: "/history/events/" + encodeURIComponent(a.id),
    })),
  ]
    .filter((x) => filter === "all" || x.type === filter)
    .sort((a, b) => Date.parse(b.at) - Date.parse(a.at))
    .slice(0, 4);
  return (
    <>
      <div className="deck">
        {f ? (
          <section className="folder-well">
            <PaperFront />
            <div className="folder-head">
              <span className="vcenter">
                <FolderIcon value={prefs.folders?.[f.id]} />
                {!ready
                  ? "Наблюдаемая папка"
                  : folders.length === 1
                    ? "Единственная папка"
                    : prefs.featured === f.id
                      ? "Закреплено на обзоре"
                      : "Первая папка в списке"}
              </span>
              <span className="mono">{f.syncthing_folder_id}</span>
            </div>
            <div className="folder-body">
              <div>
                <h2>{folderName(f, prefs)}</h2>
                <p className="folder-state">
                  <Badge
                    value={isFresh(f, n) ? "ok" : "unknown"}
                    label={observation(f, n)}
                  />
                </p>
              </div>
              <div className="folder-bottom">
                <dl>
                  <div>
                    <dt>Фактическая пауза</dt>
                    <dd>
                      {f.actual_paused === null
                        ? "Неизвестна"
                        : (f.actual_paused ? "Включена" : "Выключена") +
                          (isFresh(f, n) ? "" : " · устарело")}
                    </dd>
                  </div>
                  <div>
                    <dt>Синхронизация</dt>
                    <dd>
                      {f.runtime_state === "idle"
                        ? "Ожидание"
                        : (f.runtime_state ?? "Нет наблюдения")}
                    </dd>
                  </div>
                </dl>
                <PageLink className="small-action main" to={folderPath(f)}>
                  Состояние папки <Icon name="diagonal" />
                </PageLink>
              </div>
            </div>
          </section>
        ) : (
          <section className="panel">
            <StateCard
              title={ready ? "Папок пока нет" : "Ожидаем список папок"}
            />
          </section>
        )}
        <aside className="side-a">
          <section className="node-a">
            <div className="card-hat">
              <span className="vcenter">
                <Icon name="server" />
                Узел
              </span>
              {n && (
                <Badge
                  value={
                    !n.enabled
                      ? "disabled"
                      : n.is_stale
                        ? "stale"
                        : n.api_status
                  }
                />
              )}
            </div>
            <h2>{n?.display_name ?? "Нет подключённых узлов"}</h2>
            {n && (
              <div className="node-foot">
                <span className="mono" title={n.syncthing_device_id ?? n.id}>
                  {n.syncthing_device_id ?? n.id}
                </span>
                <span>{formatTime(n.last_checked_at)}</span>
              </div>
            )}
          </section>
          <section className="schedule-a">
            <div className="card-hat">
              <span className="vcenter">
                <Icon name="calendar" />
                Активное правило
              </span>
              {r && (
                <PageLink
                  to={rulePath(r.id)}
                  className="iconbtn"
                  aria-label="Открыть правило"
                  data-tip="Открыть правило"
                >
                  <Icon name="diagonal" />
                </PageLink>
              )}
            </div>
            {r ? (
              <>
                <h2 className="schedule-time">
                  {r.start_time.slice(0, 5)}
                  <span>—</span>
                  {r.end_time.slice(0, 5)}
                </h2>
                <p className="schedule-zone">
                  {dayNames(r.days_of_week)} ·{" "}
                  <span className="mono">{r.timezone}</span>
                </p>
                <div className="rule-now">
                  <span>Сейчас по правилу</span>
                  <strong>{desired(f.desired_paused)}</strong>
                </div>
              </>
            ) : (
              <>
                <h2>{rules ? "Без правила" : "Правила недоступны"}</h2>
                {f && rules && (
                  <PageLink
                    className="text-link"
                    to={"/rules/new?folder=" + f.id}
                  >
                    Создать правило →
                  </PageLink>
                )}
              </>
            )}
          </section>
        </aside>
      </div>
      <section className="log-a">
        <div className="log-head">
          <h2>
            Последние записи {folders.length > 1 && <small>Все папки</small>}
          </h2>
          <div className="filters">
            {[
              ["all", "Все"],
              ["command", "Команды"],
              ["event", "События"],
            ].map(([id, label]) => (
              <button
                key={id}
                aria-pressed={filter === id}
                onClick={() => setFilter(id)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <ResourceNotice resource={commands} onRetry={onRefresh} />
        <ResourceNotice resource={events} onRetry={onRefresh} />
        {records.length ? (
          <table className="record-table">
            <thead>
              <tr>
                <th>Время, UTC</th>
                <th>Запись</th>
                <th>Папка</th>
                <th>Результат</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {records.map((a) => {
                const obj = folders.find((x) => x.id === a.folder);
                return (
                  <tr key={a.type + a.id}>
                    <td className="mono">{formatTime(a.at)}</td>
                    <td>
                      {a.title}
                      <small>
                        {a.type === "event"
                          ? "Событие Syncthing"
                          : "Попытка команды"}
                      </small>
                    </td>
                    <td>{obj ? folderName(obj, prefs) : "—"}</td>
                    <td>{a.status ? <Badge value={a.status} /> : "—"}</td>
                    <td>
                      <PageLink
                        className="iconbtn"
                        to={a.path}
                        aria-label={"Открыть запись: " + a.title}
                      >
                        <Icon name="diagonal" />
                      </PageLink>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          !commands.loading &&
          !events.loading &&
          !commands.error &&
          !events.error && <p className="hint">Записей пока нет.</p>
        )}
      </section>
    </>
  );
}
