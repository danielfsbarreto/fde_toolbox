"""The catalogs a design chooses from, and the checks code makes on what it chose (offline)."""

import pytest

from fde_core.domain.builds import Design, StartsBy, ToolInput, ToolSpec
from project_builder.services import integrations


def design(*tools: ToolSpec) -> Design:
    return Design(shape="flow", rationale="r", summary="s", tools=list(tools))


def test_the_search_finds_platform_actions_and_builtins():
    assert "gmail/send_email" in integrations.search("send email")
    assert "SerperDevTool" in integrations.search("search the internet")
    assert "design a custom tool" in integrations.search("zzzz qqqq")


def test_platform_tools_are_checked_and_normalized():
    checked, warnings = integrations.check_tools(
        design(
            ToolSpec(
                id="D-1",
                name="mail",
                kind="platform",
                purpose="p",
                app="gmail",
                actions=["send-email", "gmail/search-emails", "nope"],
            ),
            ToolSpec(id="D-2", name="crm", kind="platform", purpose="p", app="not_an_app"),
        )
    )
    assert checked.tools[0].actions == ["send_email", "fetch_emails"]  # catalog slugs become selectors
    assert [need.name for need in checked.tools[0].needs] == ["gmail"]  # an AMP connection to make
    assert any("no action 'nope'" in w for w in warnings)
    assert any("'not_an_app' is not an AMP platform integration" in w for w in warnings)


def test_builtins_must_exist_and_bring_their_env_vars():
    checked, warnings = integrations.check_tools(
        design(
            ToolSpec(id="D-1", name="search", kind="builtin", purpose="p", class_name="SerperDevTool"),
            ToolSpec(id="D-2", name="code", kind="builtin", purpose="p", class_name="CodeInterpreterTool"),
        )
    )
    assert [need.name for need in checked.tools[0].needs] == ["SERPER_API_KEY"]
    assert any("'CodeInterpreterTool' is not a crewai_tools class" in w for w in warnings)


def test_custom_tools_need_a_test_plan_and_mcp_tools_a_server():
    _, warnings = integrations.check_tools(
        design(
            ToolSpec(id="D-1", name="score", kind="custom", purpose="p", inputs=[ToolInput(name="x", type="str")]),
            ToolSpec(id="D-2", name="docs", kind="mcp", purpose="p"),
        )
    )
    assert any("D-1 score: a custom tool needs a test plan" in w for w in warnings)
    assert any("D-1 score: a custom tool must say why no built-in or platform tool fits" in w for w in warnings)
    assert any("D-2 docs: an MCP tool needs its server" in w for w in warnings)


def test_connections_without_a_token_are_unknown(monkeypatch):
    monkeypatch.delenv(integrations.TOKEN, raising=False)
    statuses = integrations.check_connections(["gmail"])
    assert statuses[0].status == "unknown" and "No CREWAI_PLATFORM_INTEGRATION_TOKEN" in statuses[0].detail


@pytest.fixture
def platform(monkeypatch):
    import crewai_tools

    monkeypatch.setenv(integrations.TOKEN, "test-token")
    answers = {"gmail": ["a tool"], "slack": []}

    def fake(apps):
        if apps[0] == "hubspot":
            raise RuntimeError("AMP is down")
        return answers[apps[0]]

    monkeypatch.setattr(crewai_tools, "CrewaiPlatformTools", fake)


def test_connections_come_from_the_actions_amp_lists(platform):
    statuses = {s.app: s.status for s in integrations.check_connections(["gmail", "slack", "hubspot", "gmail"])}
    assert statuses == {"gmail": "connected", "slack": "not_connected", "hubspot": "unknown"}


def test_the_catalog_lists_every_builtin_and_app():
    catalog = integrations.catalog()
    assert all(name in catalog for name in integrations.builtins())
    assert "SerperDevTool" in catalog and "(needs SERPER_API_KEY)" in catalog
    assert "gmail" in catalog.split("AMP platform apps")[1]


def test_an_amp_trigger_must_exist():
    def starts(app: str, event: str) -> list[str]:
        trigger = StartsBy(kind="amp_trigger", app=app, event=event)
        return integrations.check_tools(design().model_copy(update={"starts_by": trigger}))[1]

    assert starts("gmail", "email-received") == []
    assert any("Gmail has no trigger 'nope'" in w for w in starts("gmail", "nope"))
    assert any("'not_an_app' is not an AMP platform integration" in w for w in starts("not_an_app", "x"))


def test_crewai_tools_come_before_amp_integrations():
    found = integrations.search("search the internet")
    assert found.index("crewai_tools (open source") < found.index("[builtin]")
    if "AMP platform integrations" in found:
        assert found.index("[builtin]") < found.index("AMP platform integrations")
    assert integrations.search("send email").startswith("AMP platform integrations")  # no crewai_tools sends email


def test_a_class_name_gives_that_tool_in_full():
    described = integrations.search("ExaSearchTool")
    assert "call arguments: search_query: str" in described and "start_published_date" in described
    assert "EXA_BASE_URL (optional)" in described and "EXA_API_KEY (optional)" not in described  # a credential


def test_optional_env_vars_of_a_builtin_do_not_block():
    checked, _ = integrations.check_tools(
        design(ToolSpec(id="D-1", name="search", kind="builtin", purpose="p", class_name="ExaSearchTool"))
    )
    needs = {need.name: need.required for need in checked.tools[0].needs}
    assert needs == {"EXA_API_KEY": True, "EXA_BASE_URL": False}
