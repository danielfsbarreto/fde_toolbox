import { useEffect, useRef, useState } from "react";
import { Inline } from "./Markdown";
import type { Answer, InputRequest, NewBuildMessage, Question } from "../../types";

interface Props {
  request: InputRequest;
  /** This form is the one the build waits for (else it was answered) */
  open: boolean;
  busy: boolean;
  onSend: (message: NewBuildMessage) => Promise<boolean>;
}

/** The recommended option first (forms are stored that way; older ones are shown that way too). */
const ordered = (question: Question) =>
  [...question.options].sort((a, b) => Number(b.value === question.recommended) - Number(a.value === question.recommended));

const answeredWell = (question: Question, answer: Answer | undefined) =>
  !!answer &&
  ((!!answer.option && question.options.some((o) => o.value === answer.option)) || answer.text.trim().length > 0);

/** The questions an agent asks instead of guessing, one per tab: pick an option, write your own answer, or both.
 *  Nothing is chosen for you: the recommended option is only marked. Enter on an unanswered question takes the
 *  recommendation and moves on, so accepting them is one key per question. The agent continues once all are. */
export function QuestionForm({ request, open, busy, onSend }: Props) {
  const [answers, setAnswers] = useState<Record<string, Answer>>(() =>
    Object.fromEntries(request.questions.map((q) => [q.id, { option: null, text: "" }])),
  );
  const [current, setCurrent] = useState(0);
  const box = useRef<HTMLTextAreaElement>(null);
  const moved = useRef(false); // focus the answer box only once the user moves between questions
  const [sending, setSending] = useState(false);
  const stored = Object.keys(request.answers).length > 0;
  const shown = open || !stored ? answers : request.answers;
  const unanswered = request.questions.filter((q) => !answeredWell(q, answers[q.id])).length;
  const locked = busy || sending;
  const done = request.questions.length - unanswered;
  const smoke = request.kind === "smoke";
  const [skipping, setSkipping] = useState(false);
  const [reason, setReason] = useState("");

  useEffect(() => {
    if (moved.current) box.current?.focus();
  }, [current]);
  useEffect(() => {
    if (open) box.current?.focus({ preventScroll: true }); // answer with the keyboard right away
  }, [open]);
  const go = (index: number) => {
    moved.current = true;
    setCurrent(index);
  };
  const set = (id: string, change: Partial<Answer>) => setAnswers((now) => ({ ...now, [id]: { ...now[id], ...change } }));
  const sendButton = useRef<HTMLButtonElement>(null);
  /** The first unanswered question after `from` (wrapping round), or -1 when all are answered. */
  const nextOpen = (given: Record<string, Answer>, from: number) => {
    const count = request.questions.length;
    for (let step = 1; step <= count; step++) {
      const index = (from + step) % count;
      if (!answeredWell(request.questions[index], given[request.questions[index].id])) return index;
    }
    return -1;
  };
  /** Choosing an option answers the question: move on to the next one still open, or to Send when none is. */
  const choose = (id: string, option: string) => {
    const now = { ...answers, [id]: { ...answers[id], option } };
    setAnswers(now);
    const next = nextOpen(now, current);
    window.setTimeout(() => (next >= 0 ? go(next) : sendButton.current?.focus()), 250);
  };
  /** Enter or Next: keep this question's answer (or take the recommendation when it has none) and go on; after the
   *  last one, send, or go to a question still unanswered. */
  const advance = () => {
    const question = request.questions[current];
    let now = answers;
    if (!answeredWell(question, answers[question.id]) && question.recommended) {
      now = { ...answers, [question.id]: { ...answers[question.id], option: question.recommended } };
      setAnswers(now);
    }
    if (current < request.questions.length - 1) return go(current + 1);
    const missing = request.questions.findIndex((q) => !answeredWell(q, now[q.id]));
    if (missing >= 0) return go(missing);
    send(now);
  };
  const send = async (given: Record<string, Answer> = answers) => {
    if (sending) return;
    setSending(true);
    const summary = request.questions
      .map((q) => {
        const answer = given[q.id];
        const label = q.options.find((o) => o.value === answer.option)?.label;
        return `- **${q.prompt}** ${[label, answer.text.trim()].filter(Boolean).join(" — ")}`;
      })
      .join("\n");
    const clean = Object.fromEntries(
      Object.entries(given).map(([id, answer]) => [id, { option: answer.option || null, text: answer.text.trim() }]),
    );
    const text = smoke ? (summary ? `Run the smoke test with:\n\n${summary}` : "Run the smoke test") : `My answers:\n\n${summary}`;
    await onSend({ text, request_id: request.id, answers: clean });
    setSending(false);
  };
  /** A smoke test that can't run unattended (e.g. it needs a connection you don't have yet): skipped, with why. */
  const skip = async () => {
    if (sending || !reason.trim()) return;
    setSending(true);
    await onSend({
      text: `Skip the smoke test: ${reason.trim()}`,
      request_id: request.id,
      answers: { skip: { option: null, text: reason.trim() } },
    });
    setSending(false);
  };

  if (!open && smoke && (request.status === "dismissed" || request.answers.skip)) {
    return (
      <div className="qform is-answered">
        <span className="label">Smoke test</span>{" "}
        <span className="section__quiet">
          {request.answers.skip ? `Skipped: ${request.answers.skip.text}` : "Not run: the conversation moved on"}
        </span>
      </div>
    );
  }

  if (!open) {
    // Answered: one compact line per question, to read back what was decided
    return (
      <div className="qform is-answered">
        <span className="label">{smoke ? "Smoke test inputs" : "Answered"}</span>
        <table className="qtable">
          <tbody>
            {request.questions.map((question, index) => {
              const answer = shown[question.id] ?? { option: null, text: "" };
              const chosen = question.options.find((option) => option.value === answer.option);
              return (
                <tr key={question.id}>
                  <td className="qtable__number">{index + 1}</td>
                  <td className="qtable__question">
                    <Inline>{question.prompt}</Inline>
                  </td>
                  <td className="qtable__given">
                    {chosen && <Inline>{chosen.label}</Inline>}
                    {chosen && answer.text && " — "}
                    {answer.text && <Inline>{answer.text}</Inline>}
                    {!chosen && !answer.text && "…"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  const skipPanel = smoke && (
    <div className="qform__skip">
      {skipping ? (
        <>
          <textarea
            className="field qform__text"
            rows={2}
            autoFocus
            value={reason}
            placeholder="Why it can't run unattended (e.g. it needs a Gmail connection I don't have yet)"
            onChange={(event) => setReason(event.target.value)}
          />
          <span className="qform__nav">
            <button type="button" className="cw-btn cw-btn--ghost cw-btn--small" disabled={!reason.trim() || locked} onClick={skip}>
              Skip the smoke test
            </button>
            <button type="button" className="icon-btn" onClick={() => setSkipping(false)}>
              Cancel
            </button>
          </span>
        </>
      ) : (
        <button type="button" className="icon-btn" onClick={() => setSkipping(true)}>
          Skip…
        </button>
      )}
    </div>
  );

  if (request.questions.length === 0) {
    // A smoke test that needs no inputs: run it, or skip it
    return (
      <div className="qform">
        <header className="qform__head">
          <span className="label">Smoke test</span>
        </header>
        <div className="qform__submit">
          <button className="cw-btn cw-btn--primary cw-btn--small" disabled={locked} onClick={() => send({})}>
            {sending ? "Starting…" : "Run the smoke test"}
          </button>
        </div>
        {skipPanel}
      </div>
    );
  }

  const question = request.questions[current];
  const answer = answers[question.id] ?? { option: null, text: "" };
  const last = current === request.questions.length - 1;
  return (
    <form
      className="qform"
      onSubmit={(event) => {
        event.preventDefault();
        if (!unanswered) send();
      }}
    >
      <header className="qform__head">
        <span className="label">{smoke ? "Smoke test inputs" : `${request.agent} needs to know`}</span>
        <span className="qform__progress" aria-label={`${done} of ${request.questions.length} answered`}>
          <span className="qform__bar">
            <span
              className={`qform__fill${unanswered ? "" : " is-done"}`}
              style={{ width: `${(done / request.questions.length) * 100}%` }}
            />
          </span>
          <span className="qform__count">
            {unanswered ? `${done} of ${request.questions.length} answered` : "all answered"}
          </span>
        </span>
      </header>

      {request.questions.length > 1 && (
        <div className="qtabs" role="tablist" aria-label="Questions">
          {request.questions.map((q, index) => {
            const answeredThis = answeredWell(q, answers[q.id]);
            return (
              <button
                key={q.id}
                type="button"
                role="tab"
                aria-selected={index === current}
                className={`qtab${index === current ? " is-on" : ""}${answeredThis ? " is-done" : " is-open"}`}
                title={q.prompt}
                onClick={() => go(index)}
              >
                <span className="qtab__mark" aria-hidden>
                  {answeredThis ? "✓" : index + 1}
                </span>
                <span className="qtab__label">Question {index + 1}</span>
              </button>
            );
          })}
        </div>
      )}

      <fieldset className="qform__question" role="tabpanel" disabled={locked}>
        <legend className="qform__prompt">
          <Inline>{question.prompt}</Inline>
        </legend>
        {question.why && (
          <p className="qform__why">
            <Inline>{question.why}</Inline>
          </p>
        )}
        {question.options.length > 0 && (
          <div className="qform__options" role="radiogroup">
            {ordered(question).map((option) => {
              const chosen = answer.option === option.value;
              return (
                <label key={option.value} className={`qform__option${chosen ? " is-chosen" : ""}`}>
                  <input
                    type="radio"
                    name={`${request.id}-${question.id}`}
                    checked={chosen}
                    onChange={() => choose(question.id, option.value)}
                  />
                  <span className="qform__option-body">
                    <span className="qform__option-label">
                      <Inline>{option.label}</Inline>
                      {option.value === question.recommended && <span className="qform__recommended">Recommended</span>}
                    </span>
                    {option.description && (
                      <span className="qform__option-description">
                        <Inline>{option.description}</Inline>
                      </span>
                    )}
                  </span>
                </label>
              );
            })}
            {answer.option && (
              <button type="button" className="icon-btn qform__clear" onClick={() => set(question.id, { option: null })}>
                None of these: I'll write my own
              </button>
            )}
          </div>
        )}
        <textarea
          ref={box}
          className="field qform__text"
          rows={2}
          value={answer.text}
          placeholder={answer.option ? "Anything to add? (optional)" : "Your answer, in your own words"}
          onChange={(event) => set(question.id, { text: event.target.value })}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              advance();
            }
          }}
        />
        <span className="qform__keys">
          Enter:{" "}
          {answeredWell(question, answer) || !question.recommended
            ? last
              ? smoke
                ? "run the smoke test"
                : "send the answers"
              : "next question"
            : `take the recommendation and ${last ? "send" : "continue"}`}{" "}
          · Shift+Enter: new line
        </span>
      </fieldset>

      <div className="qform__submit">
        {request.questions.length > 1 && (
          <span className="qform__nav">
            <button
              type="button"
              className="icon-btn"
              disabled={current === 0}
              onClick={() => go(current - 1)}
            >
              ‹ Back
            </button>
            <button type="button" className="icon-btn" disabled={last} onClick={advance}>
              Next ›
            </button>
          </span>
        )}
        <button
          ref={sendButton}
          className="cw-btn cw-btn--primary cw-btn--small"
          type="submit"
          disabled={!!unanswered || locked}
        >
          {sending ? "Sending…" : smoke ? "Run the smoke test" : "Send answers"}
        </button>
        {unanswered > 0 && <span className="section__quiet">Every question needs an option or your own answer.</span>}
      </div>
      {skipPanel}
    </form>
  );
}
