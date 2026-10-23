import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "./api";
import { Build } from "./pages/Build";
import { Builds } from "./pages/Builds";
import { Day } from "./pages/Day";
import { DaySettings } from "./pages/DaySettings";
import { Inbox } from "./pages/Inbox";
import { Learnings } from "./pages/Learnings";
import { Report } from "./pages/Report";
import { Reports } from "./pages/Reports";
import type { BuildList, BuildStatus } from "./types";

export interface ToastAction {
  label: string;
  onClick: () => void;
}
interface Toast {
  message: string;
  action?: ToastAction;
}

function usePath() {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const navigate = useCallback((next: string) => {
    window.history.pushState({}, "", next);
    setPath(next);
    window.scrollTo({ top: 0 });
  }, []);
  return { path, navigate };
}

export default function App() {
  const { path, navigate } = usePath();
  const [user, setUser] = useState<{ login: string; name: string } | null | undefined>(undefined);
  const [toast, setToast] = useState<Toast | null>(null);
  const [dev, setDev] = useState(false);
  const [proposals, setProposals] = useState(0);
  const countProposals = useCallback(() => {
    api
      .learnings()
      .then((data) => setProposals(data.learnings.filter((item) => item.status === "proposed").length))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    api.me().then(setUser).catch((error) => setUser(error instanceof ApiError && error.status === 401 ? null : null));
  }, []);
  useEffect(() => {
    api.env().then(({ env }) => setDev(env === "dev")).catch(() => setDev(false));
  }, []);
  useEffect(() => {
    document.documentElement.classList.toggle("is-dev", dev);
  }, [dev]);
  useEffect(() => {
    if (user) countProposals();
  }, [user, path, countProposals]);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), toast.action ? 8000 : 4000);
    return () => window.clearTimeout(timer);
  }, [toast]);
  const onToast = useCallback((message: string, action?: ToastAction) => setToast({ message, action }), []);

  // Builds run side by side: one list, polled while signed in, tells when another build answered or waits for you
  const [builds, setBuilds] = useState<BuildList | null>(null);
  const statuses = useRef<Record<string, BuildStatus> | null>(null);
  const refreshBuilds = useCallback(async () => {
    try {
      const data = await api.builds();
      const before = statuses.current;
      for (const build of data.builds) {
        const open = window.location.pathname.startsWith(`/builds/${build.id}`);
        if (before && before[build.id] === "working" && build.status !== "working" && !open) {
          onToast(`${build.title}: ${build.status === "waiting" ? "waiting for you" : "the agent answered"}.`, {
            label: "Open",
            onClick: () => navigate(`/builds/${build.id}`),
          });
        }
      }
      statuses.current = Object.fromEntries(data.builds.map((build) => [build.id, build.status]));
      setBuilds(data);
    } catch {
      // the next poll tries again
    }
  }, [onToast, navigate]);
  const anyWorking = builds?.builds.some((build) => build.status === "working") ?? false;
  useEffect(() => {
    if (!user) return;
    refreshBuilds();
    const timer = window.setInterval(refreshBuilds, anyWorking ? 5_000 : 30_000);
    return () => window.clearInterval(timer);
  }, [user, refreshBuilds, anyWorking]);

  const envBar = dev && (
    <div className="envbar" role="note">
      <span className="envbar__label">Dev</span>
      <span className="envbar__host">Local app on {window.location.host}, not Heroku</span>
    </div>
  );

  if (user === undefined) return envBar || null;
  if (user === null) {
    return (
      <>
      {envBar}
      <div className="signin">
        <div className="cw-card">
          <img src="/crewai-logo-dark.png" alt="CrewAI" className="topbar__logo" />
          <h1>
            FDE Activity <em>Log</em>
          </h1>
          <p>Sign in with GitHub to review your days.</p>
          <a className="cw-btn cw-btn--primary" href="/auth/login" style={{ textAlign: "center", textDecoration: "none" }}>
            Sign in with GitHub
          </a>
        </div>
      </div>
      </>
    );
  }

  const reportMatch = path.match(/^\/reports\/([0-9a-f]+)\/?$/);
  const buildMatch = path.match(/^\/builds\/([\w-]+)\/?$/);
  const dayMatch = path.match(/^\/day\/(\d{4}-\d{2}-\d{2})(\/settings)?\/?$/);
  // Signed in, every page has the breadcrumb bar, which carries the dev marker itself (no separate strip)
  return (
    <>
    <div className={`shell${buildMatch ? " shell--wide" : ""}`}>
      <header className="topbar">
        <a
          href="/"
          onClick={(event) => {
            event.preventDefault();
            navigate("/");
          }}
        >
          <img src="/crewai-logo-dark.png" alt="CrewAI" className="topbar__logo" />
        </a>
        <div className="topbar__nav">
          <a
            className={`icon-btn${path.startsWith("/builds") ? " is-current" : ""}`}
            href="/builds"
            onClick={(event) => {
              event.preventDefault();
              navigate("/builds");
            }}
          >
            Builds
            {(builds?.waiting ?? 0) > 0 && (
              <span className="topbar__badge" title={`${builds!.waiting} waiting for you`}>
                {builds!.waiting}
              </span>
            )}
          </a>
          <a
            className={`icon-btn${path.startsWith("/reports") ? " is-current" : ""}`}
            href="/reports"
            onClick={(event) => {
              event.preventDefault();
              navigate("/reports");
            }}
          >
            Reports
          </a>
          <a
            className={`icon-btn${path.startsWith("/learnings") ? " is-current" : ""}`}
            href="/learnings"
            onClick={(event) => {
              event.preventDefault();
              navigate("/learnings");
            }}
          >
            Learnings
            {proposals > 0 && (
              <span className="topbar__badge" title={`${proposals} proposed, waiting for your decision`}>
                {proposals}
              </span>
            )}
          </a>
          <span className="tag">{user.name}</span>
          <button
            className="icon-btn"
            onClick={() => fetch("/auth/logout", { method: "POST" }).then(() => setUser(null))}
          >
            Sign out
          </button>
        </div>
      </header>
      {buildMatch ? (
        <Build key={buildMatch[1]} id={buildMatch[1]} list={builds} refreshList={refreshBuilds} onToast={onToast} navigate={navigate} />
      ) : path.startsWith("/builds") ? (
        <Builds list={builds} refresh={refreshBuilds} onToast={onToast} navigate={navigate} />
      ) : reportMatch ? (
        <Report key={reportMatch[1]} id={reportMatch[1]} navigate={navigate} />
      ) : path.startsWith("/reports") ? (
        <Reports onToast={onToast} navigate={navigate} />
      ) : path.startsWith("/learnings") ? (
        <Learnings onToast={onToast} navigate={navigate} onChanged={countProposals} />
      ) : dayMatch?.[2] ? (
        <DaySettings key={`${dayMatch[1]}-settings`} date={dayMatch[1]} onToast={onToast} navigate={navigate} />
      ) : dayMatch ? (
        <Day key={dayMatch[1]} date={dayMatch[1]} onToast={onToast} navigate={navigate} />
      ) : (
        <Inbox navigate={navigate} />
      )}
      {toast && (
        <div className="toast" role="status">
          <span>{toast.message}</span>
          {toast.action && (
            <button
              className="toast__action"
              onClick={() => {
                toast.action!.onClick();
                setToast(null);
              }}
            >
              {toast.action.label}
            </button>
          )}
        </div>
      )}
    </div>
    </>
  );
}
