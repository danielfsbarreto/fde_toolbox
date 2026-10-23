import { useState } from "react";
import type { ActivityEntry, Phase } from "../../types";
import { Redacted } from "./Redacted";

const PHASES: Record<Phase, string> = {
  sandbox: "Sandbox",
  writing: "Writing",
  checks: "Checks",
  smoke: "Smoke test",
  result: "Result",
};

interface View {
  phase: Phase;
  state: "running" | "done" | "failed";
  title: string;
  ended: string;
  steps: ActivityEntry[];
  start: string | null;
  end: string | null;
}

/** A code turn's phases, from its activity: each phase as it started and ended, with the steps logged inside it. */
function phasesOf(entries: ActivityEntry[]): View[] {
  const views: View[] = [];
  for (const entry of entries) {
    const phase = entry.detail.phase as Phase | undefined;
    if (!phase) continue;
    if (entry.kind === "phase" && entry.detail.state === "running") {
      views.push({ phase, state: "running", title: entry.text, ended: "", steps: [], start: entry.at, end: null });
      continue;
    }
    const view = [...views].reverse().find((v) => v.phase === phase);
    if (!view) continue;
    if (entry.kind === "phase") {
      view.state = entry.detail.state === "failed" ? "failed" : "done";
      view.ended = entry.text;
      view.end = entry.at;
    } else {
      view.steps.push(entry);
    }
  }
  return views;
}

const seconds = (start: string | null, end: string | null) =>
  start ? Math.max(0, ((end ? Date.parse(end) : Date.now()) - Date.parse(start)) / 1000) : 0;

const duration = (s: number) => (s < 60 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`);

function summary(view: View): string {
  const count = (step: string) => view.steps.filter((e) => e.detail.step === step).length;
  if (view.phase === "writing") {
    const skills = view.steps.filter((e) => e.kind === "tool" && String(e.detail.tool) === "load_skill").length;
    const files = new Set(view.steps.filter((e) => e.detail.step === "file").map((e) => String(e.detail.path))).size;
    const parts = [`${files} files`, `${count("command")} commands`];
    if (skills) parts.push(`${skills} skills`);
    return parts.join(" · ");
  }
  if (view.phase === "sandbox") return view.steps.find((e) => e.detail.step === "note")?.text ?? view.title;
  if (view.state === "running") return view.title;
  return view.ended || view.title;
}

const MARK = { running: "●", done: "✓", failed: "✗" };

interface Props {
  entries: ActivityEntry[];
  /** The turn is running: the timeline is open and its current phase expanded */
  live: boolean;
}

/** What the engineer is doing (or did) in one turn, phase by phase: one line each, the running one open, every step
 *  expandable to its exact command, output, file or check. Values of environment variables show as locked chips. */
export function CodeTimeline({ entries, live }: Props) {
  const views = phasesOf(entries);
  const [open, setOpen] = useState(live);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  if (!views.length) return null;
  const total = seconds(views[0].start, live ? null : views[views.length - 1].end);
  const failed = views.some((v) => v.state === "failed");

  if (!open && !live) {
    return (
      <button className="timeline-toggle" onClick={() => setOpen(true)}>
        What happened · {views.length} phases · {duration(total)}
        {failed ? " · something failed" : ""} ⌄
      </button>
    );
  }
  return (
    <div className={`timeline${live ? " is-live" : ""}`}>
      <div className="timeline__head">
        <span className="label">{live ? `Engineer · running ${duration(total)}` : `What happened · ${duration(total)}`}</span>
        {!live && (
          <button className="icon-btn" onClick={() => setOpen(false)}>
            Hide
          </button>
        )}
      </div>
      <ol className="timeline__phases">
        {views.map((view, index) => {
          const isOpen = expanded[index] ?? (live && view.state === "running");
          return (
            <li key={index} className={`timeline__phase is-${view.state}`}>
              <button className="timeline__row" onClick={() => setExpanded({ ...expanded, [index]: !isOpen })}>
                <span className="timeline__mark">{MARK[view.state]}</span>
                <span className="timeline__name">{PHASES[view.phase]}</span>
                <span className="timeline__summary">
                  <Redacted text={summary(view)} />
                </span>
                <span className="timeline__time">{duration(seconds(view.start, view.end))}</span>
                <span className="timeline__chevron">{isOpen ? "⌃" : "⌄"}</span>
              </button>
              {isOpen && <Steps view={view} live={live} />}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function Steps({ view, live }: { view: View; live: boolean }) {
  const console = view.steps.filter((e) => e.detail.step === "console");
  const others = view.steps.filter((e) => e.detail.step !== "console");
  return (
    <div className="timeline__steps">
      {others.map((entry) => (
        <Step key={entry.seq} entry={entry} />
      ))}
      {console.length > 0 && <Console text={console.map((e) => String(e.detail.output ?? "")).join("")} live={live} />}
    </div>
  );
}

function Step({ entry }: { entry: ActivityEntry }) {
  const [open, setOpen] = useState(false);
  const d = entry.detail;
  const step = String(d.step ?? entry.kind);
  if (step === "file") {
    const implemented = (d.implements as string[] | undefined) ?? [];
    return (
      <div className="tstep">
        <button className="tstep__row" onClick={() => setOpen(!open)}>
          <span className={`tstep__op is-${String(d.op)}`}>{d.op === "added" ? "+" : "~"}</span>
          <code className="tstep__path">{String(d.path)}</code>
          <span className="tstep__diff">
            <span className="is-added">+{String(d.added ?? 0)}</span> <span className="is-removed">−{String(d.removed ?? 0)}</span>
          </span>
          {implemented.length > 0 && <span className="tstep__ids">{implemented.join(" ")}</span>}
        </button>
        {open && (
          <pre className="tstep__output">
            <Redacted text={String(d.content ?? "")} />
          </pre>
        )}
      </div>
    );
  }
  if (step === "command") {
    const exit = Number(d.exit ?? 0);
    return (
      <div className="tstep">
        <button className="tstep__row" onClick={() => setOpen(!open)}>
          <code className="tstep__path">
            <Redacted text={entry.text} />
          </code>
          <span className={`tstep__exit${exit ? " is-failed" : ""}`}>
            exit {exit} · {Math.round(Number(d.seconds ?? 0))}s
          </span>
        </button>
        {open && (
          <pre className="tstep__output">
            <Redacted text={String(d.output ?? "") || "(no output)"} />
          </pre>
        )}
      </div>
    );
  }
  if (step === "check") {
    const status = String(d.status ?? "");
    const mismatches = (d.mismatches as string[] | undefined) ?? [];
    const trace = d.trace_url ? String(d.trace_url) : null;
    return (
      <div className="tstep">
        <div className={`tstep__check is-${status}`}>
          {status === "passed" ? "✓" : status === "failed" ? "✗" : "–"} <Redacted text={entry.text} />
          {d.reason ? (
            <span className="tstep__reason">
              {" "}
              · <Redacted text={String(d.reason)} />
            </span>
          ) : null}
          {trace && (
            <a className="tstep__link" href={trace} target="_blank" rel="noreferrer" title="Readable by anyone with the link, for 24 hours">
              Trace ›
            </a>
          )}
        </div>
        {mismatches.length > 0 && (
          <ul className="tstep__list">
            {mismatches.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        )}
      </div>
    );
  }
  const prefix = entry.kind === "tool" ? "▸ " : step === "note" ? "" : "";
  return (
    <div className={`tstep tstep--line${step === "note" ? " is-note" : ""}`}>
      {prefix}
      <Redacted text={entry.text} />
    </div>
  );
}

function Console({ text, live }: { text: string; live: boolean }) {
  const [full, setFull] = useState(false);
  const lines = text.split("\n");
  const shown = full ? text : lines.slice(-14).join("\n");
  return (
    <div className="tconsole">
      <pre className={`tconsole__text${live ? " is-live" : ""}`}>
        <Redacted text={shown} />
        {live && <span className="tconsole__cursor">▌</span>}
      </pre>
      {lines.length > 14 && (
        <button className="icon-btn tconsole__more" onClick={() => setFull(!full)}>
          {full ? "Show the end" : `Show full output (${lines.length} lines)`}
        </button>
      )}
    </div>
  );
}
