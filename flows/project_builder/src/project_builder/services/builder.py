"""One chat turn of a build: resolve the build, pick the route, run the specialist, record what happened.

Every agent asks instead of guessing: when its result carries questions, the turn ends with a form (_ask) and no work;
the build then takes only the answers (PENDING re-states the form), which resume the same step (ANSWER).

The review app's UI reads everything from MongoDB (messages, artifacts, activity), so each step is written there as it
happens; the reply a handler returns is also what the conversational flow keeps in its own history.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TypeVar

from crewai import LLM, Agent
from crewai.lite_agent_output import LiteAgentOutput
from pydantic import BaseModel

from fde_core.domain.builds import (
    EMPTY,
    SKIP,
    STAGE_NAMES,
    ArtifactCard,
    Build,
    Check,
    Code,
    Design,
    InputRequest,
    Question,
    QuestionOption,
    SmokeTest,
    Spec,
    Stage,
    Turn,
    affected,
    answered,
    approve,
    criteria_changes,
    go_back,
    later,
    missing_answers,
    missing_env,
    record_draft,
    smoke_inputs,
    tidy_code,
    tidy_design,
    tidy_questions,
    uncovered,
)
from fde_core.domain.topics import new_id
from fde_core.repositories.build_env import BuildEnvRepository
from fde_core.repositories.builds import MongoBuildRepository
from fde_core.vault import EnvVault
from project_builder import agents
from project_builder.activity import Activity
from project_builder.drafts import AskTurn, CodeTurn, DesignTurn, RouteDecision, SmokeInput, SpecTurn
from project_builder.envelope import Envelope, parse
from project_builder.services import checks, integrations
from project_builder.services.sandbox import BuildSandbox
from project_builder.settings import Settings
from project_builder.tools.sandbox import sandbox_tools

log = logging.getLogger("project_builder")
T = TypeVar("T", bound=BaseModel)

Route = str  # a label the flow listens for: DRAFT, ASK, APPROVE, ANSWER, PENDING
HISTORY_MESSAGES = 30
SPEC_WRITER = "Spec writer"
ARCHITECT = "Architect"
ENGINEER = "Engineer"
SMOKE = "SMOKE"  # what a smoke test form's answers resume: the run
BUILDER = "Builder"  # replies written by code, not by an agent


@dataclass
class TurnContext:
    build: Build
    envelope: Envelope
    turn_id: str
    activity: Activity
    started: float = field(default_factory=time.monotonic)
    tokens: int = 0
    # The turn's model usage, by kind: total, prompt, completion, cached prompt, requests (shown with "done")
    usage: dict[str, int] = field(default_factory=dict)
    decision: RouteDecision | None = None


class Builder:
    def __init__(self, builds: MongoBuildRepository, settings: Settings, env: BuildEnvRepository | None = None) -> None:
        self._builds = builds
        self._settings = settings
        self._env = env

    # The turn's frame ------------------------------------------------------------------------------------------

    def begin(self, session_id: str, raw_message: str | None) -> TurnContext:
        envelope = parse(raw_message)
        build = self._builds.find(envelope.build_id) if envelope.build_id else self._builds.by_session(session_id)
        if build is None:
            raise LookupError(f"No build for this message (build {envelope.build_id}, session {session_id})")
        if envelope.message_id is None:  # a terminal chat: record the message, as the review app does
            message = self._builds.add_message(build, "user", "you", envelope.text, delivery="sent")
            envelope.message_id = message.id
        turn_id = envelope.message_id
        if build.turn is None:  # started outside the app and the worker (a terminal chat)
            self._builds.start_turn(build, Turn(id=turn_id, started_at=datetime.now(timezone.utc)))
        activity = Activity(self._builds, build, turn_id)
        activity.log("turn", "Turn started")
        return TurnContext(build=build, envelope=envelope, turn_id=turn_id, activity=activity)

    def run(self, ctx: TurnContext, handler: Callable[[TurnContext], str]) -> str:
        """Run a route's handler; whatever happens, the turn ends and the user gets an answer."""
        try:
            reply = handler(ctx)
            delivery = "answered"
        except Exception as error:
            log.exception("Turn %s failed", ctx.turn_id)
            ctx.activity.log("error", f"{type(error).__name__}: {str(error)[:300]}")
            reply = f"Something went wrong ({type(error).__name__}). Your message is kept; send it again to retry."
            self._say(ctx, BUILDER, reply)
            delivery = "failed"
        took = time.monotonic() - ctx.started
        ctx.activity.log("done", f"Done in {took:.0f}s", seconds=round(took, 1), tokens=ctx.tokens, usage=ctx.usage)
        self._builds.set_delivery(ctx.build, ctx.turn_id, delivery)
        self._builds.finish_turn(ctx.build, ctx.turn_id)
        return reply

    # Routing ---------------------------------------------------------------------------------------------------

    def route(self, ctx: TurnContext) -> Route:
        envelope = ctx.envelope
        if envelope.answers is not None:  # a form's answers: they resume the step that asked
            ctx.activity.log("route", "ANSWER (form)", intent="ANSWER", by="form")
            return "ANSWER"
        pending = ctx.build.pending_input
        if pending and pending.kind == "smoke":  # a smoke test's form never holds the build: moving on dismisses it
            self._builds.dismiss_input(ctx.build, pending.message_id)
        elif pending:  # a form is open: nothing else happens until it is answered
            ctx.activity.log("route", "PENDING (questions open)", intent="PENDING", by="form")
            return "PENDING"
        if envelope.action:  # a gate button: no LLM needed
            label = {"approve": "APPROVE", "request_changes": "DRAFT", "smoke": "SMOKE"}[envelope.action]
            ctx.activity.log("route", f"{label} (button)", intent=label, by="button")
            return label
        try:
            decision = self._decide(ctx)
        except Exception as error:
            log.exception("Routing failed")
            decision = RouteDecision(intent="ASK", reasoning=f"The router failed ({type(error).__name__}); answering")
        ctx.decision = decision
        ctx.activity.log(
            "route",
            f"{decision.intent}: {decision.reasoning}",
            intent=decision.intent,
            stage=decision.stage,
            reasoning=decision.reasoning,
            by="llm",
            model=self._settings.router_llm_model,
        )
        return decision.intent

    def _decide(self, ctx: TurnContext) -> RouteDecision:
        build = ctx.build
        stages = ", ".join(f"{STAGE_NAMES[s]}: {state.status} v{state.version}" for s, state in build.stages.items())
        prompt = (
            f"A user is building a CrewAI project, stage by stage (spec, design, code, tests). "
            f"Current stage: {STAGE_NAMES[build.current]}. Stages: {stages}.\n\n"
            f"Recent conversation:\n{self._history(ctx, limit=6)}\n\n"
            f"The user's new message:\n{ctx.envelope.text}\n\n"
            "Classify the new message."
        )
        llm = LLM(model=self._settings.router_llm_model, timeout=60)
        decision = llm.call(messages=[{"role": "user", "content": prompt}], response_model=RouteDecision)
        if not isinstance(decision, RouteDecision):
            decision = RouteDecision.model_validate_json(str(decision))
        return decision

    # Routes ----------------------------------------------------------------------------------------------------

    def draft(self, ctx: TurnContext) -> str:
        stage = ctx.envelope.stage or (ctx.decision.stage if ctx.decision else None) or ctx.build.current
        if stage == "spec":
            return self._write_spec(ctx, message=ctx.envelope.text)
        if stage == "design":
            return self._write_design(ctx, message=ctx.envelope.text)
        if stage == "code":
            return self._write_code(ctx, message=ctx.envelope.text)
        return self._say(
            ctx,
            BUILDER,
            f"The {STAGE_NAMES[stage]} stage is not available yet: so far the builder writes specs, designs and code.",
        )

    def ask(self, ctx: TurnContext, message: str | None = None, answers: str = "", stage: Stage | None = None) -> str:
        """A question about the work: the current stage's agent answers, changing nothing (or asks a form)."""
        stage = stage or ctx.build.current
        on_design = stage == "design"
        name = ARCHITECT if on_design else SPEC_WRITER
        model = self._settings.builder_llm_model
        agent = agents.architect(model) if on_design else agents.spec_writer(model)
        ctx.activity.log("agent", f"{name} is answering")
        spec = self._builds.latest_artifact(ctx.build, "spec")
        design = self._builds.latest_artifact(ctx.build, "design") if on_design else None
        message = message if message is not None else ctx.envelope.text
        prompt = (
            f"Project: {ctx.build.title}\n\n"
            f"Current spec (JSON, version {spec.version if spec else 0}):\n"
            f"{spec.content.model_dump_json(indent=1) if spec else 'none yet'}\n\n"
            + (
                f"Current design (JSON, version {design.version if design else 0}):\n"
                f"{design.content.model_dump_json(indent=1) if design else 'none yet'}\n\n"
                if on_design
                else ""
            )
            + f"Conversation so far:\n{self._history(ctx)}\n\n"
            f"{_answers_block(answers)}"
            f"Answer the user's message without changing the {STAGE_NAMES[stage].lower()}. Be brief; Markdown. If you "
            f"cannot answer without knowing more, ask instead.\n\nUser: {message}"
        )
        with ctx.activity.watching(agent):
            result = _kickoff(agent, prompt, response_format=AskTurn)
        _count(ctx, result)
        try:
            turn = self._structured(ctx, result, AskTurn)
        except ValueError:
            return self._say(ctx, name, result.raw)
        if turn.questions:
            return self._ask(ctx, name, stage, "ASK", message, turn.questions, turn.reply)
        return self._say(ctx, name, turn.reply)

    def answer(self, ctx: TurnContext) -> str:
        """The answers to the open form: store them, then resume the step that asked, with them."""
        build = ctx.build
        pending = build.pending_input
        asked = self._builds.message(build, pending.message_id) if pending else None
        if asked is None or asked.input_request is None or ctx.envelope.request_id != asked.input_request.id:
            return self._say(ctx, BUILDER, "These answers are for a form that is no longer open.")
        request = asked.input_request
        answers = ctx.envelope.answers or {}
        missing = missing_answers(request, answers)
        if missing:
            return self._say(ctx, BUILDER, f"Answer every question first (missing: {', '.join(missing)}).")
        request = request.model_copy(
            update={"status": "answered", "answers": answers, "answered_at": datetime.now(timezone.utc)}
        )
        self._builds.resolve_input(build, asked.id, request)
        ctx.activity.log("artifact", f"Answers received ({len(request.questions)})", request=request.id)
        if request.route == "ASK":
            return self.ask(ctx, message=request.request, answers=answered(request), stage=request.stage)
        if request.route == SMOKE:
            return self._smoke(ctx, request)
        if request.stage == "code":
            return self._write_code(ctx, message=request.request, answers=answered(request))
        if request.stage == "design":
            return self._write_design(ctx, message=request.request, answers=answered(request))
        return self._write_spec(ctx, message=request.request, answers=answered(request))

    def smoke_form(self, ctx: TurnContext) -> str:
        """ "Run the smoke test" on the code's card: its form again, for the latest code version."""
        build = ctx.build
        latest = self._builds.latest_artifact(build, "code")
        last = self._builds.last_smoke_form(build)
        if latest is None or last is None:
            return self._say(ctx, BUILDER, "There is no code to run yet.")
        form = InputRequest(
            agent=ENGINEER,
            stage="code",
            route=SMOKE,
            request=f"Code v{latest.version}",
            questions=last.questions,
            kind="smoke",
            keys=last.keys,
        )
        text = f"**The smoke test** runs Code v{latest.version} for real with these inputs."
        ctx.activity.begin("smoke", "Smoke test: waiting for your inputs")
        self._builds.add_message(build, "assistant", ENGINEER, text, turn_id=ctx.turn_id, input_request=form)
        return text

    def pending(self, ctx: TurnContext) -> str:
        """A message while a form is open: the agent waits for the answers (no LLM call)."""
        return self._say(
            ctx, BUILDER, "Answer the open questions above first; the agent continues once they are answered."
        )

    def approve(self, ctx: TurnContext) -> str:
        build = ctx.build
        stage: Stage = ctx.envelope.stage or build.current
        version = ctx.envelope.version or build.stages[stage].version
        try:
            approve(
                build,
                stage,
                version,
                self._missing_env(build) if stage == "design" else None,
                self._smoke_of(build) if stage == "code" else None,
            )
        except ValueError as reason:
            return self._say(ctx, BUILDER, f"Not approved: {reason}.")
        self._builds.save_stages(build)
        ctx.activity.log("artifact", f"{STAGE_NAMES[stage]} v{version} approved", stage=stage, version=version)
        following = later(stage)
        next_step = f" Next: {STAGE_NAMES[following[0]]}." if following else " The build is complete."
        said = self._say(ctx, BUILDER, f"**{STAGE_NAMES[stage]} v{version} approved.**{next_step}")
        if stage == "spec" and build.stages["design"].status in ("not_started", "stale"):
            return self._write_design(ctx, message="The spec is approved: design the project.")
        if stage == "design" and build.stages["code"].status in ("not_started", "stale"):
            return self._write_code(ctx, message="The design is approved: write the project.")
        return said

    def _missing_env(self, build: Build) -> list[str]:
        """The latest design's required env vars without a value (set in the review app, never in the chat)."""
        design = self._builds.latest_artifact(build, "design")
        if self._env is None or design is None or not isinstance(design.content, Design):
            return []
        return missing_env(design.content, self._env.names(build))

    def _write_spec(self, ctx: TurnContext, message: str, answers: str = "") -> str:
        build = ctx.build
        agent = agents.spec_writer(self._settings.builder_llm_model)
        ctx.activity.log("agent", f"{SPEC_WRITER} is working on the spec")
        latest = self._builds.latest_artifact(build, "spec")
        state = build.stages["spec"]
        prompt = (
            f"Project: {build.title}\n\n"
            f"Current spec (JSON, version {latest.version if latest else 0}, {state.status}):\n"
            f"{latest.content.model_dump_json(indent=1) if latest else 'none yet'}\n\n"
            f"Conversation so far:\n{self._history(ctx)}\n\n"
            f"The user's request:\n{message}\n\n"
            f"{_answers_block(answers)}"
            "If anything that shapes the spec is unclear, ask about it (questions) and write no spec this turn. "
            "Otherwise write the whole spec as it should now be, folding in every answer and dropping the open "
            "questions they settle. Keep criterion ids stable."
        )
        with ctx.activity.watching(agent):
            result = _kickoff(agent, prompt, response_format=SpecTurn)
        _count(ctx, result)
        turn = self._structured(ctx, result, SpecTurn)
        if turn.questions:  # unsure: halt and ask, no new version
            return self._ask(ctx, SPEC_WRITER, "spec", "DRAFT", message, turn.questions, turn.reply)
        if turn.spec is None:
            return self._say(ctx, SPEC_WRITER, turn.reply)

        impact = ""
        if state.status == "approved":  # changing an approved spec reopens it, and everything after it
            impact = self._design_impact(build, turn.spec)
            stale = go_back(build, "spec")
            if stale:
                ctx.activity.log("artifact", f"{', '.join(STAGE_NAMES[s] for s in stale)} now stale", stale=stale)
            if impact:
                ctx.activity.log("artifact", impact)
        artifact = self._builds.add_artifact(build, "spec", turn.spec, turn.summary, SPEC_WRITER)
        record_draft(build, "spec", artifact.version)
        self._builds.save_stages(build)
        ctx.activity.log("artifact", f"Spec v{artifact.version} written", stage="spec", version=artifact.version)
        card = ArtifactCard(stage="spec", version=artifact.version, summary=turn.summary, changes=turn.changes)
        reply = f"{turn.reply}\n\n{impact}" if impact else turn.reply
        return self._say(ctx, SPEC_WRITER, reply, card=card)

    def _design_impact(self, build: Build, new_spec: Spec) -> str:
        """What a change to the approved spec does to the design built on it: which elements need rework."""
        approved_version = build.stages["spec"].approved_version
        design = self._builds.latest_artifact(build, "design")
        old = self._builds.artifact(build, "spec", approved_version) if approved_version else None
        if design is None or old is None or not isinstance(old.content, Spec) or not isinstance(design.content, Design):
            return ""
        changes = criteria_changes(old.content, new_spec)
        touched = affected(design.content, changes)
        changed = [*changes.changed, *changes.removed]
        if not touched:
            return f"Design v{design.version} is stale: review it against the new spec."
        return f"Design v{design.version} is stale: {', '.join(changed)} changed, so {', '.join(touched)} need rework."

    # The design -------------------------------------------------------------------------------------------------

    def _write_design(self, ctx: TurnContext, message: str, answers: str = "") -> str:
        build = ctx.build
        spec_version = build.stages["spec"].approved_version
        spec = self._builds.artifact(build, "spec", spec_version) if spec_version else None
        if spec is None or not isinstance(spec.content, Spec):
            return self._say(ctx, BUILDER, "The design starts once the spec is approved.")
        agent = agents.architect(self._settings.builder_llm_model)
        ctx.activity.log("agent", f"{ARCHITECT} is working on the design")
        latest = self._builds.latest_artifact(build, "design")
        state = build.stages["design"]
        prompt = (
            f"Project: {build.title}\n\n"
            f"The approved spec (JSON, version {spec.version}):\n{spec.content.model_dump_json(indent=1)}\n\n"
            f"Current design (JSON, version {latest.version if latest else 0}, {state.status}):\n"
            f"{latest.content.model_dump_json(indent=1) if latest else 'none yet'}\n\n"
            f"Conversation so far:\n{self._history(ctx)}\n\n"
            f"The user's request:\n{message}\n\n"
            f"{_answers_block(answers)}"
            f"What already exists, to reuse before designing anything custom (for a crewai_tools tool's arguments and "
            f"settings, call find_integrations with its class name; never web_search CrewAI or crewai_tools):\n"
            f"{integrations.catalog()}\n\n"
            "If anything that shapes the design is unclear (an integration choice, a trade-off only the user can "
            "make), ask about it (questions) and write no design this turn. Otherwise write the whole design as it "
            "should now be: choose a flow or a crew and say why; look tools up in this order: a crewai_tools built-in, "
            "else an AMP integration, else a custom tool (an MCP server only when the user names one; look them up "
            "with find_integrations; a custom tool only when nothing fits, and it says why_custom, "
            "naming the closest existing tool, plus inputs, output, failures, a test plan and its PyPI packages); "
            "list what each tool needs (AMP connections, env vars); for a flow, define the state and "
            "give every step the fields it reads and how it changes the ones it writes; define the structured outputs "
            "as models and name them where they are returned; say how a run starts; use details for anything without "
            "a field; give every element the acceptance criteria it serves; keep element ids stable."
        )
        # No response_format: the design's schema is too large for structured output; the agent answers in JSON as
        # text, against the schema given here, and _structured validates (and repairs) it
        prompt += "\n\nAnswer with only a JSON object, no text around it, matching this JSON schema:\n" + json.dumps(
            DesignTurn.model_json_schema(), separators=(",", ":")
        )
        with ctx.activity.watching(agent):
            result = _kickoff(agent, prompt)
        _count(ctx, result)
        turn = self._structured(ctx, result, DesignTurn)
        if turn.questions:  # unsure: halt and ask, no new version
            return self._ask(ctx, ARCHITECT, "design", "DRAFT", message, turn.questions, turn.reply)
        if turn.design is None:
            return self._say(ctx, ARCHITECT, turn.reply)

        # Warnings are code's to give; what the agent put there anyway are its notes
        drafted = turn.design.model_copy(
            update={"notes": [*turn.design.notes, *turn.design.warnings], "warnings": [], "integrations": []}
        )
        design = tidy_design(drafted, spec.content)
        design, tool_warnings = integrations.check_tools(design)
        design.warnings = [*tool_warnings, *design.warnings]
        design = tidy_design(design, spec.content)  # the tools' needs, now complete, become env vars
        apps = [tool.app for tool in design.tools if tool.kind == "platform" and tool.app]
        if apps:
            ctx.activity.log("tool", f"Checking AMP connections: {', '.join(dict.fromkeys(apps))}")
        design.integrations = integrations.check_connections(apps)

        if state.status == "approved":  # a new design version reopens the design
            go_back(build, "design")
        artifact = self._builds.add_artifact(
            build, "design", design, turn.summary, ARCHITECT, based_on={"spec": spec.version}
        )
        record_draft(build, "design", artifact.version)
        self._builds.save_stages(build)
        ctx.activity.log("artifact", f"Design v{artifact.version} written", stage="design", version=artifact.version)
        card = ArtifactCard(
            stage="design",
            version=artifact.version,
            summary=turn.summary,
            changes=[*turn.changes, *_design_notes(spec.content, design)],
        )
        return self._say(ctx, ARCHITECT, turn.reply, card=card)

    # Code -------------------------------------------------------------------------------------------------------

    def _write_code(self, ctx: TurnContext, message: str, answers: str = "") -> str:
        """The engineer writes (or changes) the project in the build's sandbox; code checks it; the result is a new
        code version, and a form asks the user for the smoke test's inputs."""
        build = ctx.build
        spec, design = self._approved(build, "spec"), self._approved(build, "design")
        if not isinstance(spec, Spec) or not isinstance(design, Design):
            return self._say(ctx, BUILDER, "The code starts once the design is approved.")
        latest = self._builds.latest_artifact(build, "code")
        previous = latest.content if latest and isinstance(latest.content, Code) else None
        sandbox = self._sandbox(ctx, previous.package if previous else _package(build.title), previous)
        try:
            ctx.activity.begin("writing", "Writing the code")
            if not sandbox.exists():
                scaffolded = checks.scaffold(ctx.activity, sandbox, design.shape)
                if scaffolded.exit_code != 0:
                    ctx.activity.end("The project could not be created", failed=True)
                    return self._say(
                        ctx, ENGINEER, f"The project could not be created:\n\n```\n{scaffolded.output[-1500:]}\n```"
                    )
            turn = self._engineer(ctx, sandbox, spec, design, previous, message, answers)
            if turn.questions:  # unsure: halt and ask; what it wrote stays in the sandbox
                ctx.activity.end("Waiting for your answers")
                return self._ask(ctx, ENGINEER, "code", "DRAFT", message, turn.questions, turn.reply)
            ctx.activity.end(f"Wrote {len(sandbox.implements)} file(s)")
            version, _ = self._save_code(ctx, sandbox, design, previous, turn.summary)
            return self._ask_smoke(ctx, turn, design, version)
        finally:
            sandbox.pause()

    def _engineer(
        self,
        ctx: TurnContext,
        sandbox: BuildSandbox,
        spec: Spec,
        design: Design,
        previous: Code | None,
        message: str,
        answers: str = "",
    ) -> CodeTurn:
        agent = agents.engineer(self._settings.engineer_llm_model, sandbox_tools(sandbox, ctx.activity))
        ctx.activity.log("agent", f"{ENGINEER} is writing the code")
        failed = previous.smoke if previous and previous.smoke.status == "failed" else None
        prompt = (
            f"Project: {ctx.build.title}. Package: {sandbox.package} (already scaffolded with the CrewAI CLI, "
            f"{sandbox.crewai or 'latest CrewAI'}; paths are relative to the project root).\n\n"
            f"The approved spec (JSON):\n{spec.model_dump_json(indent=1)}\n\n"
            f"The approved design (JSON; implement it, element by element):\n{design.model_dump_json(indent=1)}\n\n"
            f"The project's files now:\n{chr(10).join(sandbox.list()) or '(none)'}\n\n"
            f"Conversation so far:\n{self._history(ctx)}\n\n"
            f"The user's request:\n{message}\n\n"
            f"{_answers_block(answers)}"
            + (
                f"The last smoke test FAILED (inputs {json.dumps(failed.inputs)}): {failed.reason}\n"
                f"Its output ended with:\n```\n{failed.output[-3000:]}\n```\nFix what made it fail.\n\n"
                if failed
                else ""
            )
            + "Implement every design element: write each file with write_file, saying which elements (D-n) it "
            "implements; install what the code imports with run_command (uv add ...); write the custom tools' unit "
            "tests from their test plans and run them (uv run pytest). Use the models the design names. Read every "
            "key from the environment; never write one into a file. Don't run the project: the smoke test does that. "
            "If something that changes the code is unclear, ask (questions) instead of guessing.\n\n"
            "When you are done, list in smoke_inputs what the smoke test needs from the user: every kickoff input of "
            "the design (with realistic suggestions from the spec's examples), a sample payload when a trigger starts "
            "it (name crewai_trigger_payload, a JSON string), and a test target for any tool that writes to the "
            "outside world."
        )
        prompt += "\n\nAnswer with only a JSON object, no text around it, matching this JSON schema:\n" + json.dumps(
            CodeTurn.model_json_schema(), separators=(",", ":")
        )
        with ctx.activity.watching(agent):
            result = _kickoff(agent, prompt)
        _count(ctx, result)
        return self._structured(ctx, result, CodeTurn)

    def _save_code(
        self, ctx: TurnContext, sandbox: BuildSandbox, design: Design, previous: Code | None, summary: str
    ) -> tuple[int, Code]:
        """Check the project, then store it as the next code version (every file, redacted)."""
        build = ctx.build
        ctx.activity.begin("checks", "Checking the code")
        found, structure, mismatches = checks.run_checks(ctx.activity, sandbox, design)
        files, skipped, leaked = sandbox.snapshot()
        before = {file.path: file.implements for file in previous.files} if previous else {}
        for file in files:
            file.implements = sandbox.implements.get(file.path, before.get(file.path, []))
        if leaked:
            found.append(
                Check(
                    name="no keys in files",
                    status="failed",
                    summary=f"a key was in {', '.join(leaked)}: stored redacted",
                )
            )
        code = tidy_code(
            Code(
                package=sandbox.package,
                shape=design.shape,
                files=files,
                skipped_files=skipped,
                checks=found,
                structure=structure,
                mismatches=mismatches,
            ),
            design,
        )
        failed = [check for check in found if check.status == "failed"]
        ctx.activity.end(f"{len(found) - len(failed)} of {len(found)} checks passed", failed=bool(failed))
        if build.stages["code"].status == "approved":  # a new version reopens the code
            go_back(build, "code")
        design_version = build.stages["design"].approved_version or 0
        artifact = self._builds.add_artifact(
            build, "code", code, summary or f"{len(files)} files", ENGINEER, based_on={"design": design_version}
        )
        record_draft(build, "code", artifact.version)
        self._builds.save_stages(build)
        ctx.activity.log("artifact", f"Code v{artifact.version} written", stage="code", version=artifact.version)
        return artifact.version, code

    def _ask_smoke(self, ctx: TurnContext, turn: CodeTurn, design: Design, version: int) -> str:
        """The smoke test's form: the kickoff inputs (with suggestions), a trigger's payload, test targets."""
        ctx.activity.begin("smoke", "Smoke test: waiting for your inputs")
        wanted = {item.name: item for item in turn.smoke_inputs}
        if design.starts_by.kind == "kickoff":
            for needed in design.starts_by.inputs:  # every kickoff input is asked, suggested or not
                wanted.setdefault(
                    needed.name, SmokeInput(name=needed.name, prompt=f"{needed.name}?", why=needed.description)
                )
        questions = []
        for item in wanted.values():
            suggested = [value for value in item.suggestions if value.strip()][:3]
            options = [QuestionOption(value=value, label=value) for value in suggested]
            if item.optional or len(suggested) < len(item.suggestions):  # optional: its default applies when empty
                options.append(QuestionOption(value=EMPTY, label="Leave it empty (use the default)"))
            questions.append(
                Question(
                    prompt=item.prompt,
                    why=item.why or None,
                    options=options,
                    recommended=suggested[0] if suggested else (EMPTY if options else None),
                )
            )
        form = InputRequest(
            agent=ENGINEER,
            stage="code",
            route=SMOKE,
            request=f"Code v{version}",
            questions=tidy_questions(questions),
            kind="smoke",
            keys=list(wanted)[: len(questions)],
        )
        unattended = "" if turn.unattended else " It may not run unattended here; skip it if so, and say why."
        text = f"{turn.reply}\n\n**Next: the smoke test.** It runs the project for real with these inputs.{unattended}"
        self._builds.add_message(ctx.build, "assistant", ENGINEER, text, turn_id=ctx.turn_id, input_request=form)
        ctx.activity.log("artifact", "The smoke test waits for your inputs", request=form.id)
        return text

    def _smoke(self, ctx: TurnContext, request: InputRequest) -> str:
        """The smoke test's answers: run the project for real; on failure, the engineer fixes once and it runs
        again. Then the version's card, with its gate."""
        build = ctx.build
        latest = self._builds.latest_artifact(build, "code")
        design = self._approved(build, "design")
        if latest is None or not isinstance(latest.content, Code) or not isinstance(design, Design):
            return self._say(ctx, BUILDER, "There is no code to run yet.")
        version, code = latest.version, latest.content
        inputs = smoke_inputs(request)
        if inputs is None:
            reason = request.answers[SKIP].text.strip()
            smoke = SmokeTest(status="skipped", reason=reason)
            self._builds.set_smoke(build, version, smoke)
            ctx.activity.begin("smoke", "Smoke test skipped")
            ctx.activity.step("note", f"Skipped: {reason}")
            ctx.activity.end("Skipped")
            return self._code_card(ctx, version, code, smoke, f"Smoke test skipped: {reason}")

        spec = self._approved(build, "spec")
        sandbox = self._sandbox(ctx, code.package, code)
        try:
            ctx.activity.begin("smoke", "Smoke test")
            smoke = checks.run_smoke(ctx.activity, sandbox, inputs, attempts=1)
            ctx.activity.end(f"Smoke test {smoke.status}", failed=smoke.status == "failed")
            self._builds.set_smoke(build, version, smoke)
            reply = "**The smoke test passed.**" if smoke.status == "passed" else ""
            if smoke.status == "failed" and isinstance(spec, Spec):  # the engineer fixes once, then it runs again
                ctx.activity.begin("writing", "Fixing what failed")
                fixing = code.model_copy(update={"smoke": smoke})
                turn = self._engineer(ctx, sandbox, spec, design, fixing, "The smoke test failed: fix it.")
                if turn.questions:
                    ctx.activity.end("Waiting for your answers")
                    return self._ask(
                        ctx, ENGINEER, "code", "DRAFT", "The smoke test failed: fix it.", turn.questions, turn.reply
                    )
                ctx.activity.end(f"Changed {len(sandbox.implements)} file(s)")
                version, code = self._save_code(ctx, sandbox, design, code, turn.summary or "Fixed the smoke test")
                ctx.activity.begin("smoke", "Smoke test, again")
                smoke = checks.run_smoke(ctx.activity, sandbox, inputs, attempts=2)
                ctx.activity.end(f"Smoke test {smoke.status}", failed=smoke.status == "failed")
                self._builds.set_smoke(build, version, smoke)
                reply = turn.reply + (
                    "\n\n**The smoke test passed after the fix.**"
                    if smoke.status == "passed"
                    else "\n\n**It still fails.** Tell me what you see, or what to change, and I'll keep fixing it."
                )
            return self._code_card(ctx, version, code, smoke, reply)
        finally:
            sandbox.pause()

    def _code_card(self, ctx: TurnContext, version: int, code: Code, smoke: SmokeTest, reply: str) -> str:
        ctx.activity.begin("result", f"Code v{version}")
        notes = [
            ", ".join(
                f"{'✓' if c.status == 'passed' else '–' if c.status == 'skipped' else '✗'} {c.name}"
                for c in code.checks
            )
        ]
        notes.append(
            {
                "passed": f"✓ smoke test passed in {smoke.seconds or 0:.0f}s",
                "failed": f"✗ smoke test failed: {smoke.reason}",
                "skipped": f"– smoke test skipped: {smoke.reason}",
                "waiting": "smoke test not run",
            }[smoke.status]
        )
        if code.mismatches:
            notes.append(f"{len(code.mismatches)} difference(s) from the design: see the Code tab")
        ctx.activity.end("Done")
        card = ArtifactCard(stage="code", version=version, summary=f"{len(code.files)} files", changes=notes)
        return self._say(ctx, ENGINEER, reply or f"Code v{version}.", card=card)

    def _sandbox(self, ctx: TurnContext, package: str, restore: Code | None) -> BuildSandbox:
        """The build's sandbox for this turn, with its env var values (decrypted here, never logged)."""
        build = ctx.build
        values: dict[str, str] = {}
        if self._env is not None and self._settings.build_env_key:
            values = self._env.values(build, EnvVault(self._settings.build_env_key.get_secret_value()))
        key = self._settings.e2b_api_key.get_secret_value() if self._settings.e2b_api_key else None
        sandbox = BuildSandbox(package, values, build.sandbox_id, key)
        ctx.activity.begin("sandbox", "Opening the sandbox")
        sandbox_id = sandbox.open(restore=restore.files if restore else None)
        if sandbox_id != build.sandbox_id:
            self._builds.set_sandbox(build, sandbox_id)
            build.sandbox_id = sandbox_id
        how = (
            "Created a sandbox" + (", restored the latest code" if restore else "")
            if sandbox.fresh
            else "Resumed the sandbox"
        )
        ctx.activity.step("note", f"{how} · {sandbox.crewai}" if sandbox.crewai else how, sandbox_id=sandbox_id)
        names = sorted(values)
        ctx.activity.step("env", f"Environment variables: {', '.join(names) or 'none set'}", names=names)
        ctx.activity.end("Sandbox ready")
        return sandbox

    def _approved(self, build: Build, stage: Stage):
        version = build.stages[stage].approved_version
        artifact = self._builds.artifact(build, stage, version) if version else None
        return artifact.content if artifact else None

    def _smoke_of(self, build: Build) -> SmokeTest | None:
        latest = self._builds.latest_artifact(build, "code")
        return latest.content.smoke if latest and isinstance(latest.content, Code) else None

    # Helpers ----------------------------------------------------------------------------------------------------

    def _structured(self, ctx: TurnContext, result: LiteAgentOutput, model: type[T]) -> T:
        """The agent's answer as `model`. CrewAI parses it when it can. Otherwise the JSON in the text is validated, and
        when it does not fit, the model gets one repair call with the validation errors. That call is plain text: a
        large schema (the design's) is too big for a provider's structured output ("compiled grammar too large")."""
        if isinstance(result.pydantic, model):
            return result.pydantic
        raw = result.raw or ""
        text = _json_in(raw)
        try:
            return model.model_validate_json(text)
        except ValueError as error:
            problem = str(error)[:3000] if text else "The answer has no JSON object."
        ctx.activity.log("agent", f"Repairing the answer to fit {model.__name__}")
        schema = json.dumps(model.model_json_schema(), separators=(",", ":"))
        llm = LLM(model=self._settings.builder_llm_model, timeout=300)
        repaired = llm.call(
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"This answer should be a JSON object matching the JSON schema below, but: {problem}\n\n"
                        f"Schema:\n{schema}\n\nAnswer:\n{raw}\n\n"
                        "Return only the corrected JSON object, keeping its content."
                    ),
                }
            ]
        )
        try:
            return model.model_validate_json(_json_in(str(repaired)))
        except ValueError as error:
            raise ValueError(f"No {model.__name__} in the agent's answer: {raw[:300]!r}") from error

    def _ask(
        self,
        ctx: TurnContext,
        agent: str,
        stage: Stage,
        route: Route,
        request: str,
        questions: list[Question],
        text: str,
    ) -> str:
        """Halt and ask: the reply carries a form, and the build waits for its answers before anything else."""
        form = InputRequest(agent=agent, stage=stage, route=route, request=request, questions=tidy_questions(questions))
        self._builds.add_message(ctx.build, "assistant", agent, text, turn_id=ctx.turn_id, input_request=form)
        ctx.activity.log("artifact", f"{agent} asked {len(form.questions)} question(s)", request=form.id)
        return text

    def _say(self, ctx: TurnContext, author: str, text: str, card: ArtifactCard | None = None) -> str:
        self._builds.add_message(ctx.build, "assistant", author, text, card=card, turn_id=ctx.turn_id)
        return text

    def _history(self, ctx: TurnContext, limit: int = HISTORY_MESSAGES) -> str:
        """The conversation before the message being answered (which each prompt gives on its own)."""
        messages = self._builds.messages(ctx.build)
        current = next((message.seq for message in messages if message.id == ctx.turn_id), None)
        lines = []
        earlier = [message for message in messages if current is None or message.seq < current]
        for message in earlier[-limit:]:
            text = message.text
            if message.card:
                text += f"\n[{STAGE_NAMES[message.card.stage]} v{message.card.version}: {message.card.summary}]"
            lines.append(f"{'User' if message.role == 'user' else message.author}: {text}")
        return "\n\n".join(lines) or "(none)"


def _json_in(text: str) -> str:
    """The JSON object in an answer: inside a ```json fence, or from its first { to its last }."""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        return fenced.group(1)
    first, last = text.find("{"), text.rfind("}")
    return text[first : last + 1] if first >= 0 and last > first else ""


def _answers_block(answers: str) -> str:
    if not answers:
        return ""
    return f"You asked the user these questions; their answers are final:\n{answers}\n\n"


def _kickoff(agent: Agent, prompt: str, **kwargs: Any) -> LiteAgentOutput:
    """A synchronous kickoff (the handlers are sync), typed as such."""
    result = agent.kickoff(prompt, **kwargs)
    if not isinstance(result, LiteAgentOutput):
        raise TypeError(f"Expected a LiteAgentOutput, got {type(result).__name__}")
    return result


USAGE = ("total_tokens", "prompt_tokens", "completion_tokens", "cached_prompt_tokens", "successful_requests")


def _count(ctx: TurnContext, result: object) -> None:
    """Add an agent run's model usage to the turn's: tokens in total, and how many prompt tokens came from the
    provider's cache (CrewAI caches each agent's system prompt with Anthropic)."""
    usage = getattr(result, "usage_metrics", None) or {}
    read = usage.get if isinstance(usage, dict) else (lambda key, default=0: getattr(usage, key, default))
    for key in USAGE:
        ctx.usage[key] = ctx.usage.get(key, 0) + int(read(key, 0) or 0)
    ctx.tokens = ctx.usage["total_tokens"]


def new_terminal_build(builds: MongoBuildRepository, fde_id: str, title: str) -> Build:
    """A build to chat with from the terminal (bin/flow project_builder kickoff)."""
    now = datetime.now(timezone.utc)
    build_id = new_id()
    return builds.create(
        Build(id=build_id, fde_id=fde_id, title=title, session_id=build_id, created_at=now, updated_at=now)
    )


def _design_notes(spec: Spec, design: Design) -> list[str]:
    """What the card says besides the architect's changes: coverage, AMP connections, warnings. (Values still to set
    show live under the card's gate, from the build's env_missing.)"""
    notes = []
    gaps = uncovered(spec, design)
    covered = len(spec.criteria) - len(gaps)
    notes.append(
        f"Covers {covered} of {len(spec.criteria)} criteria" + (f" · not covered: {', '.join(gaps)}" if gaps else "")
    )
    unconnected = [status.app for status in design.integrations if status.status == "not_connected"]
    if unconnected:
        notes.append(f"Connect in AMP: {', '.join(unconnected)}")
    others = [w for w in design.warnings if "is not covered" not in w]
    if others:
        notes.append(f"{len(others)} warning(s): see the Design tab")
    return notes


def _package(title: str) -> str:
    """The project's Python package, from the build's title: e.g. "Recent News Searcher" → recent_news_searcher."""
    words = re.findall(r"[a-z0-9]+", title.lower())[:5]
    name = "_".join(words) or "project"
    return name if name[0].isalpha() else f"p_{name}"
