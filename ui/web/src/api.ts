import type {
  AgentRequest,
  Artifact,
  ArtifactVersion,
  BuildList,
  BuildMessage,
  BuildStage,
  BuildSummary,
  EnvStatus,
  NewBuildMessage,
  Timeline,
  Day,
  DaySummary,
  Learning,
  LearningStatus,
  LearningStep,
  ManualEntry,
  ReportRequest,
  ReportSummary,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 401) {
    window.location.href = "/auth/login";
    throw new ApiError(401, "Sign in first.");
  }
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new ApiError(response.status, (detail as { detail?: string }).detail ?? `Request failed (${response.status}).`);
  }
  return (await response.json()) as T;
}

export const api = {
  me: () => call<{ login: string; name: string; avatar: string | null }>("GET", "/api/me"),
  env: () => call<{ env: "dev" | "production" }>("GET", "/api/env"),
  days: () => call<{ fde_id: string; days: DaySummary[] }>("GET", "/api/days"),
  reports: () =>
    call<{ reports: ReportSummary[]; accounts: Record<string, string>; runs_on_amp: boolean }>("GET", "/api/reports"),
  report: (id: string) => call<ReportSummary>("GET", `/api/reports/${id}`),
  addReport: (request: ReportRequest) => call<ReportSummary>("POST", "/api/reports", request),
  deleteReport: (id: string) => call<{ deleted: string }>("DELETE", `/api/reports/${id}`),
  builds: () => call<BuildList>("GET", "/api/builds"),
  addBuild: (title: string, account: string | null) => call<BuildSummary>("POST", "/api/builds", { title, account }),
  renameBuild: (id: string, title: string) => call<BuildSummary>("PATCH", `/api/builds/${id}`, { title }),
  deleteBuild: (id: string) => call<{ deleted: string }>("DELETE", `/api/builds/${id}`),
  timeline: (id: string, after: number) => call<Timeline>("GET", `/api/builds/${id}?after=${after}`),
  artifact: (id: string, stage: BuildStage, version?: number) =>
    call<{ artifact: Artifact | null; versions: ArtifactVersion[] }>(
      "GET",
      `/api/builds/${id}/artifacts/${stage}${version ? `?version=${version}` : ""}`,
    ),
  buildEnv: (id: string) => call<EnvStatus>("GET", `/api/builds/${id}/env`),
  setBuildEnv: (id: string, name: string, value: string) =>
    call<EnvStatus>("PUT", `/api/builds/${id}/env/${name}`, { value }),
  reuseBuildEnv: (id: string, name: string, fromBuild: string) =>
    call<EnvStatus>("POST", `/api/builds/${id}/env/${name}/reuse`, { from_build: fromBuild }),
  deleteBuildEnv: (id: string, name: string) => call<EnvStatus>("DELETE", `/api/builds/${id}/env/${name}`),
  send: (id: string, message: NewBuildMessage) => call<BuildMessage>("POST", `/api/builds/${id}/messages`, message),
  learnings: () => call<{ learnings: Learning[]; accounts: Record<string, string> }>("GET", "/api/learnings"),
  addLearning: (learning: { scope: string; steps: LearningStep[]; text: string; status: "approved" | "proposed" }) =>
    call<Learning>("POST", "/api/learnings", learning),
  editLearning: (
    id: string,
    changes: Partial<{ scope: string; steps: LearningStep[]; text: string; status: LearningStatus }>,
  ) => call<Learning>("PATCH", `/api/learnings/${id}`, changes),
  deleteDay: (date: string) => call<{ deleted: Record<string, number> }>("DELETE", `/api/days/${date}`, { confirm: date }),
  day: (date: string) => call<Day>("GET", `/api/days/${date}`),

  editTopic: (day: Day, topicId: string, patch: Record<string, unknown>) =>
    call<Day>("PATCH", `/api/days/${day.date}/topics/${topicId}`, { version: day.version, ...patch }),
  moveTopic: (day: Day, topicId: string, accountKey: string) =>
    call<Day>("POST", `/api/days/${day.date}/topics/${topicId}/move`, { version: day.version, account_key: accountKey }),
  removeTopic: (day: Day, topicId: string) =>
    call<Day>("POST", `/api/days/${day.date}/topics/${topicId}/remove`, { version: day.version }),
  restoreTopic: (day: Day, topicId: string) =>
    call<Day>("POST", `/api/days/${day.date}/topics/${topicId}/restore`, { version: day.version }),

  addFollowUp: (day: Day, topicId: string, fields: { what: string; owner?: string | null; due_if_stated?: string | null }) =>
    call<Day>("POST", `/api/days/${day.date}/topics/${topicId}/follow-ups`, { version: day.version, ...fields }),
  editFollowUp: (day: Day, topicId: string, followUpId: string, patch: Record<string, unknown>) =>
    call<Day>("PATCH", `/api/days/${day.date}/topics/${topicId}/follow-ups/${followUpId}`, {
      version: day.version,
      ...patch,
    }),
  removeFollowUp: (day: Day, topicId: string, followUpId: string) =>
    call<Day>("POST", `/api/days/${day.date}/topics/${topicId}/follow-ups/${followUpId}/remove`, {
      version: day.version,
    }),
  restoreFollowUp: (day: Day, topicId: string, followUpId: string) =>
    call<Day>("POST", `/api/days/${day.date}/topics/${topicId}/follow-ups/${followUpId}/restore`, {
      version: day.version,
    }),
  moveFollowUp: (day: Day, topicId: string, followUpId: string, toTopicId: string) =>
    call<Day>("POST", `/api/days/${day.date}/topics/${topicId}/follow-ups/${followUpId}/move`, {
      version: day.version,
      to_topic_id: toTopicId,
    }),

  setHeadline: (day: Day, accountKey: string, text: string) =>
    call<Day>("PUT", `/api/days/${day.date}/headlines/${accountKey}`, { version: day.version, text }),
  setComment: (day: Day, accountKey: string, text: string) =>
    call<Day>("PUT", `/api/days/${day.date}/comments/${accountKey}`, { version: day.version, text }),

  addEntry: (day: Day, entry: ManualEntry) =>
    call<Day>("POST", `/api/days/${day.date}/entries`, { version: day.version, entry }),
  editEntry: (day: Day, entryId: string, entry: ManualEntry) =>
    call<Day>("PUT", `/api/days/${day.date}/entries/${entryId}`, { version: day.version, entry }),
  removeEntry: (day: Day, entryId: string) =>
    call<Day>("POST", `/api/days/${day.date}/entries/${entryId}/remove`, { version: day.version }),

  sendRequests: (reviewId: string, requests: AgentRequest[]) =>
    call<Day>("POST", `/api/reviews/${reviewId}/requests`, {
      requests: requests.map(({ scope, instruction }) => ({ scope, instruction })),
    }),
  finalize: (reviewId: string) => call<Day>("POST", `/api/reviews/${reviewId}/finalize`),
};
