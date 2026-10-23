import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Crumbs } from "../components/Crumbs";
import type { Day } from "../types";
import { dayName } from "../dayState";

interface Props {
  date: string;
  onToast: (message: string) => void;
  navigate: (path: string) => void;
}

/** A day's settings: kept apart from the review page, so what cannot be undone is never one click away. */
export function DaySettings({ date, onToast, navigate }: Props) {
  const [day, setDay] = useState<Day | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    api
      .day(date)
      .then(setDay)
      .catch((err) => setError(err instanceof ApiError ? err.message : "The day could not be loaded."));
  }, [date]);

  const heading = dayName(date);
  if (error || !day) {
    return (
      <>
        <Crumbs
          items={[
            { label: "All days", onClick: () => navigate("/") },
            { label: heading.short, onClick: () => navigate(`/day/${date}`) },
            { label: "Settings" },
          ]}
        />
        <section className="hero hero--compact">
          <Badge label="Activity log · Settings" />
          <h1>
            {heading.weekday}, <em>{heading.rest}</em>
          </h1>
          <p className="hero__sub">{error ? "" : "Loading…"}</p>
        </section>
        {error && <div className="notice">{error}</div>}
      </>
    );
  }

  const name = `${heading.weekday}, ${heading.rest}`;
  const working = day.status === "working";

  const remove = async () => {
    setDeleting(true);
    try {
      await api.deleteDay(day.date);
      onToast(`${name} was deleted.`);
      navigate("/");
    } catch (err) {
      onToast(err instanceof Error ? err.message : "The day could not be deleted.");
      setDeleting(false);
    }
  };

  return (
    <>
      <Crumbs
        items={[
          { label: "All days", onClick: () => navigate("/") },
          { label: heading.short, onClick: () => navigate(`/day/${day.date}`) },
          { label: "Settings" },
        ]}
      />
      <section className="hero hero--compact">
        <Badge label="Activity log · Settings" />
        <h1>
          {heading.weekday}, <em>{heading.rest}</em>
        </h1>
        <p className="hero__sub">
          <button className="icon-btn" onClick={() => navigate(`/day/${day.date}`)}>
            Back to the day
          </button>
        </p>
      </section>

      <section className="danger-zone" aria-labelledby="danger-zone">
        <div className="section__head">
          <Badge label="Danger zone" />
          <h2 id="danger-zone">
            Cannot be <em>undone</em>
          </h2>
        </div>

        <div className="danger-zone__item">
          <div className="danger-zone__text">
            <h3>Delete this day</h3>
            <p>
              Removes everything recorded for {name}: what was collected, the log, your edits, comments and additions,
              and its review rounds. Run the day again on AMP to rebuild it. Tasks already sent to Todoist stay there: a
              re-run writes its follow-ups again, and any worded differently become new tasks.
            </p>
            {working && <p>The agent is working on this day; it can be deleted once it is done.</p>}
          </div>
          {!confirming && (
            <button className="cw-btn cw-btn--ghost" disabled={working} onClick={() => setConfirming(true)}>
              Delete this day…
            </button>
          )}
        </div>

        {confirming && (
          <div className="danger__confirm" role="alertdialog" aria-label="Confirm deleting the day">
            <p>
              Delete {name} for good? This cannot be undone.
              {day.review?.status === "waiting" ? " Its paused run on AMP stays paused: cancel it there." : ""}
            </p>
            <div className="danger__buttons">
              <button className="cw-btn cw-btn--ghost" disabled={deleting} onClick={() => setConfirming(false)}>
                Keep it
              </button>
              <button className="cw-btn cw-btn--danger" disabled={deleting} onClick={remove}>
                {deleting ? "Deleting…" : "Delete the day"}
              </button>
            </div>
          </div>
        )}
      </section>
    </>
  );
}
