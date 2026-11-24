import { useSyncExternalStore, type AnchorHTMLAttributes } from "react";

function subscribe(fn: () => void) {
  window.addEventListener("popstate", fn);
  window.addEventListener("hashchange", fn);
  return () => {
    window.removeEventListener("popstate", fn);
    window.removeEventListener("hashchange", fn);
  };
}
export function useRoute() {
  const hash = useSyncExternalStore(subscribe, () => location.hash);
  const [path, search = ""] = (hash.slice(1) || "/overview").split("?");
  const [, section, id, sub] = path.split("/");
  return { path, section, id, sub, search, query: new URLSearchParams(search) };
}
export function navigate(
  path: string,
  options: { replace?: boolean; root?: boolean; focus?: string } = {},
) {
  const previous = location.hash.slice(1) || "/overview";
  const source = history.state ?? {};
  const inList = previous.split("?")[0] === "/folders";
  const origin = options.root
    ? undefined
    : inList
      ? { path: previous, y: scrollY, focus: options.focus, depth: 1 }
      : source.origin && {
          ...source.origin,
          depth: source.origin.depth + (options.replace ? 0 : 1),
        };
  if (inList && !options.replace)
    history.replaceState(
      { ...source, list: { y: scrollY, focus: options.focus } },
      "",
    );
  history[options.replace ? "replaceState" : "pushState"](
    { origin, previous },
    "",
    "#" + path,
  );
  window.dispatchEvent(new PopStateEvent("popstate"));
  if (!options.replace) window.scrollTo(0, 0);
}
export function useQueryUpdate() {
  const { path, query } = useRoute();
  return (changes: Record<string, string | number | undefined>) => {
    for (const [k, v] of Object.entries(changes)) {
      if (v === "" || v === undefined || v === 0) query.delete(k);
      else query.set(k, String(v));
    }
    const suffix = query.toString();
    history.replaceState(
      history.state,
      "",
      "#" + path + (suffix ? "?" + suffix : ""),
    );
    window.dispatchEvent(new PopStateEvent("popstate"));
  };
}
export function PageLink({
  to,
  root,
  focusKey,
  children,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement> & {
  to: string;
  root?: boolean;
  focusKey?: string;
}) {
  return (
    <a
      {...props}
      href={"#" + to}
      data-focus={focusKey}
      onClick={(e) => {
        if (e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey)
          return;
        e.preventDefault();
        navigate(to, { root, focus: focusKey });
      }}
    >
      {children}
    </a>
  );
}
export function BackToFolders() {
  const origin = history.state?.origin;
  return (
    origin && (
      <a
        className="return-link"
        href={"#" + origin.path}
        onClick={(e) => {
          if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
          e.preventDefault();
          history.go(-origin.depth);
        }}
      >
        ← К списку папок
      </a>
    )
  );
}
