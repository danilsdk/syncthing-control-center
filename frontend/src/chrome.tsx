import { useLayoutEffect, useRef } from "react";
import { Icon } from "./icons";
import { PageLink } from "./navigation";
const items = [
  ["overview", "Обзор", "home"],
  ["folders", "Папки", "folder"],
  ["rules", "Правила", "calendar"],
  ["history", "История", "history"],
  ["analytics", "Аналитика", "chart"],
] as const;
export const sectionName = (key: string) =>
  items.find((x) => x[0] === key)?.[1] ?? "Страница не найдена";
export function Brand() {
  return (
    <div className="brand">
      <span className="emblem" aria-hidden="true" />
      Syncthing <span className="brand-sub">Control Center</span>
    </div>
  );
}
export function Mast({
  section,
  user,
  logout,
  busy,
}: {
  section: string;
  user: string;
  logout: () => void;
  busy: boolean;
}) {
  const nav = useRef<HTMLElement>(null),
    glass = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    const root = nav.current!,
      surface = glass.current!,
      selected = root.querySelector<HTMLElement>("[aria-current]");
    if (!selected) {
      surface.hidden = true;
      return;
    }
    function place() {
      if (!selected) return;
      surface.hidden = false;
      Object.assign(surface.style, {
        transform: "translateX(" + selected.offsetLeft + "px)",
        width: selected.offsetWidth + "px",
        height: selected.offsetHeight + "px",
        top: selected.offsetTop + "px",
      });
    }
    place();
    const ro = new ResizeObserver(place);
    ro.observe(root);
    ro.observe(selected);
    return () => ro.disconnect();
  }, [section]);
  return (
    <header className="mast">
      <Brand />
      <nav ref={nav} aria-label="Разделы">
        <i ref={glass} className="nav-glass" aria-hidden="true" hidden />
        {items.map(([id, label, icon]) => (
          <PageLink
            root
            key={id}
            to={id === "history" ? "/history/commands" : "/" + id}
            aria-current={section === id ? "page" : undefined}
          >
            <Icon name={icon} />
            <span>{label}</span>
          </PageLink>
        ))}
      </nav>
      <div className="operator">
        <span className="avatar">{user[0]?.toUpperCase()}</span>
        <span>{user}</span>
        <button
          className="iconbtn"
          aria-label="Выйти"
          data-tip="Выйти"
          onClick={logout}
          disabled={busy}
        >
          <Icon name="diagonal" />
        </button>
      </div>
    </header>
  );
}
