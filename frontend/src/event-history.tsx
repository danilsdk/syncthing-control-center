import { useCallback, useState } from "react";
import type { EventQuery, Folder, Node, Provider } from "./types";
import {
  formatTime,
  Pagination,
  ResourceNotice,
  StateCard,
  useResource,
} from "./ui";
import type { OnFailure } from "./ui";

const eventLabels = new Map([
  ["ItemStarted", "Начата обработка элемента"],
  ["ItemFinished", "Обработка элемента завершена"],
  ["StateChanged", "Изменилось состояние папки"],
  ["HistoryGap", "Пропуск в истории"],
  ["SourceRestarted", "Syncthing перезапущен"],
  ["SourceContinuityLost", "Непрерывность истории не подтверждена"],
]);

export function EventHistory({
  provider,
  nodes,
  folders,
  revision,
  onFailure,
  onRefresh,
}: {
  provider: Provider;
  nodes: Node[];
  folders: Folder[];
  revision: number;
  onFailure: OnFailure;
  onRefresh: () => void;
}) {
  const [query, setQuery] = useState<EventQuery>({});
  const load = useCallback(() => provider.listEvents(query), [provider, query]);
  const resource = useResource(load, revision, onFailure);
  const page = resource.data;
  const folderOptions = folders.filter(
    (folder) => !query.node_id || folder.node_id === query.node_id,
  );
  const nodeName = (id: string) =>
    nodes.find((node) => node.id === id)?.display_name ?? id;
  return (
    <>
      <section className="hero-row compact">
        <div>
          <h2>Последние события Syncthing</h2>
          <p className="muted">
            Для файловых событий журнал хранит тип, папку и время.
          </p>
        </div>
      </section>
      <div className="history-filters form-stack">
        <label>
          Узел
          <select
            value={query.node_id ?? ""}
            onChange={(event) => setQuery({ node_id: event.target.value })}
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
          Папка
          <select
            value={query.folder_id ?? ""}
            onChange={(event) =>
              setQuery({ ...query, folder_id: event.target.value, offset: 0 })
            }
          >
            <option value="">Все папки</option>
            {folderOptions.map((folder) => (
              <option key={folder.id} value={folder.id}>
                {folder.label || folder.syncthing_folder_id} ·{" "}
                {nodeName(folder.node_id)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <ResourceNotice resource={resource} onRetry={onRefresh} />
      {page && (
        <section className="panel history-panel">
          {page.items.length ? (
            <div
              className="history-table event-history-table"
              tabIndex={0}
              aria-label="Прокручиваемый журнал событий"
            >
              <table>
                <caption className="sr-only">События Syncthing</caption>
                <thead>
                  <tr>
                    <th scope="col">Событие</th>
                    <th scope="col">Папка и узел</th>
                    <th scope="col">Время (UTC)</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((event) => {
                    const folder = folders.find(
                      (item) => item.id === event.folder_id,
                    );
                    const { state, from_id, to_id } = event.payload_summary;
                    return (
                      <tr key={event.id}>
                        <td>
                          <strong>
                            {eventLabels.get(event.event_type) ?? event.event_type}
                          </strong>
                          {event.kind === "gap" && (
                            <small>Пропущены события {from_id}–{to_id}</small>
                          )}
                          {state && <small>Состояние папки · {state}</small>}
                        </td>
                        <td>
                          <strong>
                            {folder?.label ||
                              folder?.syncthing_folder_id ||
                              event.folder_id ||
                              "—"}
                          </strong>
                          <small>{nodeName(event.node_id)}</small>
                        </td>
                        <td>
                          {formatTime(event.occurred_at ?? event.received_at, "medium")}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <StateCard title="Событий пока нет">
              Для выбранных узла и папки журнал пуст.
            </StateCard>
          )}
          <Pagination
            page={page}
            onChange={(offset) => setQuery({ ...query, offset })}
          />
        </section>
      )}
    </>
  );
}
