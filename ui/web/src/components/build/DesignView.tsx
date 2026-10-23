import { useEffect, useId, useMemo, useState } from "react";
import { api } from "../../api";
import type {
  Design,
  FlowStep,
  Spec,
  StageState,
  StartsBy,
  ToolSpec,
} from "../../types";
import { EnvChecklist } from "./EnvChecklist";
import { Inline, Markdown } from "./Markdown";

interface Props {
  buildId: string;
  design: Design;
  previous: Design | null;
  /** The spec version the design was built on, and the stage's state (stale or not) */
  basedOn: number | undefined;
  stage: StageState;
  /** A value was set or removed: the build's gate (what is missing) changed */
  onEnvChange?: () => void;
}

type Change = "added" | "changed" | null;

/** Every element of a design, the ones that have ids. */
const elementsOf = (d: Design) => [
  ...d.steps,
  ...d.crews,
  ...d.agents,
  ...d.tasks,
  ...d.tools,
  ...(d.models ?? []),
];

/** Element ids added or changed since the previous version, and those removed. */
function changesSince(design: Design, previous: Design | null) {
  const all = elementsOf;
  const before = new Map(
    (previous ? all(previous) : []).map((e) => [e.id, JSON.stringify(e)]),
  );
  const marks = new Map<string, Change>();
  if (previous) {
    for (const element of all(design)) {
      const old = before.get(element.id);
      marks.set(
        element.id,
        old === undefined
          ? "added"
          : old === JSON.stringify(element)
            ? null
            : "changed",
      );
    }
  }
  const now = new Set(all(design).map((e) => e.id));
  const removed = previous ? all(previous).filter((e) => !now.has(e.id)) : [];
  return { marks, removed };
}

const quote = (text: string) =>
  `"${text.replace(/"/g, "#quot;").replace(/[<>]/g, "")}"`;
const node = (id: string) => id.replace(/[^A-Za-z0-9]/g, "_");

/** The diagram, from the design itself (never written by the LLM): a flow's steps and how they trigger each other,
 *  or a crew's agents and the tasks they perform. */
export function mermaidOf(design: Design): string {
  if (design.shape === "flow" && design.steps.length) {
    const lines = ["flowchart TD"];
    const byName = new Map(design.steps.map((s) => [s.name, s]));
    const routers = design.steps.filter((s) => s.kind === "router");
    for (const step of design.steps) {
      const label = quote(`${step.name}`);
      const shape =
        step.kind === "start"
          ? `([${label}])`
          : step.kind === "router"
            ? `{${label}}`
            : `[${label}]`;
      lines.push(`  ${node(step.id)}${shape}:::${step.kind}`);
    }
    for (const step of design.steps) {
      // A step that waits for all its triggers (and_) gets thick edges labelled "and"
      const all = step.join === "all" && step.triggers.length > 1;
      for (const trigger of step.triggers) {
        const from = byName.get(trigger);
        if (from)
          lines.push(
            all
              ? `  ${node(from.id)} ==>|and| ${node(step.id)}`
              : `  ${node(from.id)} --> ${node(step.id)}`,
          );
        for (const router of routers.filter(
          (r) => r.routes.includes(trigger) && !from,
        )) {
          lines.push(
            `  ${node(router.id)} -->|${quote(trigger)}| ${node(step.id)}`,
          );
        }
      }
    }
    lines.push("  classDef start fill:#fff0ef,stroke:#ff5a50,color:#0d0d0d");
    lines.push("  classDef router fill:#fff7e6,stroke:#ff9f1c,color:#0d0d0d");
    lines.push("  classDef listen fill:#ffffff,stroke:#474747,color:#0d0d0d");
    return lines.join("\n");
  }
  const lines = ["flowchart LR"];
  for (const agent of design.agents)
    lines.push(
      `  ${node(agent.id)}([${quote(agent.role || agent.name)}]):::agent`,
    );
  design.tasks.forEach((task, index) => {
    lines.push(`  ${node(task.id)}[${quote(task.name)}]:::task`);
    if (task.agent) lines.push(`  ${node(task.agent)} -.-> ${node(task.id)}`);
    const next = design.tasks[index + 1];
    if (next) lines.push(`  ${node(task.id)} --> ${node(next.id)}`);
  });
  lines.push("  classDef agent fill:#fff0ef,stroke:#ff5a50,color:#0d0d0d");
  lines.push("  classDef task fill:#ffffff,stroke:#474747,color:#0d0d0d");
  return lines.join("\n");
}

function Diagram({ code }: { code: string }) {
  const id = useId().replace(/[^A-Za-z0-9]/g, "");
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    import("mermaid")
      .then(async ({ default: mermaid }) => {
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "base",
          themeVariables: {
            fontFamily: "Inter, system-ui, sans-serif",
            fontSize: "13px",
            lineColor: "#8e8e8e",
          },
        });
        const { svg: drawn } = await mermaid.render(`design-${id}`, code);
        if (live) setSvg(drawn);
      })
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [code, id]);
  if (failed) return <pre className="design__code">{code}</pre>;
  if (!svg) return <div className="design__diagram is-loading">Drawing…</div>;
  return (
    <div
      className="design__diagram"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}

function Serves({ ids, touched }: { ids: string[]; touched?: Set<string> }) {
  if (!ids.length) return null;
  return (
    <span className="design__serves">
      {ids.map((ac) => (
        <span
          key={ac}
          className={`design__ac${touched?.has(ac) ? " is-touched" : ""}`}
        >
          {ac}
        </span>
      ))}
    </span>
  );
}

function Mark({ change }: { change: Change | undefined }) {
  return change ? (
    <span className={`tag design__mark is-${change}`}>{change}</span>
  ) : null;
}

/** What an element says beyond its fields, folded away. */
function Details({ text }: { text?: string }) {
  if (!text?.trim()) return null;
  return (
    <details className="design__details">
      <summary>Details</summary>
      <div className="md">
        <Markdown>{text}</Markdown>
      </div>
    </details>
  );
}

function startsBy(starts: StartsBy) {
  if (starts.kind === "amp_trigger")
    return `On the AMP trigger ${starts.app ?? "?"} · ${starts.event ?? "?"}`;
  if (starts.kind === "schedule")
    return `On a schedule: ${starts.schedule || "not set"}`;
  return "When kicked off with inputs";
}

const KIND: Record<ToolSpec["kind"], string> = {
  platform: "AMP integration",
  builtin: "crewai_tools",
  mcp: "MCP",
  custom: "Custom",
};

function ToolRow({
  tool,
  status,
  change,
  touched,
}: {
  tool: ToolSpec;
  status?: string;
  change?: Change;
  touched?: Set<string>;
}) {
  const [open, setOpen] = useState(false);
  const what =
    tool.kind === "platform"
      ? `${tool.app}${tool.actions.length ? ` · ${tool.actions.join(", ")}` : ""}`
      : tool.kind === "builtin"
        ? tool.class_name
        : tool.kind === "mcp"
          ? tool.server
          : null;
  return (
    <li className={`design__tool is-${tool.kind}`}>
      <div className="design__tool-head">
        <span className="design__id">{tool.id}</span>
        <span className={`design__kind is-${tool.kind}`}>
          {KIND[tool.kind]}
        </span>
        <strong>
          <Inline>{tool.name}</Inline>
        </strong>
        {tool.kind === "platform" && status && (
          <span className={`design__conn is-${status}`}>
            {status === "connected"
              ? "✓ Connected"
              : status === "not_connected"
                ? "Connect in AMP"
                : "Not checked"}
          </span>
        )}
        <Mark change={change} />
      </div>
      <p className="design__text">
        <Inline>{tool.purpose}</Inline>
        {what && <code className="design__what">{what}</code>}
      </p>
      {tool.kind === "custom" && tool.why_custom && (
        <p className="design__when">
          <span className="label">Why custom</span>{" "}
          <Inline>{tool.why_custom}</Inline>
        </p>
      )}
      <Serves ids={tool.serves} touched={touched} />
      <Details text={tool.details} />
      {tool.kind === "custom" && (
        <>
          <button
            className="icon-btn design__more"
            onClick={() => setOpen(!open)}
          >
            {open
              ? "Hide the tool's design"
              : "Inputs, output, failures, test plan, packages"}
          </button>
          {open && (
            <div className="design__custom">
              {tool.inputs.length > 0 && (
                <p>
                  <span className="label">Inputs</span>{" "}
                  {tool.inputs.map((input) => (
                    <code key={input.name} title={input.description}>
                      {input.name}: {input.type}
                    </code>
                  ))}
                </p>
              )}
              {tool.output && (
                <p>
                  <span className="label">Returns</span>{" "}
                  <Inline>{tool.output}</Inline>
                </p>
              )}
              {tool.calls && (
                <p>
                  <span className="label">Calls</span>{" "}
                  <Inline>{tool.calls}</Inline>
                </p>
              )}
              {tool.failures.length > 0 && (
                <div>
                  <span className="label">Failures it handles</span>
                  <ul>
                    {tool.failures.map((f, i) => (
                      <li key={i}>
                        <Inline>{f}</Inline>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {(tool.packages ?? []).length > 0 && (
                <p>
                  <span className="label">Packages</span>{" "}
                  {(tool.packages ?? []).map((p) => (
                    <code key={p.name} title={p.why}>
                      {p.name}
                      {p.version}
                    </code>
                  ))}
                </p>
              )}
              {tool.test_plan.length > 0 && (
                <div>
                  <span className="label">Test plan</span>
                  <ul>
                    {tool.test_plan.map((t, i) => (
                      <li key={i}>
                        <Inline>{t}</Inline>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </li>
  );
}

const trigger = (step: FlowStep) =>
  step.kind === "start"
    ? "On kickoff"
    : step.triggers.length
      ? `After ${step.triggers.join(step.join === "all" && step.triggers.length > 1 ? " and " : " or ")}`
      : "—";

/** The design: its shape and why, the diagram, the steps, crews, agents, tasks, tools and integrations, what it needs
 *  to run, how it covers the spec, and (when the spec changed) which parts need rework. */
export function DesignView({
  buildId,
  design,
  previous,
  basedOn,
  stage,
  onEnvChange,
}: Props) {
  const [spec, setSpec] = useState<Spec | null>(null);
  const [latestSpec, setLatestSpec] = useState<{
    version: number;
    spec: Spec;
  } | null>(null);
  useEffect(() => {
    let live = true;
    if (basedOn)
      api
        .artifact(buildId, "spec", basedOn)
        .then(
          (d) =>
            live &&
            d.artifact?.content.kind === "spec" &&
            setSpec(d.artifact.content),
        );
    api.artifact(buildId, "spec").then((d) => {
      if (live && d.artifact?.content.kind === "spec")
        setLatestSpec({
          version: d.artifact.version,
          spec: d.artifact.content,
        });
    });
    return () => {
      live = false;
    };
  }, [buildId, basedOn]);

  const { marks, removed } = useMemo(
    () => changesSince(design, previous),
    [design, previous],
  );
  const code = useMemo(() => mermaidOf(design), [design]);
  const status = new Map(design.integrations.map((s) => [s.app, s.status]));
  const named = new Map(elementsOf(design).map((e) => [e.id, e.name]));
  const names = (ids: string[]) =>
    ids.map((id) => named.get(id) ?? id).join(", ");

  // Stale: the criteria changed since the spec this design was built on, and the elements serving them
  const touched = useMemo(() => {
    if (stage.status !== "stale" || !spec || !latestSpec)
      return new Set<string>();
    const now = new Map(
      latestSpec.spec.criteria.map((c) => [c.id, JSON.stringify(c)]),
    );
    return new Set(
      spec.criteria
        .filter((c) => now.get(c.id) !== JSON.stringify(c))
        .map((c) => c.id),
    );
  }, [stage.status, spec, latestSpec]);
  const rework = elementsOf(design).filter((e) =>
    e.serves.some((ac) => touched.has(ac)),
  );
  const coverage = (spec?.criteria ?? []).map((criterion) => ({
    criterion,
    by: elementsOf(design).filter((e) => e.serves.includes(criterion.id)),
  }));
  const connections = design.tools.flatMap((t) =>
    t.needs.filter((n) => n.kind === "amp_connection"),
  );
  const models = design.models ?? [];
  const packages = design.dependencies ?? [];
  // Who returns each structured output
  const returnedBy = (model: string) =>
    [...design.steps, ...design.tasks].filter((e) => e.output_model === model);
  // The flow's state, and which steps write and read each field
  const stateRows = design.state.map((field) => ({
    field,
    writes: design.steps.flatMap((s) =>
      (s.writes ?? [])
        .filter((w) => w.field === field.name)
        .map((w) => ({ step: s, how: w.how })),
    ),
    reads: design.steps.filter((s) => (s.reads ?? []).includes(field.name)),
  }));
  // Designs written before steps declared their reads and writes have no state flow to show
  const traced = design.steps.some(
    (s) => (s.reads ?? []).length || (s.writes ?? []).length,
  );
  const kickoffInputs = new Set(
    design.starts_by?.kind === "kickoff"
      ? design.starts_by.inputs.map((i) => i.name)
      : [],
  );

  return (
    <article className="design">
      <header className="design__head">
        <span className={`design__shape is-${design.shape}`}>
          {design.shape === "flow" ? "Flow" : "Crew"}
        </span>
        {basedOn && <span className="tag">Built on Spec v{basedOn}</span>}
      </header>
      <div className="md">
        <Markdown>{design.summary}</Markdown>
      </div>
      <p className="design__rationale">
        <span className="label">Why a {design.shape}</span>{" "}
        <Inline>{design.rationale}</Inline>
      </p>
      {design.starts_by && (
        <p className="design__rationale">
          <span className="label">Starts</span> {startsBy(design.starts_by)}
          {design.starts_by.kind === "kickoff" &&
            design.starts_by.inputs.map((input) => (
              <code
                key={input.name}
                className="design__what"
                title={input.description}
              >
                {input.name}: {input.type}
              </code>
            ))}
        </p>
      )}

      {stage.status === "stale" && (
        <div className="design__stale" role="note">
          <strong>The spec changed since this design.</strong>{" "}
          {touched.size
            ? `${[...touched].join(", ")} changed: ${rework.length ? `${rework.map((e) => `${e.id} ${e.name}`).join(", ")} need rework.` : "no element serves them directly."}`
            : `It was built on Spec v${basedOn}; the spec is now v${latestSpec?.version ?? "?"}.`}
        </div>
      )}
      {design.warnings.length > 0 && (
        <ul className="design__warnings">
          {design.warnings.map((warning, i) => (
            <li key={i}>{warning}</li>
          ))}
        </ul>
      )}

      <section className="design__section">
        <span className="label">Diagram</span>
        <Diagram code={code} />
      </section>

      {design.steps.length > 0 && (
        <section className="design__section">
          <span className="label">Steps</span>
          <ol className="design__list">
            {design.steps.map((step) => (
              <li
                key={step.id}
                className={rework.includes(step) ? "is-rework" : ""}
              >
                <div className="design__row">
                  <span className="design__id">{step.id}</span>
                  <code>{step.name}</code>
                  <span className={`design__kind is-${step.kind}`}>
                    {step.kind}
                  </span>
                  <span className="design__when">{trigger(step)}</span>
                  {step.persist && (
                    <span className="design__badge">@persist</span>
                  )}
                  {step.human_feedback && (
                    <span className="design__badge" title={step.human_feedback}>
                      @human_feedback
                    </span>
                  )}
                  {step.output_model && <code>{step.output_model}</code>}
                  <Mark change={marks.get(step.id)} />
                </div>
                <p className="design__text">
                  <Inline>{step.does}</Inline>
                  {step.routes.length > 0 && (
                    <span className="design__when">
                      {" "}
                      Routes: {step.routes.join(", ")}.
                    </span>
                  )}
                  {step.uses.length > 0 && (
                    <span className="design__when">
                      {" "}
                      Uses {names(step.uses)}.
                    </span>
                  )}
                </p>
                {step.human_feedback && (
                  <p className="design__when">
                    Asks a person: <Inline>{step.human_feedback}</Inline>
                  </p>
                )}
                <Serves ids={step.serves} touched={touched} />
                <Details text={step.details} />
              </li>
            ))}
          </ol>
        </section>
      )}

      {stateRows.length > 0 && (
        <section className="design__section">
          <span className="label">State and how it changes</span>
          {!traced && (
            <p className="design__when">
              This version predates tracing state through the steps, so it
              doesn't say which steps change or read each field. Request changes
              on the design to have the architect add it.
            </p>
          )}
          <div className="design__table-wrap">
            <table className="design__table">
              <thead>
                <tr>
                  <th>Field</th>
                  <th>Starts as</th>
                  {traced && <th>Changed by</th>}
                  {traced && <th>Read by</th>}
                </tr>
              </thead>
              <tbody>
                {stateRows.map(({ field, writes, reads }) => (
                  <tr key={field.name}>
                    <td>
                      <code>
                        {field.name}: {field.type}
                      </code>
                      {field.description && (
                        <div className="design__when">
                          <Inline>{field.description}</Inline>
                        </div>
                      )}
                    </td>
                    <td>
                      {field.initial ? (
                        <code>{field.initial}</code>
                      ) : (
                        <span className="design__when">—</span>
                      )}
                    </td>
                    {traced && (
                      <td>
                        {writes.length ? (
                          writes.map(({ step, how }, i) => (
                            <div key={i}>
                              <code>{step.name}</code> <Inline>{how}</Inline>
                            </div>
                          ))
                        ) : kickoffInputs.has(field.name) ? (
                          <span className="design__when">
                            Only the kickoff input
                          </span>
                        ) : (
                          <span className="design__unset">
                            No step changes it
                          </span>
                        )}
                      </td>
                    )}
                    {traced && (
                      <td>
                        {reads.length ? (
                          reads.map((s) => (
                            <div key={s.id}>
                              <code>{s.name}</code>
                            </div>
                          ))
                        ) : (
                          <span className="design__when">—</span>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {design.crews.length > 0 && (
        <section className="design__section">
          <span className="label">Crews</span>
          <ul className="design__list">
            {design.crews.map((crew) => (
              <li
                key={crew.id}
                className={rework.includes(crew) ? "is-rework" : ""}
              >
                <div className="design__row">
                  <span className="design__id">{crew.id}</span>
                  <strong>{crew.name}</strong>
                  <span className="design__when">{crew.process}</span>
                  <Mark change={marks.get(crew.id)} />
                </div>
                <p className="design__text">
                  Agents: {names(crew.agents) || "—"} · Tasks:{" "}
                  {names(crew.tasks) || "—"}
                </p>
                <Serves ids={crew.serves} touched={touched} />
                <Details text={crew.details} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {design.agents.length > 0 && (
        <section className="design__section">
          <span className="label">Agents</span>
          <ul className="design__cards">
            {design.agents.map((agent) => (
              <li
                key={agent.id}
                className={`design__card${rework.includes(agent) ? " is-rework" : ""}`}
              >
                <div className="design__row">
                  <span className="design__id">{agent.id}</span>
                  <strong>
                    <Inline>{agent.role}</Inline>
                  </strong>
                  <Mark change={marks.get(agent.id)} />
                </div>
                <p className="design__text">
                  <Inline>{agent.goal}</Inline>
                </p>
                <p className="design__when">
                  {agent.llm && <code>{agent.llm}</code>}{" "}
                  {agent.tools.length > 0 && <>Tools: {names(agent.tools)}</>}
                </p>
                {(agent.knowledge ?? []).length > 0 && (
                  <p className="design__when">
                    Knowledge: {(agent.knowledge ?? []).join(", ")}
                  </p>
                )}
                <Serves ids={agent.serves} touched={touched} />
                <Details text={agent.details} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {design.tasks.length > 0 && (
        <section className="design__section">
          <span className="label">Tasks</span>
          <ol className="design__list">
            {design.tasks.map((task) => (
              <li
                key={task.id}
                className={rework.includes(task) ? "is-rework" : ""}
              >
                <div className="design__row">
                  <span className="design__id">{task.id}</span>
                  <strong>{task.name}</strong>
                  {task.agent && (
                    <span className="design__when">
                      by {names([task.agent])}
                    </span>
                  )}
                  {task.output_model && <code>{task.output_model}</code>}
                  <Mark change={marks.get(task.id)} />
                </div>
                <p className="design__text">
                  <Inline>{task.description}</Inline>
                </p>
                <p className="design__when">
                  Expected: <Inline>{task.expected_output}</Inline>
                </p>
                {task.guardrail && (
                  <p className="design__when">
                    Guardrail: <Inline>{task.guardrail}</Inline>
                  </p>
                )}
                <Serves ids={task.serves} touched={touched} />
                <Details text={task.details} />
              </li>
            ))}
          </ol>
        </section>
      )}

      {models.length > 0 && (
        <section className="design__section">
          <span className="label">Structured outputs</span>
          <ul className="design__cards">
            {models.map((model) => (
              <li
                key={model.id}
                className={`design__card${rework.includes(model) ? " is-rework" : ""}`}
              >
                <div className="design__row">
                  <span className="design__id">{model.id}</span>
                  <code>{model.name}</code>
                  <Mark change={marks.get(model.id)} />
                </div>
                <ul className="design__fields">
                  {model.fields.map((f) => (
                    <li key={f.name}>
                      <code>
                        {f.name}
                        {f.required ? "" : "?"}: {f.type}
                      </code>{" "}
                      <Inline>{f.description}</Inline>
                    </li>
                  ))}
                </ul>
                {returnedBy(model.name).length > 0 && (
                  <p className="design__when">
                    Returned by{" "}
                    {returnedBy(model.name)
                      .map((e) => e.name)
                      .join(", ")}
                  </p>
                )}
                <Serves ids={model.serves} touched={touched} />
                <Details text={model.details} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {design.tools.length > 0 && (
        <section className="design__section">
          <span className="label">Tools and integrations</span>
          <ul className="design__list">
            {design.tools.map((tool) => (
              <ToolRow
                key={tool.id}
                tool={tool}
                status={tool.app ? status.get(tool.app) : undefined}
                change={marks.get(tool.id)}
                touched={touched}
              />
            ))}
          </ul>
        </section>
      )}

      {(connections.length > 0 ||
        design.env_vars.length > 0 ||
        packages.length > 0) && (
        <section className="design__section">
          <span className="label">What it needs to run</span>
          <ul className="design__needs">
            {connections.map((need) => (
              <li key={`amp-${need.name}`} className="design__need">
                <span
                  className={`design__conn is-${status.get(need.name) ?? "unknown"}`}
                >
                  {status.get(need.name) === "connected" ? "✓" : "○"}
                </span>
                <Inline>{need.purpose || `Connect ${need.name} in AMP`}</Inline>
              </li>
            ))}
            {packages.map((p) => (
              <li key={`pkg-${p.name}`}>
                <span className="tag">PyPI</span>{" "}
                <code>
                  {p.name}
                  {p.version}
                </code>{" "}
                <Inline>{p.why}</Inline>
              </li>
            ))}
          </ul>
          {design.env_vars.length > 0 && (
            <>
              <span className="label env__label">Environment variables</span>
              <EnvChecklist buildId={buildId} vars={design.env_vars} onChange={onEnvChange} readOnly />
            </>
          )}
        </section>
      )}

      {coverage.length > 0 && (
        <section className="design__section">
          <span className="label">Coverage of the spec</span>
          <ul className="design__coverage">
            {coverage.map(({ criterion, by }) => (
              <li key={criterion.id} className={by.length ? "" : "is-gap"}>
                <span
                  className={`design__ac${touched.has(criterion.id) ? " is-touched" : ""}`}
                >
                  {criterion.id}
                </span>
                <span className="design__text">
                  {by.length
                    ? by.map((e) => `${e.id} ${e.name}`).join(", ")
                    : "Not covered"}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {design.notes.length > 0 && (
        <section className="design__section">
          <span className="label">Notes for the code</span>
          <ul>
            {design.notes.map((note, i) => (
              <li key={i}>
                <Inline>{note}</Inline>
              </li>
            ))}
          </ul>
        </section>
      )}

      {removed.length > 0 && (
        <p className="design__when">
          Removed since the previous version:{" "}
          {removed.map((e) => `${e.id} ${e.name}`).join(", ")}
        </p>
      )}
    </article>
  );
}
