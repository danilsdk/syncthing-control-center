import { useCallback, useEffect, useMemo, useState } from "react";
import { MockProvider } from "./mock";
import { ApiProvider, isProviderError } from "./provider";
import type { Folder, Provider, Session } from "./types";
import { ErrorNotice, ResourceNotice, StateCard, useResource } from "./ui";
import { Analytics, History } from "./screens";
import { Overview, PaperFront } from "./overview";
import { FolderCatalog, FolderAppearance, FolderDetail } from "./folders";
import { Rules } from "./rules";
import { RecordDetail } from "./records";
import { Icon } from "./icons";
import { Brand, Mast, sectionName } from "./chrome";
import { PageLink, navigate, useRoute } from "./navigation";

const mode = import.meta.env.VITE_DATA_MODE === "mock" ? "mock" : "api";
function Login({
  provider,
  onLogin,
  initialError,
}: {
  provider: Provider;
  onLogin: (session: Session) => void;
  initialError: string;
}) {
  const [login, setLogin] = useState(mode === "mock" ? "admin" : "");
  const [password, setPassword] = useState(
    mode === "mock" ? "demo-password" : "",
  );
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(initialError);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      onLogin(await provider.login(login, password));
      setPassword("");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Нет ответа сервера.",
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="scc objects auth">
      <main className="page-a">
        <div className="login-layout">
          <section className="folder-well" aria-hidden="true">
            <PaperFront />
            <div className="folder-head">
              <Icon name="folder" />
              Панель администратора
            </div>
            <div className="folder-body">
              <div className="login-wordmark">
                <span className="emblem" />
                <div>
                  Syncthing
                  <br />
                  Control Center
                </div>
              </div>
            </div>
          </section>
          <section className="login-panel">
            <Brand />
            <h1>Войти в систему</h1>
            <form onSubmit={submit} className="form-stack">
              <label>
                Логин
                <input
                  name="username"
                  autoComplete="username"
                  value={login}
                  onChange={(event) => setLogin(event.target.value)}
                  required
                  maxLength={64}
                />
              </label>
              <label>
                Пароль
                <span className="password">
                  <input
                    name="password"
                    type={show ? "text" : "password"}
                    autoComplete="current-password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    required
                    maxLength={1024}
                  />
                  <button
                    type="button"
                    aria-label={show ? "Скрыть пароль" : "Показать пароль"}
                    onClick={() => setShow(!show)}
                  >
                    <Icon name="eye" />
                  </button>
                </span>
              </label>
              {error && (
                <div className="form-error" role="alert">
                  {error}
                </div>
              )}
              <button className="btn primary" disabled={busy}>
                {busy ? "Проверяем…" : "Войти"}{" "}
                <span aria-hidden="true">→</span>
              </button>
            </form>
            <div className="login-foot">
              {mode === "mock"
                ? "Демонстрационные данные · пароль не проверяется"
                : "Вход администратора · Backend API"}
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
export default function App() {
  const route = useRoute();
  const provider = useMemo<Provider>(
    () => (mode === "mock" ? new MockProvider() : new ApiProvider()),
    [],
  );
  const [session, setSession] = useState<Session | null>(null);
  const [checking, setChecking] = useState(true);
  const [loginError, setLoginError] = useState("");
  const invalidate = useCallback((error: unknown) => {
    if (isProviderError(error) && error.status === 401) {
      setSession(null);
      setLoginError("Сессия завершена. Войдите снова.");
    }
  }, []);
  useEffect(() => {
    let current = true;
    provider
      .currentSession()
      .then((value) => {
        if (current) setSession(value);
      })
      .catch((error) => {
        if (current && !(isProviderError(error) && error.status === 401))
          setLoginError(
            error instanceof Error ? error.message : "Нет ответа сервера.",
          );
      })
      .finally(() => {
        if (current) setChecking(false);
      });
    return () => {
      current = false;
    };
  }, [provider]);
  useEffect(() => {
    if (!session) return;
    const expiry = setTimeout(
      () => {
        setSession(null);
        setLoginError("Срок сессии истёк. Войдите снова.");
      },
      Math.max(0, new Date(session.expires_at).getTime() - Date.now()),
    );
    return () => clearTimeout(expiry);
  }, [session]);
  useEffect(() => {
    if (checking) return;
    if (!session && route.section !== "login")
      navigate(
        "/login?next=" +
          encodeURIComponent(location.hash.slice(1) || "/overview"),
        { replace: true },
      );
    if (session && route.section === "login") {
      const next = route.query.get("next") ?? "/overview";
      navigate(
        /^\/(overview|folders|rules|history|analytics)([/?]|$)/.test(next)
          ? next
          : "/overview",
        { replace: true },
      );
    }
  }, [checking, session, route.path]);
  if (checking)
    return (
      <main className="session-check">
        <StateCard title="Проверяем сессию…" loading />
      </main>
    );
  if (!session)
    return (
      <Login
        provider={provider}
        initialError={loginError}
        onLogin={(value) => {
          setLoginError("");
          setSession(value);
        }}
      />
    );
  return (
    <Workspace
      key={session.csrf_token}
      provider={provider}
      session={session}
      onFailure={invalidate}
      onLogout={() => setSession(null)}
    />
  );
}
function Workspace({
  provider,
  session,
  onFailure,
  onLogout,
}: {
  provider: Provider;
  session: Session;
  onFailure: (error: unknown) => void;
  onLogout: () => void;
}) {
  const route = useRoute();
  const view = route.section;
  const [revision, setRevision] = useState(0);
  const [logoutError, setLogoutError] = useState("");
  const [loggingOut, setLoggingOut] = useState(false);
  const reload = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    const timer = setInterval(reload, 30_000);
    return () => clearInterval(timer);
  }, [reload]);
  const loadNodes = useCallback(() => provider.listNodes(), [provider]);
  const loadRules = useCallback(() => provider.listRules(), [provider]);
  const nodes = useResource(loadNodes, revision, onFailure, true);
  const rules = useResource(loadRules, revision, onFailure, true);
  const loadFolders = useCallback(async () => {
    if (!nodes.data) return { items: [] as Folder[], failures: [] as string[] };
    const pages = await Promise.all(
      nodes.data.map(async (node) => {
        try {
          return { items: await provider.listFolders(node.id), failure: "" };
        } catch (error) {
          if (isProviderError(error) && error.status === 401) throw error;
          return {
            items: [] as Folder[],
            failure: `${node.display_name}: ${error instanceof Error ? error.message : "Нет ответа"}`,
          };
        }
      }),
    );
    return {
      items: pages.flatMap((page) => page.items),
      failures: pages.map((page) => page.failure).filter(Boolean),
    };
  }, [provider, nodes.data]);
  const folders = useResource(loadFolders, revision, onFailure, true);
  useEffect(() => {
    document.title = sectionName(view) + " · Syncthing Control Center";
  }, [view]);
  useEffect(() => {
    if (route.path !== "/folders") {
      window.scrollTo(0, 0);
      document.getElementById("content")?.focus({ preventScroll: true });
    }
  }, [route.path]);
  const logout = async () => {
    setLoggingOut(true);
    setLogoutError("");
    try {
      await provider.logout();
      onLogout();
    } catch (error) {
      onFailure(error);
      setLogoutError(
        error instanceof Error
          ? error.message
          : "Выход не подтверждён сервером.",
      );
    } finally {
      setLoggingOut(false);
    }
  };
  const folderItems = folders.data?.items ?? [];
  const foldersReady =
    !nodes.loading &&
    !!nodes.data &&
    !folders.loading &&
    !!folders.data &&
    !folders.data.failures.length;
  const data = {
    provider,
    nodes: nodes.data ?? [],
    folders: folderItems,
    rules: rules.data,
    ready: foldersReady && !rules.loading && !rules.error,
    revision,
    onFailure,
    onRefresh: reload,
  };
  const heading =
    view === "folders" && route.sub === "appearance"
      ? "Оформление папки"
      : view === "history" && route.sub
        ? route.id === "events"
          ? "Событие"
          : "Попытка команды"
        : sectionName(view);
  return (
    <div className="scc objects">
      <a
        className="skip"
        href="#content"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("content")?.focus();
        }}
      >
        К содержимому
      </a>
      <Mast
        section={view}
        user={session.user.login}
        logout={() => void logout()}
        busy={loggingOut}
      />
      <main className="page-a" id="content" tabIndex={-1}>
        <header className="title-a">
          <h1>{heading}</h1>
          <div className="right-meta">
            <span className="source-label">
              {mode === "mock" ? "Демонстрационные данные" : "Backend API"}
            </span>
            <button
              className="iconbtn"
              data-tip="Перечитать данные"
              aria-label="Обновить"
              onClick={reload}
            >
              <Icon name="reset" />
            </button>
          </div>
        </header>
        {logoutError && <ErrorNotice message={logoutError} />}
        <ResourceNotice resource={nodes} onRetry={reload} />
        {view !== "analytics" && (
          <>
            <ResourceNotice resource={folders} onRetry={reload} />
            {folders.data?.failures.map((message) => (
              <ErrorNotice key={message} message={message} onRetry={reload} />
            ))}
          </>
        )}
        {["overview", "folders", "rules"].includes(view) && (
          <ResourceNotice resource={rules} onRetry={reload} />
        )}
        <div className="route-enter" key={route.path}>
          <WorkspaceScreen
            route={route}
            data={data}
            foldersReady={foldersReady}
          />
        </div>
      </main>
    </div>
  );
}

function WorkspaceScreen({
  route,
  data,
  foldersReady,
}: {
  route: ReturnType<typeof useRoute>;
  data: import("./folders").WorkspaceData;
  foldersReady: boolean;
}) {
  const missing = (
    <StateCard title="Страница не найдена">
      <PageLink to="/overview">На обзор</PageLink>
    </StateCard>
  );
  if (route.path.split("/").length > 4) return missing;
  switch (route.section) {
    case "overview":
      return route.id ? missing : <Overview {...data} />;
    case "analytics":
      return route.id ? missing : <Analytics {...data} />;
    case "rules":
      if (route.sub && (route.sub !== "edit" || route.id === "new"))
        return missing;
      return <Rules {...data} />;
    case "history":
      if (route.id && route.id !== "commands" && route.id !== "events")
        return missing;
      return route.sub ? (
        <RecordDetail {...data} id={route.sub} events={route.id === "events"} />
      ) : (
        <History {...data} />
      );
    case "folders": {
      if (route.sub && route.sub !== "appearance") return missing;
      if (!route.id) return <FolderCatalog {...data} />;
      const folder = data.folders.find((f) => f.id === route.id);
      if (!folder)
        return foldersReady ? (
          <StateCard title="Папка не найдена">
            <PageLink to="/folders">К списку папок</PageLink>
          </StateCard>
        ) : null;
      if (route.sub === "appearance") return <FolderAppearance f={folder} />;
      return (
        <FolderDetail
          {...data}
          f={folder}
          n={data.nodes.find((n) => n.id === folder.node_id)}
        />
      );
    }
    default:
      return missing;
  }
}
