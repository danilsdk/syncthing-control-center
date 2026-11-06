import { useCallback, useEffect, useMemo, useState } from "react";
import { MockProvider } from "./mock";
import { ApiProvider, isProviderError } from "./provider";
import type { Folder, Provider, Session } from "./types";
import { ErrorNotice, ResourceNotice, StateCard, useResource } from "./ui";
import { Analytics, Folders, History, Overview, Rules } from "./screens";

export type View = "overview" | "folders" | "rules" | "history" | "analytics";
const navItems: { id: View; label: string; icon: string }[] = [
  { id: "overview", label: "Обзор", icon: "◒" },
  { id: "folders", label: "Папки", icon: "▣" },
  { id: "rules", label: "Правила", icon: "◫" },
  { id: "history", label: "История", icon: "◷" },
  { id: "analytics", label: "Аналитика", icon: "↗" },
];
const mode = import.meta.env.VITE_DATA_MODE === "mock" ? "mock" : "api";
function currentView(): View {
  const name = window.location.hash.replace("#/", "");
  return navItems.find((item) => item.id === name)?.id ?? "overview";
}
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
    <main className="login-page">
      <div className="login-art">
        <div className="brand-mark large" aria-hidden="true">
          S
        </div>
        <span className="eyebrow">Syncthing Control Center</span>
        <h1>
          Ваши папки.
          <br />
          Ваше расписание.
        </h1>
        <p>
          Состояние узлов, расписания и история подтверждённых команд в одной
          панели.
        </p>
        <div className="login-orbit" aria-hidden="true">
          <i />
          <i />
          <i />
        </div>
      </div>
      <section className="login-panel" aria-labelledby="login-heading">
        <div className="brand-row">
          <div className="brand-mark" aria-hidden="true">
            S
          </div>
          <span>
            Syncthing
            <br />
            <b>Control Center</b>
          </span>
        </div>
        <div className="login-copy">
          <h2 id="login-heading">Войти в систему</h2>
          <p>Учётная запись администратора</p>
        </div>
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
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
              maxLength={1024}
            />
          </label>
          {error && (
            <div className="form-error" role="alert">
              {error}
            </div>
          )}
          <button className="button button-primary button-wide" disabled={busy}>
            {busy ? "Проверяем…" : "Войти"} <span aria-hidden="true">→</span>
          </button>
        </form>
        <div className="login-foot">
          {mode === "mock"
            ? "Демонстрационные данные · пароль не проверяется"
            : "Вход администратора · Backend API"}
        </div>
      </section>
    </main>
  );
}
export default function App() {
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
  const [view, setView] = useState<View>(currentView);
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
  const nodes = useResource(loadNodes, revision, onFailure);
  const rules = useResource(loadRules, revision, onFailure);
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
  const folders = useResource(loadFolders, revision, onFailure);
  const loadLatest = useCallback(
    () => provider.listExecutions({ limit: 1 }),
    [provider],
  );
  const latest = useResource(loadLatest, revision, onFailure);
  useEffect(() => {
    const update = () => setView(currentView());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  useEffect(() => {
    document.title = `${navItems.find((item) => item.id === view)?.label} · Syncthing Control Center`;
  }, [view]);
  const navigate = (target: View) => {
    window.location.hash = `/${target}`;
    setView(target);
  };
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
  return (
    <div className="app-shell">
      <a
        className="skip-link"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("main-content")?.focus();
        }}
      >
        К содержимому
      </a>
      <aside className="sidebar">
        <div className="brand-row app-brand">
          <div className="brand-mark" aria-hidden="true">
            S
          </div>
          <span>
            Syncthing
            <br />
            <b>Control Center</b>
          </span>
        </div>
        <div className="workspace-label">
          ПАНЕЛЬ <span>{mode === "mock" ? "ДЕМО" : "API"}</span>
        </div>
        <nav aria-label="Разделы">
          {navItems.map((item) => (
            <button
              key={item.id}
              aria-label={item.label}
              aria-current={view === item.id ? "page" : undefined}
              className={`nav-item ${view === item.id ? "active" : ""}`}
              onClick={() => navigate(item.id)}
            >
              <span aria-hidden="true">{item.icon}</span>
              <span className="nav-label">{item.label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="connection-state">
            <span className="mode-dot" aria-hidden="true" />
            <div>
              <small>Источник данных</small>
              <strong>{mode === "mock" ? "Mock API" : "Backend API"}</strong>
            </div>
          </div>
          <div className="user-chip">
            <span className="avatar" aria-hidden="true">
              {session.user.login.slice(0, 1).toUpperCase()}
            </span>
            <div>
              <strong>{session.user.login}</strong>
              <small>Администратор</small>
            </div>
            <button
              className="logout-button"
              aria-label="Выйти"
              onClick={() => void logout()}
              disabled={loggingOut}
            >
              ↗
            </button>
          </div>
        </div>
      </aside>
      <main className="main-content" id="main-content" tabIndex={-1}>
        <header className="topbar">
          <div>
            <span className="breadcrumb">
              Control Center /{" "}
              {navItems.find((item) => item.id === view)?.label}
            </span>
            <h1>{navItems.find((item) => item.id === view)?.label}</h1>
          </div>
          <div className="top-actions">
            <span className={`mode-pill ${mode}`}>
              {mode === "mock" ? "ДЕМО" : "BACKEND API"}
            </span>
            <button
              className="icon-button"
              aria-label="Обновить"
              onClick={reload}
              disabled={nodes.loading || rules.loading || folders.loading}
            >
              ↻
            </button>
          </div>
        </header>
        {mode === "mock" && (
          <div className="mock-banner" role="status">
            <span>Демонстрационные данные</span>Mock-режим изолирован от
            Syncthing. Он не подтверждает работу интеграции.
          </div>
        )}
        {logoutError && <ErrorNotice message={logoutError} />}
        <div className="view-content">
          {view !== "analytics" && (
            <ResourceNotice resource={nodes} onRetry={reload} />
          )}
          {["overview", "folders", "rules"].includes(view) && (
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
          {view === "overview" && (
            <>
              <ResourceNotice resource={latest} onRetry={reload} />
              <Overview
                nodes={nodes.data}
                folders={foldersReady ? folderItems : undefined}
                rules={rules.data}
                latest={latest.data?.items[0]}
                onNavigate={navigate}
              />
            </>
          )}
          {view === "folders" && (
            <Folders
              folders={folderItems}
              nodes={nodes.data ?? []}
              rules={rules.data}
              ready={foldersReady}
              provider={provider}
              onRefresh={reload}
              onFailure={onFailure}
            />
          )}
          {view === "rules" && (
            <Rules
              folders={folderItems}
              nodes={nodes.data ?? []}
              rules={rules.data}
              ready={foldersReady}
              provider={provider}
              onRefresh={reload}
              onFailure={onFailure}
            />
          )}
          {view === "history" && (
            <History
              provider={provider}
              nodes={nodes.data ?? []}
              folders={folderItems}
              revision={revision}
              onFailure={onFailure}
            />
          )}
          {view === "analytics" && (
            <Analytics
              provider={provider}
              revision={revision}
              onFailure={onFailure}
            />
          )}
        </div>
        <footer className="footer">
          <span>Время истории и наблюдений — UTC</span>
          <span>Расписания — в выбранном часовом поясе</span>
        </footer>
      </main>
    </div>
  );
}
