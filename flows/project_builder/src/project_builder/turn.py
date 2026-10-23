"""One chat turn in its own process: `python -m project_builder.turn <session_id>`, the message (JSON) on stdin.

The local worker starts one per turn, so turns of different builds never share CrewAI's process-wide event bus.
"""

import sys

from project_builder.main import ProjectBuilderFlow


def main() -> None:
    session_id = sys.argv[1]
    message = sys.stdin.read()
    flow = ProjectBuilderFlow()
    try:
        flow.handle_turn(message, session_id=session_id)
    finally:
        flow.finalize_session_traces()


if __name__ == "__main__":
    main()
