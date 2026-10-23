import { useMemo, useState } from "react";
import type { Code, CodeFile } from "../../types";
import { Redacted } from "./Redacted";

interface Props {
  buildId: string;
  version: number;
  code: Code;
}

const MARK = { passed: "✓", failed: "✗", skipped: "–" } as const;

/** A code version: how it checked out (its checks and smoke test), where it differs from the design, and its files,
 *  each with the design elements it implements. Values of environment variables show as locked chips. */
export function CodeView({ buildId, version, code }: Props) {
  const files = useMemo(() => [...code.files].sort((a, b) => a.path.localeCompare(b.path)), [code.files]);
  const main = files.find((f) => f.path.endsWith(`${code.package}/main.py`)) ?? files[0];
  const [path, setPath] = useState<string | null>(null);
  const file = files.find((f) => f.path === path) ?? main;
  const smoke = code.smoke;

  return (
    <article className="codeview">
      <div className="codeview__checks">
        {code.checks.map((check) => (
          <span key={check.name} className={`codeview__check is-${check.status}`} title={check.summary}>
            {MARK[check.status]} {check.name}
          </span>
        ))}
        <span className={`codeview__check is-${smoke.status === "waiting" ? "skipped" : smoke.status}`}>
          {smoke.status === "passed" ? "✓" : smoke.status === "failed" ? "✗" : "–"} smoke test
          {smoke.status === "waiting" ? " (not run)" : ""}
        </span>
        <a className="icon-btn codeview__zip" href={`/api/builds/${buildId}/code.zip?version=${version}`}>
          Download ZIP
        </a>
      </div>

      {smoke.status !== "waiting" && (
        <section className="codeview__section">
          <span className="label">Smoke test</span>
          {smoke.status === "skipped" ? (
            <p className="design__when">Skipped: {smoke.reason}</p>
          ) : (
            <>
              <p className="design__when">
                {smoke.status === "passed" ? "Passed" : `Failed: ${smoke.reason}`} in {Math.round(smoke.seconds ?? 0)}s ·
                inputs{" "}
                {Object.entries(smoke.inputs).map(([key, value]) => (
                  <code key={key} className="design__what">
                    {key}={value.length > 40 ? `${value.slice(0, 40)}…` : value}
                  </code>
                ))}
                {smoke.trace_url && (
                  <>
                    {" "}
                    ·{" "}
                    <a href={smoke.trace_url} target="_blank" rel="noreferrer" title="Readable by anyone with the link, for 24 hours">
                      Trace ›
                    </a>
                  </>
                )}
              </p>
              {smoke.result && (
                <details className="design__details" open={smoke.status === "passed"}>
                  <summary>What it returned</summary>
                  <pre className="tstep__output">
                    <Redacted text={smoke.result} />
                  </pre>
                </details>
              )}
              <details className="design__details" open={smoke.status === "failed"}>
                <summary>Its output</summary>
                <pre className="tstep__output">
                  <Redacted text={smoke.output || "(no output)"} />
                </pre>
              </details>
            </>
          )}
        </section>
      )}

      {code.mismatches.length > 0 && (
        <section className="codeview__section">
          <span className="label">Differences from the design</span>
          <ul className="design__warnings">
            {code.mismatches.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        </section>
      )}

      <section className="codeview__section">
        <span className="label">
          Files · {files.length}
          {code.skipped_files.length > 0 && ` (${code.skipped_files.length} not stored: too large or not text)`}
        </span>
        <div className="codeview__files">
          <ul className="codeview__tree">
            {folders(files).map(([folder, inside]) => (
              <li key={folder}>
                {folder && <span className="codeview__folder">{folder}/</span>}
                <ul>
                  {inside.map((f) => (
                    <li key={f.path}>
                      <button
                        className={`codeview__file${f.path === file?.path ? " is-on" : ""}`}
                        title={f.implements.length ? `${f.path} · implements ${f.implements.join(", ")}` : f.path}
                        onClick={() => setPath(f.path)}
                      >
                        <span className="codeview__name">{f.path.split("/").pop()}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
          {file && <FileView file={file} />}
        </div>
      </section>
    </article>
  );
}

/** The files by folder (the project root first), each folder's files by name. */
function folders(files: CodeFile[]): [string, CodeFile[]][] {
  const by = new Map<string, CodeFile[]>();
  for (const file of files) {
    const folder = file.path.includes("/") ? file.path.slice(0, file.path.lastIndexOf("/")) : "";
    by.set(folder, [...(by.get(folder) ?? []), file]);
  }
  return [...by.entries()].sort(([a], [b]) => (a === "" ? -1 : b === "" ? 1 : a.localeCompare(b)));
}

function FileView({ file }: { file: CodeFile }) {
  const lines = file.content.split("\n");
  return (
    <div className="codeview__view">
      <div className="codeview__viewhead">
        <code>{file.path}</code>
        {file.implements.length > 0 && <span className="design__when">implements {file.implements.join(", ")}</span>}
      </div>
      <pre className="codeview__code">
        {lines.map((line, i) => (
          <div key={i} className="codeview__line">
            <span className="codeview__num">{i + 1}</span>
            <span>
              <Redacted text={line} />
            </span>
          </div>
        ))}
      </pre>
    </div>
  );
}
