import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Crumbs } from "../components/Crumbs";
import { Inline } from "../components/Inline";
import type { Learning, LearningStatus, LearningStep } from "../types";

interface Props {
  onToast: (message: string) => void;
  navigate: (path: string) => void;
  /** Called after any change, so the top bar's count of proposals stays right */
  onChanged?: () => void;
}

const STEPS: { key: LearningStep; label: string; hint: string }[] = [
  { key: "attribution", label: "Attribution", hint: "which account a conversation belongs to" },
  { key: "topics", label: "Topics", hint: "what a topic is and how it is written" },
  { key: "follow_ups", label: "Follow-ups", hint: "owners, wording, due dates, Todoist" },
];
const STATUSES: { key: LearningStatus; label: string; empty: string }[] = [
  { key: "proposed", label: "Proposed", empty: "Nothing waiting for a decision." },
  { key: "approved", label: "Approved", empty: "No approved learnings yet: runs follow none." },
  { key: "denied", label: "Denied", empty: "Nothing denied." },
  { key: "retired", label: "Retired", empty: "Nothing retired." },
];
const GENERAL = "general";
const LONG_LIST = 15; // approved, per scope: past this, retire or merge before adding more

/** The learnings the flow's LLM steps follow. Each one has a status, and only approved ones reach the runs:
 *  proposed (waiting for you), approved, denied, retired. Written here by hand, or proposed from your reviews. */
export function Learnings({ onToast, navigate, onChanged }: Props) {
  const [learnings, setLearnings] = useState<Learning[] | null>(null);
  const [accounts, setAccounts] = useState<Record<string, string>>({ [GENERAL]: "General" });
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<LearningStatus | null>(null);
  const [scope, setScope] = useState(GENERAL);
  const [steps, setSteps] = useState<LearningStep[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await api.learnings();
      setLearnings(data.learnings);
      setAccounts(data.accounts);
      setError(null);
      // Open on what needs you: proposals first, else what the runs follow
      setTab((current) => current ?? (data.learnings.some((item) => item.status === "proposed") ? "proposed" : "approved"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The learnings could not be loaded.");
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const change = async (learning: Learning, changes: Parameters<typeof api.editLearning>[1], done?: string) => {
    try {
      const updated = await api.editLearning(learning.id, changes);
      setLearnings((current) => (current ?? []).map((item) => (item.id === updated.id ? updated : item)));
      if (done) onToast(done);
      onChanged?.();
    } catch (err) {
      onToast(err instanceof Error ? err.message : "The learning could not be saved.");
    }
  };
  const add = async (status: "approved" | "proposed") => {
    if (text.trim().length < 3) return;
    setBusy(true);
    try {
      const created = await api.addLearning({ scope, steps, text: text.trim(), status });
      setLearnings((current) => [...(current ?? []), created]);
      setText("");
      setSteps([]);
      setTab(status);
      onToast(status === "approved" ? "Approved: the next run follows it." : "Saved as proposed: approve it to use it.");
      onChanged?.();
    } catch (err) {
      onToast(err instanceof Error ? err.message : "The learning could not be added.");
    } finally {
      setBusy(false);
    }
  };

  const name = (key: string) => accounts[key] ?? key;
  const count = (status: LearningStatus) => (learnings ?? []).filter((item) => item.status === status).length;
  const shown = (learnings ?? []).filter((item) => item.status === tab);
  const scopes = [GENERAL, ...Object.keys(accounts).filter((key) => key !== GENERAL)];
  const groups = [...scopes, ...new Set(shown.map((item) => item.scope).filter((key) => !scopes.includes(key)))]
    .map((key) => ({ key, items: shown.filter((item) => item.scope === key) }))
    .filter((group) => group.items.length > 0);
  const toggle = (list: LearningStep[], step: LearningStep) =>
    list.includes(step) ? list.filter((item) => item !== step) : [...list, step];

  const actions = (learning: Learning) => {
    const to = (status: LearningStatus, label: string, done: string, primary = false) => (
      <button
        key={status}
        className={primary ? "cw-btn cw-btn--ghost cw-btn--small" : "icon-btn"}
        onClick={() => change(learning, { status }, done)}
      >
        {label}
      </button>
    );
    switch (learning.status) {
      case "proposed":
        return [to("denied", "Deny", "Denied: it will not be proposed again."), to("approved", "Approve", "Approved: the next run follows it.", true)];
      case "approved":
        return [to("retired", "Retire", "Retired: runs no longer follow it.")];
      case "denied":
        return [to("approved", "Approve", "Approved: the next run follows it.", true)];
      case "retired":
        return [to("approved", "Restore", "Restored: the next run follows it.", true)];
    }
  };

  const card = (learning: Learning) => {
    const editable = learning.status !== "retired";
    return (
      <article className={`cw-card learning learning--${learning.status}`} key={learning.id}>
        <div className="learning__head">
          <span className={`state state--${learning.status === "approved" ? "done" : learning.status === "proposed" ? "action" : "idle"}`}>
            {STATUSES.find((status) => status.key === learning.status)?.label}
          </span>
          <select
            className="icon-btn learning__scope"
            value={learning.scope}
            disabled={!editable}
            aria-label="About"
            onChange={(event) => change(learning, { scope: event.target.value }, `Moved to ${name(event.target.value)}.`)}
          >
            {scopes.map((key) => (
              <option key={key} value={key}>
                {name(key)}
              </option>
            ))}
          </select>
        </div>
        <Inline
          multiline
          value={learning.text}
          disabled={!editable}
          onSave={(value) => value.trim().length >= 3 && value !== learning.text && change(learning, { text: value }, "Saved.")}
          ariaLabel="Learning"
        />
        {learning.evidence.length > 0 && (
          <details className="learning__evidence">
            <summary>Why it was proposed ({learning.evidence.length})</summary>
            <ul>
              {learning.evidence.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </details>
        )}
        <div className="learning__meta">
          <div className="learning__steps" role="group" aria-label="Applies to">
            {STEPS.map((step) => (
              <button
                key={step.key}
                className={`step-chip${learning.steps.includes(step.key) ? " is-on" : ""}`}
                title={step.hint}
                disabled={!editable}
                onClick={() => change(learning, { steps: toggle(learning.steps, step.key) })}
              >
                {step.label}
              </button>
            ))}
            {learning.steps.length === 0 && <span className="tag">every step</span>}
          </div>
          <div className="learning__actions">
            <span className="tag">{learning.source === "manual" ? "by you" : "from your reviews"}</span>
            {actions(learning)}
          </div>
        </div>
      </article>
    );
  };

  return (
    <>
      <Crumbs items={[{ label: "All days", onClick: () => navigate("/") }, { label: "Learnings" }]} />
      <section className="hero hero--compact">
        <Badge label="Learnings" />
        <h1>
          What the agent <em>follows</em>
        </h1>
        <p className="hero__sub">
          Only approved learnings guide the runs: which account a conversation belongs to, how topics and follow-ups
          are written, and how they reach Todoist. Keep each one a single, specific rule.
        </p>
      </section>

      {error && <div className="notice">{error}</div>}

      <section className="cw-card learning-form" aria-label="Add a learning">
        <div className="learning-form__row">
          <label className="learning-form__field">
            <span className="label">About</span>
            <select className="field" value={scope} onChange={(event) => setScope(event.target.value)}>
              {scopes.map((key) => (
                <option key={key} value={key}>
                  {name(key)}
                </option>
              ))}
            </select>
          </label>
          <div className="learning-form__field">
            <span className="label">Applies to</span>
            <div className="learning__steps">
              {STEPS.map((step) => (
                <button
                  key={step.key}
                  className={`step-chip${steps.includes(step.key) ? " is-on" : ""}`}
                  title={step.hint}
                  onClick={() => setSteps(toggle(steps, step.key))}
                >
                  {step.label}
                </button>
              ))}
              {steps.length === 0 && <span className="tag">every step</span>}
            </div>
          </div>
        </div>
        <textarea
          className="field"
          rows={2}
          value={text}
          placeholder='One rule, e.g. "Tiana Taylor is the CSM; her follow-ups are Waiting on Tiana."'
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) add("approved");
          }}
        />
        <div className="learning-form__submit">
          <button className="cw-btn cw-btn--ghost" disabled={busy || text.trim().length < 3} onClick={() => add("proposed")}>
            Add as proposed
          </button>
          <button className="cw-btn cw-btn--primary" disabled={busy || text.trim().length < 3} onClick={() => add("approved")}>
            {busy ? "Adding…" : "Add as approved"}
          </button>
        </div>
      </section>

      <div className="status-tabs" role="tablist" aria-label="Status">
        {STATUSES.map((status) => (
          <button
            key={status.key}
            role="tab"
            aria-selected={tab === status.key}
            className={`status-tab${tab === status.key ? " is-current" : ""}`}
            onClick={() => setTab(status.key)}
          >
            {status.label}
            <span className="status-tab__count">{count(status.key)}</span>
          </button>
        ))}
      </div>

      {learnings === null ? (
        <p>Loading…</p>
      ) : shown.length === 0 ? (
        <p className="section__quiet">{STATUSES.find((status) => status.key === tab)?.empty}</p>
      ) : (
        groups.map((group) => (
          <section className="section learning-group" key={group.key}>
            <div className="section__head">
              <h2>{name(group.key)}</h2>
              {tab === "approved" && group.items.length > LONG_LIST && (
                <p className="notice">
                  {group.items.length} approved learnings: a long list dilutes each one. Retire or merge some.
                </p>
              )}
            </div>
            {group.items.map(card)}
          </section>
        ))
      )}
    </>
  );
}
