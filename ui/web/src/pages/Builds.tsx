import { useState } from "react";
import { api } from "../api";
import { Badge } from "../components/Badge";
import { Crumbs, StatePill } from "../components/Crumbs";
import { BUILD_STATUS, STAGE_NAMES, STAGES, whereItIs } from "../buildState";
import type { BuildList, BuildSummary } from "../types";

interface Props {
  list: BuildList | null;
  refresh: () => Promise<void>;
  onToast: (message: string) => void;
  navigate: (path: string) => void;
}

/** Builds: CrewAI projects built conversationally, spec first. Several can run at once. */
const TITLE_CHARS = 80;

/** The build's name: the description's first line, cut at a word near 80 characters. */
function titleOf(description: string): string {
  const first = description.trim().split("\n")[0].trim();
  if (first.length <= TITLE_CHARS) return first;
  const cut = first.slice(0, TITLE_CHARS);
  return `${cut.slice(0, cut.lastIndexOf(" ") > 40 ? cut.lastIndexOf(" ") : TITLE_CHARS).trim()}…`;
}

export function Builds({ list, refresh, onToast, navigate }: Props) {
  const [description, setDescription] = useState("");
  const [account, setAccount] = useState("");
  const [busy, setBusy] = useState(false);

  /** Start the build, and send the description as its first message: the spec writer starts on it right away. */
  const create = async () => {
    const text = description.trim();
    if (!text || busy) return;
    setBusy(true);
    try {
      const build = await api.addBuild(titleOf(text), account.trim() || null);
      try {
        await api.send(build.id, { text });
      } catch (err) {
        onToast(`The build is created, but the description was not sent: ${err instanceof Error ? err.message : "try again"}`);
      }
      await refresh();
      navigate(`/builds/${build.id}`);
    } catch (err) {
      onToast(err instanceof Error ? err.message : "The build could not be created.");
      setBusy(false);
    }
  };
  const remove = async (build: BuildSummary) => {
    if (!window.confirm(`Delete "${build.title}" with its conversation and artifacts?`)) return;
    try {
      await api.deleteBuild(build.id);
      await refresh();
      onToast("Build deleted.");
    } catch (err) {
      onToast(err instanceof Error ? err.message : "The build could not be deleted.");
    }
  };

  return (
    <>
      <Crumbs items={[{ label: "All days", onClick: () => navigate("/") }, { label: "Builds" }]} />
      <section className="hero hero--compact">
        <Badge label="Builds" />
        <h1>
          Project <em>builder</em>
        </h1>
        <p className="hero__sub">
          Build a CrewAI project with spec-driven development: agree on the spec first, then the design, the code and its
          tests. Each stage waits for your approval, and changing an earlier one sends the later ones back for rework.
        </p>
      </section>

      <section className="cw-card report-form" aria-label="New build">
        <label className="report-form__field">
          <span className="label">What are you building?</span>
          <textarea
            className="field build-form__description"
            rows={5}
            value={description}
            maxLength={20_000}
            placeholder={
              "What it should do, for whom, what goes in and what comes out. For example: a flow for our sales " +
              "team that takes a company's web page and writes a short prospecting brief with a hook for a first email."
            }
            onChange={(event) => setDescription(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                create();
              }
            }}
          />
          <span className="section__quiet">
            The first line names the build; the whole text is your first message to the spec writer. ⌘/Ctrl+Enter
            starts it.
          </span>
        </label>
        <div className="build-form__row">
          <label className="report-form__field build-form__account">
            <span className="label">Account (optional)</span>
            <input
              className="field"
              value={account}
              maxLength={80}
              placeholder="e.g. Acme"
              onChange={(event) => setAccount(event.target.value)}
            />
          </label>
          <button className="cw-btn cw-btn--primary" disabled={!description.trim() || busy} onClick={create}>
            {busy ? "Starting…" : "Start the build"}
          </button>
        </div>
        {list?.local_command && (
          <p className="section__quiet">
            This copy of the app does not send turns to AMP: messages wait for the local worker. Run{" "}
            <code>{list.local_command}</code> from the repository root.
          </p>
        )}
      </section>

      <section className="section">
        <div className="section__head">
          <Badge label="Your builds" />
        </div>
        {list === null ? (
          <p>Loading…</p>
        ) : list.builds.length === 0 ? (
          <p className="section__quiet">No builds yet.</p>
        ) : (
          <div className="days">
            {list.builds.map((build) => (
              <div className={`cw-card report-row${build.status === "waiting" ? " is-action" : ""}`} key={build.id}>
                <button className="report-row__main" onClick={() => navigate(`/builds/${build.id}`)}>
                  <h3>{build.title}</h3>
                  <span className="tag">
                    {build.status === "working" && build.now ? build.now : whereItIs(build)}
                    {build.account ? ` · ${build.account}` : ""}
                  </span>
                </button>
                <span className="day-row__side">
                  <span className="build-stages" aria-label="Stages">
                    {STAGES.map((stage) => (
                      <span
                        key={stage}
                        className={`build-stages__dot is-${build.stages[stage].status}`}
                        title={`${STAGE_NAMES[stage]}: ${build.stages[stage].status.replace("_", " ")}`}
                      />
                    ))}
                  </span>
                  <StatePill label={BUILD_STATUS[build.status].label} tone={BUILD_STATUS[build.status].tone} />
                  <button className="icon-btn" disabled={build.status === "working"} onClick={() => remove(build)}>
                    Delete
                  </button>
                </span>
              </div>
            ))}
          </div>
        )}
      </section>
    </>
  );
}
