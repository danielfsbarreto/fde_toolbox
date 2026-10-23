import type { BuildStage, BuildStatus, BuildSummary, StageStatus } from "./types";

/** The stages in order, and their names (the flow's fde_core.domain.builds says the same). */
export const STAGES: BuildStage[] = ["spec", "design", "code", "test"];
export const STAGE_NAMES: Record<BuildStage, string> = { spec: "Spec", design: "Design", code: "Code", test: "Tests" };
/** What each stage's specialist does; the stages after the spec arrive in later phases of the builder. */
export const STAGE_ABOUT: Record<BuildStage, string> = {
  spec: "What it does and how we know it works: acceptance criteria",
  design: "Flow or crew, agents, tasks and tools, drawn as a diagram",
  code: "The CrewAI project, written and run in a sandbox",
  test: "A test per acceptance criterion, run offline",
};
export const AVAILABLE: BuildStage[] = ["spec", "design", "code"];

export const STAGE_STATUS: Record<StageStatus, { label: string; tone: string }> = {
  not_started: { label: "Not started", tone: "idle" },
  draft: { label: "Waiting for you", tone: "action" },
  approved: { label: "Approved", tone: "done" },
  stale: { label: "Stale", tone: "busy" },
};

export const BUILD_STATUS: Record<BuildStatus, { label: string; tone: string }> = {
  working: { label: "Working", tone: "busy" },
  waiting: { label: "Waiting for you", tone: "action" },
  idle: { label: "Idle", tone: "idle" },
};

/** One line on where the build is: its current stage and that stage's state. */
export function whereItIs(build: BuildSummary): string {
  const stage = build.stages[build.current];
  const version = stage.version ? ` v${stage.version}` : "";
  return `${STAGE_NAMES[build.current]}${version} · ${STAGE_STATUS[stage.status].label}`;
}
