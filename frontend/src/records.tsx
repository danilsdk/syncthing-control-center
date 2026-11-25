import { useCallback, useEffect, useState } from "react";
import {
  Badge,
  formatTime,
  ResourceNotice,
  useResource,
  type OnFailure,
  StateCard,
} from "./ui";
import type {
  Page,
  Execution,
  SyncEvent,
  Rule,
  Provider,
  Folder,
} from "./types";
import { PageLink, BackToFolders } from "./navigation";
import {
  attemptPath,
  folderName,
  folderPath,
  rulePath,
  useSettings,
} from "./identity";
import { eventName } from "./event-history";
async function findPaged<T>(
  load: (offset: number) => Promise<Page<T>>,
  match: (item: T) => boolean,
  signal: AbortSignal,
) {
  for (let offset = 0; !signal.aborted; ) {
    const page = await load(offset);
    const item = page.items.find(match);
    if (item) return item;
    offset += page.items.length;
    if (!page.items.length || offset >= page.total) break;
  }
  return null;
}
type ServerProps = {
  provider: Provider;
  revision: number;
  onFailure: OnFailure;
  onRefresh: () => void;
};
export function LatestAttempt({
  rule,
  provider,
  revision,
  onFailure,
  onRefresh,
}: { rule: Rule } & ServerProps) {
  const load = useCallback(
    (signal: AbortSignal) =>
      findPaged(
        (offset) =>
          provider.listExecutions({
            folder_id: rule.folder_id,
            source: "rule",
            limit: 100,
            offset,
          }),
        (e) => e.rule_id === rule.id && e.folder_id === rule.folder_id,
        signal,
      ),
    [provider, rule.id, rule.folder_id],
  );
  const resource = useResource(load, revision, onFailure),
    a = resource.data;
  return (
    <section className="panel">
      <h2>Последняя попытка</h2>
      <ResourceNotice resource={resource} onRetry={onRefresh} />
      {a ? (
        <>
          <Badge value={a.status} />
          <p className="hint">
            {formatTime(a.created_at)} · версия {a.rule_version ?? "—"}
          </p>
          <PageLink className="text-link" to={attemptPath(a.id)}>
            Открыть попытку →
          </PageLink>
        </>
      ) : (
        resource.data === null && (
          <p className="hint">Попыток для этого правила пока нет.</p>
        )
      )}
    </section>
  );
}
export function RecordDetail({
  id,
  events,
  folders,
  provider,
  revision,
  onFailure,
  onRefresh,
}: { id: string; events: boolean; folders: Folder[] } & ServerProps) {
  const prefs = useSettings(),
    [poll, setPoll] = useState(0);
  const load = useCallback(
    (signal: AbortSignal): Promise<Execution | SyncEvent | null> =>
      events
        ? findPaged(
            (offset) => provider.listEvents({ offset, limit: 100 }),
            (e) => e.id === id,
            signal,
          )
        : provider.getExecution(id),
    [provider, id, events],
  );
  const resource = useResource(load, revision + poll, onFailure);
  const a = resource.data;
  useEffect(() => {
    if (a && "status" in a && a.status === "pending") {
      const timer = setTimeout(() => setPoll((p) => p + 1), 2000);
      return () => clearTimeout(timer);
    }
  }, [a]);
  const f = folders.find((f) => f.id === a?.folder_id);
  return (
    <>
      <BackToFolders />
      <PageLink
        className="crumb"
        to={events ? "/history/events" : "/history/commands"}
      >
        ← Журнал {events ? "событий" : "команд"}
      </PageLink>
      <ResourceNotice resource={resource} onRetry={onRefresh} />
      {a ? (
        <div className="dossier-grid">
          <section className="panel">
            <h2>
              {"status" in a ? "Попытка команды" : eventName(a.event_type)}
            </h2>
            {"status" in a ? (
              <>
                <Badge value={a.status} />
                <dl className="facts">
                  {[
                    [
                      "Цель",
                      a.desired_paused ? "Приостановить" : "Возобновить",
                    ],
                    ["Создана", formatTime(a.created_at)],
                    ["Отправлена", formatTime(a.sent_at)],
                    ["Проверена", formatTime(a.verified_at)],
                    [
                      "Наблюдаемая пауза",
                      a.observed_paused === null
                        ? "Неизвестна"
                        : a.observed_paused
                          ? "Включена"
                          : "Выключена",
                    ],
                    ["Завершена", formatTime(a.completed_at)],
                    ["Код результата", a.error_code ?? "—"],
                  ].map(([k, v]) => (
                    <div key={k}>
                      <dt>{k}</dt>
                      <dd>{v}</dd>
                    </div>
                  ))}
                </dl>
              </>
            ) : (
              <dl className="facts">
                {[
                  ["Время события", formatTime(a.occurred_at)],
                  ["Получено", formatTime(a.received_at)],
                  ["Тип", a.event_type],
                  ["Состояние", a.payload_summary.state ?? "—"],
                  ...(a.kind === "gap"
                    ? [
                        [
                          "Пропущены события",
                          a.payload_summary.from_id +
                            "–" +
                            a.payload_summary.to_id,
                        ],
                      ]
                    : []),
                ].map(([k, v]) => (
                  <div key={k}>
                    <dt>{k}</dt>
                    <dd>{v}</dd>
                  </div>
                ))}
              </dl>
            )}
          </section>
          <section className="panel">
            <h2>Объект</h2>
            {f ? (
              <PageLink className="text-link" to={folderPath(f)}>
                {folderName(f, prefs)} →
              </PageLink>
            ) : (
              <p>{a.folder_id ?? "Событие узла"}</p>
            )}
            <p className="hint">Узел · {a.node_id}</p>
            <p className="hint">Запись · {a.id}</p>
            {"rule_id" in a && a.rule_id && (
              <PageLink className="text-link" to={rulePath(a.rule_id)}>
                Правило · версия {a.rule_version ?? "—"} →
              </PageLink>
            )}
          </section>
        </div>
      ) : (
        resource.data === null && <StateCard title="Запись не найдена" />
      )}
    </>
  );
}
