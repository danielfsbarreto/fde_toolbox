import { Inline, Markdown } from "./Markdown";
import { AVAILABLE, STAGE_NAMES, STAGES } from "../../buildState";
import type { ActivityEntry, Artifact, ArtifactVersion, BuildStage, BuildSummary, Criterion, Spec } from "../../types";
import { CodeView } from "./CodeView";
import { DesignView } from "./DesignView";

interface Props {
  shown: BuildStage | "log";
  onShow: (tab: BuildStage | "log") => void;
  artifact: Artifact | null;
  previous: Artifact | null;
  versions: ArtifactVersion[];
  version: number | null;
  onVersion: (version: number | null) => void;
  activity: ActivityEntry[];
  build: BuildSummary;
  onEnvChange?: () => void;
}

/** The right column: the artifact of a stage (any version, with what changed from the one before), or the router log. */
export function ArtifactPanel({
  shown,
  onShow,
  artifact,
  previous,
  versions,
  version,
  onVersion,
  activity,
  build,
  onEnvChange,
}: Props) {
  return (
    <section className="build-panel" aria-label="Artifacts">
      <div className="build-panel__tabs" role="tablist">
        {STAGES.map((stage) => (
          <button
            key={stage}
            role="tab"
            aria-selected={shown === stage}
            className={`build-tab${shown === stage ? " is-on" : ""}`}
            disabled={!AVAILABLE.includes(stage)}
            title={AVAILABLE.includes(stage) ? undefined : "Comes in a later phase of the builder"}
            onClick={() => onShow(stage)}
          >
            {STAGE_NAMES[stage]}
          </button>
        ))}
        <button
          role="tab"
          aria-selected={shown === "log"}
          className={`build-tab build-tab--log${shown === "log" ? " is-on" : ""}`}
          onClick={() => onShow("log")}
        >
          Router log
        </button>
      </div>

      <div className="build-panel__body">
        {shown === "log" ? (
          <RouterLog activity={activity} />
        ) : !artifact ? (
          <p className="section__quiet">No {STAGE_NAMES[shown].toLowerCase()} yet.</p>
        ) : (
          <>
            <div className="build-panel__version">
              <select
                className="icon-btn"
                value={version ?? ""}
                onChange={(event) => onVersion(event.target.value ? Number(event.target.value) : null)}
                aria-label="Version"
              >
                {versions.map((item, index) => (
                  <option key={item.version} value={index === 0 ? "" : item.version}>
                    v{item.version}
                    {index === 0 ? " (latest)" : ""} · {item.summary || item.author}
                  </option>
                ))}
              </select>
              <span className="tag">by {artifact.author}</span>
            </div>
            {artifact.content.kind === "spec" && (
              <SpecView spec={artifact.content} previous={previous?.content.kind === "spec" ? previous.content : null} />
            )}
            {artifact.content.kind === "code" && (
              <CodeView buildId={build.id} version={artifact.version} code={artifact.content} />
            )}
            {artifact.content.kind === "design" && (
              <DesignView
                buildId={build.id}
                design={artifact.content}
                previous={previous?.content.kind === "design" ? previous.content : null}
                basedOn={artifact.based_on?.spec}
                stage={build.stages.design}
                onEnvChange={onEnvChange}
              />
            )}
          </>
        )}
      </div>
    </section>
  );
}

type Change = "added" | "changed" | null;

function criterionChange(criterion: Criterion, previous: Spec | null): Change {
  if (!previous) return null;
  const before = previous.criteria.find((item) => item.id === criterion.id);
  if (!before) return "added";
  return JSON.stringify(before) === JSON.stringify(criterion) ? null : "changed";
}

function List({ label, items }: { label: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="spec__block">
      <span className="label">{label}</span>
      <ul>
        {items.map((item, index) => (
          <li key={index}>
            <Inline>{item}</Inline>
          </li>
        ))}
      </ul>
    </div>
  );
}

function SpecView({ spec, previous }: { spec: Spec; previous: Spec | null }) {
  const removed = previous ? previous.criteria.filter((item) => !spec.criteria.some((now) => now.id === item.id)) : [];
  return (
    <article className="spec">
      <h2 className="spec__title">
        <Inline>{spec.title}</Inline>
      </h2>
      <div className="spec__block md">
        <span className="label">Goal</span>
        <Markdown>{spec.goal}</Markdown>
      </div>
      <List label="Users" items={spec.users} />
      <List label="Inputs" items={spec.inputs} />
      <List label="Outputs" items={spec.outputs} />
      <div className="spec__block">
        <span className="label">Acceptance criteria</span>
        <ol className="spec__criteria">
          {spec.criteria.map((criterion) => {
            const change = criterionChange(criterion, previous);
            return (
              <li key={criterion.id} className={`criterion${change ? ` is-${change}` : ""}`}>
                <span className="criterion__id">{criterion.id}</span>
                <div className="criterion__body">
                  <p>
                    <Inline>{criterion.text}</Inline>
                  </p>
                  {(criterion.given || criterion.when || criterion.then) && (
                    <p className="criterion__gwt">
                      {criterion.given && (
                        <span>
                          Given <Inline>{criterion.given}</Inline>.{" "}
                        </span>
                      )}
                      {criterion.when && (
                        <span>
                          When <Inline>{criterion.when}</Inline>.{" "}
                        </span>
                      )}
                      {criterion.then && (
                        <span>
                          Then <Inline>{criterion.then}</Inline>.
                        </span>
                      )}
                    </p>
                  )}
                </div>
                {change && <span className="tag criterion__change">{change}</span>}
              </li>
            );
          })}
          {removed.map((criterion) => (
            <li key={criterion.id} className="criterion is-removed">
              <span className="criterion__id">{criterion.id}</span>
              <div className="criterion__body">
                <p>{criterion.text}</p>
              </div>
              <span className="tag criterion__change">removed</span>
            </li>
          ))}
        </ol>
      </div>
      <List label="Examples" items={spec.examples} />
      <List label="Non-goals" items={spec.non_goals} />
      <List label="Open questions" items={spec.open_questions} />
    </article>
  );
}

/** Per turn: how the router routed it (and why), and what the agents did. */
function RouterLog({ activity }: { activity: ActivityEntry[] }) {
  if (!activity.length) return <p className="section__quiet">Nothing yet: every turn's route and steps show here.</p>;
  const turns: ActivityEntry[][] = [];
  for (const entry of activity) {
    if (entry.kind === "turn" || !turns.length) turns.push([]);
    turns[turns.length - 1].push(entry);
  }
  return (
    <ol className="router-log">
      {[...turns].reverse().map((entries) => (
        <li key={entries[0].seq} className="router-log__turn">
          {entries.map((entry) => (
            <div key={entry.seq} className={`router-log__entry is-${entry.kind}`}>
              <span className="router-log__kind">{entry.kind}</span>
              <span className="router-log__text">
                {entry.text}
                {entry.kind === "done" && typeof entry.detail.tokens === "number" && entry.detail.tokens > 0
                  ? ` · ${entry.detail.tokens.toLocaleString()} tokens`
                  : ""}
              </span>
              <time className="router-log__at">{entry.at ? new Date(entry.at).toLocaleTimeString() : ""}</time>
            </div>
          ))}
        </li>
      ))}
    </ol>
  );
}
