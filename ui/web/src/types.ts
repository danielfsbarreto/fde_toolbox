export type Workstream =
  | "pre-sales" | "poc" | "enablement" | "support" | "delivery" | "internal-initiative" | "hiring" | "events" | "other";
export type Involvement = "led" | "contributed" | "observed";

export interface FollowUp {
  id: string;
  reviewed: boolean;
  what: string;
  owner: string | null;
  due_if_stated: string | null;
  /** The Todoist task this follow-up created when the day was finalized, if any */
  todoist?: {
    url: string;
    target: string | null;
    due_date: string | null;
    /** commented: an open task already covered it, so it was added there as a comment */
    action: "created" | "commented";
    title: string | null;
  } | null;
}

export interface Topic {
  id: string;
  reviewed: boolean;
  agent_added: boolean;
  title: string;
  workstream: Workstream;
  what_happened: string;
  status_or_decision: string | null;
  risks: string[];
  my_involvement: Involvement;
  follow_ups: FollowUp[];
  /** Removed in review, shown in place so they can be restored */
  removed_follow_ups: Pick<FollowUp, "id" | "what" | "owner" | "due_if_stated">[];
  people: string[];
  confidence: "high" | "medium" | "low";
  inferred: boolean;
  account_key: string;
  attribution: "rules" | "content" | "review" | "manual";
  attribution_reason: string;
  links: string[];
}

export interface Section {
  key: string;
  name: string;
  headline: string;
  comment: string;
  quiet: boolean;
  events: number;
  topics: Topic[];
  entries: ManualEntry[];
}

export interface ManualEntry {
  id?: string;
  account_key: string;
  title: string;
  what: string;
  workstream: Workstream;
  time: string | null;
  duration_min: number | null;
  people: string[];
  follow_ups: { what: string; owner?: string | null; due_if_stated?: string | null }[];
}

export interface Review {
  id: string;
  round: number;
  status: "waiting" | "answered" | "superseded";
  created_at: string;
  can_answer: boolean;
}

export interface Day {
  date: string;
  status: "draft" | "working" | "final";
  version: number;
  reviewed_at: string | null;
  sections: Section[];
  removed: Topic[];
  manual_entries: ManualEntry[];
  agent_notes: string[];
  accounts: Record<string, string>;
  review: Review | null;
}

export interface DaySummary {
  date: string;
  status: Day["status"];
  reviewed_at: string | null;
  review: Review | null;
  topics: number;
  reviewed: number;
}

export type Scope = { kind: "day" } | { kind: "account"; key: string } | { kind: "topic"; key: string };

export interface AgentRequest {
  scope: Scope;
  label: string;
  instruction: string;
}

export const WORKSTREAMS: Workstream[] = [
  "pre-sales", "poc", "enablement", "support", "delivery", "internal-initiative", "hiring", "events", "other",
];

export type LearningStep = "attribution" | "topics" | "follow_ups";
export type LearningStatus = "proposed" | "approved" | "denied" | "retired";

/** Short guidance the flow's LLM steps follow; only approved learnings reach them. */
export interface Learning {
  id: string;
  /** "general", or an account key */
  scope: string;
  /** The steps it applies to; empty means every step */
  steps: LearningStep[];
  text: string;
  status: LearningStatus;
  source: "manual" | "assessment";
  evidence: string[];
  created_at: string | null;
  updated_at: string | null;
}

export type ReportLanguage = "en" | "pt" | "es";

export interface ReportRequest {
  accounts: string[];
  start: string;
  end: string;
  language: ReportLanguage;
  comment: string;
}

/** An account report: saved by the app, written by the account_report flow from final days. */
export interface ReportSummary {
  id: string;
  request: ReportRequest;
  status: "queued" | "running" | "done" | "failed";
  error: string | null;
  title: string | null;
  created_at: string | null;
  finished_at: string | null;
  /** Set when the app does not start runs on AMP: how to run this one locally */
  local_command: string | null;
}

// Builds (the project builder) ---------------------------------------------------------------------------------

export type BuildStage = "spec" | "design" | "code" | "test";
export type StageStatus = "not_started" | "draft" | "approved" | "stale";
export type BuildStatus = "working" | "waiting" | "idle";

export interface StageState {
  status: StageStatus;
  version: number;
  approved_version: number | null;
}

export interface BuildSummary {
  id: string;
  title: string;
  account: string | null;
  status: BuildStatus;
  /** While working: what the agent is doing (the latest activity) */
  now: string | null;
  current: BuildStage;
  stages: Record<BuildStage, StageState>;
  /** A form of questions waiting for your answers: nothing else happens on the build until they are given */
  pending_input: { message_id: string; request_id: string; kind?: "questions" | "smoke" } | null;
  /** The env vars the latest design needs, and the required ones without a value: the design cannot be approved
   *  until they are set (both null in lists) */
  env_needed?: EnvVar[] | null;
  env_missing?: string[] | null;
  /** The latest code version's smoke test: it must pass (or be skipped) before the code can be approved */
  smoke?: { version: number; status: SmokeStatus; reason: string } | null;
  seq: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface ArtifactCard {
  stage: BuildStage;
  version: number;
  summary: string;
  changes: string[];
  gate: boolean;
}

export interface QuestionOption {
  value: string;
  label: string;
  description: string | null;
}

export interface Question {
  id: string;
  prompt: string;
  why: string | null;
  options: QuestionOption[];
  recommended: string | null;
}

export interface Answer {
  option?: string | null;
  text: string;
}

/** Questions an agent asks instead of guessing; its answers resume the same step */
export interface InputRequest {
  id: string;
  agent: string;
  stage: BuildStage;
  questions: Question[];
  /** smoke: the smoke test's inputs; it can be skipped with a reason, and doesn't hold the build */
  kind?: "questions" | "smoke";
  status: "pending" | "answered" | "dismissed";
  answers: Record<string, Answer>;
}

export interface BuildMessage {
  id: string;
  seq: number;
  role: "user" | "assistant";
  author: string;
  text: string;
  card: ArtifactCard | null;
  input_request: InputRequest | null;
  /** The form whose answers this user message carries: the answered form shows them, so it is not shown again */
  answers_for?: string | null;
  turn_id: string | null;
  delivery: "queued" | "sending" | "sent" | "answered" | "failed" | null;
  created_at: string | null;
}

export interface ActivityEntry {
  seq: number;
  turn_id: string | null;
  /** phase: a stage of a code turn starting or ending; step: one thing done in it (detail.step says what) */
  kind: "turn" | "route" | "agent" | "tool" | "output" | "artifact" | "error" | "done" | "phase" | "step";
  text: string;
  detail: Record<string, unknown>;
  at: string | null;
}

export interface Criterion {
  id: string;
  text: string;
  given: string | null;
  when: string | null;
  then: string | null;
}

export interface Spec {
  kind: "spec";
  title: string;
  goal: string;
  users: string[];
  inputs: string[];
  outputs: string[];
  non_goals: string[];
  examples: string[];
  criteria: Criterion[];
  open_questions: string[];
}

// Fields marked optional arrived after the first designs were saved
export interface StateField {
  name: string;
  type: string;
  description: string;
  initial?: string;
}

interface Element {
  id: string;
  name: string;
  serves: string[];
  details?: string;
}

export interface StateChange {
  field: string;
  how: string;
}

export interface FlowStep extends Element {
  kind: "start" | "listen" | "router";
  triggers: string[];
  join?: "any" | "all";
  routes: string[];
  does: string;
  uses: string[];
  reads?: string[];
  writes?: StateChange[];
  output_model?: string | null;
  persist?: boolean;
  human_feedback?: string | null;
}

export interface CrewSpec extends Element {
  process: "sequential" | "hierarchical";
  agents: string[];
  tasks: string[];
}

export interface AgentSpec extends Element {
  role: string;
  goal: string;
  backstory: string;
  llm: string;
  tools: string[];
  knowledge?: string[];
}

export interface TaskSpec extends Element {
  description: string;
  expected_output: string;
  agent: string;
  context: string[];
  output_model: string | null;
  guardrail?: string | null;
}

export interface ToolNeed {
  kind: "amp_connection" | "env_var";
  name: string;
  purpose: string;
}

export interface Package {
  name: string;
  version: string;
  why: string;
}

export interface ToolSpec extends Element {
  kind: "platform" | "builtin" | "mcp" | "custom";
  purpose: string;
  why_custom?: string;
  app: string | null;
  actions: string[];
  class_name: string | null;
  server: string | null;
  inputs: { name: string; type: string; description: string }[];
  output: string;
  calls: string;
  failures: string[];
  test_plan: string[];
  packages?: Package[];
  needs: ToolNeed[];
}

export interface ModelField {
  name: string;
  type: string;
  description: string;
  required: boolean;
}

export interface OutputModel extends Element {
  fields: ModelField[];
}

export interface StartsBy {
  kind: "kickoff" | "amp_trigger" | "schedule";
  inputs: { name: string; type: string; description: string }[];
  app: string | null;
  event: string | null;
  schedule: string;
}

export interface IntegrationStatus {
  app: string;
  status: "connected" | "not_connected" | "unknown";
  detail: string;
  checked_at: string | null;
}

export interface EnvVar {
  name: string;
  purpose: string;
  secret: boolean;
  required?: boolean; // designs before 2026-10-21 have no `required`: treat as required
}

/** Which of a build's env vars have a value (never the values), and your other builds that have one to reuse. */
export interface EnvStatus {
  set: { name: string; set_at: string | null }[];
  elsewhere: Record<string, { build_id: string; title: string }[]>;
  can_store: boolean;
}

export interface Design {
  kind: "design";
  shape: "flow" | "crew";
  rationale: string;
  summary: string;
  starts_by?: StartsBy;
  state: StateField[];
  models?: OutputModel[];
  steps: FlowStep[];
  crews: CrewSpec[];
  agents: AgentSpec[];
  tasks: TaskSpec[];
  tools: ToolSpec[];
  env_vars: EnvVar[];
  dependencies?: Package[];
  notes: string[];
  integrations: IntegrationStatus[];
  warnings: string[];
}

export type SmokeStatus = "waiting" | "passed" | "failed" | "skipped";
export type Phase = "sandbox" | "writing" | "checks" | "smoke" | "result";

export interface CodeFile {
  path: string;
  content: string;
  implements: string[];
  author: string;
}

export interface Check {
  name: string;
  status: "passed" | "failed" | "skipped";
  summary: string;
  output: string;
}

export interface SmokeTest {
  status: SmokeStatus;
  inputs: Record<string, string>;
  result: string;
  output: string;
  seconds: number | null;
  trace_url: string | null;
  reason: string;
  attempts: number;
}

/** A version of the project: its files (values redacted), how it checked out, and its smoke test */
export interface Code {
  kind: "code";
  package: string;
  shape: "flow" | "crew";
  files: CodeFile[];
  skipped_files: string[];
  checks: Check[];
  structure: Record<string, unknown> | null;
  mismatches: string[];
  smoke: SmokeTest;
  notes: string[];
}

export interface Artifact {
  id: string;
  stage: BuildStage;
  version: number;
  content: Spec | Design | Code;
  based_on: Partial<Record<BuildStage, number>>;
  summary: string;
  author: string;
  created_at: string | null;
}

export interface ArtifactVersion {
  version: number;
  summary: string;
  author: string;
  created_at: string | null;
}

export interface BuildList {
  builds: BuildSummary[];
  waiting: number;
  runs_on_amp: boolean;
  local_command: string | null;
}

export interface Timeline {
  build: BuildSummary;
  messages: BuildMessage[];
  activity: ActivityEntry[];
}

export interface NewBuildMessage {
  text: string;
  action?: "approve" | "request_changes" | "smoke";
  stage?: BuildStage;
  version?: number;
  ref?: { path: string; start: number; end: number };
  request_id?: string;
  answers?: Record<string, Answer>;
}
