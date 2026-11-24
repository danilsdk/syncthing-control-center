import { useEffect, useRef, useState } from "react";
import type { Rule, RuleInput } from "./types";
import type { WorkspaceData } from "./folders";
import { BackToFolders, PageLink, navigate, useRoute } from "./navigation";
import { Badge, ErrorNotice, StateCard } from "./ui";
import {
  folderName,
  folderPath,
  FolderIcon,
  isFresh,
  rulePath,
  useSettings,
} from "./identity";
import { days, desired } from "./overview";
import { WeekBoard, NextWindows, WindowNote } from "./planner";
import { LatestAttempt } from "./records";
import { Icon } from "./icons";

const blank = (folder: string): RuleInput => ({
  folder_id: folder,
  timezone: "Europe/Moscow",
  days_of_week: [1, 2, 3, 4, 5],
  start_time: "09:00",
  end_time: "18:00",
  enabled: true,
});
const inputOf = (r: Rule): RuleInput => ({
  folder_id: r.folder_id,
  timezone: r.timezone,
  days_of_week: r.days_of_week,
  start_time: r.start_time.slice(0, 5),
  end_time: r.end_time.slice(0, 5),
  enabled: r.enabled,
});
export function Rules(props: WorkspaceData) {
  const route = useRoute(),
    prefs = useSettings(),
    items = props.rules ?? [];
  const rule =
    route.id && route.id !== "new"
      ? items.find((x) => x.id === route.id)
      : (items.find((x) => x.id === route.query.get("rule")) ??
        items.find((x) => x.enabled) ??
        items[0]);
  if (!props.rules) return null;
  if (route.id && route.id !== "new" && !rule)
    return (
      <StateCard title="Правило не найдено">
        <PageLink to="/rules">Все правила</PageLink>
      </StateCard>
    );
  const folder = props.folders.find((f) => f.id === rule?.folder_id);
  const create = route.id === "new" || (!rule && !route.id),
    editing = route.sub === "edit";
  return (
    <>
      <BackToFolders />
      {!create && rule && (
        <div className="planner-selection">
          <div className="identity">
            <FolderIcon value={prefs.folders?.[rule.folder_id]} />
            <div>
              <span className="panel-label">Правило папки</span>
              <h2>{folder ? folderName(folder, prefs) : rule.folder_id}</h2>
            </div>
            <Badge
              value={rule.enabled ? "ok" : "disabled"}
              label={rule.enabled ? "Включено" : "Выключено"}
            />
          </div>
          <div className="action-pair">
            {items.length > 1 && (
              <label>
                Выбрать правило
                <select
                  value={rule.id}
                  onChange={(e) => navigate(rulePath(e.target.value))}
                >
                  {items.map((r) => {
                    const f = props.folders.find((x) => x.id === r.folder_id);
                    return (
                      <option key={r.id} value={r.id}>
                        {f ? folderName(f, prefs) : r.folder_id} ·{" "}
                        {r.start_time.slice(0, 5)}–{r.end_time.slice(0, 5)} ·{" "}
                        {r.enabled ? "вкл." : "выкл."} · {r.id.slice(0, 8)}
                      </option>
                    );
                  })}
                </select>
              </label>
            )}
            <PageLink to="/rules/new" className="btn">
              <Icon name="add" />
              Создать правило
            </PageLink>
          </div>
        </div>
      )}
      {!items.length && route.id !== "new" && (
        <p className="hint">Правил пока нет</p>
      )}
      <RuleForm
        key={(create ? "new" : rule?.id) + (editing ? "-edit" : "")}
        {...props}
        selected={create ? undefined : rule}
        expanded={create || editing}
        initialFolder={route.query.get("folder") ?? ""}
      />
    </>
  );
}
function RuleForm(
  props: WorkspaceData & {
    selected?: Rule;
    expanded: boolean;
    initialFolder: string;
  },
) {
  const {
    selected,
    expanded,
    initialFolder,
    provider,
    onRefresh,
    onFailure,
    folders,
    nodes,
    ready,
  } = props;
  const prefs = useSettings(),
    [draft, setDraft] = useState<{ base?: Rule; value: RuleInput } | null>(
      null,
    ),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [ask, setAsk] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (ask) dialog.current?.showModal();
  }, [ask]);
  const form =
      draft?.value ?? (selected ? inputOf(selected) : blank(initialFolder)),
    base = draft?.base ?? selected;
  const f = folders.find((x) => x.id === form.folder_id),
    n = nodes.find((x) => x.id === f?.node_id);
  const update = (value: RuleInput) => {
    setDraft({ base, value });
    setError("");
    setMessage("");
  };
  const conflict =
    !!draft?.base && !!selected && draft.base.version !== selected.version;
  async function mutate(op: () => Promise<unknown>, success: string) {
    if (busy) return false;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await op();
      setMessage(success);
      onRefresh();
      return true;
    } catch (e) {
      onFailure(e);
      setError(e instanceof Error ? e.message : "Изменение не подтверждено.");
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!form.days_of_week.length)
      return setError("Выберите хотя бы один день недели.");
    if (!form.start_time || !form.end_time || form.start_time === form.end_time)
      return setError("Начало и конец окна должны различаться.");
    if (!form.timezone.trim()) return setError("Укажите часовой пояс.");
    try {
      new Intl.DateTimeFormat("ru", { timeZone: form.timezone });
    } catch {
      return setError("Укажите существующий часовой пояс IANA.");
    }
    let saved: Rule | undefined;
    if (
      await mutate(async () => {
        saved = base
          ? await provider.updateRule(base, form)
          : await provider.createRule(form);
      }, "Изменения сохранены.")
    ) {
      setDraft(null);
      if (saved && (expanded || !base))
        navigate(rulePath(saved.id), { replace: true });
    }
  }
  const windowInputs = (
    <div className="range-times">
      {(["start_time", "end_time"] as const).map((k, i) => (
        <label key={k}>
          {i ? "Конец окна" : "Начало окна"}
          <input
            type="time"
            required
            value={form[k]}
            onChange={(e) => update({ ...form, [k]: e.target.value })}
          />
        </label>
      ))}
    </div>
  );
  return (
    <>
      {message && (
        <div className="feedback" role="status">
          {message}
          {message === "Расписание отключено." && f && (
            <>
              {" "}
              {isFresh(f, n)
                ? f.actual_paused
                  ? "По последнему наблюдению папка на паузе."
                  : "По последнему наблюдению синхронизация разрешена."
                : "Текущее состояние папки неизвестно."}{" "}
              <PageLink to={folderPath(f)}>Открыть папки</PageLink>
            </>
          )}
        </div>
      )}
      {error && <ErrorNotice message={error} onRetry={onRefresh} />}
      <div className="planner-layout">
        <form className="panel quick-window editor" onSubmit={save}>
          <div className="planner-heading">
            <h2>
              {expanded
                ? selected
                  ? "Изменить правило"
                  : "Создать правило"
                : "Неделя синхронизации"}
            </h2>
            <span className="mono">{form.timezone}</span>
          </div>
          {expanded && (
            <>
              <label>
                Папка
                <select
                  required
                  value={form.folder_id}
                  disabled={!!selected}
                  onChange={(e) =>
                    update({ ...form, folder_id: e.target.value })
                  }
                >
                  <option value="">Выберите папку</option>
                  {folders.map((f) => (
                    <option key={f.id} value={f.id}>
                      {folderName(f, prefs)} ·{" "}
                      {nodes.find((n) => n.id === f.node_id)?.display_name ??
                        f.node_id}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Часовой пояс
                <input
                  required
                  list="zones"
                  value={form.timezone}
                  onChange={(e) =>
                    update({ ...form, timezone: e.target.value })
                  }
                />
              </label>
              <datalist id="zones">
                {["Europe/Moscow", "UTC", "Europe/Berlin"].map((z) => (
                  <option key={z}>{z}</option>
                ))}
              </datalist>
              <fieldset>
                <legend>Дни начала окна</legend>
                <div className="days">
                  {days.map((d, i) => (
                    <button
                      type="button"
                      key={d}
                      className="day-toggle"
                      aria-pressed={form.days_of_week.includes(i + 1)}
                      onClick={() =>
                        update({
                          ...form,
                          days_of_week: form.days_of_week.includes(i + 1)
                            ? form.days_of_week.filter((n) => n !== i + 1)
                            : [...form.days_of_week, i + 1].sort(),
                        })
                      }
                    >
                      {d}
                    </button>
                  ))}
                </div>
              </fieldset>
              <label className="switch-line">
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(e) =>
                    update({ ...form, enabled: e.target.checked })
                  }
                />{" "}
                Правило включено
              </label>
            </>
          )}
          {windowInputs}
          <WeekBoard form={form} update={update} disabled={busy} />
          <WindowNote form={form} />
          {conflict && (
            <div role="alert">
              На сервере версия {selected?.version} ·{" "}
              {selected?.start_time.slice(0, 5)}–
              {selected?.end_time.slice(0, 5)} · {selected?.timezone} ·{" "}
              {selected?.days_of_week.map((d) => days[d - 1]).join(", ")} ·{" "}
              {selected?.enabled ? "включено" : "выключено"}. Ваши значения
              сохранены в форме.{" "}
              <button
                type="button"
                className="text-link"
                onClick={() =>
                  setDraft({
                    value: expanded
                      ? form
                      : {
                          ...inputOf(selected!),
                          start_time: form.start_time,
                          end_time: form.end_time,
                        },
                    base: selected,
                  })
                }
              >
                Сверено, использовать эту версию
              </button>
            </div>
          )}
          <div className="planner-foot">
            <span className="hint">
              {draft
                ? "Есть несохранённые изменения"
                : selected
                  ? "Сохранено · версия " + selected.version
                  : "Новое правило"}
            </span>
            <div className="action-pair">
              {(draft || (expanded && selected)) && (
                <button
                  type="button"
                  className="quiet"
                  onClick={() => {
                    setDraft(null);
                    setError("");
                    if (expanded && selected)
                      navigate(rulePath(selected.id), { replace: true });
                  }}
                >
                  Отменить
                </button>
              )}
              <button
                className="btn primary"
                disabled={busy || !ready || conflict || (!expanded && !draft)}
              >
                {selected
                  ? expanded
                    ? "Сохранить изменения"
                    : "Сохранить окно"
                  : "Создать правило"}
              </button>
            </div>
          </div>
        </form>
        <aside className="planner-aside">
          <section className="panel">
            <h2>Ближайшие окна</h2>
            <NextWindows rule={form} />
          </section>
          {selected && (
            <>
              <section className="panel">
                <h2>Сейчас по правилу</h2>
                <p>
                  {selected.enabled && f
                    ? desired(f.desired_paused)
                    : "Расписание выключено"}
                </p>
                <p className="hint">
                  В этом окне расписание разрешает синхронизацию.
                </p>
                {f && (
                  <PageLink to={folderPath(f)} className="text-link">
                    Состояние папки →
                  </PageLink>
                )}
              </section>
              <LatestAttempt rule={selected} {...props} />
            </>
          )}
        </aside>
      </div>
      {selected && !expanded && (
        <div className="action-pair rule-controls">
          <PageLink to={rulePath(selected.id) + "/edit"} className="btn">
            Изменить
          </PageLink>
          <button
            className="quiet"
            disabled={busy || !!draft || !ready}
            onClick={() =>
              void mutate(
                () => provider.setRuleEnabled(selected, !selected.enabled),
                selected.enabled
                  ? "Расписание отключено."
                  : "Расписание включено.",
              )
            }
          >
            {selected.enabled ? "Отключить расписание" : "Включить расписание"}
          </button>
          <button
            className="quiet"
            disabled={busy || !ready}
            onClick={() => setAsk(true)}
          >
            Удалить
          </button>
        </div>
      )}
      {ask && selected && (
        <dialog
          ref={dialog}
          className="confirm-dialog"
          aria-labelledby="delete-heading"
          onCancel={() => setAsk(false)}
        >
          <h2 id="delete-heading">Удалить правило?</h2>
          <p>История попыток сохранится.</p>
          <div className="actions">
            <button autoFocus className="quiet" onClick={() => setAsk(false)}>
              Отмена
            </button>
            <button
              className="btn primary"
              disabled={busy}
              onClick={async () => {
                if (
                  await mutate(
                    () => provider.deleteRule(selected),
                    "Правило удалено.",
                  )
                ) {
                  setAsk(false);
                  navigate("/rules", { replace: true });
                } else setAsk(false);
              }}
            >
              Удалить правило
            </button>
          </div>
        </dialog>
      )}
    </>
  );
}
