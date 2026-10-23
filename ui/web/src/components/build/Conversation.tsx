import { useEffect, useRef, useState } from "react";
import { Markdown } from "./Markdown";
import { STAGE_NAMES } from "../../buildState";
import { CodeTimeline } from "./CodeTimeline";
import { EnvChecklist } from "./EnvChecklist";
import { QuestionForm } from "./QuestionForm";
import type { ActivityEntry, ArtifactCard, BuildMessage, BuildSummary, NewBuildMessage } from "../../types";

const WORKER_GRACE_MS = 8_000;

/** A user message that only carries a form's answers: the answered form already shows them. Messages saved before
 *  answers_for existed are recognised by the summary the form writes. */
const carriesAnswers = (message: BuildMessage) =>
  message.role === "user" && (!!message.answers_for || message.text.startsWith("My answers:\n"));

export interface Changing {
  stage: ArtifactCard["stage"];
  version: number;
}

interface Props {
  build: BuildSummary;
  messages: BuildMessage[];
  activity: ActivityEntry[];
  /** Messages not answered yet, for the local worker hint */
  pending: Set<string>;
  localCommand: string | null;
  onSend: (message: NewBuildMessage) => Promise<boolean>;
  onShowArtifact: (card: ArtifactCard) => void;
  /** A value was set or removed in the design's gate: read the build again (what is missing) */
  onEnvChange: () => void;
}

/** The conversation: messages from you and the agents, artifacts as cards with their gate, and the composer. */
export function Conversation({
  build,
  messages,
  activity,
  pending,
  localCommand,
  onSend,
  onShowArtifact,
  onEnvChange,
}: Props) {
  const [text, setText] = useState("");
  const [changing, setChanging] = useState<Changing | null>(null);
  const [sending, setSending] = useState(false);
  const list = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const working = build.status === "working";
  const asking =
    build.pending_input === undefined // an older server: the open form shows in the messages
      ? messages.some((message) => message.input_request?.status === "pending")
      : !!build.pending_input && build.pending_input.kind !== "smoke"; // a form waits; a smoke test's form doesn't
  // A message the local worker has not picked up after a few seconds: it is probably not running
  const waitingForWorker =
    !working &&
    localCommand !== null &&
    messages.some(
      (message) => pending.has(message.id) && message.created_at && Date.now() - Date.parse(message.created_at) > WORKER_GRACE_MS,
    );
  const now = working ? [...activity].reverse().find((entry) => entry.kind !== "done") : undefined;
  // A code turn's timeline (its phases and steps), by turn: shown above the turn's first reply, or live below
  const timelines = new Map<string, ActivityEntry[]>();
  for (const entry of activity) {
    if (entry.turn_id && (entry.kind === "phase" || entry.detail.phase)) {
      timelines.set(entry.turn_id, [...(timelines.get(entry.turn_id) ?? []), entry]);
    }
  }
  const liveTurn = working ? now?.turn_id ?? null : null;
  const shownTurns = new Set<string>();
  const timelineFor = (message: BuildMessage) => {
    const turn = message.turn_id;
    if (message.role !== "assistant" || !turn || turn === liveTurn || shownTurns.has(turn) || !timelines.has(turn)) return null;
    shownTurns.add(turn);
    return <CodeTimeline entries={timelines.get(turn)!} live={false} />;
  };
  const smokeOpen = build.pending_input?.kind === "smoke";
  // The code's gate needs its smoke test passed (or skipped)
  const smokeOk = (card: ArtifactCard) =>
    card.stage !== "code" ||
    (build.smoke?.version === card.version && (build.smoke.status === "passed" || build.smoke.status === "skipped"));

  useEffect(() => {
    const element = list.current; // scroll the conversation only, never the page
    if (element) element.scrollTop = element.scrollHeight;
  }, [messages.length, working]);

  const send = async (message: NewBuildMessage) => {
    setSending(true);
    const sent = await onSend(message);
    setSending(false);
    if (sent) {
      setText("");
      setChanging(null);
    }
  };
  const submit = () => {
    if (!text.trim() || sending || working || asking) return;
    send(changing ? { text: text.trim(), action: "request_changes", ...changing } : { text: text.trim() });
  };
  // A design's gate holds its values first (step 1), then the approval (step 2)
  const needsValues = (card: ArtifactCard) => card.stage === "design" && (build.env_needed ?? []).length > 0;
  const gateOpen = (card: ArtifactCard) => {
    const state = build.stages[card.stage];
    return card.gate && !working && !asking && state.status === "draft" && state.version === card.version;
  };

  return (
    <section className="build-chat" aria-label="Conversation">
      <div className="build-chat__messages" ref={list}>
        {messages.length === 0 && (
          <div className="build-chat__empty">
            <h2>
              Start with the <em>spec</em>
            </h2>
            <p>
              Say what the project should do, for whom, what goes in and what comes out. The spec writer drafts the spec
              with acceptance criteria and asks what is missing.
            </p>
          </div>
        )}
        {messages.filter((message) => !carriesAnswers(message)).map((message) => (
          <article key={message.id} className={`build-msg build-msg--${message.role}`}>
            {timelineFor(message)}
            <header className="build-msg__head">
              <span className="build-msg__author">{message.role === "user" ? "You" : message.author}</span>
            </header>
            <div className="build-msg__body md">
              <Markdown>{message.text}</Markdown>
            </div>
            {message.input_request && (
              <QuestionForm
                key={message.input_request.id}
                request={message.input_request}
                open={
                  build.pending_input === undefined
                    ? message.input_request.status === "pending"
                    : build.pending_input?.request_id === message.input_request.id
                }
                busy={working}
                onSend={onSend}
              />
            )}
            {message.card && (
              <div className="build-card">
                <button className="build-card__head" onClick={() => onShowArtifact(message.card!)}>
                  <span className="build-card__name">
                    {STAGE_NAMES[message.card.stage]} v{message.card.version}
                  </span>
                  <span className="build-card__summary">{message.card.summary}</span>
                  <span className="build-card__open">Open ›</span>
                </button>
                {message.card.stage === "code" && (
                  <a className="build-card__download" href={`/api/builds/${build.id}/code.zip?version=${message.card.version}`}>
                    Download ZIP
                  </a>
                )}
                {message.card.changes.length > 0 && (
                  <ul className="build-card__changes">
                    {message.card.changes.map((change, index) => (
                      <li key={index}>{change}</li>
                    ))}
                  </ul>
                )}
                {gateOpen(message.card) && needsValues(message.card) && (
                  <div className="build-card__step">
                    <span className="build-card__num">1</span>
                    <div className="build-card__stepbody">
                      <span className="label">Environment variables</span>
                      <EnvChecklist buildId={build.id} vars={build.env_needed ?? []} onChange={onEnvChange} />
                    </div>
                  </div>
                )}
                {gateOpen(message.card) && (
                  <div className="build-card__gate">
                    {needsValues(message.card) && <span className="build-card__num">2</span>}
                    <button
                      className="cw-btn cw-btn--primary cw-btn--small"
                      disabled={
                        sending ||
                        (needsValues(message.card) && (build.env_missing ?? []).length > 0) ||
                        !smokeOk(message.card)
                      }
                      onClick={() =>
                        send({
                          text: `Approve ${STAGE_NAMES[message.card!.stage]} v${message.card!.version}`,
                          action: "approve",
                          stage: message.card!.stage,
                          version: message.card!.version,
                        })
                      }
                    >
                      Approve
                    </button>
                    <button
                      className="cw-btn cw-btn--ghost cw-btn--small"
                      disabled={sending}
                      onClick={() => {
                        setChanging({ stage: message.card!.stage, version: message.card!.version });
                        box.current?.focus();
                      }}
                    >
                      Request changes
                    </button>
                    {needsValues(message.card) && (build.env_missing ?? []).length > 0 && (
                      <span className="build-card__hint">Approve unlocks once the environment variables above are set</span>
                    )}
                    {message.card.stage === "code" && !smokeOk(message.card) && !smokeOpen && (
                      <button
                        className="cw-btn cw-btn--ghost cw-btn--small"
                        disabled={sending}
                        onClick={() => send({ text: "Run the smoke test", action: "smoke" })}
                      >
                        Run the smoke test
                      </button>
                    )}
                    {message.card.stage === "code" && !smokeOk(message.card) && (
                      <span className="build-card__hint">
                        {smokeOpen ? "Approve unlocks once the smoke test above passes" : "Approve unlocks once the smoke test passes"}
                      </span>
                    )}
                  </div>
                )}
              </div>
            )}
          </article>
        ))}
        {working && liveTurn && timelines.has(liveTurn) ? (
          <CodeTimeline key={liveTurn} entries={timelines.get(liveTurn)!} live />
        ) : (
          working && (
            <div className="build-now" role="status">
              <span className="spinner" aria-hidden />
              <span>{now?.text ?? "Working…"}</span>
            </div>
          )
        )}
        {waitingForWorker && (
          <div className="build-now build-now--idle" role="status">
            <span>
              Waiting for the local worker: run <code>{localCommand}</code> from the repository root.
            </span>
          </div>
        )}
      </div>

      <div className="build-composer">
        {changing && (
          <div className="bar__about">
            <span className="bar__scope">
              Changes to {STAGE_NAMES[changing.stage]} v{changing.version}
            </span>
            <button className="icon-btn bar__close" onClick={() => setChanging(null)}>
              Cancel
            </button>
          </div>
        )}
        <div className="bar__row">
          <textarea
            ref={box}
            className="field bar__box"
            rows={2}
            value={text}
            placeholder={
              working
                ? "The agent is answering…"
                : asking
                  ? "Answer the questions above to continue"
                  : changing
                  ? "What should change?"
                  : "Describe what to build, answer a question, or ask about the work"
            }
            disabled={asking}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
          />
          <button
            className="cw-btn cw-btn--primary"
            disabled={!text.trim() || sending || working || asking}
            onClick={submit}
          >
            Send
          </button>
        </div>
      </div>
    </section>
  );
}
