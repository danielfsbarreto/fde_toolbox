"""Builds: CrewAI projects built conversationally by the project_builder flow, spec first (spec-driven development).

A build moves through stages (spec, design, code, test). Each stage produces versioned artifacts written by an agent;
the FDE approves a version at the stage's gate. Going back to an earlier stage makes every later stage stale until it
is reworked and approved again, so nothing downstream is approved on top of an outdated spec.

When an agent is unsure, it does not guess: it halts and asks the user a form of questions (each with suggested
options, one recommended, and room for the user's own answer). Nothing else happens on the build until every question
is answered; the answers resume the same agent at the same step.

The review app creates a build and its user messages; the flow writes the artifacts, the agents' messages and the
activity (the router's decisions and the work in progress). Messages and activity share one sequence per build, so
the app polls one timeline.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from fde_core.domain.topics import new_id

Stage = Literal["spec", "design", "code", "test"]
STAGES: tuple[Stage, ...] = ("spec", "design", "code", "test")
STAGE_NAMES: dict[Stage, str] = {"spec": "Spec", "design": "Design", "code": "Code", "test": "Tests"}

# not_started: no version yet; draft: a version waits at the gate; approved: the approved version is current;
# stale: an earlier stage changed after this one was drafted or approved, so it needs rework
StageStatus = Literal["not_started", "draft", "approved", "stale"]
BuildStatus = Literal["working", "waiting", "idle"]


class StageState(BaseModel):
    status: StageStatus = "not_started"
    version: int = 0  # the latest version written (0: none)
    approved_version: int | None = None


class Turn(BaseModel):
    """The turn in progress: one user message being answered. One at a time per build."""

    id: str  # the user message it answers
    started_at: datetime
    kickoff_id: str | None = None  # on AMP: the chat turn's kickoff, to check on it


class PendingInput(BaseModel):
    """A form of questions waiting for the user's answers: the message that asks it, and its request."""

    message_id: str
    request_id: str
    # A smoke test's form doesn't hold the build: any other message dismisses it (the code still needs its smoke test)
    kind: Literal["questions", "smoke"] = "questions"


class Build(BaseModel):
    id: str = Field(default_factory=new_id)
    fde_id: str
    title: str
    account: str | None = Field(default=None, description="The account it is for, when there is one")
    session_id: str = Field(description="The chat session: AMP's when it runs there, else the build id")
    stages: dict[Stage, StageState] = Field(default_factory=lambda: {stage: StageState() for stage in STAGES})
    current: Stage = "spec"
    turn: Turn | None = None
    pending_input: PendingInput | None = None  # while set, the build waits for these answers and nothing else
    sandbox_id: str | None = None
    seq: int = 0  # the last sequence number given to a message or an activity entry
    created_at: datetime
    updated_at: datetime


# Artifacts ------------------------------------------------------------------------------------------------------


class Criterion(BaseModel):
    """An acceptance criterion: one observable, testable behavior. Later stages point at it by id."""

    id: str = Field(pattern=r"^AC-\d+$", description="AC-1, AC-2, ...; kept when the text changes")
    text: str = Field(description="The behavior, in one sentence")
    given: str | None = None
    when: str | None = None
    then: str | None = None


class Spec(BaseModel):
    kind: Literal["spec"] = "spec"
    title: str
    goal: str = Field(description="What the project is for and who benefits, in two or three sentences")
    users: list[str] = []
    inputs: list[str] = Field(default=[], description="What a run receives")
    outputs: list[str] = Field(default=[], description="What a run produces")
    non_goals: list[str] = []
    examples: list[str] = Field(default=[], description="Concrete input → output examples")
    criteria: list[Criterion] = []
    open_questions: list[str] = Field(default=[], description="What is still undecided; empty when ready")


# The design: the shape of the CrewAI project -------------------------------------------------------------------
# Every element has a stable id (D-1, D-2, ...) and the acceptance criteria it serves, so a spec change shows which
# parts of the design need rework. Tools say what they need (an AMP connection, env vars) so access is a checklist.


class StateField(BaseModel):
    name: str
    type: str = Field(description="A Python type, e.g. str, list[str], dict[str, int], or a model's name")
    initial: str = Field(default="", description="Its default value, as code, e.g. '' or [] or 0")
    description: str = ""


class Element(BaseModel):
    id: str = Field(default="", description="D-1, D-2, ...; kept when the element changes; a new one gets the next")
    name: str
    serves: list[str] = Field(default=[], description="The acceptance criteria (AC-n) this element serves")
    details: str = Field(default="", description="Anything that matters here and has no field of its own (Markdown)")


class StateChange(BaseModel):
    field: str = Field(description="The state field's name")
    how: str = Field(description="How the step changes it, e.g. 'set to the fetched HTML', 'appends one entry'")


class FlowStep(Element):
    kind: Literal["start", "listen", "router"]
    triggers: list[str] = Field(default=[], description="The step names or router labels it listens to")
    join: Literal["any", "all"] = Field(
        default="any", description="With several triggers: run after any of them (or_) or after all of them (and_)"
    )
    routes: list[str] = Field(default=[], description="For a router: the labels it returns")
    does: str = Field(description="What the step does, in one or two sentences")
    uses: list[str] = Field(default=[], description="The ids of the crews, agents or tools it runs")
    reads: list[str] = Field(default=[], description="The state fields it reads")
    writes: list[StateChange] = Field(default=[], description="The state fields it changes, and how")
    output_model: str | None = Field(default=None, description="The model of its structured output, if any")
    persist: bool = Field(default=False, description="Saves the state after it runs (@persist), to resume from")
    human_feedback: str | None = Field(
        default=None, description="What a person reviews or decides here (@human_feedback); null when no one does"
    )


class CrewSpec(Element):
    process: Literal["sequential", "hierarchical"] = "sequential"
    agents: list[str] = Field(default=[], description="Agent ids")
    tasks: list[str] = Field(default=[], description="Task ids, in order")


class AgentSpec(Element):
    role: str
    goal: str
    backstory: str = ""
    llm: str = Field(default="", description="The model, e.g. anthropic/claude-sonnet-5-5")
    tools: list[str] = Field(default=[], description="Tool ids")
    knowledge: list[str] = Field(default=[], description="Knowledge sources it draws on (files, URLs, text)")


class TaskSpec(Element):
    description: str
    expected_output: str
    agent: str = Field(default="", description="The agent id")
    context: list[str] = Field(default=[], description="The ids of the tasks whose output it needs")
    output_model: str | None = Field(default=None, description="The model of its structured output, if any")
    guardrail: str | None = Field(default=None, description="The check its output must pass, and what happens if not")


class ToolNeed(BaseModel):
    kind: Literal["amp_connection", "env_var"]
    name: str = Field(description="The app (for an AMP connection) or the env var's name")
    purpose: str = ""
    required: bool = Field(default=True, description="env_var: False when the tool works without it (e.g. a base URL)")


CREDENTIAL = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)$")


def is_credential(name: str) -> bool:
    """A key, token or secret: always needed by the tool that names it, and kept secret."""
    return bool(CREDENTIAL.search(name.upper()))


class ToolInput(BaseModel):
    name: str
    type: str
    description: str = ""


class Package(BaseModel):
    name: str = Field(description="The PyPI package, e.g. httpx")
    version: str = Field(default="", description="A version range only when it matters, e.g. >=0.27")
    why: str = Field(default="", description="What the tool uses it for")


class ToolSpec(Element):
    kind: Literal["platform", "builtin", "mcp", "custom"]
    purpose: str
    why_custom: str = Field(
        default="", description="custom: why no existing tool fits, naming the closest built-in or platform action"
    )
    app: str | None = Field(default=None, description="platform: the AMP app slug, e.g. gmail")
    actions: list[str] = Field(default=[], description="platform: the actions used, e.g. send_email")
    class_name: str | None = Field(default=None, description="builtin: the crewai_tools class, e.g. SerperDevTool")
    server: str | None = Field(default=None, description="mcp: the server URL (or the stdio command shipped with it)")
    inputs: list[ToolInput] = Field(default=[], description="custom: the tool's arguments")
    output: str = Field(default="", description="custom: what it returns")
    calls: str = Field(default="", description="custom: what it calls (an API, a library), if anything")
    failures: list[str] = Field(default=[], description="custom: the failure cases it handles")
    test_plan: list[str] = Field(default=[], description="custom: the cases its unit test checks")
    packages: list[Package] = Field(default=[], description="custom: the PyPI packages it needs beyond crewai")
    needs: list[ToolNeed] = Field(default=[], description="What it needs to run: AMP connections, env vars")


class ModelField(BaseModel):
    name: str
    type: str = Field(description="A Python type, e.g. str, int, list[str], or another model's name")
    description: str = ""
    required: bool = True


class OutputModel(Element):
    """A structured output: the Pydantic model a task or step returns (output_pydantic, response_format)."""

    fields: list[ModelField]


class StartsBy(BaseModel):
    """How a run starts."""

    kind: Literal["kickoff", "amp_trigger", "schedule"] = Field(
        default="kickoff", description="Called with inputs (kickoff), an AMP trigger event, or on a schedule"
    )
    inputs: list[ToolInput] = Field(default=[], description="kickoff: the inputs a run receives")
    app: str | None = Field(default=None, description="amp_trigger: the app, e.g. gmail")
    event: str | None = Field(default=None, description="amp_trigger: the event, e.g. email-received")
    schedule: str = Field(default="", description="schedule: when, e.g. 'weekdays at 8:00'")


class EnvVar(BaseModel):
    name: str
    purpose: str = ""
    secret: bool = True
    required: bool = True


# The env var each LLM provider reads its key from (CrewAI's native providers), by the model string's prefix
LLM_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gpt": "OPENAI_API_KEY",
    "o1": "OPENAI_API_KEY",
    "o3": "OPENAI_API_KEY",
    "o4": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "google": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "xai": "XAI_API_KEY",
    "cohere": "COHERE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def llm_key(llm: str) -> str | None:
    """The env var an agent's model needs: no model is CrewAI's default (OpenAI); local models (ollama) need none."""
    if not llm.strip():
        return "OPENAI_API_KEY"
    prefix = re.split(r"[/:-]", llm.strip().lower(), maxsplit=1)[0]
    return LLM_KEYS.get(prefix)


class IntegrationStatus(BaseModel):
    app: str
    status: Literal["connected", "not_connected", "unknown"]
    detail: str = ""
    checked_at: datetime | None = None


class Design(BaseModel):
    kind: Literal["design"] = "design"
    shape: Literal["flow", "crew"] = Field(description="A Flow (steps, routing, state) or a single Crew")
    rationale: str = Field(description="Why this shape, in one or two sentences")
    summary: str = Field(description="How it works, in two or three sentences")
    starts_by: StartsBy = Field(default_factory=StartsBy)
    state: list[StateField] = Field(default=[], description="flow: the state the steps share")
    steps: list[FlowStep] = Field(default=[], description="flow: its methods")
    crews: list[CrewSpec] = Field(default=[], description="The crews (exactly one for a crew design)")
    agents: list[AgentSpec] = []
    tasks: list[TaskSpec] = []
    tools: list[ToolSpec] = []
    models: list[OutputModel] = Field(default=[], description="The structured outputs tasks and steps return")
    env_vars: list[EnvVar] = Field(default=[], description="Besides the tools' needs: e.g. the LLM provider's key")
    notes: list[str] = Field(default=[], description="Anything the code stage should know")
    # Set by code, never by the agent: kept out of the schema the agent sees (SkipJsonSchema)
    integrations: SkipJsonSchema[list[IntegrationStatus]] = []
    dependencies: SkipJsonSchema[list[Package]] = []  # the custom tools' packages, collected
    warnings: SkipJsonSchema[list[str]] = []


class CodeFile(BaseModel):
    path: str = Field(description="Relative to the project root, e.g. src/news_digest/main.py")
    content: str
    implements: list[str] = Field(default=[], description="The design elements (D-n) this file implements")
    author: str = "Engineer"


CheckStatus = Literal["passed", "failed", "skipped"]


class Check(BaseModel):
    """One check the builder ran on the code in the sandbox (installs, matches the design, tests)."""

    name: str
    status: CheckStatus
    summary: str = ""
    output: str = Field(default="", description="The end of its output, values redacted")


class SmokeTest(BaseModel):
    """The project run for real in the sandbox, with the user's inputs and the build's env vars."""

    status: Literal["waiting", "passed", "failed", "skipped"] = "waiting"
    inputs: dict[str, str] = {}
    result: str = Field(default="", description="What the run returned (redacted)")
    output: str = Field(default="", description="The end of its console output (redacted)")
    seconds: float | None = None
    trace_url: str | None = None
    reason: str = Field(default="", description="Why it was skipped, or why it failed")
    attempts: int = 0


class Code(BaseModel):
    """A version of the project: every file, what each implements, and how it checked out in the sandbox."""

    kind: Literal["code"] = "code"
    package: str = Field(description="The Python package, e.g. news_digest")
    shape: Literal["flow", "crew"] = "flow"
    files: list[CodeFile] = []
    skipped_files: list[str] = Field(default=[], description="Files not stored: too large or not text")
    checks: list[Check] = []
    structure: dict[str, object] | None = Field(default=None, description="The real flow graph (build_flow_structure)")
    mismatches: list[str] = Field(default=[], description="Where the code does not match the design")
    smoke: SmokeTest = SmokeTest()
    notes: list[str] = []


ArtifactContent = Annotated[Spec | Design | Code, Field(discriminator="kind")]


class Artifact(BaseModel):
    """One version of a stage's work. Code and tests join `content` as they are built."""

    id: str = Field(default_factory=new_id)
    fde_id: str
    build_id: str
    stage: Stage
    version: int
    content: ArtifactContent
    based_on: dict[Stage, int] = Field(default={}, description="The versions it was built on, e.g. {'spec': 2}")
    summary: str = Field(default="", description="What changed in this version, in one line")
    author: str = Field(description="The agent that wrote it, or 'you'")
    created_at: datetime


# Questions for the user ---------------------------------------------------------------------------------------


class QuestionOption(BaseModel):
    value: str = Field(description="A short identifier, e.g. 'company_overview'")
    label: str = Field(description="The option as the user reads it")
    description: str | None = Field(default=None, description="What choosing it means, in one sentence")


class Question(BaseModel):
    """One question of a form. The user picks an option, writes their own answer, or both."""

    id: str = Field(default="", description="q1, q2, ... (set by code)")
    prompt: str = Field(description="The question, in one sentence")
    why: str | None = Field(default=None, description="Why it matters for the work, in one sentence")
    options: list[QuestionOption] = Field(default=[], description="2 to 4 concrete options")
    recommended: str | None = Field(default=None, description="The value of the option you recommend")


class Answer(BaseModel):
    option: str | None = None  # the chosen option's value
    text: str = ""  # the user's own words, alone or adding to the option


class InputRequest(BaseModel):
    """A form an agent asked instead of guessing, and what to resume once it is answered."""

    id: str = Field(default_factory=new_id)
    agent: str
    stage: Stage
    route: str  # the step to resume with the answers (e.g. DRAFT)
    request: str  # the user's message the agent was working on
    questions: list[Question]
    # A smoke test's form: each question fills the kickoff input (or test setting) named in `keys`, in order; it can
    # be skipped, with a reason, when the project can't run unattended
    kind: Literal["questions", "smoke"] = "questions"
    keys: list[str] = []
    status: Literal["pending", "answered", "dismissed"] = "pending"
    answers: dict[str, Answer] = {}
    answered_at: datetime | None = None


MAX_QUESTIONS = 5
MAX_OPTIONS = 4


def tidy_questions(questions: list[Question]) -> list[Question]:
    """The form as the user sees it: ids q1.., at most 5 questions and 4 options, a recommendation that exists and
    comes first."""
    tidy = []
    for number, question in enumerate(questions[:MAX_QUESTIONS], start=1):
        options = question.options[:MAX_OPTIONS]
        values = [option.value for option in options]
        recommended = question.recommended if question.recommended in values else (values[0] if values else None)
        options = sorted(options, key=lambda option: option.value != recommended)  # stable: the rest keep their order
        tidy.append(question.model_copy(update={"id": f"q{number}", "options": options, "recommended": recommended}))
    return tidy


SKIP = "skip"  # the answer key that skips a smoke test; its text is the reason
EMPTY = "__empty__"  # a smoke test option: leave an optional input out, so the project's default applies


def missing_answers(request: InputRequest, answers: dict[str, Answer]) -> list[str]:
    """The questions without an answer: an answer is a valid option, the user's own words, or both. A smoke test
    skipped with a reason needs nothing else."""
    if request.kind == "smoke" and SKIP in answers:
        return [] if answers[SKIP].text.strip() else [SKIP]
    missing = []
    for question in request.questions:
        answer = answers.get(question.id)
        values = {option.value for option in question.options}
        if answer is None or not (answer.option in values or answer.text.strip()):
            missing.append(question.id)
    return missing


def smoke_inputs(request: InputRequest) -> dict[str, str] | None:
    """A smoke test's inputs from its answers (the user's own words win over a picked option); None when skipped."""
    if SKIP in request.answers:
        return None
    inputs = {}
    for key, question in zip(request.keys, request.questions, strict=False):
        answer = request.answers.get(question.id, Answer())
        value = answer.text.strip() or ("" if answer.option == EMPTY else answer.option or "")
        if value:  # an empty optional input is left out: the project's default applies
            inputs[key] = value
    return inputs


def answered(request: InputRequest) -> str:
    """The questions and the user's answers, for the agent's next prompt."""
    lines = []
    for question in request.questions:
        answer = request.answers.get(question.id, Answer())
        labels = {option.value: option.label for option in question.options}
        parts = [labels[answer.option]] if answer.option in labels else []
        if answer.text.strip():
            parts.append(answer.text.strip())
        lines.append(f"Q: {question.prompt}\nA: {' - '.join(parts)}")
    return "\n\n".join(lines)


# The conversation and the work in progress --------------------------------------------------------------------

MessageRole = Literal["user", "assistant"]
Delivery = Literal["queued", "sending", "sent", "answered", "failed"]  # queued: for a local worker; sending: to AMP
# phase: a stage of a code turn starting or ending (sandbox, writing, checks, smoke, result); step: one thing done in
# it (a command, a file written, a check, a batch of live output, a note), with its details
ActivityKind = Literal["turn", "route", "agent", "tool", "output", "artifact", "error", "done", "phase", "step"]
Phase = Literal["sandbox", "writing", "checks", "smoke", "result"]


class ArtifactCard(BaseModel):
    """An artifact shown in the conversation, with its gate when it waits for approval."""

    stage: Stage
    version: int
    summary: str
    changes: list[str] = []
    gate: bool = True


class BuildMessage(BaseModel):
    id: str = Field(default_factory=new_id)
    fde_id: str
    build_id: str
    seq: int
    role: MessageRole
    author: str = Field(description="'you', or the agent that answered")
    text: str = Field(description="Markdown")
    card: ArtifactCard | None = None
    input_request: InputRequest | None = None  # a form of questions the agent asks instead of guessing
    turn_id: str | None = Field(default=None, description="The user message this answers")
    delivery: Delivery | None = Field(default=None, description="User messages: queued, sent to the flow, answered")
    envelope: str | None = Field(default=None, description="User messages: the JSON sent to the flow as the turn")
    answers_for: str | None = Field(default=None, description="User messages: the form whose answers this carries")
    created_at: datetime


class ActivityEntry(BaseModel):
    """A step of a turn: the router's decision (with its reasoning), an agent starting, a tool call, output..."""

    fde_id: str
    build_id: str
    seq: int
    turn_id: str | None = None
    kind: ActivityKind
    text: str = Field(description="One line, for the live status and the router log")
    detail: dict[str, object] = {}
    at: datetime


# Rules --------------------------------------------------------------------------------------------------------


def earlier(stage: Stage) -> tuple[Stage, ...]:
    return STAGES[: STAGES.index(stage)]


def later(stage: Stage) -> tuple[Stage, ...]:
    return STAGES[STAGES.index(stage) + 1 :]


def record_draft(build: Build, stage: Stage, version: int) -> None:
    """A new version of the stage waits at its gate; the build is now working on that stage."""
    build.stages[stage] = StageState(
        status="draft", version=version, approved_version=build.stages[stage].approved_version
    )
    build.current = stage


def missing_env(design: Design, have: set[str]) -> list[str]:
    """The design's required env vars without a value: a design cannot be approved while any is missing."""
    return [var.name for var in design.env_vars if var.required and var.name not in have]


def blocker(build: Build, stage: Stage, missing: list[str] | None = None, smoke: SmokeTest | None = None) -> str | None:
    """Why the stage's latest version cannot be approved now, or None when it can. `missing`: the design's required
    env vars without a value (the caller reads them; values live outside the build). `smoke`: the latest code
    version's smoke test, which must have passed (or been skipped, when the project can't run unattended)."""
    if build.pending_input and build.pending_input.kind != "smoke":
        return "Answer the open questions first"
    for before in earlier(stage):
        if build.stages[before].status != "approved":
            return f"{STAGE_NAMES[before]} must be approved first"
    state = build.stages[stage]
    if state.status != "draft":
        return f"{STAGE_NAMES[stage]} has no version waiting for approval"
    if stage == "design" and missing:
        return f"set the environment variables {', '.join(missing)} first (on the design's card)"
    if stage == "code" and (smoke is None or smoke.status not in ("passed", "skipped")):
        return "the smoke test must pass first (or be skipped when the project can't run unattended)"
    return None


def approve(
    build: Build, stage: Stage, version: int, missing: list[str] | None = None, smoke: SmokeTest | None = None
) -> None:
    """Approve the version at the stage's gate and move on to the next stage."""
    reason = blocker(build, stage, missing, smoke)
    if reason:
        raise ValueError(reason)
    state = build.stages[stage]
    if version != state.version:
        raise ValueError(f"{STAGE_NAMES[stage]} v{version} is not the latest version (v{state.version})")
    build.stages[stage] = StageState(status="approved", version=version, approved_version=version)
    following = later(stage)
    build.current = following[0] if following else stage


def go_back(build: Build, stage: Stage) -> list[Stage]:
    """Reopen an earlier stage: every later stage that has work becomes stale. Returns those stages."""
    stale: list[Stage] = [after for after in later(stage) if build.stages[after].status != "not_started"]
    for after in stale:
        build.stages[after].status = "stale"
    if build.stages[stage].status == "approved":
        build.stages[stage].status = "draft"
    build.current = stage
    return stale


def status(build: Build) -> BuildStatus:
    """For the builds list: an agent is working, a gate waits for the FDE, or nothing is happening."""
    if build.turn:
        return "working"
    if build.pending_input or any(state.status in ("draft", "stale") for state in build.stages.values()):
        return "waiting"
    return "idle"


class CriteriaChanges(BaseModel):
    added: list[str] = []
    changed: list[str] = []
    removed: list[str] = []

    @property
    def any(self) -> bool:
        return bool(self.added or self.changed or self.removed)


def criteria_changes(old: Spec, new: Spec) -> CriteriaChanges:
    """Which acceptance criteria a new spec version added, changed or removed, by id."""
    before = {criterion.id: criterion for criterion in old.criteria}
    after = {criterion.id: criterion for criterion in new.criteria}
    return CriteriaChanges(
        added=[id for id in after if id not in before],
        changed=[id for id in after if id in before and after[id] != before[id]],
        removed=[id for id in before if id not in after],
    )


# Design rules -------------------------------------------------------------------------------------------------

DESIGN_ID = re.compile(r"^D-(\d+)$")


def elements(design: Design) -> list[Element]:
    """The design's elements that carry an id, in a stable order."""
    return [*design.steps, *design.crews, *design.agents, *design.tasks, *design.tools, *design.models]


def tidy_design(design: Design, spec: Spec) -> Design:
    """The design as stored: every element with a unique D-n id, `serves` limited to the spec's criteria, references
    resolved to ids (an element named instead of its id is accepted), the env vars collected from the tools' needs,
    and warnings for what does not hold together. Tool names are checked by the caller (it knows the catalogs)."""
    design = design.model_copy(deep=True)
    warnings: list[str] = []
    every = elements(design)

    used = {int(m.group(1)) for e in every if (m := DESIGN_ID.match(e.id))}
    seen: set[str] = set()
    for element in every:
        if not DESIGN_ID.match(element.id) or element.id in seen:
            element.id = f"D-{max(used, default=0) + 1}"
            used.add(int(element.id[2:]))
        seen.add(element.id)

    criteria = {criterion.id for criterion in spec.criteria}
    for element in every:
        element.serves = [ac for ac in dict.fromkeys(element.serves) if ac in criteria]

    by_name = {e.name: e.id for e in every}

    def resolve(refs: list[str], allowed: set[str], owner: Element, what: str) -> list[str]:
        resolved = []
        for ref in refs:
            target = ref if ref in allowed else by_name.get(ref)
            if target in allowed:
                resolved.append(target)
            else:
                warnings.append(f"{owner.id} {owner.name}: unknown {what} {ref!r}")
        return list(dict.fromkeys(resolved))

    ids = lambda items: {item.id for item in items}  # noqa: E731
    tool_ids, agent_ids, task_ids = ids(design.tools), ids(design.agents), ids(design.tasks)
    for step in design.steps:
        step.uses = resolve(step.uses, ids(design.crews) | agent_ids | tool_ids, step, "crew, agent or tool")
    for crew in design.crews:
        crew.agents = resolve(crew.agents, agent_ids, crew, "agent")
        crew.tasks = resolve(crew.tasks, task_ids, crew, "task")
    for agent in design.agents:
        agent.tools = resolve(agent.tools, tool_ids, agent, "tool")
    for task in design.tasks:
        task.agent = (resolve([task.agent], agent_ids, task, "agent") or [""])[0] if task.agent else ""
        task.context = resolve(task.context, task_ids, task, "task")

    fields = {field.name for field in design.state}
    models = {model.name for model in design.models} | {model.id for model in design.models}
    by_id = {model.id: model.name for model in design.models}
    for step in design.steps:
        if design.shape == "flow":
            for name in [*step.reads, *[change.field for change in step.writes]]:
                if name not in fields:
                    warnings.append(f"{step.id} {step.name}: unknown state field {name!r}")
        if step.join == "all" and len(step.triggers) < 2:
            step.join = "any"  # all of one trigger is just that trigger
    if design.shape == "flow" and any(step.reads or step.writes for step in design.steps):
        written = {change.field for step in design.steps for change in step.writes}
        given = {field.name for field in design.starts_by.inputs} if design.starts_by.kind == "kickoff" else set()
        for field in design.state:
            if field.name not in written | given:
                warnings.append(f"State field {field.name!r}: no step changes it and it is not a kickoff input")
    for owner in [*design.steps, *design.tasks]:
        if owner.output_model and owner.output_model not in models:
            warnings.append(f"{owner.id} {owner.name}: unknown output model {owner.output_model!r}")
        elif owner.output_model in by_id:
            owner.output_model = by_id[owner.output_model]  # named by id: keep the name

    packages: dict[str, Package] = {}
    for tool in design.tools:
        for package in tool.packages:
            packages.setdefault(package.name.lower(), package)
    design.dependencies = list(packages.values())

    env = {var.name: var for var in design.env_vars}
    for tool in design.tools:
        for need in tool.needs:
            if need.kind != "env_var":
                continue
            # The tool decides whether it needs the variable; a credential is always needed, and secret
            var = env.get(need.name) or EnvVar(name=need.name, purpose=need.purpose or f"{tool.name}")
            var.required = need.required or is_credential(need.name)
            var.secret = is_credential(need.name)
            env[need.name] = var
    for agent in design.agents:
        key = llm_key(agent.llm)
        if key and key not in env:
            env[key] = EnvVar(name=key, purpose=f"The LLM of {agent.name} ({agent.llm or 'CrewAI default, OpenAI'})")
    if any(tool.kind == "platform" for tool in design.tools) and "CREWAI_PLATFORM_INTEGRATION_TOKEN" not in env:
        env["CREWAI_PLATFORM_INTEGRATION_TOKEN"] = EnvVar(
            name="CREWAI_PLATFORM_INTEGRATION_TOKEN", purpose="Runs the AMP platform integrations"
        )
    design.env_vars = list(env.values())

    for ac in uncovered(spec, design):
        warnings.append(f"{ac} is not covered by any element")
    design.warnings = [*warnings, *[w for w in design.warnings if w not in warnings]]
    return design


def coverage(spec: Spec, design: Design) -> dict[str, list[str]]:
    """Each acceptance criterion and the design elements that serve it."""
    return {
        criterion.id: [element.id for element in elements(design) if criterion.id in element.serves]
        for criterion in spec.criteria
    }


def uncovered(spec: Spec, design: Design) -> list[str]:
    return [ac for ac, served_by in coverage(spec, design).items() if not served_by]


def affected(design: Design, changes: CriteriaChanges) -> list[str]:
    """The design elements serving criteria that changed or were removed: they need rework."""
    touched = set(changes.changed) | set(changes.removed)
    return [element.id for element in elements(design) if touched & set(element.serves)]


# Code ---------------------------------------------------------------------------------------------------------


def structure_mismatches(design: Design, structure: dict[str, object] | None) -> list[str]:
    """Where the real flow (build_flow_structure) differs from the designed steps: a missing or extra method, a
    different kind, different triggers, or an and/or join that differs. Empty for a crew design."""
    if design.shape != "flow" or not design.steps:
        return []
    if not structure:
        return ["The flow's structure could not be read"]
    nodes = structure.get("nodes")
    nodes = nodes if isinstance(nodes, dict) else {}
    found: list[str] = []
    for step in design.steps:
        node = nodes.get(step.name)
        if not isinstance(node, dict):
            found.append(f"{step.id} {step.name}: no method of that name in the flow")
            continue
        kind = node.get("type")
        if kind != step.kind and not (step.kind == "router" and node.get("is_router")):
            found.append(f"{step.id} {step.name}: designed as {step.kind}, written as {kind}")
        written = set(node.get("trigger_methods") or [])  # type: ignore[arg-type]
        if step.kind != "start" and set(step.triggers) != written:
            found.append(
                f"{step.id} {step.name}: triggered by {sorted(written) or 'nothing'}, designed {sorted(step.triggers)}"
            )
        join = "all" if node.get("condition_type") == "AND" else "any"
        if len(step.triggers) > 1 and join != step.join:
            found.append(f"{step.id} {step.name}: waits for {join} of its triggers, designed {step.join}")
    designed = {step.name for step in design.steps}
    found += [f"{name}: a flow method the design does not have" for name in nodes if name not in designed]
    return found


def tidy_code(code: Code, design: Design) -> Code:
    """Only known design ids in `implements`; every design element implemented by some file, or a mismatch."""
    code = code.model_copy(deep=True)
    ids = {element.id for element in elements(design)}
    for file in code.files:
        file.implements = [element for element in dict.fromkeys(file.implements) if element in ids]
    done = {element for file in code.files for element in file.implements}
    unimplemented = [f"{element.id} {element.name}" for element in elements(design) if element.id not in done]
    if unimplemented:
        code.mismatches = [
            *code.mismatches,
            *[f"{element}: no file says it implements it" for element in unimplemented],
        ]
    return code


def affected_files(code: Code, element_ids: list[str]) -> list[str]:
    """The files implementing design elements that changed: they need rework."""
    touched = set(element_ids)
    return [file.path for file in code.files if touched & set(file.implements)]
