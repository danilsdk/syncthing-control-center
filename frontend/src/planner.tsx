import { useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { days, dayNames } from "./overview";
import { Icon } from "./icons";
import type { RuleInput } from "./types";

const minute = (s: string) => {
  if (!/^\d\d:\d\d/.test(s)) return NaN;
  return Number(s.slice(0, 2)) * 60 + Number(s.slice(3, 5));
};
const clock = (m: number) =>
  [Math.floor(m / 60), m % 60].map((x) => String(x).padStart(2, "0")).join(":");
type Edge = "start_time" | "end_time";
export function WeekBoard({
  form,
  update,
  disabled = false,
}: {
  form: RuleInput;
  update: (input: RuleInput) => void;
  disabled?: boolean;
}) {
  const board = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState<{ edge: Edge; owner: number } | null>(
    null,
  );
  const drag = useRef<{
    edge: Edge;
    owner: number;
    rect: DOMRect;
    initial: RuleInput;
    pointer: number;
  } | null>(null);
  const start = minute(form.start_time),
    end = minute(form.end_time),
    valid = Number.isFinite(start + end) && start !== end;
  function change(edge: Edge, m: number) {
    if (m !== minute(form[edge === "end_time" ? "start_time" : "end_time"]))
      update({ ...form, [edge]: clock(m) });
  }
  function stop(e?: PointerEvent, undo = false) {
    const d = drag.current;
    if (!d || (e && d.pointer !== e.pointerId)) return;
    drag.current = null;
    setActive(null);
    if (board.current?.hasPointerCapture(d.pointer))
      board.current.releasePointerCapture(d.pointer);
    if (undo) update(d.initial);
    focus(d.edge, d.owner);
  }
  function focus(edge: Edge, owner: number) {
    requestAnimationFrame(() =>
      board.current
        ?.querySelector<HTMLElement>(
          '[data-edge="' + edge + '"][data-owner="' + owner + '"]',
        )
        ?.focus({ preventScroll: true }),
    );
  }
  function key(e: KeyboardEvent, edge: Edge, owner: number) {
    const step = e.shiftKey || e.key.startsWith("Page") ? 60 : 15;
    const dir: Record<string, number> = {
      ArrowRight: 1,
      ArrowUp: 1,
      ArrowLeft: -1,
      ArrowDown: -1,
      PageUp: 1,
      PageDown: -1,
    };
    if (!dir[e.key] && e.key !== "Home" && e.key !== "End") return;
    e.preventDefault();
    const n =
      e.key === "Home"
        ? 0
        : e.key === "End"
          ? 1439
          : (minute(form[edge]) + dir[e.key] * step + 1440) % 1440;
    const other = minute(
      form[edge === "start_time" ? "end_time" : "start_time"],
    );
    change(
      edge,
      n === other && dir[e.key] ? (n + dir[e.key] * step + 1440) % 1440 : n,
    );
    focus(edge, owner);
  }
  function grip(edge: Edge, at: number, owner: number) {
    const value = minute(form[edge]);
    return (
      <button
        type="button"
        key={edge + owner}
        className="window-grip"
        role="slider"
        disabled={disabled}
        data-owner={owner}
        data-edge={edge}
        data-drag={
          (active?.edge === edge && active.owner === owner) || undefined
        }
        aria-label={
          (edge === "start_time" ? "Начало окна, " : "Конец окна, ") +
          days[owner - 1]
        }
        aria-valuemin={0}
        aria-valuemax={1439}
        aria-valuenow={value}
        aria-valuetext={clock(value)}
        tabIndex={owner === form.days_of_week[0] ? 0 : -1}
        style={{ left: at / 14.4 + "%" }}
        onKeyDown={(e) => key(e, edge, owner)}
        onPointerDown={(e) => {
          if (e.button !== 0 || !board.current || drag.current) return;
          e.preventDefault();
          drag.current = {
            edge,
            owner,
            rect: e.currentTarget.parentElement!.getBoundingClientRect(),
            initial: { ...form },
            pointer: e.pointerId,
          };
          setActive({ edge, owner });
          board.current.setPointerCapture(e.pointerId);
          board.current.focus({ preventScroll: true });
        }}
      >
        <span />
        <b className="grip-time">{clock(value)}</b>
      </button>
    );
  }
  const segment = (a: number, b: number) =>
    b > a && (
      <i
        className="window-fill"
        style={{ left: a / 14.4 + "%", width: (b - a) / 14.4 + "%" }}
      />
    );
  return (
    <div
      className={"week-board" + (active ? " dragging" : "")}
      tabIndex={-1}
      ref={board}
      onPointerMove={(e) => {
        const d = drag.current;
        if (!d || d.pointer !== e.pointerId) return;
        const x = Math.min(
          1,
          Math.max(0, (e.clientX - d.rect.left) / d.rect.width),
        );
        const v = Math.round(x * 96) * 15;
        change(d.edge, v === 1440 ? (d.edge === "end_time" ? 0 : 1425) : v);
      }}
      onPointerUp={(e) => stop(e)}
      onPointerCancel={(e) => stop(e, true)}
      onLostPointerCapture={(e) => stop(e, true)}
      onKeyDown={(e) => {
        if (e.key === "Escape" && drag.current) {
          e.preventDefault();
          stop(undefined, true);
        }
      }}
    >
      <div className="planner-axis">
        {["00", "06", "12", "18", "24"].map((x) => (
          <span key={x}>{x}</span>
        ))}
      </div>
      <div className="week-grid">
        {days.map((d, i) => {
          const today = i + 1,
            prev = i || 7,
            overnight = end < start,
            has = valid && form.days_of_week.includes(today),
            tail =
              valid && overnight && end > 0 && form.days_of_week.includes(prev);
          return (
            <div
              key={d}
              className={"planner-row " + (has || tail ? "has-window" : "")}
            >
              <span>{d}</span>
              <div className="planner-track">
                {has && segment(start, overnight ? 1440 : end)}
                {tail && segment(0, end)}
                {has && grip("start_time", start, today)}
                {has &&
                  (!overnight || end === 0) &&
                  grip("end_time", overnight ? 1440 : end, today)}
                {tail && grip("end_time", end, prev)}
              </div>
            </div>
          );
        })}
      </div>
      <div className="week-legend">
        <span>Синхронизация разрешена</span>
        <span>Пауза</span>
      </div>
      <p className="board-instruction">
        <Icon name="move" />
        Потяните край полосы. Время общее для выбранных дней.
      </p>
    </div>
  );
}
const MINUTE_MS = 60_000;
const DAY_MS = 1_440 * MINUTE_MS;

function localClock(timeZone: string) {
  const formatter = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  return (instant: number) => {
    const parts = Object.fromEntries(
      formatter.formatToParts(instant).map(({ type, value }) => [type, value]),
    );
    return Date.UTC(
      +parts.year,
      +parts.month - 1,
      +parts.day,
      +parts.hour,
      +parts.minute,
    );
  };
}

function nextWindowOnDate(
  start: number,
  end: number,
  now: number,
  wallTime: ReturnType<typeof localClock>,
) {
  const early = start - DAY_MS;
  const late = end + DAY_MS;
  const earlyOffset = wallTime(early) - early;
  const lateOffset = wallTime(late) - late;
  if (earlyOffset === lateOffset) return start - earlyOffset > now;

  // Check real minutes around a clock change with the same half-open
  // local interval as B1, including a repeated hour or a skipped midnight.
  const first = start - Math.max(earlyOffset, lateOffset);
  const after = end - Math.min(earlyOffset, lateOffset);
  let wasInside = false;
  for (let instant = first; instant < after; instant += MINUTE_MS) {
    const local = wallTime(instant);
    const inside = local >= start && local < end;
    if (inside && !wasInside && instant > now) return true;
    wasInside = inside;
  }
  return false;
}

export function NextWindows({ rule }: { rule: RuleInput }) {
  const start = minute(rule.start_time);
  const end = minute(rule.end_time);
  const empty = <p className="hint">Нет запланированных окон.</p>;
  if (
    !rule.enabled ||
    !rule.days_of_week.length ||
    !Number.isFinite(start + end) ||
    start === end
  )
    return empty;

  let wallTime: ReturnType<typeof localClock>;
  try {
    wallTime = localClock(rule.timezone);
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
    return <p className="hint">Проверьте часовой пояс.</p>;
  }

  const now = Date.now();
  const localDate = Math.floor(wallTime(now) / DAY_MS) * DAY_MS;
  const overnight = end < start;
  const dates: Date[] = [];
  for (let i = overnight ? -1 : 0; i <= 28 && dates.length < 3; i++) {
    const midnight = localDate + i * DAY_MS;
    const date = new Date(midnight);
    if (!rule.days_of_week.includes(date.getUTCDay() || 7)) continue;
    const from = midnight + start * MINUTE_MS;
    const to = midnight + end * MINUTE_MS + (overnight ? DAY_MS : 0);
    if (nextWindowOnDate(from, to, now, wallTime)) dates.push(date);
  }
  if (!dates.length) return empty;
  return (
    <>
      <ol className="next-windows">
        {dates.map((d) => (
          <li key={d.getTime()}>
            <div className="day-stamp">{days[(d.getUTCDay() || 7) - 1]}</div>
            <div>
              <strong>
                {d.toLocaleDateString("ru", {
                  timeZone: "UTC",
                  day: "numeric",
                  month: "short",
                })}
              </strong>
              <small>
                {clock(start)} — {clock(end)}
                {overnight ? " · +1 день" : ""}
              </small>
            </div>
          </li>
        ))}
      </ol>
      <p className="hint">Время по расписанию · {rule.timezone}</p>
    </>
  );
}
export function WindowNote({ form }: { form: RuleInput }) {
  return (
    <p className="hint">
      {dayNames(form.days_of_week)} · {form.start_time}–{form.end_time}
      {form.end_time < form.start_time ? " следующего дня" : ""}
    </p>
  );
}
