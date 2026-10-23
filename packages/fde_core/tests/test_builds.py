from datetime import datetime, timezone

import pytest

from fde_core.domain.builds import (
    Build,
    Criterion,
    Spec,
    Turn,
    approve,
    blocker,
    criteria_changes,
    go_back,
    record_draft,
    status,
)

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


def new_build() -> Build:
    return Build(fde_id="fde", title="Summarizer", session_id="s1", created_at=NOW, updated_at=NOW)


def approved_through(*stages) -> Build:
    from fde_core.domain.builds import SmokeTest

    build = new_build()
    for stage in stages:
        record_draft(build, stage, 1)
        approve(build, stage, 1, smoke=SmokeTest(status="passed") if stage == "code" else None)
    return build


def test_a_new_build_starts_at_the_spec_with_nothing_written():
    build = new_build()
    assert build.current == "spec"
    assert {state.status for state in build.stages.values()} == {"not_started"}
    assert status(build) == "idle"


def test_a_draft_waits_at_its_gate():
    build = new_build()
    record_draft(build, "spec", 1)
    assert build.stages["spec"].status == "draft"
    assert blocker(build, "spec") is None
    assert status(build) == "waiting"


def test_approving_moves_on_to_the_next_stage():
    build = approved_through("spec")
    assert build.stages["spec"].status == "approved"
    assert build.stages["spec"].approved_version == 1
    assert build.current == "design"


def test_the_last_stage_stays_current_once_approved():
    build = approved_through("spec", "design", "code", "test")
    assert build.current == "test"
    assert status(build) == "idle"


def test_nothing_is_approved_on_top_of_an_unapproved_earlier_stage():
    build = new_build()
    record_draft(build, "design", 1)
    assert blocker(build, "design") == "Spec must be approved first"
    with pytest.raises(ValueError, match="Spec must be approved first"):
        approve(build, "design", 1)


def test_only_the_latest_version_can_be_approved():
    build = new_build()
    record_draft(build, "spec", 1)
    record_draft(build, "spec", 2)
    with pytest.raises(ValueError, match="not the latest"):
        approve(build, "spec", 1)
    approve(build, "spec", 2)


def test_a_stage_without_a_waiting_version_cannot_be_approved():
    build = new_build()
    assert blocker(build, "spec") == "Spec has no version waiting for approval"


def test_going_back_makes_every_later_stage_with_work_stale():
    build = approved_through("spec", "design")
    record_draft(build, "code", 1)
    stale = go_back(build, "spec")
    assert stale == ["design", "code"]  # test was never started, so it is not stale
    assert build.stages["design"].status == "stale"
    assert build.stages["code"].status == "stale"
    assert build.stages["test"].status == "not_started"
    assert build.stages["spec"].status == "draft"
    assert build.current == "spec"


def test_a_stale_stage_cannot_be_approved_until_reworked():
    build = approved_through("spec", "design")
    go_back(build, "spec")
    approve(build, "spec", 1)  # the spec is approved again as it was
    assert blocker(build, "design") == "Design has no version waiting for approval"
    record_draft(build, "design", 2)
    approve(build, "design", 2)
    assert build.stages["design"].status == "approved"


def test_a_build_with_a_turn_in_progress_is_working():
    build = new_build()
    build.turn = Turn(id="m1", started_at=NOW)
    assert status(build) == "working"


def spec(*criteria: tuple[str, str]) -> Spec:
    return Spec(title="t", goal="g", criteria=[Criterion(id=id, text=text) for id, text in criteria])


def test_criteria_changes_by_id():
    old = spec(("AC-1", "reads the page"), ("AC-2", "writes 3 bullets"), ("AC-3", "cites the url"))
    new = spec(("AC-1", "reads the page"), ("AC-2", "writes 5 bullets"), ("AC-4", "handles a 404"))
    changes = criteria_changes(old, new)
    assert changes.added == ["AC-4"]
    assert changes.changed == ["AC-2"]
    assert changes.removed == ["AC-3"]
    assert changes.any


def test_no_criteria_changes():
    same = spec(("AC-1", "reads the page"))
    assert not criteria_changes(same, same).any


def test_criterion_ids_have_one_shape():
    with pytest.raises(ValueError):
        Criterion(id="1", text="x")


# Questions for the user --------------------------------------------------------------------------------------

from fde_core.domain.builds import (  # noqa: E402
    Answer,
    InputRequest,
    PendingInput,
    Question,
    QuestionOption,
    answered,
    missing_answers,
    tidy_questions,
)


def question(prompt: str, *values: str, recommended: str | None = None) -> Question:
    options = [QuestionOption(value=value, label=value.title()) for value in values]
    return Question(prompt=prompt, options=options, recommended=recommended)


def form(*questions: Question) -> InputRequest:
    return InputRequest(
        agent="Spec writer", stage="spec", route="DRAFT", request="r", questions=tidy_questions(list(questions))
    )


def test_tidy_questions_numbers_them_caps_them_and_fixes_the_recommendation():
    tidy = tidy_questions(
        [question("a?", "x", "y", "z", "w", "v", recommended="nope")] + [question(f"{n}?", "x", "y") for n in range(6)]
    )
    assert [q.id for q in tidy] == ["q1", "q2", "q3", "q4", "q5"]
    assert len(tidy[0].options) == 4
    assert tidy[0].recommended == "x"  # an unknown recommendation falls back to the first option


def test_the_recommended_option_comes_first():
    tidy = tidy_questions([question("a?", "x", "y", "z", recommended="z")])
    assert [option.value for option in tidy[0].options] == ["z", "x", "y"]


def test_an_answer_is_an_option_your_own_words_or_both():
    request = form(
        question("Focus?", "company", "pain"), question("Retry?", "yes", "no"), question("Limit?", "200", "500")
    )
    answers = {
        "q1": Answer(option="company"),
        "q2": Answer(text="Retry once, then fail"),
        "q3": Answer(option="200", text="but count words, not characters"),
    }
    assert missing_answers(request, answers) == []
    assert missing_answers(request, {"q1": Answer(option="other"), "q2": Answer(text="  ")}) == ["q1", "q2", "q3"]


def test_answered_reads_like_a_conversation():
    request = form(question("Focus?", "company", "pain")).model_copy(
        update={"answers": {"q1": Answer(option="company", text="and its competitors")}}
    )
    assert answered(request) == "Q: Focus?\nA: Company - and its competitors"


def test_an_open_form_blocks_approval_and_makes_the_build_wait():
    build = new_build()
    record_draft(build, "spec", 1)
    build.pending_input = PendingInput(message_id="m1", request_id="r1")
    assert blocker(build, "spec") == "Answer the open questions first"
    assert status(build) == "waiting"


# The design ----------------------------------------------------------------------------------------------------

from fde_core.domain.builds import (  # noqa: E402
    AgentSpec,
    Artifact,
    CrewSpec,
    Design,
    EnvVar,
    FlowStep,
    ModelField,
    OutputModel,
    Package,
    StateChange,
    StateField,
    TaskSpec,
    ToolNeed,
    ToolSpec,
    affected,
    coverage,
    tidy_design,
    uncovered,
)

SPEC3 = Spec(
    title="t",
    goal="g",
    criteria=[Criterion(id="AC-1", text="a"), Criterion(id="AC-2", text="b"), Criterion(id="AC-3", text="c")],
)


def design(**fields) -> Design:
    return Design(shape="flow", rationale="r", summary="s", **fields)


def test_tidy_design_gives_unique_ids_and_keeps_good_ones():
    tidy = tidy_design(
        design(
            steps=[
                FlowStep(id="D-4", name="fetch", kind="start", does="x"),
                FlowStep(name="write", kind="listen", does="y"),
            ],
            agents=[AgentSpec(id="D-4", name="writer", role="r", goal="g")],  # a duplicate id
        ),
        SPEC3,
    )
    ids = [tidy.steps[0].id, tidy.steps[1].id, tidy.agents[0].id]
    assert ids[0] == "D-4" and len(set(ids)) == 3


def test_tidy_design_limits_serves_resolves_names_and_warns():
    tidy = tidy_design(
        design(
            steps=[FlowStep(name="write", kind="start", does="x", uses=["writer", "ghost"], serves=["AC-1", "AC-9"])],
            agents=[AgentSpec(name="writer", role="r", goal="g", serves=["AC-2"])],
        ),
        SPEC3,
    )
    step, agent = tidy.steps[0], tidy.agents[0]
    assert step.serves == ["AC-1"]  # AC-9 is not in the spec
    assert step.uses == [agent.id]  # named, resolved to the id
    assert any("unknown crew, agent or tool 'ghost'" in w for w in tidy.warnings)
    assert "AC-3 is not covered by any element" in tidy.warnings


def test_tidy_design_collects_env_vars_from_tool_needs():
    tidy = tidy_design(
        design(
            tools=[
                ToolSpec(
                    name="search", kind="builtin", purpose="p", needs=[ToolNeed(kind="env_var", name="SERPER_API_KEY")]
                ),
                ToolSpec(name="mail", kind="platform", purpose="p", app="gmail"),
            ]
        ),
        SPEC3,
    )
    assert [var.name for var in tidy.env_vars] == ["SERPER_API_KEY", "CREWAI_PLATFORM_INTEGRATION_TOKEN"]


def test_coverage_uncovered_and_affected():
    built = tidy_design(
        design(
            steps=[FlowStep(name="a", kind="start", does="x", serves=["AC-1"])],
            crews=[CrewSpec(name="c", serves=["AC-1", "AC-2"])],
        ),
        SPEC3,
    )
    step, crew = built.steps[0].id, built.crews[0].id
    assert coverage(SPEC3, built) == {"AC-1": [step, crew], "AC-2": [crew], "AC-3": []}
    assert uncovered(SPEC3, built) == ["AC-3"]
    changed = spec(("AC-1", "a"), ("AC-2", "B changed"))  # AC-3 removed, AC-2 changed
    assert affected(built, criteria_changes(SPEC3, changed)) == [crew]


def test_an_artifact_holds_a_spec_or_a_design():
    base = {"fde_id": "f", "build_id": "b", "version": 1, "author": "x", "created_at": NOW}
    assert isinstance(Artifact.model_validate({**base, "stage": "spec", "content": SPEC3.model_dump()}).content, Spec)
    held = Artifact.model_validate(
        {**base, "stage": "design", "content": design().model_dump(), "based_on": {"spec": 2}}
    )
    assert isinstance(held.content, Design) and held.based_on == {"spec": 2}


def test_tidy_design_checks_state_reads_and_writes_and_joins():
    tidy = tidy_design(
        design(
            state=[StateField(name="emails", type="list[str]", initial="[]"), StateField(name="unused", type="str")],
            steps=[
                FlowStep(name="fetch", kind="start", does="x", writes=[StateChange(field="emails", how="appends")]),
                FlowStep(name="sum", kind="listen", does="y", triggers=["fetch"], join="all", reads=["ghost"]),
            ],
        ),
        SPEC3,
    )
    assert tidy.steps[1].join == "any"  # all of one trigger is just that trigger
    assert any("sum: unknown state field 'ghost'" in w for w in tidy.warnings)
    assert not any("'emails'" in w for w in tidy.warnings)
    assert "State field 'unused': no step changes it and it is not a kickoff input" in tidy.warnings


def test_tidy_design_resolves_output_models_and_collects_packages():
    tidy = tidy_design(
        design(
            models=[OutputModel(id="D-7", name="Report", fields=[ModelField(name="title", type="str")])],
            tasks=[
                TaskSpec(name="write", description="d", expected_output="e", output_model="D-7"),
                TaskSpec(name="judge", description="d", expected_output="e", output_model="Verdict"),
            ],
            tools=[
                ToolSpec(name="a", kind="custom", purpose="p", packages=[Package(name="httpx", why="calls")]),
                ToolSpec(name="b", kind="custom", purpose="p", packages=[Package(name="HTTPX"), Package(name="lxml")]),
            ],
        ),
        SPEC3,
    )
    assert tidy.tasks[0].output_model == "Report"  # named by id: the name is kept
    assert any("judge: unknown output model 'Verdict'" in w for w in tidy.warnings)
    assert [package.name for package in tidy.dependencies] == ["httpx", "lxml"]


def test_llm_keys_come_from_each_agents_model():
    from fde_core.domain.builds import llm_key

    assert llm_key("anthropic/claude-sonnet-5-5") == "ANTHROPIC_API_KEY"
    assert llm_key("openai/gpt-4o") == llm_key("gpt-4o-mini") == llm_key("") == "OPENAI_API_KEY"
    assert llm_key("ollama/llama3") is None
    tidy = tidy_design(
        design(agents=[AgentSpec(name="a", role="r", goal="g", llm="anthropic/claude-haiku-5-5")]), SPEC3
    )
    assert [var.name for var in tidy.env_vars] == ["ANTHROPIC_API_KEY"]
    assert tidy.env_vars[0].required and tidy.env_vars[0].secret


def test_missing_env_lists_required_values_without_one():
    from fde_core.domain.builds import EnvVar, missing_env

    built = design(env_vars=[EnvVar(name="A_KEY"), EnvVar(name="B_KEY"), EnvVar(name="C_OPTIONAL", required=False)])
    assert missing_env(built, {"B_KEY"}) == ["A_KEY"]


def test_credentials_are_required_and_secret_other_vars_follow_the_tool():
    tidy = tidy_design(
        design(
            tools=[
                ToolSpec(
                    name="search",
                    kind="builtin",
                    purpose="p",
                    needs=[
                        ToolNeed(kind="env_var", name="EXA_API_KEY", required=False),
                        ToolNeed(kind="env_var", name="EXA_BASE_URL", required=False),
                    ],
                )
            ],
            env_vars=[EnvVar(name="EXA_BASE_URL", purpose="listed by the architect")],  # required by default
        ),
        SPEC3,
    )
    by_name = {var.name: (var.required, var.secret) for var in tidy.env_vars}
    assert by_name == {"EXA_BASE_URL": (False, False), "EXA_API_KEY": (True, True)}


def test_smoke_inputs_leave_out_empty_optional_ones_and_skipping_needs_a_reason():
    from fde_core.domain.builds import EMPTY, Answer, InputRequest, missing_answers, smoke_inputs

    form = InputRequest(
        agent="Engineer",
        stage="code",
        route="SMOKE",
        request="Code v1",
        kind="smoke",
        keys=["topic", "run_date"],
        questions=[
            Question(id="q1", prompt="Topic?", options=[QuestionOption(value="AI", label="AI")]),
            Question(id="q2", prompt="Date?", options=[QuestionOption(value=EMPTY, label="Leave it empty")]),
        ],
    )
    answered_form = form.model_copy(update={"answers": {"q1": Answer(text="Bees"), "q2": Answer(option=EMPTY)}})
    assert smoke_inputs(answered_form) == {"topic": "Bees"}  # own words win; the empty one is left out
    assert missing_answers(form, {"skip": Answer(text="")}) == ["skip"]
    assert missing_answers(form, {"skip": Answer(text="needs Gmail")}) == []
    assert smoke_inputs(form.model_copy(update={"answers": {"skip": Answer(text="needs Gmail")}})) is None
