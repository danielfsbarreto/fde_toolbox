import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../api";
import { AVAILABLE, BUILD_STATUS, STAGES, whereItIs } from "../buildState";
import { ArtifactPanel } from "../components/build/ArtifactPanel";
import { Conversation } from "../components/build/Conversation";
import { EnvChecklist } from "../components/build/EnvChecklist";
import { StageBar } from "../components/build/StageBar";
import { Crumbs, StatePill } from "../components/Crumbs";
import { EditableTitle } from "../components/EditableTitle";
import type {
  ActivityEntry,
  Artifact,
  ArtifactCard,
  ArtifactVersion,
  BuildList,
  BuildMessage,
  BuildStage,
  BuildSummary,
  NewBuildMessage,
} from "../types";

interface Props {
  id: string;
  list: BuildList | null;
  refreshList: () => Promise<void>;
  onToast: (message: string) => void;
  navigate: (path: string) => void;
}

const FAST_MS = 1_500; // while the agent works, or a message waits
const SLOW_MS = 10_000;

/** One build: its stages, the conversation with the agents, and the artifacts they write. */
export function Build({ id, list, refreshList, onToast, navigate }: Props) {
  const [build, setBuild] = useState<BuildSummary | null>(null);
  const [messages, setMessages] = useState<BuildMessage[]>([]);
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState<BuildStage | "log">("spec");
  const [valuesOpen, setValuesOpen] = useState(false);
  const [version, setVersion] = useState<number | null>(null);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [previous, setPrevious] = useState<Artifact | null>(null);
  const [versions, setVersions] = useState<ArtifactVersion[]>([]);
  const seq = useRef(0);
  const grid = useRef<HTMLDivElement>(null);

  // The columns fill the window below the header: measured, since the header's height varies (dev bar, wrapping)
  useLayoutEffect(() => {
    const element = grid.current;
    if (!element) return;
    const fit = () => {
      const top = element.getBoundingClientRect().top + window.scrollY;
      element.style.setProperty("--build-height", `calc(100vh - ${Math.round(top)}px - 16px)`);
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  });

  const load = useCallback(async () => {
    try {
      const data = await api.timeline(id, seq.current);
      setBuild(data.build);
      if (data.messages.length) {
        setMessages((current) => {
          const known = new Set(current.map((message) => message.id));
          return [...current, ...data.messages.filter((message) => !known.has(message.id))].sort((a, b) => a.seq - b.seq);
        });
      }
      if (data.activity.length) setActivity((current) => [...current, ...data.activity]);
      seq.current = Math.max(seq.current, ...data.messages.map((m) => m.seq), ...data.activity.map((a) => a.seq));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The build could not be loaded.");
    }
  }, [id]);

  // Open on the stage the build is at (the one waiting for you or being worked on), and follow it when it moves on.
  // A stage the builder doesn't offer yet shows the last one it does (e.g. Design while Code is still to come).
  const current = build?.current;
  const followed = useRef<string | null>(null);
  useEffect(() => {
    if (!current || followed.current === `${id}:${current}`) return;
    followed.current = `${id}:${current}`;
    const shownStage = AVAILABLE.includes(current)
      ? current
      : [...AVAILABLE].reverse().find((stage) => STAGES.indexOf(stage) < STAGES.indexOf(current));
    if (!shownStage) return;
    setShown(shownStage);
    setVersion(null);
  }, [id, current]);

  // A form that closes (answered) changed an older message: read the timeline again from the start
  const openForm = build?.pending_input?.message_id ?? null;
  const lastForm = useRef<string | null>(null);
  useEffect(() => {
    const closed = lastForm.current !== null && lastForm.current !== openForm;
    lastForm.current = openForm;
    if (!closed) return;
    api
      .timeline(id, 0)
      .then((data) => {
        setMessages(data.messages);
        setActivity(data.activity);
        seq.current = Math.max(0, ...data.messages.map((m) => m.seq), ...data.activity.map((a) => a.seq));
      })
      .catch(() => undefined);
  }, [id, openForm]);

  // A user message is pending until a reply names it as its turn
  const pending = useMemo(() => {
    const answered = new Set(messages.filter((m) => m.role === "assistant").map((m) => m.turn_id));
    return new Set(messages.filter((m) => m.role === "user" && !answered.has(m.id)).map((m) => m.id));
  }, [messages]);
  const fast = build?.status === "working" || pending.size > 0;

  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    const timer = window.setTimeout(load, fast ? FAST_MS : SLOW_MS);
    return () => window.clearTimeout(timer);
  }, [load, fast, messages, activity, build]);

  // The shown stage's artifact: the chosen version (latest by default) and the one before it, for what changed
  const stageVersion = shown !== "log" ? build?.stages[shown].version : undefined;
  useEffect(() => {
    if (shown === "log" || !stageVersion) {
      setArtifact(null);
      setPrevious(null);
      setVersions([]);
      return;
    }
    let live = true;
    api
      .artifact(id, shown, version ?? undefined)
      .then(async (data) => {
        if (!live) return;
        setArtifact(data.artifact);
        setVersions(data.versions);
        const before = data.artifact && data.artifact.version > 1 ? data.artifact.version - 1 : null;
        const prior = before ? (await api.artifact(id, shown, before)).artifact : null;
        if (live) setPrevious(prior);
      })
      .catch(() => live && setArtifact(null));
    return () => {
      live = false;
    };
  }, [id, shown, version, stageVersion]);

  const send = async (message: NewBuildMessage): Promise<boolean> => {
    try {
      const sent = await api.send(id, message);
      setMessages((current) => (current.some((m) => m.id === sent.id) ? current : [...current, sent]));
      setBuild((current) => (current && list?.runs_on_amp ? { ...current, status: "working" } : current));
      window.setTimeout(load, 300);
      refreshList();
      return true;
    } catch (err) {
      onToast(err instanceof Error ? err.message : "The message was not sent.");
      return false;
    }
  };
  const showCard = (card: ArtifactCard) => {
    setShown(card.stage);
    setVersion(card.version === build?.stages[card.stage].version ? null : card.version);
  };

  if (!build) {
    return (
      <>
        <Crumbs items={[{ label: "All days", onClick: () => navigate("/") }, { label: "Builds", onClick: () => navigate("/builds") }, { label: "Build" }]} />
        {error ? <div className="notice">{error}</div> : <p>Loading…</p>}
      </>
    );
  }
  const others = (list?.builds ?? []).filter((other) => other.id !== build.id);
  return (
    <>
      <Crumbs
        items={[
          { label: "All days", onClick: () => navigate("/") },
          { label: "Builds", onClick: () => navigate("/builds") },
          {
            label: build.title,
            node: (
              <EditableTitle
                value={build.title}
                onSave={async (title) => {
                  try {
                    setBuild(await api.renameBuild(id, title));
                    refreshList();
                    return true;
                  } catch (err) {
                    onToast(err instanceof Error ? err.message : "The title was not saved.");
                    return false;
                  }
                }}
              />
            ),
          },
        ]}
      >
        <span className="tag">{whereItIs(build)}</span>
        <StatePill label={BUILD_STATUS[build.status].label} tone={BUILD_STATUS[build.status].tone} />
      </Crumbs>
      {error && <div className="notice">{error}</div>}
      <StageBar
        build={build}
        others={others}
        shown={shown}
        onShow={(stage) => {
          setShown(stage);
          setVersion(null);
        }}
        navigate={navigate}
        onValues={() => setValuesOpen((open) => !open)}
      />
      {valuesOpen && (build.env_needed ?? []).length > 0 && (
        <section className="values-panel" aria-label="Environment variables">
          <div className="values-panel__head">
            <span className="label">Environment variables</span>
            <button className="icon-btn" onClick={() => setValuesOpen(false)}>
              Close
            </button>
          </div>
          <EnvChecklist buildId={build.id} vars={build.env_needed ?? []} onChange={load} />
        </section>
      )}
      <div className="build" ref={grid}>
        <Conversation
          build={build}
          messages={messages}
          activity={activity}
          pending={pending}
          localCommand={list && !list.runs_on_amp ? list.local_command : null}
          onSend={send}
          onShowArtifact={showCard}
          onEnvChange={load}
        />
        <ArtifactPanel
          shown={shown}
          onShow={(tab) => {
            setShown(tab);
            setVersion(null);
          }}
          artifact={artifact}
          previous={previous}
          versions={versions}
          version={version}
          onVersion={setVersion}
          activity={activity}
          build={build}
          onEnvChange={load}
        />
      </div>
    </>
  );
}
