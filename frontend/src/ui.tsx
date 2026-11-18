import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { isProviderError } from "./provider";
import type { Page } from "./types";

export function formatTime(
  value: string | null | undefined,
  timeStyle: Intl.DateTimeFormatOptions["timeStyle"] = "short",
) {
  if (!value) return "Нет наблюдения";
  const formatter = new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "medium",
    timeStyle,
    timeZone: "UTC",
  });
  return `${formatter.format(new Date(value))} UTC`;
}
export function statusLabel(value: string) {
  const labels: Record<string, string> = {
    ok: "Доступен",
    stale: "Устаревшее наблюдение",
    disabled: "Отключён",
    unknown: "Неизвестно",
    unreachable: "Недоступен",
    auth_error: "Ошибка доступа",
    invalid_response: "Некорректный ответ",
    identity_mismatch: "Смена идентичности",
    applied: "Подтверждено",
    failed: "Не применено",
    pending: "Ожидает подтверждения",
    cancelled: "Отменено",
  };
  return labels[value] ?? value;
}
export function Badge({
  value,
  label = statusLabel(value),
}: {
  value: string;
  label?: string;
}) {
  const tone = ["ok", "applied"].includes(value)
    ? "success"
    : ["unknown", "pending", "stale"].includes(value)
      ? "warning"
      : [
            "unreachable",
            "auth_error",
            "invalid_response",
            "identity_mismatch",
            "failed",
          ].includes(value)
        ? "danger"
        : "neutral";
  return (
    <span className={`badge badge-${tone}`}>
      <i aria-hidden="true" />
      {label}
    </span>
  );
}
export function ErrorNotice({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="notice notice-error" role="alert">
      <span className="notice-mark" aria-hidden="true">
        !
      </span>
      <div>
        <strong>Данные недоступны</strong>
        <p>{message}</p>
        {onRetry && (
          <button className="button button-small" onClick={onRetry}>
            Перечитать данные
          </button>
        )}
      </div>
    </div>
  );
}
export function StateCard({
  title,
  children,
  loading = false,
}: {
  title: string;
  children?: ReactNode;
  loading?: boolean;
}) {
  return (
    <div
      className={`state-card ${loading ? "state-loading" : "state-empty"}`}
      role={loading ? "status" : undefined}
    >
      <div className="state-icon" aria-hidden="true">
        {loading ? "…" : "∅"}
      </div>
      <strong>{title}</strong>
      {children && <p>{children}</p>}
    </div>
  );
}
export type Resource<T> = { data?: T; error: string; loading: boolean };
export type OnFailure = (error: unknown) => void;
export function useResource<T>(
  load: () => Promise<T>,
  revision: number,
  onFailure: OnFailure,
): Resource<T> {
  const [state, setState] = useState<Resource<T>>({ error: "", loading: true });
  useEffect(() => {
    let current = true;
    setState({ error: "", loading: true });
    load()
      .then((data) => {
        if (current) setState({ data, error: "", loading: false });
      })
      .catch((error) => {
        if (!current) return;
        if (isProviderError(error) && error.status === 401) onFailure(error);
        setState({
          error: error instanceof Error ? error.message : "Нет ответа сервера.",
          loading: false,
        });
      });
    return () => {
      current = false;
    };
  }, [load, revision, onFailure]);
  return state;
}
export function ResourceNotice({
  resource,
  onRetry,
}: {
  resource: Resource<unknown>;
  onRetry: () => void;
}) {
  if (resource.loading) return <StateCard title="Загружаем данные…" loading />;
  return resource.error ? (
    <ErrorNotice message={resource.error} onRetry={onRetry} />
  ) : null;
}
export function PanelHeader({ title }: { title: string }) {
  return (
    <div className="panel-header">
      <h3>{title}</h3>
    </div>
  );
}
export function Pagination({
  page: { offset, limit, items, total },
  onChange,
}: {
  page: Page<unknown>;
  onChange: (offset: number) => void;
}) {
  const end = offset + items.length;
  return (
    <div className="pagination">
      <button
        className="button button-small"
        disabled={!offset}
        onClick={() => onChange(Math.max(0, offset - limit))}
      >
        Назад
      </button>
      <span>{items.length ? `${offset + 1}–${end}` : "0"} из {total}</span>
      <button
        className="button button-small"
        disabled={end >= total}
        onClick={() => onChange(offset + limit)}
      >
        Далее
      </button>
    </div>
  );
}
