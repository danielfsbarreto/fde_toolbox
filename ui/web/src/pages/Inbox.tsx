import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge } from "../components/Badge";
import { Crumbs, Progress, StatePill } from "../components/Crumbs";
import { dayName, dayState } from "../dayState";
import type { DaySummary } from "../types";

const POLL_MS = 10_000;

export function Inbox({ navigate }: { navigate: (path: string) => void }) {
  const [days, setDays] = useState<DaySummary[] | null>(null);

  useEffect(() => {
    const load = () => api.days().then((result) => setDays(result.days)).catch(() => undefined);
    load();
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, []);

  const head = (
    <>
      <Crumbs items={[{ label: "All days" }]} />
      <section className="hero hero--compact">
        <Badge label="FDE Activity Log" />
        <h1>
          Your days, <em>item by item</em>
        </h1>
        <p className="hero__sub">Open a day to review it topic by topic. Edits are saved as you make them.</p>
      </section>
    </>
  );
  // The bar and the title show at once; only the list waits for the data
  if (!days) {
    return (
      <>
        {head}
        <p>Loading your days…</p>
      </>
    );
  }
  const waiting = days.filter((day) => day.review?.status === "waiting" || day.status === "working");
  const rest = days.filter((day) => !waiting.includes(day));

  const row = (day: DaySummary) => {
    const name = dayName(day.date);
    const state = dayState(day.status, day.review);
    return (
      <a
        key={day.date}
        className={`cw-card day-row${state.tone === "action" ? " is-action" : ""}`}
        href={`/day/${day.date}`}
        onClick={(event) => {
          event.preventDefault();
          navigate(`/day/${day.date}`);
        }}
      >
        <h3>
          {name.weekday}, <em>{name.rest}</em>
        </h3>
        <span className="day-row__side">
          <StatePill label={state.label} tone={state.tone} />
          {day.topics > 0 && <Progress done={day.reviewed} total={day.topics} label="reviewed" />}
          {state.tone === "action" && <span className="cw-btn cw-btn--primary cw-btn--small">Review it →</span>}
        </span>
      </a>
    );
  };

  return (
    <>
      {head}
      <div className="stats">
        <div className="cw-card">
          <div className="stat__num">{waiting.length}</div>
          <div className="stat__label">Waiting for review</div>
        </div>
        <div className="cw-card">
          <div className="stat__num">{days.filter((day) => day.status === "final").length}</div>
          <div className="stat__label">Final</div>
        </div>
      </div>
      {waiting.length > 0 && (
        <section className="section">
          <div className="section__head">
            <Badge label="Needs you" />
          </div>
          <div className="days">{waiting.map(row)}</div>
        </section>
      )}
      <section className="section">
        <div className="section__head">
          <Badge label="Recent days" />
        </div>
        <div className="days">{rest.length ? rest.map(row) : <p>No other days yet.</p>}</div>
      </section>
    </>
  );
}
