"""The local worker: runs the turns the review app queued when it does not send them to AMP.

`bin/flow project_builder worker`. Each turn runs in its own process, several builds at once (WORKER_CONCURRENCY,
default 4) and one turn at a time per build; a build's next message waits until its turn ends.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from fde_core.domain.builds import Build, Turn
from project_builder.bootstrap import app

log = logging.getLogger("project_builder.worker")
POLL_SECONDS = 1.0


@dataclass
class Running:
    build: Build
    message_id: str
    process: subprocess.Popen
    started: float


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    builds = app().builds
    settings = app().settings
    _recover()
    running: dict[str, Running] = {}
    log.info(
        "Worker ready: up to %d turns at once, for %s. Ctrl-C to stop.", settings.worker_concurrency, settings.fde_id
    )
    try:
        while True:
            for build_id, turn in list(running.items()):
                code = turn.process.poll()
                if code is None:
                    continue
                del running[build_id]
                took = time.monotonic() - turn.started
                log.info("%s: turn ended in %.0fs (exit %d)", turn.build.title, took, code)
                if code != 0:
                    _fail(turn.build, turn.message_id, f"The turn's process exited with {code}")
            if len(running) < settings.worker_concurrency:
                for message in builds.queued(settings.fde_id):
                    if message.build_id in running or len(running) >= settings.worker_concurrency:
                        continue
                    build = builds.find(message.build_id)
                    if build is None:
                        continue
                    turn = Turn(id=message.id, started_at=datetime.now(timezone.utc))
                    if not builds.start_turn(build, turn):
                        continue  # the build is busy; its message waits
                    builds.set_delivery(build, message.id, "sent")
                    process = subprocess.Popen(
                        [sys.executable, "-m", "project_builder.turn", build.session_id],
                        stdin=subprocess.PIPE,
                        text=True,
                    )
                    assert process.stdin is not None
                    process.stdin.write(message.envelope or message.text)
                    process.stdin.close()
                    running[build.id] = Running(build, message.id, process, time.monotonic())
                    log.info("%s: turn started (%d running)", build.title, len(running))
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        for turn in running.values():
            turn.process.terminate()
            _fail(turn.build, turn.message_id, "The worker stopped during this turn")
        log.info("Stopped.")


def _recover() -> None:
    """Turns left in progress by a worker that stopped: end them so their builds can take messages again."""
    builds = app().builds
    for build in builds.in_turn(app().settings.fde_id):
        if build.turn:
            _fail(build, build.turn.id, "The worker stopped during this turn")


def _fail(build: Build, message_id: str, reason: str) -> None:
    builds = app().builds
    builds.add_activity(build, "error", reason, turn_id=message_id)
    builds.add_message(
        build, "assistant", "Builder", f"{reason}. Send your message again to retry.", turn_id=message_id
    )
    builds.set_delivery(build, message_id, "failed")
    builds.finish_turn(build, message_id)


if __name__ == "__main__":
    main()
