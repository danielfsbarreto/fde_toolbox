import { useCallback, useEffect, useRef, useState } from "react";
import type { ToastAction } from "../App";
import { ApiError, api } from "../api";
import { AdditionForm } from "../components/AdditionForm";
import { Badge } from "../components/Badge";
import { Crumbs, Progress, StatePill } from "../components/Crumbs";
import { Inline } from "../components/Inline";
import { RequestBar } from "../components/RequestBar";
import { TOPIC_MIME, TopicCard } from "../components/TopicCard";
import { dayName, dayState } from "../dayState";
import type { AgentRequest, Day as DayData, ManualEntry, Section } from "../types";

interface Props {
  date: string;
  onToast: (message: string, action?: ToastAction) => void;
  navigate: (path: string) => void;
}

const POLL_MS = 10_000;

export const title = dayName;

/** The line under the pinned bar: what sits at or above it is "where you are". */
function readingLine(): number {
  const style = getComputedStyle(document.documentElement);
  const px = (name: string) => parseFloat(style.getPropertyValue(name)) || 0;
  return px("--env-bar") + px("--crumbs-height") + 24;
}

function inside(selector: string, key: string, line: number): string | null {
  for (const element of document.querySelectorAll<HTMLElement>(selector)) {
    const box = element.getBoundingClientRect();
    if (box.top <= line && box.bottom > line) return element.dataset[key] ?? null;
  }
  return null;
}

/** The current values of the fields a patch changes: what Undo writes back. */
function previous<T extends object>(source: T, patch: Partial<T>): Partial<T> {
  return Object.fromEntries(Object.keys(patch).map((key) => [key, source[key as keyof T]])) as Partial<T>;
}
const onlyReviewed = (patch: object) => Object.keys(patch).every((key) => key === "reviewed");

export function Day({ date, onToast, navigate }: Props) {
  const [day, setDayState] = useState<DayData | null>(null);
  const latest = useRef<DayData | null>(null);
  const setDay = useCallback((next: DayData) => {
    latest.current = next;
    setDayState(next);
  }, []);
  const [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState<AgentRequest[]>([]);
  const [target, setTarget] = useState<{ scope: AgentRequest["scope"]; label: string } | null>(null);
  const clearTarget = useCallback(() => setTarget(null), []);
  const [pending, setPending] = useState(0);
  const busy = pending > 0;
  const [editingEntry, setEditingEntry] = useState<string | null>(null);
  const [additionAccount, setAdditionAccount] = useState<string | undefined>(undefined);
  const [dropSection, setDropSection] = useState<string | null>(null);
  // Topics are compact rows until opened; several can be open at once
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const toggle = useCallback(
    (id: string) =>
      setExpanded((open) => {
        const next = new Set(open);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      }),
    [],
  );
  // Where you are (the account and topic under the pinned bar), for the breadcrumb and the side list
  const [here, setHere] = useState<{ section: string | null; topic: string | null }>({ section: null, topic: null });
  useEffect(() => {
    let frame = 0;
    const spy = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const line = readingLine();
        const next = { section: inside("[data-section]", "section", line), topic: inside("[data-topic]", "topic", line) };
        setHere((now) => (now.section === next.section && now.topic === next.topic ? now : next));
      });
    };
    spy();
    window.addEventListener("scroll", spy, { passive: true });
    window.addEventListener("resize", spy);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", spy);
      window.removeEventListener("resize", spy);
    };
  }, [day]);

  const load = useCallback(async () => {
    try {
      setDay(await api.day(date));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The day could not be loaded.");
    }
  }, [date, setDay]);

  useEffect(() => {
    load();
  }, [load]);

  // While the agent works, or until the flow is waiting on this day, check for the next review round.
  useEffect(() => {
    if (!day) return;
    const waitingHere = day.review?.status === "waiting" && day.review.can_answer;
    if (day.status !== "working" && waitingHere) return;
    const timer = window.setInterval(async () => {
      try {
        const fresh = await api.day(date);
        if (fresh.version !== day.version || fresh.review?.id !== day.review?.id || fresh.review?.can_answer !== day.review?.can_answer) {
          setDay(fresh);
        }
      } catch {
        /* keep the current view; the next poll retries */
      }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [day, date]);

  type Action = (current: DayData) => Promise<DayData>;
  // Saves run one after another, each on the version the previous one returned: quick edits in a row (blur one
  // field, type in the next) are never refused as stale.
  const queue = useRef<Promise<void>>(Promise.resolve());
  const run = useCallback(
    (action: Action, success?: string, undo?: Action) => {
      setPending((count) => count + 1);
      queue.current = queue.current
        .then(() => perform(action, success, undo))
        .finally(() => setPending((count) => count - 1));
      return queue.current;
    },
    [],
  );
  const perform = useCallback(
    async (action: Action, success?: string, undo?: Action) => {
      const current = latest.current;
      if (!current) return;
      try {
        setDay(await action(current));
        if (success) {
          onToast(success, undo ? { label: "Undo", onClick: () => run(undo, "Undone.") } : undefined);
        }
      } catch (err) {
        if (err instanceof ApiError && (err.status === 409 || err.status === 423)) {
          onToast(err.message);
          await load();
        } else {
          onToast(err instanceof Error ? err.message : "Something went wrong.");
        }
      }
    },
    [load, onToast, setDay],
  );

  // Until the day arrives, the bar and the title are already in place (the date is in the address): nothing jumps
  if (error || !day) {
    const name = dayName(date);
    return (
      <>
        <Crumbs items={[{ label: "All days", onClick: () => navigate("/") }, { label: name.short }]} />
        <section className="hero hero--compact">
          <Badge label="Activity log" />
          <h1>
            {name.weekday}, <em>{name.rest}</em>
          </h1>
          <p className="hero__sub">{error ? "" : "Loading the day…"}</p>
        </section>
        {error && <div className="notice">{error}</div>}
      </>
    );
  }

  // A final day is the record of what happened: read-only. To redo it, it is deleted (Settings) and run again.
  const final = day.status === "final";
  const editable = day.status === "draft";
  const topics = day.sections.flatMap((section) => section.topics);
  const reviewable = topics;
  const reviewed = reviewable.filter((topic) => topic.reviewed).length;
  const visible = day.sections.filter(
    (section) =>
      section.topics.length > 0 ||
      section.entries.length > 0 ||
      !section.quiet ||
      section.comment ||
      (!final && day.removed.some((topic) => topic.account_key === section.key)),
  );
  const quiet = day.sections.filter((section) => !visible.includes(section));
  const heading = dayName(day.date);
  const state = dayState(day.status, day.review);

  // Next unreviewed: the first one after the topic you are on, else the first one left
  const order = visible.flatMap((section) => section.topics);
  // Between topics (an account's head), start from that account's first topic
  const sectionStart = order.findIndex((topic) => topic.account_key === here.section);
  const at = here.topic ? order.findIndex((topic) => topic.id === here.topic) : sectionStart - 1;
  const nextUp = order.slice(at + 1).find((topic) => !topic.reviewed) ?? order.find((topic) => !topic.reviewed);
  const open = (id: string) => {
    setExpanded((now) => new Set(now).add(id));
    requestAnimationFrame(() => document.getElementById(`topic-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }));
  };
  const jump = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  // After a topic is marked reviewed: fold it and move on to the next unreviewed one (in this account or the next),
  // opened if you were reading this one open. Nothing moves when a topic is unmarked, or when the save failed.
  const advance = (fromId: string, wasOpen: boolean) => {
    const order = (latest.current?.sections ?? []).flatMap((section) => section.topics);
    const at = order.findIndex((topic) => topic.id === fromId);
    if (at < 0 || !order[at].reviewed) return;
    const next = order.slice(at + 1).find((topic) => !topic.reviewed) ?? order.find((topic) => !topic.reviewed);
    setExpanded((now) => {
      const updated = new Set(now);
      updated.delete(fromId);
      if (next && wasOpen) updated.add(next.id);
      return updated;
    });
    if (!next) {
      onToast("Every topic is reviewed. Finalize the day when you are ready.");
      return;
    }
    // Two frames: the folded card is laid out before scrolling, so the scroll lands on the right place
    requestAnimationFrame(() =>
      requestAnimationFrame(() =>
        document.getElementById(`topic-${next.id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }),
      ),
    );
  };
  const sectionNames: Record<string, string> = {
    ...Object.fromEntries(day.sections.map((section) => [section.key, section.name])),
    quiet: "Quiet today",
    additions: "Added by you",
  };
  const hereTopic = here.topic ? topics.find((topic) => topic.id === here.topic) : undefined;
  const allOpen = topics.length > 0 && topics.every((topic) => expanded.has(topic.id));

  const entryCard = (entry: ManualEntry) =>
    editingEntry === entry.id ? (
      <AdditionForm
        key={entry.id}
        accounts={day.accounts}
        initial={entry}
        onCancel={() => setEditingEntry(null)}
        onSave={(saved) => {
          setEditingEntry(null);
          run(
            (current) => api.editEntry(current, entry.id!, saved),
            "Addition saved.",
            (current) => api.editEntry(current, entry.id!, entry),
          );
        }}
      />
    ) : (
      <article className="cw-card topic" key={entry.id}>
        <div className="tags">
          <span className="tag">Added by you</span>
          {entry.time && <span className="tag">{entry.time}</span>}
          {entry.duration_min && <span className="tag">{entry.duration_min} min</span>}
        </div>
        <div className="topic__title">{entry.title}</div>
        <p>{entry.what}</p>
        {entry.follow_ups.length > 0 && (
          <div className="topic__row">
            <span className="label">Follow-ups</span>
            <div className="followups">
              {entry.follow_ups.map((item) => (
                <p key={item.what}>{item.what}</p>
              ))}
            </div>
          </div>
        )}
        {editable && (
          <div className="topic__actions">
            <button className="icon-btn" onClick={() => setEditingEntry(entry.id!)}>
              Edit
            </button>
            <button
              className="icon-btn"
              onClick={() =>
                run(
                  (current) => api.removeEntry(current, entry.id!),
                  "Addition removed.",
                  (current) => api.addEntry(current, { ...entry, id: undefined }),
                )
              }
            >
              Remove
            </button>
          </div>
        )}
      </article>
    );

  const ask = (label: string, scope: AgentRequest["scope"]) => setTarget({ scope, label });

  const dropTopic = (event: React.DragEvent, section: Section) => {
    const topicId = event.dataTransfer.getData(TOPIC_MIME);
    setDropSection(null);
    if (!topicId) return;
    event.preventDefault();
    const topic = topics.find((item) => item.id === topicId);
    if (topic && topic.account_key !== section.key) {
      run((current) => api.moveTopic(current, topicId, section.key), `Moved to ${section.name}.`);
    }
  };
  const dropTarget = (section: Section) => ({
    onDragOver: (event: React.DragEvent) => {
      if (editable && event.dataTransfer.types.includes(TOPIC_MIME)) {
        event.preventDefault();
        setDropSection(section.key);
      }
    },
    onDragLeave: () => setDropSection(null),
    onDrop: (event: React.DragEvent) => dropTopic(event, section),
  });

  return (
    <>
      <Crumbs
        items={[
          { label: "All days", onClick: () => navigate("/") },
          { label: heading.short, onClick: () => window.scrollTo({ top: 0, behavior: "smooth" }) },
          ...(here.section ? [{ label: sectionNames[here.section] ?? here.section, onClick: () => jump(here.section!) }] : []),
          ...(hereTopic ? [{ label: hereTopic.title }] : []),
        ]}
      >
        <StatePill label={state.label} tone={state.tone} />
        <Progress done={reviewed} total={reviewable.length} label="reviewed" />
        {!final &&
          (nextUp ? (
            <button className="icon-btn icon-btn--strong" onClick={() => open(nextUp.id)}>
              Next unreviewed ↓
            </button>
          ) : (
            reviewable.length > 0 && <span className="tag">All reviewed ✓</span>
          ))}
        <span className={`save-state${busy ? " is-saving" : ""}`} data-saving={busy} role="status">
          {busy ? "Saving…" : "Saved"}
        </span>
      </Crumbs>

      <section className="hero hero--compact">
        <Badge label="Activity log" />
        <h1>
          {heading.weekday}, <em>{heading.rest}</em>
        </h1>
        <p className="hero__sub">
          {final && <span>{state.next}</span>}
          <button className="icon-btn" onClick={() => setExpanded(allOpen ? new Set() : new Set(topics.map((topic) => topic.id)))}>
            {allOpen ? "Collapse all" : "Expand all"}
          </button>
          <button className="icon-btn" onClick={() => navigate(`/day/${day.date}/settings`)}>
            Settings
          </button>
        </p>
      </section>

      {day.agent_notes.length > 0 && (
        <div className="notice">
          <span className="label">Notes from the agent</span>
          <ul>
            {day.agent_notes.map((note) => (
              <li key={note}>
                <p>{note}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="columns">
        <nav className="sidenav" aria-label="Accounts">
          {visible.map((section) => {
            const own = section.topics;
            return (
              <a
                key={section.key}
                href={`#${section.key}`}
                className={`sidenav__item${here.section === section.key ? " is-current" : ""}${dropSection === section.key ? " is-drop" : ""}`}
                aria-current={here.section === section.key ? "location" : undefined}
                {...dropTarget(section)}
              >
                <span className="sidenav__name">{section.name}</span>
                {own.length > 0 ? (
                  <Progress done={own.filter((topic) => topic.reviewed).length} total={own.length} />
                ) : (
                  <span className="sidenav__count">no topics</span>
                )}
              </a>
            );
          })}
          {quiet.map((section) => (
            <a
              key={section.key}
              href={`#quiet-${section.key}`}
              className={`sidenav__item sidenav__item--quiet${here.section === "quiet" ? " is-current" : ""}`}
            >
              <span>{section.name}</span>
              <span className="sidenav__count">quiet</span>
            </a>
          ))}
          <a className={`sidenav__item${here.section === "additions" ? " is-current" : ""}`} href="#additions">
            <span>Added by you</span>
            <span className="sidenav__count">{day.manual_entries.length}</span>
          </a>
        </nav>

        <div>
          <select
            className="field sidenav__mobile"
            aria-label="Jump to an account"
            onChange={(event) => document.getElementById(event.target.value)?.scrollIntoView({ behavior: "smooth" })}
          >
            {visible.map((section) => (
              <option key={section.key} value={section.key}>
                {section.name}
              </option>
            ))}
            <option value="additions">Added by you</option>
          </select>

          {visible.map((section) => (
            <section
              key={section.key}
              id={section.key}
              data-section={section.key}
              className={`section${dropSection === section.key ? " is-drop" : ""}`}
              {...dropTarget(section)}
            >
              <div className="section__head">
                <Badge label={section.key === "internal" || section.key === "unassigned" ? section.name : "Account"} />
                <h2>{section.name}</h2>
                {section.key !== "internal" && section.key !== "unassigned" && (
                  <Inline
                    multiline
                    className="inline--serif"
                    value={section.headline}
                    placeholder="No headline"
                    disabled={!editable}
                    onSave={(text) =>
                      run(
                        (current) => api.setHeadline(current, section.key, text),
                        "Headline saved.",
                        (current) => api.setHeadline(current, section.key, section.headline),
                      )
                    }
                    ariaLabel="Headline"
                  />
                )}
                <div className="quote">
                  <div className="quote__body">
                    <span className="label">Your comment</span>
                    <Inline
                      multiline
                      value={section.comment}
                      placeholder="Add context for this account (a holiday, a meeting that did not happen)"
                      disabled={!editable}
                      onSave={(text) =>
                        run(
                          (current) => api.setComment(current, section.key, text),
                          "Comment saved.",
                          (current) => api.setComment(current, section.key, section.comment),
                        )
                      }
                    />
                  </div>
                </div>
                <div className="tags">
                  <span className="tag">{section.events} events</span>
                  {editable && (
                    <button className="icon-btn" onClick={() => ask(section.name, { kind: "account", key: section.key })}>
                      Ask the agent about {section.name}…
                    </button>
                  )}
                </div>
              </div>
              {section.topics.length === 0 && section.entries.length === 0 && <p className="section__quiet">No topics.</p>}
              {section.topics.map((topic) => (
                <TopicCard
                  key={topic.id}
                  topic={topic}
                  expanded={expanded.has(topic.id)}
                  onToggle={() => toggle(topic.id)}
                  accounts={day.accounts}
                  editable={editable}
                  onEdit={(patch) =>
                    onlyReviewed(patch)
                      ? run((current) => api.editTopic(current, topic.id, patch)).then(() => {
                          if (patch.reviewed) advance(topic.id, expanded.has(topic.id));
                        })
                      : run(
                          (current) => api.editTopic(current, topic.id, patch),
                          "Saved.",
                          (current) => api.editTopic(current, topic.id, previous(topic, patch)),
                        )
                  }
                  onMove={(key) =>
                    run(
                      (current) => api.moveTopic(current, topic.id, key),
                      `Moved to ${day.accounts[key]}.`,
                      (current) => api.moveTopic(current, topic.id, topic.account_key),
                    )
                  }
                  onRemove={() =>
                    run(
                      (current) => api.removeTopic(current, topic.id),
                      "Topic removed.",
                      (current) => api.restoreTopic(current, topic.id),
                    )
                  }
                  onAsk={() => ask(`Topic: ${topic.title}`, { kind: "topic", key: topic.id })}
                  onFollowUpEdit={(id, patch) => {
                    const before = topic.follow_ups.find((item) => item.id === id);
                    if (!before || onlyReviewed(patch)) return run((current) => api.editFollowUp(current, topic.id, id, patch));
                    return run(
                      (current) => api.editFollowUp(current, topic.id, id, patch),
                      "Follow-up saved.",
                      (current) => api.editFollowUp(current, topic.id, id, previous(before, patch)),
                    );
                  }}
                  onFollowUpAdd={(what) => run((current) => api.addFollowUp(current, topic.id, { what }))}
                  onFollowUpRemove={(id) =>
                    run(
                      (current) => api.removeFollowUp(current, topic.id, id),
                      "Follow-up removed.",
                      (current) => api.restoreFollowUp(current, topic.id, id),
                    )
                  }
                  onFollowUpRestore={(id) => run((current) => api.restoreFollowUp(current, topic.id, id), "Follow-up restored.")}
                  onFollowUpMove={(from, id) => run((current) => api.moveFollowUp(current, from, id, topic.id))}
                />
              ))}
              {section.entries.map(entryCard)}
              {!final &&
                day.removed
                .filter((topic) => topic.account_key === section.key)
                .map((topic) => (
                  <article className="cw-card topic removed" key={topic.id}>
                    <div className="topic__head">
                      <div className="tags">
                        <span className="tag">Removed</span>
                      </div>
                      {editable && (
                        <button className="review-btn" onClick={() => run((current) => api.restoreTopic(current, topic.id), "Topic restored.")}>
                          Restore
                        </button>
                      )}
                    </div>
                    <div className="topic__title">{topic.title}</div>
                  </article>
                ))}
            </section>
          ))}

          {quiet.length > 0 && (
            <section className="section" id="quiet" data-section="quiet">
              <div className="section__head">
                <Badge label="Quiet today" />
                <p>No tool recorded anything for these. Add context, ask the agent to look again, or add what you did.</p>
              </div>
              <div className="quiet-grid">
                {quiet.map((section) => (
                  <article className="cw-card quiet-card" id={`quiet-${section.key}`} key={section.key}>
                    <h3>{section.name}</h3>
                    <p className="section__quiet">No activity recorded.</p>
                    <div className="quote">
                      <div className="quote__body">
                        <span className="label">Your comment</span>
                        <Inline
                          multiline
                          value={section.comment}
                          placeholder="Add context (a holiday, a meeting that did not happen)"
                          disabled={!editable}
                          onSave={(text) =>
                        run(
                          (current) => api.setComment(current, section.key, text),
                          "Comment saved.",
                          (current) => api.setComment(current, section.key, section.comment),
                        )
                      }
                        />
                      </div>
                    </div>
                    {editable && (
                      <div className="topic__actions">
                        <button className="icon-btn" onClick={() => ask(section.name, { kind: "account", key: section.key })}>
                          Ask the agent…
                        </button>
                        <button
                          className="icon-btn"
                          onClick={() => {
                            setAdditionAccount(section.key);
                            document.getElementById("additions")?.scrollIntoView({ behavior: "smooth" });
                          }}
                        >
                          Add offline work
                        </button>
                      </div>
                    )}
                  </article>
                ))}
              </div>
            </section>
          )}

          <section className="section" id="additions" data-section="additions">
            <div className="section__head">
              <Badge label="Offline work" />
              <h2>
                Added by <em>you</em>
              </h2>
              <p>What you did that no tool recorded. It shows under its account, and is part of the day when you finalize it.</p>
            </div>
            {editable && (
              <AdditionForm
                key={additionAccount ?? "new"}
                accounts={day.accounts}
                account={additionAccount}
                onSave={(entry) => {
                  setAdditionAccount(undefined);
                  run((current) => api.addEntry(current, entry), "Added to the day.");
                }}
              />
            )}
          </section>

        </div>
      </div>

      {!final && (
      <RequestBar
        review={day.review}
        status={day.status}
        hint={state.next}
        remaining={reviewable.length - reviewed}
        queued={queued}
        onChange={setQueued}
        target={target}
        onTargetUsed={clearTarget}
        busy={busy}
        onSend={(requests) => {
          if (!day.review) return;
          run((current) => api.sendRequests(current.review!.id, requests), "Sent to the agent. The day comes back when it is done.");
          setQueued([]);
        }}
        onFinalize={() => {
          if (!day.review) return;
          run((current) => api.finalize(current.review!.id), "Finalized. Its follow-ups go to Todoist.");
        }}
      />
      )}

      {day.status === "working" && (
        <div className="working" role="status">
          <div className="working__box">
            <h2>
              The agent is <em>reviewing</em>…
            </h2>
            <p>The day comes back here when it is done.</p>
            <div className="working__pill" />
          </div>
        </div>
      )}
    </>
  );
}
