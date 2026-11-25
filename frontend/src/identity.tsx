import { useSyncExternalStore } from "react";
import type { Folder, Node } from "./types";
import { Icon } from "./icons";

export const choices = {
  folder: "Папка",
  docs: "Документы",
  archive: "Архив",
  photo: "Фотографии",
  music: "Музыка",
  code: "Код",
  briefcase: "Проекты",
  book: "Библиотека",
};
export type Appearance = {
  label?: string;
  glyph?: keyof typeof choices;
  symbol?: string;
};
type Settings = { featured?: string; folders?: Record<string, Appearance> };
const key = "scc-folder-appearance-v1";
const changed = "scc-appearance";
function read() {
  try {
    return localStorage.getItem(key) || "{}";
  } catch {
    return "{}";
  }
}
function parse(value: string): Settings {
  try {
    return JSON.parse(value) || {};
  } catch {
    return {};
  }
}
function subscribe(fn: () => void) {
  window.addEventListener(changed, fn);
  window.addEventListener("storage", fn);
  return () => {
    window.removeEventListener(changed, fn);
    window.removeEventListener("storage", fn);
  };
}
export function useSettings() {
  return parse(useSyncExternalStore(subscribe, read));
}
export function saveAppearance(id: string, value?: Appearance) {
  const settings = parse(read());
  if (value) settings.folders = { ...settings.folders, [id]: value };
  else settings.featured = id;
  localStorage.setItem(key, JSON.stringify(settings));
  window.dispatchEvent(new Event(changed));
}
export function folderName(f: Folder, settings: Settings) {
  return settings.folders?.[f.id]?.label || f.label || f.syncthing_folder_id;
}
export function FolderIcon({ value = {} }: { value?: Appearance }) {
  return (
    <span
      className={"collection-mark mark-" + (value.glyph || "folder")}
      data-glyph={value.glyph || "folder"}
      aria-hidden="true"
    >
      {value.symbol ? (
        <span className="custom-symbol">{value.symbol}</span>
      ) : (
        <Icon name={value.glyph || "folder"} />
      )}
    </span>
  );
}
export function isFresh(f: Folder, n?: Node) {
  return !!(
    f.present &&
    !f.is_stale &&
    f.config_observed_at &&
    f.actual_paused !== null &&
    n?.enabled &&
    !n.is_stale &&
    n.api_status === "ok"
  );
}
export function observation(f: Folder, n?: Node) {
  return !f.present
    ? "Отсутствует у источника"
    : !isFresh(f, n)
      ? "Нет свежих данных"
      : f.actual_paused
        ? "Приостановлена"
        : "Синхронизация разрешена";
}
export const folderPath = (f: Folder) => "/folders/" + encodeURIComponent(f.id);
export const rulePath = (id: string) => "/rules/" + encodeURIComponent(id);
export const attemptPath = (id: string) =>
  "/history/commands/" + encodeURIComponent(id);
