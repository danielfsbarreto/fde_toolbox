"""The turn service, offline: MongoDB in memory (mongomock) and stub LLMs, with the network blocked."""

import json
from datetime import datetime, timezone

import mongomock
import pytest
from cryptography.fernet import Fernet
from fake_sandbox import FakeSandbox
from stub_llm import StubLLM, prompt_text, tool_call

from fde_core.domain.builds import (
    AgentSpec,
    Build,
    Criterion,
    Design,
    FlowStep,
    Question,
    QuestionOption,
    Spec,
    StartsBy,
    ToolInput,
    ToolSpec,
)
from fde_core.repositories.build_env import BuildEnvRepository
from fde_core.repositories.builds import MongoBuildRepository
from fde_core.vault import EnvVault
from project_builder import agents
from project_builder.drafts import AskTurn, CodeTurn, DesignTurn, RouteDecision, SmokeInput, SpecTurn
from project_builder.envelope import parse
from project_builder.services import builder as builder_module
from project_builder.services.builder import Builder
from project_builder.services.checks import MARK
from project_builder.settings import Settings

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)

SPEC = Spec(
    title="Page summarizer",
    goal="Summarize a web page into 3 bullets for sales.",
    inputs=["url"],
    outputs=["3 bullets"],
    criteria=[Criterion(id="AC-1", text="Returns exactly 3 bullets for a readable page")],
)


@pytest.fixture
def db():
    return mongomock.MongoClient()["test"]  # in memory


@pytest.fixture
def builds(db) -> MongoBuildRepository:
    return MongoBuildRepository(db)  # pyright: ignore[reportArgumentType]


@pytest.fixture
def service(builds, db) -> Builder:
    settings = Settings.model_validate({"mongodb_connection_string": "mongodb://unused"})
    return Builder(builds, settings, BuildEnvRepository(db))  # pyright: ignore[reportArgumentType]


@pytest.fixture
def build(builds) -> Build:
    return builds.create(
        Build(id="b1", fde_id="fde", title="Summarizer", session_id="s1", created_at=NOW, updated_at=NOW)
    )


def send(builds, build, text, **envelope) -> str:
    message = builds.add_message(build, "user", "you", text, delivery="sent")
    return json.dumps({"text": text, "message_id": message.id, **envelope})


def stub_spec_writer(monkeypatch, *responses) -> StubLLM:
    stub = StubLLM(responses=list(responses))
    original = agents.spec_writer

    def spec_writer(model):
        agent = original(model)
        agent.llm = stub
        return agent

    monkeypatch.setattr(agents, "spec_writer", spec_writer)
    return stub


def stub_router(monkeypatch, decision: RouteDecision) -> StubLLM:
    stub = StubLLM(responses=[decision])
    monkeypatch.setattr(builder_module, "LLM", lambda **kwargs: stub)
    return stub


def test_the_envelope_is_json_or_plain_text():
    assert parse('{"text": "ok", "action": "approve", "stage": "spec", "version": 2}').action == "approve"
    assert parse("just words").text == "just words"
    assert parse("{not json").text == "{not json"


def test_a_turn_finds_its_build_by_the_envelope_whatever_the_session(service, builds, build):
    """On AMP a turn's state id is its kickoff id, not the chat session: the message names the build."""
    message = builds.add_message(build, "user", "you", "hi", delivery="sent")
    ctx = service.begin("some-kickoff-id", json.dumps({"text": "hi", "build_id": "b1", "message_id": message.id}))
    assert ctx.build.id == "b1"
    with pytest.raises(LookupError):
        service.begin("some-kickoff-id", json.dumps({"text": "hi", "message_id": message.id}))


def test_a_gate_button_routes_without_an_llm(monkeypatch, service, builds, build):
    router = stub_router(monkeypatch, RouteDecision(intent="ASK", reasoning="unused"))
    ctx = service.begin("s1", send(builds, build, "Approve", action="approve", stage="spec", version=1))
    assert service.route(ctx) == "APPROVE"
    assert router.calls == []
    assert builds.activity(build)[-1].detail["by"] == "button"


def test_free_text_is_routed_by_the_llm_and_its_reasoning_logged(monkeypatch, service, builds, build):
    stub_router(monkeypatch, RouteDecision(intent="DRAFT", reasoning="New requirements for the spec"))
    ctx = service.begin("s1", send(builds, build, "It should also handle PDFs"))
    assert service.route(ctx) == "DRAFT"
    route = [entry for entry in builds.activity(build) if entry.kind == "route"][-1]
    assert route.text == "DRAFT: New requirements for the spec"
    assert route.detail["reasoning"] == "New requirements for the spec"


def test_drafting_writes_a_spec_version_with_its_card(monkeypatch, service, builds, build):
    writer = stub_spec_writer(
        monkeypatch,
        "Final Answer: " + SpecTurn(reply="Here is v1.", spec=SPEC, summary="First draft").model_dump_json(),
    )
    ctx = service.begin("s1", send(builds, build, "A flow that summarizes a web page into 3 bullets"))
    reply = service.run(ctx, service.draft)

    assert reply == "Here is v1."
    assert "summarizes a web page" in prompt_text(writer.calls[0])
    saved = builds.find("b1")
    assert saved.stages["spec"].status == "draft" and saved.stages["spec"].version == 1
    assert saved.turn is None
    assert builds.latest_artifact(saved, "spec").content == SPEC
    card = builds.messages(saved)[-1].card
    assert card.stage == "spec" and card.version == 1 and card.summary == "First draft"
    assert builds.message(saved, ctx.turn_id).delivery == "answered"


def test_changing_an_approved_spec_makes_later_stages_stale(monkeypatch, service, builds, build):
    stub_spec_writer(
        monkeypatch,
        "Final Answer: " + SpecTurn(reply="v1", spec=SPEC, summary="First").model_dump_json(),
        "Final Answer: " + SpecTurn(reply="v2", spec=SPEC, summary="PDFs too").model_dump_json(),
    )
    service.run(service.begin("s1", send(builds, build, "Summarize pages")), service.draft)
    service.run(
        service.begin("s1", send(builds, build, "Approve", action="approve", stage="spec", version=1)),
        service.approve,
    )
    saved = builds.find("b1")
    saved.stages["design"].status = "draft"  # as if the design had been written
    builds.save_stages(saved)

    # The build is at the design stage now, but a new requirement belongs to the spec: the router says so
    stub_router(monkeypatch, RouteDecision(intent="DRAFT", stage="spec", reasoning="A new requirement"))
    ctx = service.begin("s1", send(builds, build, "Handle PDFs too"))
    assert service.route(ctx) == "DRAFT"
    service.run(ctx, service.draft)
    saved = builds.find("b1")
    assert saved.stages["spec"].status == "draft" and saved.stages["spec"].version == 2
    assert saved.stages["design"].status == "stale"
    assert saved.current == "spec"


def test_approving_a_stage_that_is_not_ready_explains_why(service, builds, build):
    ctx = service.begin("s1", send(builds, build, "Approve", action="approve", stage="design", version=1))
    assert service.run(ctx, service.approve) == "Not approved: Spec must be approved first."


def test_a_failing_turn_still_ends_and_answers(monkeypatch, service, builds, build):
    def broken(model):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(agents, "spec_writer", broken)
    ctx = service.begin("s1", send(builds, build, "Summarize pages"))
    reply = service.run(ctx, service.draft)
    assert "Something went wrong (RuntimeError)" in reply
    saved = builds.find("b1")
    assert saved.turn is None
    assert builds.message(saved, ctx.turn_id).delivery == "failed"
    assert any(entry.kind == "error" for entry in builds.activity(saved))


QUESTION = Question(
    prompt="What should the bullets focus on?",
    options=[
        QuestionOption(value="company", label="What the company does"),
        QuestionOption(value="pain", label="Pain points"),
    ],
    recommended="pain",
)


def asks(monkeypatch, service, builds, build):
    """A first turn where the spec writer is unsure: it asks and writes nothing."""
    writer = stub_spec_writer(
        monkeypatch,
        "Final Answer: " + SpecTurn(reply="Two things first.", questions=[QUESTION]).model_dump_json(),
        "Final Answer: " + SpecTurn(reply="v1", spec=SPEC, summary="First").model_dump_json(),
    )
    service.run(service.begin("s1", send(builds, build, "Summarize pages for sales")), service.draft)
    return writer


def test_an_unsure_agent_asks_a_form_and_writes_nothing(monkeypatch, service, builds, build):
    asks(monkeypatch, service, builds, build)
    saved = builds.find("b1")
    assert saved.stages["spec"].version == 0  # no spec while unsure
    assert saved.pending_input is not None
    message = builds.messages(saved)[-1]
    form = message.input_request
    assert message.id == saved.pending_input.message_id and form.id == saved.pending_input.request_id
    assert [q.id for q in form.questions] == ["q1"] and form.questions[0].recommended == "pain"
    assert form.route == "DRAFT" and form.request == "Summarize pages for sales"


def test_while_a_form_is_open_other_messages_wait_without_an_llm(monkeypatch, service, builds, build):
    asks(monkeypatch, service, builds, build)
    router = stub_router(monkeypatch, RouteDecision(intent="ASK", reasoning="unused"))
    ctx = service.begin("s1", send(builds, build, "Actually, also PDFs"))
    assert service.route(ctx) == "PENDING"
    assert "Answer the open questions" in service.run(ctx, service.pending)
    assert router.calls == []
    assert builds.find("b1").pending_input is not None


def test_answers_resume_the_step_that_asked(monkeypatch, service, builds, build):
    writer = asks(monkeypatch, service, builds, build)
    request_id = builds.find("b1").pending_input.request_id
    ctx = service.begin(
        "s1",
        send(builds, build, "Answers", request_id=request_id, answers={"q1": {"option": "pain", "text": "for SMBs"}}),
    )
    assert service.route(ctx) == "ANSWER"
    service.run(ctx, service.answer)

    saved = builds.find("b1")
    assert saved.pending_input is None
    assert saved.stages["spec"].version == 1
    prompt = prompt_text(writer.calls[-1])
    assert "Summarize pages for sales" in prompt  # the original request
    assert "A: Pain points - for SMBs" in prompt  # and the answers
    answered_form = builds.message(saved, builds.messages(saved)[1].id).input_request
    assert answered_form.status == "answered" and answered_form.answers["q1"].text == "for SMBs"


def test_incomplete_answers_are_refused(monkeypatch, service, builds, build):
    asks(monkeypatch, service, builds, build)
    request_id = builds.find("b1").pending_input.request_id
    ctx = service.begin("s1", send(builds, build, "Answers", request_id=request_id, answers={"q1": {"text": " "}}))
    assert "missing: q1" in service.run(ctx, service.answer)
    assert builds.find("b1").pending_input is not None


def test_answering_a_question_can_ask_too(monkeypatch, service, builds, build):
    stub_spec_writer(
        monkeypatch, "Final Answer: " + AskTurn(reply="Which one?", questions=[QUESTION]).model_dump_json()
    )
    service.run(service.begin("s1", send(builds, build, "Which focus is best?")), service.ask)
    form = builds.messages(builds.find("b1"))[-1].input_request
    assert form is not None and form.route == "ASK"


# The design ----------------------------------------------------------------------------------------------------

DESIGN = Design(
    shape="flow",
    rationale="Steps with state: a flow.",
    summary="Fetch the page, then summarize it.",
    steps=[FlowStep(name="fetch", kind="start", does="Reads the page", uses=["read_page"], serves=["AC-1"])],
    tools=[ToolSpec(name="read_page", kind="builtin", class_name="ScrapeWebsiteTool", purpose="Reads a page")],
)


def stub_architect(monkeypatch, *responses) -> StubLLM:
    stub = StubLLM(responses=list(responses))
    original = agents.architect

    def architect(model):
        agent = original(model)
        agent.llm = stub
        return agent

    monkeypatch.setattr(agents, "architect", architect)
    return stub


def approved_spec(monkeypatch, service, builds, build):
    monkeypatch.delenv("CREWAI_PLATFORM_INTEGRATION_TOKEN", raising=False)
    stub_spec_writer(monkeypatch, "Final Answer: " + SpecTurn(reply="v1", spec=SPEC, summary="First").model_dump_json())
    service.run(service.begin("s1", send(builds, build, "Summarize pages")), service.draft)


def test_approving_the_spec_designs_in_the_same_turn(monkeypatch, service, builds, build):
    approved_spec(monkeypatch, service, builds, build)
    architect = stub_architect(
        monkeypatch,
        "Final Answer: " + DesignTurn(reply="Here is the design.", design=DESIGN, summary="First").model_dump_json(),
    )
    service.run(
        service.begin("s1", send(builds, build, "Approve", action="approve", stage="spec", version=1)), service.approve
    )

    saved = builds.find("b1")
    assert saved.stages["spec"].status == "approved"
    assert saved.stages["design"].status == "draft" and saved.stages["design"].version == 1
    stored = builds.latest_artifact(saved, "design")
    assert stored.based_on == {"spec": 1}
    assert stored.content.steps[0].id == "D-1" and stored.content.steps[0].uses == [stored.content.tools[0].id]
    assert "Returns exactly 3 bullets" in prompt_text(architect.calls[0])  # the approved spec reached the architect
    card = builds.messages(saved)[-1].card
    assert card.stage == "design" and "Covers 1 of 1 criteria" in card.changes


def test_an_unsure_architect_asks_and_the_answers_resume_the_design(monkeypatch, service, builds, build):
    approved_spec(monkeypatch, service, builds, build)
    architect = stub_architect(
        monkeypatch,
        "Final Answer: " + DesignTurn(reply="One thing first.", questions=[QUESTION]).model_dump_json(),
        "Final Answer: " + DesignTurn(reply="Designed.", design=DESIGN, summary="First").model_dump_json(),
    )
    service.run(
        service.begin("s1", send(builds, build, "Approve", action="approve", stage="spec", version=1)), service.approve
    )
    saved = builds.find("b1")
    form = builds.message(saved, saved.pending_input.message_id).input_request
    assert form.stage == "design" and form.agent == "Architect" and saved.stages["design"].version == 0

    ctx = service.begin("s1", send(builds, build, "Answers", request_id=form.id, answers={"q1": {"option": "pain"}}))
    assert service.route(ctx) == "ANSWER"
    service.run(ctx, service.answer)
    assert builds.find("b1").stages["design"].version == 1
    assert "A: Pain points" in prompt_text(architect.calls[-1])


def test_changing_the_spec_names_the_design_elements_to_rework(monkeypatch, service, builds, build):
    approved_spec(monkeypatch, service, builds, build)
    stub_architect(
        monkeypatch, "Final Answer: " + DesignTurn(reply="d", design=DESIGN, summary="First").model_dump_json()
    )
    service.run(
        service.begin("s1", send(builds, build, "Approve", action="approve", stage="spec", version=1)), service.approve
    )
    service.run(
        service.begin("s1", send(builds, build, "Approve", action="approve", stage="design", version=1)),
        service.approve,
    )

    changed = SPEC.model_copy(update={"criteria": [Criterion(id="AC-1", text="Returns exactly 5 bullets")]})
    stub_spec_writer(monkeypatch, "Final Answer: " + SpecTurn(reply="v2", spec=changed, summary="5").model_dump_json())
    stub_router(monkeypatch, RouteDecision(intent="DRAFT", stage="spec", reasoning="A new requirement"))
    ctx = service.begin("s1", send(builds, build, "Make it 5 bullets"))
    service.route(ctx)
    reply = service.run(ctx, service.draft)

    saved = builds.find("b1")
    assert saved.stages["design"].status == "stale"
    assert "Design v1 is stale: AC-1 changed, so D-1 need rework" in reply


def test_questions_on_the_design_stage_go_to_the_architect(monkeypatch, service, builds, build):
    approved_spec(monkeypatch, service, builds, build)
    stub_architect(
        monkeypatch,
        "Final Answer: " + DesignTurn(reply="d", design=DESIGN, summary="First").model_dump_json(),
        "Final Answer: " + AskTurn(reply="Because a flow keeps state.").model_dump_json(),
    )
    service.run(
        service.begin("s1", send(builds, build, "Approve", action="approve", stage="spec", version=1)), service.approve
    )
    service.run(service.begin("s1", send(builds, build, "Why a flow?")), service.ask)
    last = builds.messages(builds.find("b1"))[-1]
    assert last.author == "Architect" and last.text == "Because a flow keeps state."


def test_a_design_cannot_be_approved_until_its_values_are_set(monkeypatch, service, builds, build, db):
    approved_spec(monkeypatch, service, builds, build)
    design = DESIGN.model_copy(
        update={"agents": [AgentSpec(name="writer", role="r", goal="g", llm="anthropic/claude-haiku-5-5")]}
    )
    stub_architect(
        monkeypatch, "Final Answer: " + DesignTurn(reply="d", design=design, summary="First").model_dump_json()
    )
    service.run(
        service.begin("s1", send(builds, build, "Approve", action="approve", stage="spec", version=1)), service.approve
    )
    approve_design = send(builds, build, "Approve", action="approve", stage="design", version=1)
    service.run(service.begin("s1", approve_design), service.approve)
    saved = builds.find("b1")
    assert saved.stages["design"].status == "draft"  # still at the gate
    assert "Not approved: set the environment variables ANTHROPIC_API_KEY first" in builds.messages(saved)[-1].text

    db.build_env.insert_one({"fde_id": build.fde_id, "build_id": "b1", "name": "ANTHROPIC_API_KEY", "ciphertext": "c"})
    approve_again = send(builds, build, "Approve", action="approve", stage="design", version=1)
    service.run(service.begin("s1", approve_again), service.approve)
    assert builds.find("b1").stages["design"].status == "approved"


# The code stage ----------------------------------------------------------------------------------------------

CODE_DESIGN = DESIGN.model_copy(
    update={
        "starts_by": StartsBy(kind="kickoff", inputs=[ToolInput(name="topic", type="str", description="What about")])
    }
)


def stub_engineer(monkeypatch, *responses) -> StubLLM:
    stub = StubLLM(responses=list(responses))
    original = agents.engineer

    def engineer(model, tools):
        agent = original(model, tools)
        agent.llm = stub
        return agent

    monkeypatch.setattr(agents, "engineer", engineer)
    return stub


def wrote_main(implements=("D-1",)) -> str:
    content = "import os\nKEY = os.environ['SERPER_API_KEY']\n"
    args = {"path": "src/summarizer/main.py", "content": content, "implements": list(implements)}
    return tool_call("write_file", args)


def done(**fields) -> str:
    smoke = [SmokeInput(name="topic", prompt="Topic to summarize?", suggestions=["AI agents", "Bees"])]
    return (
        "Final Answer: "
        + CodeTurn(**{"reply": "Wrote it.", "summary": "First", "smoke_inputs": smoke, **fields}).model_dump_json()
    )


def approved_design(monkeypatch, service, builds, build):
    approved_spec(monkeypatch, service, builds, build)
    stub_architect(
        monkeypatch, "Final Answer: " + DesignTurn(reply="d", design=CODE_DESIGN, summary="First").model_dump_json()
    )
    approve_spec = send(builds, build, "Approve", action="approve", stage="spec", version=1)
    service.run(service.begin("s1", approve_spec), service.approve)
    FakeSandbox.structure = {"nodes": {"fetch": {"type": "start", "trigger_methods": []}}}


def answer_smoke(service, builds, build, answers):
    saved = builds.find("b1")
    form = builds.message(saved, saved.pending_input.message_id).input_request
    ctx = service.begin("s1", send(builds, build, "Run it", request_id=form.id, answers=answers))
    assert service.route(ctx) == "ANSWER"
    return service.run(ctx, service.answer)


def test_approving_the_design_writes_and_checks_the_code_then_asks_for_the_smoke_tests_inputs(
    monkeypatch, service, builds, build
):
    approved_design(monkeypatch, service, builds, build)
    stub_engineer(monkeypatch, wrote_main(), done())
    approve = send(builds, build, "Approve", action="approve", stage="design", version=1)
    service.run(service.begin("s1", approve), service.approve)

    saved = builds.find("b1")
    code = builds.latest_artifact(saved, "code")
    assert saved.stages["code"].status == "draft" and code.version == 1 and code.based_on == {"design": 1}
    main = next(file for file in code.content.files if file.path == "src/summarizer/main.py")
    assert main.implements == ["D-1"]
    assert [check.name for check in code.content.checks] == ["installs", "matches the design", "tests"]
    assert code.content.smoke.status == "waiting"
    form = builds.message(saved, saved.pending_input.message_id).input_request
    assert form.kind == "smoke" and form.keys == ["topic"] and saved.pending_input.kind == "smoke"
    assert [option.value for option in form.questions[0].options] == ["AI agents", "Bees"]
    assert FakeSandbox.last.paused and saved.sandbox_id == "sbx-fake"
    phases = [entry.detail.get("phase") for entry in builds.activity(saved) if entry.kind == "phase"]
    assert {"sandbox", "writing", "checks", "smoke"} <= set(phases)
    files = [entry for entry in builds.activity(saved) if entry.detail.get("step") == "file"]
    assert files[0].detail["implements"] == ["D-1"] and files[0].detail["op"] == "changed"  # the scaffold made it


def test_the_smoke_test_runs_with_the_inputs_and_the_values_redacted(monkeypatch, builds, build, db):
    key = Fernet.generate_key().decode()
    settings = Settings.model_validate({"mongodb_connection_string": "mongodb://unused", "build_env_key": key})
    service = Builder(builds, settings, BuildEnvRepository(db))  # pyright: ignore[reportArgumentType]
    db.build_env.insert_one(
        {"fde_id": "fde", "build_id": "b1", "name": "SERPER_API_KEY", "ciphertext": EnvVault(key).seal("serper-123456")}
    )
    approved_design(monkeypatch, service, builds, build)
    stub_engineer(monkeypatch, wrote_main(), done())
    service.run(
        service.begin("s1", send(builds, build, "Approve", action="approve", stage="design", version=1)),
        service.approve,
    )
    FakeSandbox.smoke_runs = [(0, "searching with serper-123456\n" + MARK + "5 bullets")]
    answer_smoke(service, builds, build, {"q1": {"option": "AI agents"}})

    saved = builds.find("b1")
    smoke = builds.latest_artifact(saved, "code").content.smoke
    assert smoke.status == "passed" and smoke.inputs == {"topic": "AI agents"} and smoke.result == "5 bullets"
    assert FakeSandbox.last.envs[-1]["SERPER_API_KEY"] == "serper-123456"  # the run had the value
    shown = " ".join(str(entry.detail) + entry.text for entry in builds.activity(saved))
    assert "serper-123456" not in shown and "[redacted:SERPER_API_KEY]" in shown  # never shown
    assert builds.messages(saved)[-1].card.stage == "code"
    approve = send(builds, build, "Approve", action="approve", stage="code", version=1)
    service.run(service.begin("s1", approve), service.approve)
    assert builds.find("b1").stages["code"].status == "approved"


def test_a_failed_smoke_test_is_fixed_once_and_run_again(monkeypatch, service, builds, build):
    approved_design(monkeypatch, service, builds, build)
    engineer = stub_engineer(monkeypatch, wrote_main(), done(), wrote_main(), done(reply="Fixed the import."))
    service.run(
        service.begin("s1", send(builds, build, "Approve", action="approve", stage="design", version=1)),
        service.approve,
    )
    FakeSandbox.smoke_runs = [(1, "Traceback\nKeyError: 'topic'"), (0, MARK + "ok")]
    reply = answer_smoke(service, builds, build, {"q1": {"text": "Bees"}})

    saved = builds.find("b1")
    code = builds.latest_artifact(saved, "code")
    assert code.version == 2 and code.content.smoke.status == "passed" and code.content.smoke.attempts == 2
    assert builds.artifact(saved, "code", 1).content.smoke.status == "failed"
    assert "KeyError: 'topic'" in prompt_text(engineer.calls[-1])  # the failure reached the engineer
    assert "passed after the fix" in reply


def test_a_smoke_test_still_failing_waits_for_the_user_to_steer(monkeypatch, service, builds, build):
    approved_design(monkeypatch, service, builds, build)
    stub_engineer(monkeypatch, wrote_main(), done(), wrote_main(), done())
    service.run(
        service.begin("s1", send(builds, build, "Approve", action="approve", stage="design", version=1)),
        service.approve,
    )
    FakeSandbox.smoke_runs = [(1, "Error: one"), (1, "Error: two")]
    reply = answer_smoke(service, builds, build, {"q1": {"option": "Bees"}})
    assert "still fails" in reply
    approve = send(builds, build, "Approve", action="approve", stage="code", version=2)
    service.run(service.begin("s1", approve), service.approve)
    assert "the smoke test must pass first" in builds.messages(builds.find("b1"))[-1].text


def test_skipping_the_smoke_test_needs_a_reason_and_then_allows_approval(monkeypatch, service, builds, build):
    approved_design(monkeypatch, service, builds, build)
    stub_engineer(monkeypatch, wrote_main(), done())
    service.run(
        service.begin("s1", send(builds, build, "Approve", action="approve", stage="design", version=1)),
        service.approve,
    )
    answer_smoke(service, builds, build, {"skip": {"text": "It needs a Gmail connection I don't have yet"}})
    saved = builds.find("b1")
    assert builds.latest_artifact(saved, "code").content.smoke.status == "skipped"
    service.run(
        service.begin("s1", send(builds, build, "Approve", action="approve", stage="code", version=1)), service.approve
    )
    assert builds.find("b1").stages["code"].status == "approved"


def test_another_message_dismisses_the_smoke_form_and_the_button_reopens_it(monkeypatch, service, builds, build):
    approved_design(monkeypatch, service, builds, build)
    stub_engineer(monkeypatch, wrote_main(), done(), wrote_main(), done(summary="Second"))
    service.run(
        service.begin("s1", send(builds, build, "Approve", action="approve", stage="design", version=1)),
        service.approve,
    )
    first = builds.find("b1").pending_input.message_id

    ctx = service.begin("s1", send(builds, build, "Use 3 bullets", action="request_changes", stage="code", version=1))
    assert service.route(ctx) == "DRAFT"  # not PENDING: the smoke form doesn't hold the build
    assert builds.message(builds.find("b1"), first).input_request.status == "dismissed"
    service.run(ctx, service.draft)
    assert builds.latest_artifact(builds.find("b1"), "code").version == 2

    ctx = service.begin("s1", send(builds, build, "Run the smoke test", action="smoke"))
    assert service.route(ctx) == "SMOKE"
    service.run(ctx, service.smoke_form)
    saved = builds.find("b1")
    assert builds.message(saved, saved.pending_input.message_id).input_request.request == "Code v2"
