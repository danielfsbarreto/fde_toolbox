import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Crumbs, StatePill } from "../components/Crumbs";
import { dayName } from "../dayState";
import type { ReportLanguage, ReportSummary } from "../types";

interface Props {
  onToast: (message: string) => void;
  navigate: (path: string) => void;
}

const POLL_MS = 5_000;
const LANGUAGES: { key: ReportLanguage; label: string }[] = [
  { key: "en", label: "English" },
  { key: "pt", label: "Portuguese" },
  { key: "es", label: "Spanish" },
];
const STATUS: Record<ReportSummary["status"], { label: string; tone: string }> = {
  queued: { label: "Queued", tone: "idle" },
  running: { label: "Being written", tone: "busy" },
  done: { label: "Ready", tone: "done" },
  failed: { label: "Failed", tone: "action" },
};

const iso = (day: Date) => `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;

/** Last week, Monday to Friday: the usual report. */
function lastWeek(): { start: string; end: string } {
  const today = new Date();
  const monday = new Date(today);
  monday.setDate(today.getDate() - ((today.getDay() + 6) % 7) - 7);
  const friday = new Date(monday);
  friday.setDate(monday.getDate() + 4);
  return { start: iso(monday), end: iso(friday) };
}

export function period(start: string, end: string): string {
  const a = dayName(start);
  const b = dayName(end);
  return `${a.short} – ${b.short}`;
}

/** Account reports: pick accounts, dates, language and a goal; the agent writes it from your final days. */
export function Reports({ onToast, navigate }: Props) {
  const [reports, setReports] = useState<ReportSummary[] | null>(null);
  const [accounts, setAccounts] = useState<Record<string, string>>({});
  const [onAmp, setOnAmp] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const week = lastWeek();
  const [chosen, setChosen] = useState<string[]>([]);
  const [start, setStart] = useState(week.start);
  const [end, setEnd] = useState(week.end);
  const [language, setLanguage] = useState<ReportLanguage>("en");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await api.reports();
      setReports(data.reports);
      setAccounts(data.accounts);
      setOnAmp(data.runs_on_amp);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The reports could not be loaded.");
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  // While a report is being written, check again
  useEffect(() => {
    if (!reports?.some((item) => item.status === "queued" || item.status === "running")) return;
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [reports, load]);

  const valid = chosen.length > 0 && start && end && start <= end;
  const generate = async () => {
    if (!valid) return;
    setBusy(true);
    try {
      const report = await api.addReport({ accounts: chosen, start, end, language, comment });
      onToast(report.local_command ? "Saved. Run it locally with the command shown." : "The agent is writing the report.");
      navigate(`/reports/${report.id}`);
    } catch (err) {
      onToast(err instanceof Error ? err.message : "The report could not be started.");
      setBusy(false);
    }
  };
  const remove = async (report: ReportSummary) => {
    try {
      await api.deleteReport(report.id);
      setReports((current) => (current ?? []).filter((item) => item.id !== report.id));
      onToast("Report deleted.");
    } catch (err) {
      onToast(err instanceof Error ? err.message : "The report could not be deleted.");
    }
  };

  return (
    <>
      <Crumbs items={[{ label: "All days", onClick: () => navigate("/") }, { label: "Reports" }]} />
      <section className="hero hero--compact">
        <Badge label="Reports" />
        <h1>
          Account <em>reports</em>
        </h1>
        <p className="hero__sub">
          A written account of a period, from your final days only: a summary, key developments, decisions, risks and
          the follow-ups still open. Download it as a PDF.
        </p>
      </section>

      {error && <div className="notice">{error}</div>}

      <section className="cw-card report-form" aria-label="New report">
        <div className="report-form__field">
          <span className="label">Accounts</span>
          <div className="learning__steps">
            {Object.entries(accounts).map(([key, name]) => (
              <button
                key={key}
                className={`step-chip${chosen.includes(key) ? " is-on" : ""}`}
                onClick={() => setChosen(chosen.includes(key) ? chosen.filter((item) => item !== key) : [...chosen, key])}
              >
                {name}
              </button>
            ))}
          </div>
        </div>
        <div className="report-form__row">
          <label className="report-form__field">
            <span className="label">From</span>
            <input className="field" type="date" value={start} max={end} onChange={(event) => setStart(event.target.value)} />
          </label>
          <label className="report-form__field">
            <span className="label">To</span>
            <input className="field" type="date" value={end} min={start} onChange={(event) => setEnd(event.target.value)} />
          </label>
          <label className="report-form__field">
            <span className="label">Language</span>
            <select className="field" value={language} onChange={(event) => setLanguage(event.target.value as ReportLanguage)}>
              {LANGUAGES.map((item) => (
                <option key={item.key} value={item.key}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className="report-form__field">
          <span className="label">Goal for the agent (optional)</span>
          <textarea
            className="field"
            rows={2}
            value={comment}
            placeholder="Who it is for and what it should stress, e.g. For my manager's weekly review: what is at risk and where I need help."
            onChange={(event) => setComment(event.target.value)}
          />
        </label>
        <div className="learning-form__submit">
          <button className="cw-btn cw-btn--primary" disabled={!valid || busy} onClick={generate}>
            {busy ? "Starting…" : "Write the report"}
          </button>
        </div>
        {!onAmp && (
          <p className="section__quiet">
            This copy of the app does not start runs on AMP: a new report is saved, and its page shows the command to run
            it locally.
          </p>
        )}
      </section>

      <section className="section">
        <div className="section__head">
          <Badge label="Past reports" />
        </div>
        {reports === null ? (
          <p>Loading…</p>
        ) : reports.length === 0 ? (
          <p className="section__quiet">No reports yet.</p>
        ) : (
          <div className="days">
            {reports.map((report) => (
              <div className="cw-card report-row" key={report.id}>
                <button className="report-row__main" onClick={() => navigate(`/reports/${report.id}`)}>
                  <h3>{report.title ?? report.request.accounts.map((key) => accounts[key] ?? key).join(", ")}</h3>
                  <span className="tag">
                    {period(report.request.start, report.request.end)} ·{" "}
                    {report.request.accounts.map((key) => accounts[key] ?? key).join(", ")} · {report.request.language}
                  </span>
                </button>
                <span className="day-row__side">
                  <StatePill label={STATUS[report.status].label} tone={STATUS[report.status].tone} />
                  {report.status === "done" && (
                    <a className="cw-btn cw-btn--ghost cw-btn--small" href={`/api/reports/${report.id}/pdf`}>
                      PDF
                    </a>
                  )}
                  <button className="icon-btn" onClick={() => remove(report)}>
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
