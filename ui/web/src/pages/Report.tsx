import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Crumbs, StatePill } from "../components/Crumbs";
import type { ReportSummary } from "../types";
import { period } from "./Reports";

interface Props {
  id: string;
  navigate: (path: string) => void;
}

const POLL_MS = 5_000;

/** One report: its preview (the same HTML the PDF is drawn from) and the download, or its progress. */
export function Report({ id, navigate }: Props) {
  const [report, setReport] = useState<ReportSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [height, setHeight] = useState(1200);

  const load = useCallback(async () => {
    try {
      setReport(await api.report(id));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The report could not be loaded.");
    }
  }, [id]);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (!report || (report.status !== "queued" && report.status !== "running")) return;
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [report, load]);

  const label = report?.title ?? (report ? period(report.request.start, report.request.end) : "Report");
  return (
    <>
      <Crumbs
        items={[
          { label: "All days", onClick: () => navigate("/") },
          { label: "Reports", onClick: () => navigate("/reports") },
          { label },
        ]}
      >
        {report && (
          <StatePill
            label={{ queued: "Queued", running: "Being written", done: "Ready", failed: "Failed" }[report.status]}
            tone={{ queued: "idle", running: "busy", done: "done", failed: "action" }[report.status]}
          />
        )}
        {report?.status === "done" && (
          <a className="cw-btn cw-btn--primary cw-btn--small" href={`/api/reports/${id}/pdf`}>
            Download PDF
          </a>
        )}
      </Crumbs>

      {error && <div className="notice">{error}</div>}
      {report && report.status !== "done" && (
        <section className="cw-card report-progress">
          {report.status === "failed" ? (
            <>
              <h2>The report could not be written</h2>
              <p>{report.error ?? "The run failed."}</p>
            </>
          ) : report.local_command ? (
            <>
              <h2>
                Saved, <em>waiting</em> for a run
              </h2>
              <p>This copy of the app does not start runs on AMP. Run it locally from the repository root:</p>
              <pre className="report-command">{report.local_command}</pre>
              <p>This page updates when it is done.</p>
            </>
          ) : (
            <>
              <h2>
                The agent is <em>writing</em>…
              </h2>
              <p>It reads your final days for the period and writes each account; this takes a minute or two.</p>
              <div className="working__pill" />
            </>
          )}
        </section>
      )}
      {report?.status === "done" && (
        <iframe
          className="report-preview"
          title="Report preview"
          src={`/api/reports/${id}/html`}
          style={{ height }}
          onLoad={(event) => {
            const doc = event.currentTarget.contentDocument;
            if (doc) setHeight(doc.documentElement.scrollHeight + 32);
          }}
        />
      )}
    </>
  );
}
