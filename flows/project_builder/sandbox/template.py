"""The E2B template every build's sandbox starts from: Python, uv, the CrewAI CLI and a warm package cache.

Run it from the repository root to build or refresh the template (it takes a few minutes):

    bin/flow project_builder python flows/project_builder/sandbox/template.py

CrewAI is never pinned. The template installs the release that is current on PyPI when it is built, and each sandbox
compares it with PyPI when it starts (FRESHEN, about a second) and upgrades only when a newer release exists, so
generated projects always use the latest CrewAI. Rebuilding after a release only makes new sandboxes start faster.
"""

from __future__ import annotations

import json
import sys
from urllib.request import urlopen

from dotenv import load_dotenv
from e2b import Template

ALIAS = "crewai-builder"
PATH = "/home/user/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Run in each new sandbox: the latest CrewAI on PyPI against the installed one (read from uv, not by importing crewai,
# which is slow on a cold sandbox); upgrades only when they differ.
FRESHEN = (
    "latest=$(curl -fsS https://pypi.org/pypi/crewai/json"
    ' | python3 -c \'import json,sys; print(json.load(sys.stdin)["info"]["version"])\')'
    ' && installed=$(uv tool list | sed -n "s/^crewai v//p")'
    ' && if [ "$latest" != "$installed" ]; then UV_COMPILE_BYTECODE=1 uv tool install -q "crewai==$latest"; fi'
    ' && echo "crewai $latest"'
)


def latest_crewai() -> str:
    with urlopen("https://pypi.org/pypi/crewai/json", timeout=30) as response:
        return json.load(response)["info"]["version"]


def main() -> None:
    load_dotenv(override=False)  # E2B_API_KEY, from the flow's .env
    version = latest_crewai()
    template = (
        Template()
        .from_base_image()  # E2B's own image, the one its default sandboxes use
        .pip_install(["uv"])
        .set_envs({"PATH": PATH})
        # The version is in the command so the build reruns this step (and the cache below) after a release.
        # Bytecode compiled now, so the CLI's first run in a sandbox does not compile it.
        .run_cmd(f"UV_COMPILE_BYTECODE=1 uv tool install crewai=={version}", user="user")
        # Scaffold and sync a throwaway project once: its packages stay in uv's cache for every build's own sync.
        # It sits on the cache's filesystem (not /tmp), so uv links the packages instead of copying them.
        .run_cmd(
            "cd /home/user && crewai create flow warmup && cd warmup && uv add -q --dev pytest"
            " && cd /home/user && rm -rf warmup",
            user="user",
        )
    )
    print(f"Building the {ALIAS} template with crewai {version}…")
    info = Template.build(
        template,
        alias=ALIAS,
        tags=["default", f"crewai-{version}"],  # "default" is what Sandbox.create(template=ALIAS) starts
        on_build_logs=lambda entry: print(f"  {entry.message}", file=sys.stderr),
    )
    print(f"Built {ALIAS} (crewai {version}): {info}")


if __name__ == "__main__":
    main()
