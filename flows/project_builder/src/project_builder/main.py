"""The project builder flow: a conversation that builds a CrewAI project, spec first. One chat message is one turn.

Each message names its build (the review app puts the build's id in it), so a turn finds its build however the chat
transport identifies turns: on AMP a turn's state.id is its kickoff, not the session. The conversation itself lives in
MongoDB (build_messages), so the flow keeps no state between turns. Every turn goes through route_turn: gate buttons
route without an LLM, free text through a router LLM whose reasoning is logged.
"""

import sys
from typing import Any

from crewai.flow import ConversationConfig, ConversationState, Flow, listen
from pydantic import PrivateAttr

from project_builder.bootstrap import app
from project_builder.services.builder import TurnContext, new_terminal_build


# No built-in conversation: every turn has a route of its own. Each turn closes its own trace: on AMP every turn is its
# own execution, and a deferred trace (the default) only finishes when the session is finalized, which no AMP turn
# does, so its executions would show as running forever.
@ConversationConfig(system_prompt="", defer_trace_finalization=False)
class ProjectBuilderFlow(Flow[ConversationState]):
    _turn: Any = PrivateAttr(default=None)  # this turn's TurnContext, set by route_turn

    def route_turn(self, context):
        builder = app().builder
        self._turn = builder.begin(self.state.id, self.state.current_user_message)
        return builder.route(self._turn)

    def _run(self, handler) -> str:
        turn: TurnContext = self._turn
        return app().builder.run(turn, handler)

    @listen("DRAFT")
    def handle_draft(self) -> str:
        """Write or change the current stage's artifact."""
        return self._run(app().builder.draft)

    @listen("ASK")
    def handle_ask(self) -> str:
        """Answer a question or discuss, changing nothing."""
        return self._run(app().builder.ask)

    @listen("APPROVE")
    def handle_approve(self) -> str:
        """Approve the version waiting at the gate."""
        return self._run(app().builder.approve)

    @listen("ANSWER")
    def handle_answer(self) -> str:
        """The answers to the open form: resume the step that asked."""
        return self._run(app().builder.answer)

    @listen("SMOKE")
    def handle_smoke(self) -> str:
        """Open the smoke test's form again (its button on the code's card)."""
        return self._run(app().builder.smoke_form)

    @listen("PENDING")
    def handle_pending(self) -> str:
        """A message while a form is open: wait for its answers."""
        return self._run(app().builder.pending)


def kickoff():
    """`bin/flow project_builder kickoff ["<title>"]`: a new build, chatted with in this terminal."""
    title = sys.argv[1] if len(sys.argv) > 1 else "Terminal build"
    settings = app().settings
    build = new_terminal_build(app().builds, settings.fde_id, title)
    print(f"Build {build.id} ({title}); it also shows in the review app's Builds tab.")
    ProjectBuilderFlow().chat(session_id=build.session_id)


def plot():
    ProjectBuilderFlow().plot()


if __name__ == "__main__":
    kickoff()
