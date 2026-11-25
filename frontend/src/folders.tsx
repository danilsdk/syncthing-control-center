import { useEffect, useRef, useState } from "react";
import type { Folder, Node, Rule, Provider, Execution } from "./types";
import {
  Badge,
  ErrorNotice,
  formatTime,
  StateCard,
  type OnFailure,
} from "./ui";
import {
  BackToFolders,
  PageLink,
  useQueryUpdate,
  useRoute,
  navigate,
} from "./navigation";
import {
  folderName,
  folderPath,
  observation,
  isFresh,
  FolderIcon,
  choices,
  useSettings,
  saveAppearance,
  rulePath,
  attemptPath,
  type Appearance,
} from "./identity";
import { Icon } from "./icons";
import { desired, dayNames } from "./overview";

export type WorkspaceData = {
  folders: Folder[];
  nodes: Node[];
  rules?: Rule[];
  ready: boolean;
  provider: Provider;
  onRefresh: () => void;
  onFailure: OnFailure;
  revision: number;
};
export function FolderCatalog({ folders, nodes, ready }: WorkspaceData) {
  const prefs = useSettings(),
    { query } = useRoute(),
    update = useQueryUpdate();
  const search = query.get("search") ?? "",
    node = query.get("node") ?? "",
    state = query.get("state") ?? "";
  const input = useRef<HTMLInputElement>(null),
    restored = useRef(false);
  useEffect(() => {
    const fn = (e: KeyboardEvent) => {
      if (
        e.key === "/" &&
        !e.ctrlKey &&
        !e.metaKey &&
        !(
          e.target instanceof HTMLElement &&
          e.target.closest("input,select,textarea,[contenteditable]")
        )
      ) {
        e.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, []);
  useEffect(() => {
    if (!ready || restored.current) return;
    const pos = history.state?.list;
    if (!pos) return;
    const frame = requestAnimationFrame(() => {
      restored.current = true;
      if (pos.focus)
        document
          .querySelector<HTMLElement>(
            '[data-focus="' + CSS.escape(pos.focus) + '"]',
          )
          ?.focus({ preventScroll: true });
      window.scrollTo(0, pos.y);
    });
    return () => cancelAnimationFrame(frame);
  }, [ready]);
  const groups = nodes
    .filter((n) => !node || n.id === node)
    .map((n) => ({
      n,
      items: folders.filter(
        (f) =>
          f.node_id === n.id &&
          (!state || (state === "fresh") === isFresh(f, n)) &&
          [
            folderName(f, prefs),
            f.label,
            f.syncthing_folder_id,
            n.display_name,
            n.syncthing_device_id,
          ]
            .join(" ")
            .toLocaleLowerCase()
            .includes(search.toLocaleLowerCase().trim()),
      ),
    }))
    .filter((g) => g.items.length);
  return (
    <>
      <div className="catalog-tools">
        <label className="searchbox">
          <Icon name="search" />
          <span className="only-reader">Найти папку</span>
          <input
            type="search"
            ref={input}
            placeholder="Имя папки, ID или узел"
            value={search}
            onChange={(e) => update({ search: e.target.value })}
          />
          <kbd aria-hidden="true">/</kbd>
        </label>
        <label>
          Узел
          <select
            value={node}
            onChange={(e) => update({ node: e.target.value })}
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
          Наблюдение
          <select
            value={state}
            onChange={(e) => update({ state: e.target.value })}
          >
            <option value="">Все состояния</option>
            <option value="stale">Без свежих данных</option>
            <option value="fresh">Свежие данные</option>
          </select>
        </label>
      </div>
      <div id="inventory">
        {groups.map(({ n, items }) => (
          <section key={n.id} className="catalog-group">
            <header>
              <h2>
                <Icon name="server" /> {n.display_name}
              </h2>
              <span className="mono">{items.length}</span>
            </header>
            <div className="catalog-head">
              <span>Папка</span>
              <span>Наблюдение</span>
              <span>По расписанию сейчас</span>
              <span />
            </div>
            {items.map((f) => (
              <div className="catalog-row" key={f.id}>
                <PageLink
                  to={folderPath(f)}
                  focusKey={f.id}
                  className="identity-link"
                >
                  <FolderIcon value={prefs.folders?.[f.id]} />
                  <span className="catalog-name">
                    <strong>{folderName(f, prefs)}</strong>
                    <small>{f.syncthing_folder_id}</small>
                  </span>
                </PageLink>
                <span data-fresh="">
                  <Badge
                    value={isFresh(f, n) ? "ok" : "unknown"}
                    label={observation(f, n)}
                  />
                </span>
                <span className="catalog-desired">
                  {f.active_rule_id ? desired(f.desired_paused) : "Без правила"}
                </span>
                <PageLink
                  className="iconbtn"
                  to={folderPath(f) + "/appearance"}
                  focusKey={f.id + "-appearance"}
                  data-tip="Имя и значок"
                  aria-label={"Имя и значок: " + folderName(f, prefs)}
                >
                  <Icon name="tune" />
                </PageLink>
              </div>
            ))}
          </section>
        ))}
      </div>
      {ready && !groups.length && (
        <StateCard
          title={
            folders.length ? "Папок по этим условиям нет" : "Папок пока нет"
          }
        >
          {folders.length > 0 && (
            <button
              className="text-link"
              onClick={() => update({ search: "", node: "", state: "" })}
            >
              Сбросить фильтры
            </button>
          )}
        </StateCard>
      )}
    </>
  );
}
export function FolderAppearance({ f }: { f: Folder }) {
  const prefs = useSettings(),
    [draft, setDraft] = useState<Appearance>(() => ({
      ...prefs.folders?.[f.id],
      label: folderName(f, prefs),
    })),
    [error, setError] = useState("");
  function leave() {
    if (
      history.state?.previous?.split("?")[0] === "/folders" ||
      history.state?.previous === folderPath(f)
    )
      history.back();
    else navigate(folderPath(f), { replace: true });
  }
  return (
    <>
      <BackToFolders />
      <div className="custom-layout">
        <form
          className="panel editor"
          onSubmit={(e) => {
            e.preventDefault();
            const label = draft.label?.trim(),
              symbol = draft.symbol?.trim();
            if (!label) return setError("Введите название.");
            if (
              symbol &&
              [
                ...new Intl.Segmenter("ru", {
                  granularity: "grapheme",
                }).segment(symbol),
              ].length !== 1
            )
              return setError("Введите один символ или эмодзи.");
            try {
              saveAppearance(f.id, { ...draft, label, symbol });
              leave();
            } catch {
              setError("Браузер не смог сохранить оформление.");
            }
          }}
        >
          <h2>Имя и значок</h2>
          <p className="hint">
            Настройки действуют в этом браузере. Folder ID —{" "}
            {f.syncthing_folder_id}.
          </p>
          <label>
            Название в панели
            <input
              required
              maxLength={64}
              value={draft.label ?? ""}
              onChange={(e) => setDraft({ ...draft, label: e.target.value })}
            />
          </label>
          <fieldset>
            <legend>Значок</legend>
            <div className="glyph-picker">
              {(Object.keys(choices) as (keyof typeof choices)[]).map((k) => (
                <label key={k} data-tip={choices[k]}>
                  <input
                    type="radio"
                    name="glyph"
                    aria-label={choices[k]}
                    checked={!draft.symbol && (draft.glyph ?? "folder") === k}
                    onChange={() =>
                      setDraft({ ...draft, glyph: k, symbol: "" })
                    }
                  />
                  <span>
                    <Icon name={k} />
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <label>
            Свой символ или эмодзи
            <input
              value={draft.symbol ?? ""}
              maxLength={32}
              onChange={(e) => setDraft({ ...draft, symbol: e.target.value })}
              placeholder="Например, 📚"
            />
          </label>
          {error && <p role="alert">{error}</p>}
          <div className="actions">
            <button className="btn primary">Сохранить оформление</button>
            <button className="quiet" type="button" onClick={leave}>
              Отмена
            </button>
          </div>
        </form>
        <aside className="identity-preview">
          <span className="panel-label">Предпросмотр</span>
          <div className="identity-link">
            <FolderIcon value={draft} />
            <div>
              <strong>{draft.label || f.label}</strong>
              <small>{f.syncthing_folder_id}</small>
            </div>
          </div>
        </aside>
      </div>
    </>
  );
}
export function FolderDetail({
  f,
  n,
  rules,
  provider,
  ready,
  onRefresh,
  onFailure,
}: { f: Folder; n?: Node } & WorkspaceData) {
  const prefs = useSettings(),
    r = rules?.find((x) => x.folder_id === f.id && x.enabled),
    related = r ?? rules?.find((x) => x.folder_id === f.id);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [attempt, setAttempt] = useState<Execution | null>(null),
    [poll, setPoll] = useState(0);
  const fresh = isFresh(f, n),
    canResume =
      ready &&
      !!rules &&
      fresh &&
      !r &&
      !f.active_rule_id &&
      f.actual_paused === true;
  useEffect(() => {
    if (!attempt || attempt.status !== "pending") return;
    let live = true;
    const timer = setTimeout(async () => {
      try {
        const record = await provider.getExecution(attempt.id);
        if (live) {
          setAttempt(record);
          setError("");
          if (record.status !== "pending") onRefresh();
        }
      } catch (e) {
        if (live) {
          onFailure(e);
          setError(e instanceof Error ? e.message : "Проверка недоступна.");
        }
      }
    }, 1500);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [attempt, provider, onFailure, onRefresh, poll]);
  async function resume() {
    if (!canResume || busy) return;
    setBusy(true);
    setError("");
    try {
      const a = await provider.resume(f.id);
      setAttempt({ ...a, status: "pending", verified_at: null });
      onRefresh();
    } catch (e) {
      onFailure(e);
      setError(e instanceof Error ? e.message : "Запрос не подтверждён.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <BackToFolders />
      <section className="dossier">
        <div className="dossier-identity">
          <FolderIcon value={prefs.folders?.[f.id]} />
          <div>
            <p className="panel-label">
              Папка · {n?.display_name ?? f.node_id}
            </p>
            <h1>{folderName(f, prefs)}</h1>
            <code>{f.syncthing_folder_id}</code>
          </div>
        </div>
        <div className="dossier-actions">
          <PageLink className="quiet" to={folderPath(f) + "/appearance"}>
            <Icon name="tune" />
            Имя и значок
          </PageLink>
          <button
            className="quiet"
            disabled={prefs.featured === f.id}
            onClick={() => {
              try {
                saveAppearance(f.id);
              } catch {
                setError("Не удалось сохранить выбор в браузере.");
              }
            }}
          >
            <Icon name="pin" />
            {prefs.featured === f.id ? "На обзоре" : "На обзор"}
          </button>
        </div>
      </section>
      <div className="dossier-grid">
        <section className="panel">
          <h2>Последнее наблюдение</h2>
          <Badge value={fresh ? "ok" : "unknown"} label={observation(f, n)} />
          <dl className="facts">
            {[
              [
                "Фактическая пауза",
                f.actual_paused === null
                  ? "Неизвестна"
                  : (f.actual_paused ? "Включена" : "Выключена") +
                    (fresh ? "" : " · устарело"),
              ],
              ["Конфигурация", formatTime(f.config_observed_at)],
              ["Синхронизация", f.runtime_state ?? "Неизвестно"],
              ["Состояние прочитано", formatTime(f.runtime_observed_at)],
              ["Осталось байт", f.need_bytes ?? "—"],
              ["Ошибки получения", f.pull_errors ?? "—"],
            ].map(([k, v]) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
          {canResume ? (
            <button
              className="btn primary"
              disabled={busy || attempt?.status === "pending"}
              onClick={resume}
            >
              Возобновить
            </button>
          ) : (
            <p className="hint">
              {!fresh
                ? "Для команды нужно свежее наблюдение."
                : r
                  ? "Для ручного возобновления отключите расписание."
                  : "Возобновление не требуется."}
            </p>
          )}
        </section>
        <section className="panel">
          <div className="panel-label">
            <Icon name="calendar" />
            Расписание
          </div>
          {related ? (
            <>
              <div className="large-time">
                {related.start_time.slice(0, 5)}
                <i>—</i>
                {related.end_time.slice(0, 5)}
              </div>
              <p>
                {dayNames(related.days_of_week)} · {related.timezone}
              </p>
              <p className="hint">
                {related.enabled
                  ? "По расписанию сейчас · " + desired(f.desired_paused)
                  : "Расписание выключено"}
              </p>
              <PageLink to={rulePath(related.id)} className="btn">
                Открыть правило <Icon name="diagonal" />
              </PageLink>
            </>
          ) : rules ? (
            <PageLink className="btn" to={"/rules/new?folder=" + f.id}>
              Создать правило
            </PageLink>
          ) : (
            <p>Правила недоступны</p>
          )}
          <PageLink
            className="text-link"
            to={"/history/commands?folder_id=" + f.id}
          >
            История папки →
          </PageLink>
        </section>
      </div>
      {error && (
        <ErrorNotice
          message={error}
          onRetry={() => {
            setPoll((x) => x + 1);
            onRefresh();
          }}
        />
      )}
      {attempt && (
        <section className="panel" role="status" aria-label="Результат команды">
          <Badge value={attempt.status} />
          <p>
            {attempt.status === "pending"
              ? "Ожидаем отдельной проверки исполнителя."
              : formatTime(attempt.verified_at)}
          </p>
          <PageLink className="text-link" to={attemptPath(attempt.id)}>
            Открыть попытку
          </PageLink>
        </section>
      )}
    </>
  );
}
