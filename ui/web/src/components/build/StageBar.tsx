import { AVAILABLE, BUILD_STATUS, STAGE_ABOUT, STAGE_NAMES, STAGE_STATUS, STAGES } from "../../buildState";
import type { BuildStage, BuildSummary } from "../../types";

interface Props {
  build: BuildSummary;
  others: BuildSummary[];
  shown: BuildStage | "log";
  onShow: (stage: BuildStage) => void;
  navigate: (path: string) => void;
  /** Open the environment variables the project needs (to change one after the design's gate) */
  onValues: () => void;
}

const MARK: Record<string, string> = { approved: "✓", draft: "", stale: "!", not_started: "" }; // draft: a dot, drawn in CSS

/** One row above the work: where this build is (its stages, in order), and the other builds to switch to. */
export function StageBar({ build, others, shown, onShow, navigate, onValues }: Props) {
  const needed = build.env_needed ?? [];
  const missing = (build.env_missing ?? []).length;
  const required = needed.filter((v) => v.required !== false).length; // optional ones don't count
  return (
    <nav className="stagebar" aria-label="Stages">
      <ol className="stagebar__stages">
        {STAGES.map((stage) => {
          const state = build.stages[stage];
          const available = AVAILABLE.includes(stage);
          return (
            <li
              key={stage}
              className={`stagebar__stage is-${state.status}${build.current === stage ? " is-current" : ""}`}
            >
              <span className={`stagebar__pill${shown === stage ? " is-shown" : ""}`}>
                <button
                  className="stagebar__main"
                  onClick={() => onShow(stage)}
                  disabled={!available}
                  title={available ? STAGE_ABOUT[stage] : `${STAGE_ABOUT[stage]} (comes in a later phase)`}
                >
                  <span className="stagebar__mark" aria-hidden>
                    {MARK[state.status]}
                  </span>
                  <span className="stagebar__name">
                    {STAGE_NAMES[stage]}
                    {state.version ? <span className="stagebar__version"> v{state.version}</span> : null}
                  </span>
                  {state.status !== "not_started" ? (
                    <span className="stagebar__status">{STAGE_STATUS[state.status].label}</span>
                  ) : !available ? (
                    <span className="stagebar__status">soon</span>
                  ) : null}
                </button>
              </span>
            </li>
          );
        })}
      </ol>
      <div className="stagebar__side">
        {needed.length > 0 && (
          <button
            className={`icon-btn stagebar__values${missing ? " is-missing" : ""}`}
            onClick={onValues}
            title="The environment variables the project needs to run (API keys, IDs)"
          >
            Environment variables {required - missing}/{required}
          </button>
        )}
        {others.length > 0 && (
          <select
            className="icon-btn stagebar__switch"
            value=""
            aria-label="Switch build"
            onChange={(event) => event.target.value && navigate(`/builds/${event.target.value}`)}
          >
            <option value="">Switch build…</option>
            {others.map((other) => (
              <option key={other.id} value={other.id}>
                {other.title} · {BUILD_STATUS[other.status].label}
              </option>
            ))}
          </select>
        )}
        <button className="icon-btn" onClick={() => navigate("/builds")}>
          + New build
        </button>
      </div>
    </nav>
  );
}
