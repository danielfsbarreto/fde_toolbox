import { useEffect, useRef, useState } from "react";
import type { AgentRequest, Review, Scope } from "../types";

const DAY: { scope: Scope; label: string } = { scope: { kind: "day" }, label: "The whole day" };

interface Props {
  review: Review | null;
  status: "draft" | "working" | "final";
  /** The day's state and next step, in the words the rest of the page uses */
  hint: string;
  /** Topics not reviewed yet: Finalize says so, and is the bright button only when none are left */
  remaining: number;
  queued: AgentRequest[];
  onChange: (queued: AgentRequest[]) => void;
  /** Set by "Ask the agent…" on a topic or an account: what the box is about. */
  target: { scope: Scope; label: string } | null;
  onTargetUsed: () => void;
  onSend: (requests: AgentRequest[]) => void;
  onFinalize: () => void;
  busy: boolean;
}

/** Bottom bar: one slim row (where the day stands, Ask the agent, Finalize) until you ask the agent something; then
 *  the request box (about the day, an account or a topic) and the requests queued so far. It never grows past a few
 *  lines: the page keeps room for it (--bar-height). */
export function RequestBar(props: Props) {
  const { review, status, queued, onChange, busy, target, onTargetUsed } = props;
  const [about, setAbout] = useState(DAY);
  const [text, setText] = useState("");
  const [editing, setEditing] = useState<number | null>(null);
  const [asking, setAsking] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const bar = useRef<HTMLDivElement>(null);

  // Keep the page's bottom padding equal to the bar's height, so the bar never covers content.
  useEffect(() => {
    const element = bar.current;
    if (!element) return;
    const observer = new ResizeObserver(() =>
      document.documentElement.style.setProperty("--bar-height", `${element.offsetHeight}px`),
    );
    observer.observe(element);
    return () => {
      observer.disconnect();
      document.documentElement.style.removeProperty("--bar-height");
    };
  }, []);

  useEffect(() => {
    if (target) {
      setAbout(target);
      setEditing(null);
      setAsking(true);
      requestAnimationFrame(() => box.current?.focus());
      onTargetUsed();
    }
  }, [target, onTargetUsed]);

  const canAnswer = Boolean(review?.can_answer) && status !== "working";
  const current = text.trim() ? { ...about, instruction: text.trim() } : null;
  // While a queued request is being edited, the box replaces it rather than adding a second copy
  const all =
    editing === null
      ? [...queued, ...(current ? [current] : [])]
      : queued.map((item, i) => (i === editing ? current : item)).filter((item): item is AgentRequest => item !== null);

  const keep = () => {
    if (!text.trim()) return;
    const request = { ...about, instruction: text.trim() };
    onChange(editing === null ? [...queued, request] : queued.map((item, i) => (i === editing ? request : item)));
    setText("");
    setAbout(DAY);
    setEditing(null);
  };

  const composing = asking || queued.length > 0 || text.trim().length > 0;
  const close = () => {
    setAsking(false);
    setAbout(DAY);
    setEditing(null);
  };

  return (
    <div className="bar" ref={bar}>
      <div className="bar__inner">
        {composing && queued.length > 0 && (
          <ul className="bar__queue" aria-label="Requests for the agent">
            {queued.map((request, index) => (
              <li className={`bar__item${editing === index ? " is-editing" : ""}`} key={`${request.label}-${index}`}>
                <span className="bar__scope">{request.label}</span>
                <span className="bar__text">{request.instruction}</span>
                <button
                  className="icon-btn"
                  onClick={() => {
                    setEditing(index);
                    setAbout({ scope: request.scope, label: request.label });
                    setText(request.instruction);
                    box.current?.focus();
                  }}
                >
                  Edit
                </button>
                <button
                  className="icon-btn"
                  onClick={() => {
                    onChange(queued.filter((_, i) => i !== index));
                    if (editing === index) {
                      setEditing(null);
                      setText("");
                    }
                  }}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}

        {composing && (
        <div className="bar__composer">
          <div className="bar__about">
            <span className="label">Ask the agent about</span>
            <span className="bar__scope">{about.label}</span>
            {about.scope.kind !== "day" && (
              <button className="icon-btn" onClick={() => setAbout(DAY)} aria-label="Ask about the whole day instead">
                Whole day instead
              </button>
            )}
            {queued.length === 0 && !text.trim() && (
              <button className="icon-btn bar__close" onClick={close} aria-label="Close the request box">
                Close
              </button>
            )}
          </div>
          <div className="bar__row">
            <textarea
              ref={box}
              className="field bar__box"
              rows={1}
              value={text}
              placeholder={
                about.scope.kind === "day"
                  ? "Look deeper, or tell the agent what you think it missed"
                  : "What should the agent look for or rework here?"
              }
              onChange={(event) => setText(event.target.value)}
              onKeyDown={(event) => {
                // Enter adds the request to the list; Shift+Enter is a new line
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  keep();
                }
              }}
            />
            {/* Square, as tall as the box: + adds the request to the list, ✓ saves the one being edited */}
            <button
              className="bar__add"
              disabled={!text.trim()}
              onClick={keep}
              aria-label={editing === null ? "Add request" : "Save request"}
              title={editing === null ? "Add request (Enter)" : "Save request (Enter)"}
            >
              <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                {editing === null ? <path d="M12 5v14M5 12h14" /> : <path d="M5 12.5l4.5 4.5L19 7.5" />}
              </svg>
            </button>
          </div>
        </div>
        )}

        <div className="bar__actions">
          <span className="bar__hint">{props.hint}</span>
          <div className="bar__buttons">
            {composing ? (
              <button
                className="cw-btn cw-btn--ghost"
                disabled={!canAnswer || busy || all.length === 0}
                onClick={() => {
                  props.onSend(all);
                  setText("");
                  close();
                }}
              >
                Send to agent{all.length > 1 ? ` (${all.length})` : ""}
              </button>
            ) : (
              <button
                className="cw-btn cw-btn--ghost"
                disabled={!canAnswer || busy}
                onClick={() => {
                  setAsking(true);
                  requestAnimationFrame(() => box.current?.focus());
                }}
              >
                Ask the agent…
              </button>
            )}
            <button
              className={`cw-btn ${props.remaining > 0 ? "cw-btn--ghost" : "cw-btn--primary"}`}
              disabled={!canAnswer || busy}
              onClick={props.onFinalize}
              title={props.remaining > 0 ? "You can finalize before reviewing everything" : undefined}
            >
              {props.remaining > 0 ? (
                <>
                  Finalize · {props.remaining} <span className="wide-only">not reviewed</span>
                  <span className="narrow-only">left</span>
                </>
              ) : (
                "Finalize the day"
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
