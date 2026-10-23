"""What the builder runs in a build's sandbox, by code: the scaffold, the checks on the engineer's work (it installs,
the real flow matches the design, the tests pass) and the smoke test, the project run for real with the user's
inputs. Each is a step of the turn's timeline; the smoke test's console is shown live."""

from __future__ import annotations

import json
import re
import shlex

from fde_core.domain.builds import Check, Design, SmokeTest, structure_mismatches
from project_builder.activity import Activity
from project_builder.services.sandbox import LOGS, WORK, BuildSandbox, Result

MARK = "===BUILDER==="
SMOKE_TIMEOUT = 600
# Tracing on, output unbuffered (shown live), and a terminal wide enough that CrewAI's trace link isn't wrapped
RUN_ENV = {"CREWAI_TRACING_ENABLED": "true", "PYTHONUNBUFFERED": "1", "COLUMNS": "140"}
TRACE = re.compile(r"https://app\.crewai\.com/[^\s│]*")

# Reads the real flow graph: the Flow subclass defined in <package>.main, through build_flow_structure
STRUCTURE = f"""
import importlib, inspect, json, sys
from crewai.flow.flow import Flow
from crewai.flow.visualization import build_flow_structure
main = importlib.import_module(sys.argv[1] + ".main")
flows = [c for c in vars(main).values()
         if inspect.isclass(c) and issubclass(c, Flow) and c is not Flow and c.__module__ == main.__name__]
print("{MARK}" + json.dumps(build_flow_structure(flows[0]) if flows else None, default=str))
"""

# Runs the project once for real: the flow in <package>.main, or else the crew in <package>.crew. Tracing is on and the
# builder, as the host, consents to sharing the run's trace (crewAI's trace_consent): CrewAI then uploads it and prints
# its viewer link, which the timeline shows. Without consent a non-interactive run's trace is discarded.
SMOKE = f"""
import importlib, inspect, json, sys
from crewai.flow.flow import Flow
try:
    from crewai.telemetry.tracing.ephemeral import trace_consent
except ImportError:  # an older crewAI: no host consent hook
    from contextlib import nullcontext
    trace_consent = lambda callback: nullcontext()
package, inputs = sys.argv[1], json.loads(sys.argv[2])
main = importlib.import_module(package + ".main")
flows = [c for c in vars(main).values()
         if inspect.isclass(c) and issubclass(c, Flow) and c is not Flow and c.__module__ == main.__name__]
with trace_consent(lambda: True):
    if flows:
        result = flows[0]().kickoff(inputs=inputs)
    else:
        crew = importlib.import_module(package + ".crew")
        found = [c for c in vars(crew).values()
                 if inspect.isclass(c) and c.__module__ == crew.__name__ and hasattr(c, "crew")]
        result = found[0]().crew().kickoff(inputs=inputs)
raw = getattr(result, "raw", result)
print("{MARK}" + (raw if isinstance(raw, str) else json.dumps(raw, default=str)))
"""


def _command(activity: Activity, sandbox: BuildSandbox, text: str, command: str, **kwargs) -> Result:
    result = sandbox.run(command, **kwargs)
    activity.step(
        "command", text, command=command, exit=result.exit_code, seconds=result.seconds, output=result.output[-4_000:]
    )
    return result


def scaffold(activity: Activity, sandbox: BuildSandbox, shape: str) -> Result:
    """The project's skeleton, from the CrewAI CLI (latest release): a flow, or a classic Python/YAML crew."""
    if shape == "crew":
        command = f"crewai create crew {sandbox.package} --classic --skip-provider"
    else:
        command = f"crewai create flow {sandbox.package}"
    return _command(activity, sandbox, f"$ {command}", command, cwd=WORK, timeout=300)


def run_checks(activity: Activity, sandbox: BuildSandbox, design: Design) -> tuple[list[Check], dict | None, list[str]]:
    """It installs, its real flow matches the design, its tests pass. Returns the checks, the flow graph and the
    mismatches with the design."""
    checks: list[Check] = []
    installed = _command(activity, sandbox, "$ uv sync", "uv sync", timeout=600)
    checks.append(_check("installs", installed, "uv sync"))

    structure: dict | None = None
    mismatches: list[str] = []
    if design.shape == "flow":
        sandbox.run(f"mkdir -p {LOGS}", cwd=WORK, timeout=30)
        sandbox.write_outside(f"{LOGS}/structure.py", STRUCTURE)
        read = sandbox.run(f"uv run python {LOGS}/structure.py {shlex.quote(sandbox.package)}", timeout=300)
        read_back = _after_mark(read.output, as_json=True)
        structure = read_back if isinstance(read_back, dict) else None
        mismatches = structure_mismatches(design, structure)
        status = "passed" if structure and not mismatches else "failed"
        summary = (
            "matches the design"
            if status == "passed"
            else (f"{len(mismatches)} difference(s) from the design" if structure else "the flow could not be read")
        )
        checks.append(Check(name="matches the design", status=status, summary=summary, output=read.output[-2_000:]))
        activity.step(
            "check", f"Flow vs design: {summary}", name="matches the design", status=status, mismatches=mismatches
        )

    has_tests = any(path.startswith("tests/") and path.endswith(".py") for path in sandbox.list())
    if has_tests:
        tests = _command(activity, sandbox, "$ uv run pytest -q", "uv run pytest -q", timeout=600)
        checks.append(_check("tests", tests, "uv run pytest -q"))
    else:
        checks.append(Check(name="tests", status="skipped", summary="no tests yet"))
    return checks, structure, mismatches


def _check(name: str, result: Result, command: str) -> Check:
    passed = result.exit_code == 0
    last = next((line for line in reversed(result.output.splitlines()) if line.strip()), "")
    summary = (last[:160] if name == "tests" else "") if passed else f"{command} exited {result.exit_code}"
    return Check(name=name, status="passed" if passed else "failed", summary=summary, output=result.output[-2_000:])


def run_smoke(activity: Activity, sandbox: BuildSandbox, inputs: dict[str, str], attempts: int) -> SmokeTest:
    """The project run once for real, with the build's env vars and the user's inputs; its console shown live."""
    sandbox.run(f"mkdir -p {LOGS}", cwd=WORK, timeout=30)
    sandbox.write_outside(f"{LOGS}/smoke.py", SMOKE)
    shown = ", ".join(f"{k}={v!r}" for k, v in inputs.items()) or "no inputs"
    activity.step("note", f"Running the project for real ({shown})", inputs=inputs)

    def live(text: str) -> None:
        last = next((line for line in reversed(text.splitlines()) if line.strip()), "")
        activity.step("console", last[:200], output=text)

    command = f"uv run python {LOGS}/smoke.py {shlex.quote(sandbox.package)} {shlex.quote(json.dumps(inputs))}"
    result = sandbox.run(command, timeout=SMOKE_TIMEOUT, on_output=live, env=RUN_ENV)
    returned = _after_mark(result.output)
    found = TRACE.search(result.output)
    trace_url = found.group(0) if found else None
    passed = result.exit_code == 0 and returned is not None
    reason = "" if passed else _failure(result)
    activity.step(
        "check",
        f"Smoke test {'passed' if passed else 'failed'} in {result.seconds:.0f}s",
        name="smoke test",
        status="passed" if passed else "failed",
        seconds=result.seconds,
        trace_url=trace_url,
        reason=reason,
    )
    return SmokeTest(
        status="passed" if passed else "failed",
        inputs=inputs,
        result=(returned or "")[:20_000] if isinstance(returned, str) else "",
        output=result.output[-6_000:],
        seconds=result.seconds,
        trace_url=trace_url,
        reason=reason,
        attempts=attempts,
    )


def _failure(result: Result) -> str:
    if result.exit_code == 124:
        return f"it did not finish within {SMOKE_TIMEOUT // 60} minutes"
    lines = [line for line in result.output.splitlines() if line.strip()]
    errors = [line for line in lines if re.search(r"(Error|Exception)\b", line)]
    return (errors[-1] if errors else (lines[-1] if lines else f"exit {result.exit_code}"))[:300]


def _after_mark(output: str, as_json: bool = False):
    if MARK not in output:
        return None
    text = output.rsplit(MARK, 1)[1].strip()
    if not as_json:
        return text
    try:
        return json.loads(text.splitlines()[0]) if text else None
    except ValueError:
        return None
