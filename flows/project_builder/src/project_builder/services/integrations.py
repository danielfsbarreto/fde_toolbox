"""What a design can use, from the installed catalogs: CrewAI AMP's platform integrations (crewai_core) and the
crewai_tools built-ins. The architect searches them; code checks every tool a design names against them, and asks
AMP which platform apps the account has connected.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cache

from fde_core.domain.builds import Design, IntegrationStatus, is_credential

log = logging.getLogger("project_builder")
TOKEN = "CREWAI_PLATFORM_INTEGRATION_TOKEN"


@dataclass(frozen=True)
class Action:
    selector: str  # what an agent's apps=[...] takes after the app, e.g. send_email
    slug: str  # the catalog's own name, e.g. send-email
    name: str
    description: str


@dataclass(frozen=True)
class App:
    slug: str
    name: str
    description: str
    actions: tuple[Action, ...]
    category: str = ""
    triggers: tuple[str, ...] = ()  # the events that can start a run, e.g. email-received


@dataclass(frozen=True)
class Builtin:
    class_name: str
    name: str
    description: str
    env_vars: tuple[str, ...] = field(default=())
    optional: tuple[str, ...] = field(default=())  # env vars crewai_tools marks not required (e.g. a base URL)
    args: tuple[str, ...] = field(default=())  # what the agent passes when it calls the tool: "name: type, description"
    settings: tuple[str, ...] = field(default=())  # constructor fields beyond BaseTool's, e.g. "content: bool = False"


@cache
def platform_apps() -> dict[str, App]:
    from crewai_core import platform_apps as catalog

    category = {app: group.name for group in catalog.PLATFORM_APP_CATEGORIES for app in group.apps}
    apps = {}
    # The raw catalog: unlike the typed PLATFORM_APPLICATION_CATALOG it has the descriptions and each action's
    # selector (its crewai_oauth alias, e.g. gmail/send_email); it is not re-exported, hence vars()
    for entry in vars(catalog)["PLATFORM_CATALOG"]["applications"]:
        actions = tuple(
            Action(
                selector=(tool.get("aliases") or {}).get("crewai_oauth") or tool["slug"],
                slug=tool["slug"],
                name=tool.get("display_name") or tool["slug"],
                description=tool.get("description") or "",
            )
            for tool in entry.get("tools", [])
        )
        apps[entry["slug"]] = App(
            slug=entry["slug"],
            name=entry.get("display_name") or entry["slug"],
            description=entry.get("description") or "",
            actions=actions,
            category=category.get(entry["slug"], ""),
            triggers=tuple(trigger["slug"] for trigger in entry.get("triggers", []) if trigger.get("slug")),
        )
    return apps


@cache
def builtins() -> dict[str, Builtin]:
    import crewai_tools
    from crewai.tools import BaseTool

    found = {}
    for class_name in dir(crewai_tools):
        cls = getattr(crewai_tools, class_name)
        if not (isinstance(cls, type) and issubclass(cls, BaseTool)):
            continue
        fields = cls.model_fields
        env = fields["env_vars"].get_default(call_default_factory=True) if "env_vars" in fields else []
        found[class_name] = Builtin(
            class_name=class_name,
            name=str(fields["name"].default) if "name" in fields else class_name,
            description=" ".join(str(fields["description"].default).split()) if "description" in fields else "",
            env_vars=tuple(var.name for var in env),
            optional=tuple(
                var.name for var in env if not getattr(var, "required", True) and not is_credential(var.name)
            ),
            args=_args(fields),
            settings=_settings(cls, fields),
        )
    return found


def _args(fields: dict) -> tuple[str, ...]:
    """The tool's call arguments, from its args_schema."""
    schema = fields["args_schema"].default if "args_schema" in fields else None
    if not (isinstance(schema, type) and hasattr(schema, "model_fields")):
        return ()
    shown = []
    for name, f in schema.model_fields.items():
        kind = getattr(f.annotation, "__name__", str(f.annotation))
        shown.append(f"{name}: {kind}, {' '.join((f.description or '').split())[:120]}")
    return tuple(shown)


def _settings(cls: type, fields: dict) -> tuple[str, ...]:
    """Constructor fields the tool adds to BaseTool's (e.g. ExaSearchTool's content, summary, type)."""
    from crewai.tools import BaseTool

    base = set(BaseTool.model_fields) | {"env_vars", "package_dependencies", "client", "api_key", "base_url"}
    shown = []
    for name, f in fields.items():
        if name in base or name.startswith("_"):
            continue
        default = "" if f.is_required() else f" = {f.default!r}"[:40]
        shown.append(f"{name}: {getattr(f.annotation, '__name__', str(f.annotation))}{default}")
    return tuple(shown)


def describe(class_name: str) -> str | None:
    """One crewai_tools built-in in full: what it does, its call arguments, its settings and its env vars."""
    tool = builtins().get(class_name)
    if tool is None:
        return None
    env = ", ".join(f"{var}{' (optional)' if var in tool.optional else ''}" for var in tool.env_vars) or "none"
    return (
        f"[builtin] {tool.class_name} (offered to agents as {tool.name!r}): {tool.description}\n"
        f"  call arguments: {'; '.join(tool.args) or 'none'}\n"
        f"  settings (constructor): {'; '.join(tool.settings) or 'none'}\n"
        f"  env vars: {env}"
    )


def _words(text: str) -> set[str]:
    """Lowercase words of 3+ letters, plurals folded (emails → email)."""
    words = set()
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if len(word) > 2:
            words.add(word[:-1] if word.endswith("s") and len(word) > 3 else word)
    return words


def search(query: str, limit: int = 8) -> str:
    """What matches a need (e.g. "send email", "crm contacts", "scrape a web page"): the crewai_tools built-ins first
    (open source, the first choice), then the AMP platform integrations, each best first. A match in the name counts
    most, then the description, then (for an app) the single best-matching action."""
    exact = describe(query.strip())
    if exact:  # a class name: that tool in full
        return exact
    wanted = _words(query)
    tools: list[tuple[int, str]] = []
    for tool in builtins().values():
        score = 3 * len(wanted & _words(f"{tool.class_name} {tool.name}")) + len(wanted & _words(tool.description))
        if score >= 2:
            env = f" Needs: {', '.join(tool.env_vars)}." if tool.env_vars else ""
            tools.append((score, f"[builtin] {tool.class_name}: {tool.description[:180]}{env}"))
    scored: list[tuple[int, str]] = []
    for app in platform_apps().values():
        by_action = sorted(
            ((len(wanted & _words(f"{a.selector} {a.name} {a.description}")), a) for a in app.actions),
            key=lambda pair: -pair[0],
        )
        best = by_action[0][0] if by_action else 0
        score = (
            3 * len(wanted & _words(f"{app.slug} {app.name}"))
            + len(wanted & _words(f"{app.description} {app.category}"))
            + 2 * best
        )
        if score >= 2:
            shown = [a for n, a in by_action if n == best and n > 0][:3] or [a for _, a in by_action[:3]]
            actions = "; ".join(f"{app.slug}/{a.selector} ({(a.description or a.name)[:90]})" for a in shown)
            triggers = f"\n    triggers (can start a run): {', '.join(app.triggers)}" if app.triggers else ""
            scored.append(
                (
                    score,
                    f"[platform] {app.name}, app {app.slug!r}: {app.description[:140]}\n"
                    f"    actions: {actions}{triggers}",
                )
            )
    if not tools and not scored:
        return "No crewai_tools built-in or AMP integration matches: design a custom tool."
    tools.sort(key=lambda item: -item[0])
    scored.sort(key=lambda item: -item[0])
    sections = []
    if tools:
        sections.append("crewai_tools (open source; use one of these when it fits):")
        sections += [text for _, text in tools[: limit // 2 + 1]]
    if scored:
        heading = "AMP platform integrations (only when no crewai_tools tool fits; mostly to act in a SaaS app):"
        sections += [heading, *[text for _, text in scored[: limit // 2]]]
    return "\n".join(sections)


@cache
def catalog() -> str:
    """Everything a design can reuse, for the architect's prompt: every crewai_tools built-in with its purpose,
    and every AMP platform app by slug (find_integrations gives an app's actions)."""
    lines = []
    for tool in sorted(builtins().values(), key=lambda tool: tool.class_name):
        needs = f" (needs {', '.join(tool.env_vars)})" if tool.env_vars else ""
        lines.append(f"- {tool.class_name}: {tool.description[:110]}{needs}")
    tools = "\n".join(lines)
    return (
        f"crewai_tools built-ins (open source; the first choice):\n{tools}\n\n"
        f"AMP platform apps (when no crewai_tools tool fits; find_integrations gives their actions): "
        f"{', '.join(sorted(platform_apps()))}"
    )


def check_tools(design: Design) -> tuple[Design, list[str]]:
    """Each tool against the catalogs: platform apps and actions must exist (actions are normalized to their
    selectors), built-ins must be real crewai_tools classes, MCP tools need a server, custom tools a test plan.
    Platform tools get their AMP connection as a need, built-ins their env vars. Returns the design and warnings."""
    design = design.model_copy(deep=True)
    warnings: list[str] = []
    apps, tools = platform_apps(), builtins()
    for tool in design.tools:
        label = f"{tool.id} {tool.name}"
        if tool.kind == "platform":
            app = apps.get(tool.app or "")
            if app is None:
                warnings.append(f"{label}: {tool.app!r} is not an AMP platform integration")
                continue
            # An action may be named by its selector, its catalog slug or its display name, with or without app/
            known = {key: a.selector for a in app.actions for key in (a.selector, a.slug, a.name.lower())}
            normalized = []
            for action in (a.removeprefix(f"{app.slug}/") for a in tool.actions):
                selector = known.get(action) or known.get(action.lower()) or known.get(action.replace("-", "_"))
                if selector:
                    normalized.append(selector)
                else:
                    warnings.append(f"{label}: {app.name} has no action {action!r}")
            tool.actions = list(dict.fromkeys(normalized))
            if not any(need.kind == "amp_connection" for need in tool.needs):
                tool.needs.append(_need("amp_connection", app.slug, f"Connect {app.name} in AMP"))
        elif tool.kind == "builtin":
            builtin = tools.get(tool.class_name or "")
            if builtin is None:
                warnings.append(f"{label}: {tool.class_name!r} is not a crewai_tools class")
                continue
            have = {need.name for need in tool.needs}
            tool.needs.extend(_need("env_var", var, builtin.class_name) for var in builtin.env_vars if var not in have)
            for need in tool.needs:  # the catalog knows which of its env vars are optional
                if need.kind == "env_var" and need.name in builtin.optional:
                    need.required = False
        elif tool.kind == "mcp" and not tool.server:
            warnings.append(f"{label}: an MCP tool needs its server (an https URL, or a stdio server it ships)")
        elif tool.kind == "custom":
            if not tool.test_plan:
                warnings.append(f"{label}: a custom tool needs a test plan")
            if not tool.why_custom.strip():
                warnings.append(f"{label}: a custom tool must say why no built-in or platform tool fits")
    starts = design.starts_by
    if starts.kind == "amp_trigger":
        app = apps.get(starts.app or "")
        if app is None:
            warnings.append(f"Starts by: {starts.app!r} is not an AMP platform integration")
        elif starts.event not in app.triggers:
            known = ", ".join(app.triggers) or "none"
            warnings.append(f"Starts by: {app.name} has no trigger {starts.event!r} (it has: {known})")
    return design, warnings


def _need(kind: str, name: str, purpose: str):
    from fde_core.domain.builds import ToolNeed

    return ToolNeed.model_validate({"kind": kind, "name": name, "purpose": purpose})


def check_connections(apps: list[str]) -> list[IntegrationStatus]:
    """Which of these platform apps the AMP account has connected: an app whose actions AMP lists is connected."""
    now = datetime.now(timezone.utc)
    if not apps:
        return []
    if not os.environ.get(TOKEN):
        return [IntegrationStatus(app=app, status="unknown", detail=f"No {TOKEN}", checked_at=now) for app in apps]
    from crewai_tools import CrewaiPlatformTools

    statuses = []
    for app in dict.fromkeys(apps):
        try:
            connected = bool(CrewaiPlatformTools(apps=[app]))
            statuses.append(
                IntegrationStatus(app=app, status="connected" if connected else "not_connected", checked_at=now)
            )
        except Exception as error:  # the check informs, it never fails the turn
            log.warning("Connection check for %s failed: %s", app, type(error).__name__)
            statuses.append(IntegrationStatus(app=app, status="unknown", detail=type(error).__name__, checked_at=now))
    return statuses
