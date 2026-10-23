# FDE Toolbox

What a Forward Deployed Engineer did, day by day: what happened on each account they own, and what else they worked on. A CrewAI Flow writes one log per day, organized by account and topic, and stores it in MongoDB Atlas. The sources are Gmail, Google Calendar, Slack, Microsoft Teams, Fathom, Granola, Linear and GitHub, all read through the FDE's own CrewAI AMP integrations. The FDE reviews each day in the review app (`ui/`); a finalized day is the record of what happened.

Status: daily logs work end to end, one day per run, with one review pause answered from the review app.

## Repository

One repository, several deployables, one shared core:

| Path | What it is | Deployed |
|---|---|---|
| `packages/fde_core/` | The shared contract: the daily log models (`domain/`) and MongoDB access (`repositories/`). No CrewAI | With each flow |
| `flows/daily_log/` | The daily log flow (package `daily_log`) | AMP, working directory `flows/daily_log` |
| `flows/account_report/` | The account report flow (package `account_report`): writes a report from final days | AMP, working directory `flows/account_report` |
| `flows/project_builder/` | The project builder (package `project_builder`): a conversational flow that builds CrewAI projects spec first | AMP, working directory `flows/project_builder` |
| `ui/` | The review app: `web/` (React) and `server/` (thin API) | Heroku (`bin/deploy --heroku`, see `ui/README.md`) |
| `bin/` | `flow` (run any command in a flow's project), `deploy` (the flow to AMP and the review app to Heroku), `start` (review app behind ngrok), `update_skills` (refresh the project builder's CrewAI skills), `init_db.py` (database setup) | |

Each flow is its own uv project with its own `uv.lock`, and depends on `fde_core` by path (`../../packages/fde_core`). AMP pulls the whole repository and builds a flow from its folder (the deployment's working directory). The root `pyproject.toml` only holds the shared lint settings. New flows go next to the others under `flows/`, each with its own AMP deployment.

### The daily log flow (`flows/daily_log/src/daily_log/`)

| Path | What it holds |
|---|---|
| `main.py` | `ActivityLogFlow`: routing only; each step calls one facade |
| `bootstrap.py` | Composition root (`app()`) |
| `settings.py` | Environment-driven settings |
| `ports/` | Protocols for collectors, storage, integrations and LLM steps |
| `facades/` | One per use case: run resolution, daily log, review, action points, run recording |
| `services/` | Profile, identity, reference data, collection, normalization, redaction, matching, relevance, topic grouping and writing, deep review, day writes, rendering |
| `crews/` | The LLM steps: topic grouping, topic writing, deep review (review requests), action points, open task matching |
| `clients/` | AMP action runner, MongoDB and Todoist clients |
| `profiles/` | FDE profiles, one `<fde_id>.yaml` per FDE (`example.yaml` is the template), loaded by `yaml_profiles.py` |

## Setup

```bash
cp flows/daily_log/.env.example flows/daily_log/.env   # Mongo, CREWAI_PLATFORM_INTEGRATION_TOKEN, ANTHROPIC_API_KEY (the review app has ui/.env.example)
bin/flow daily_log sync                                 # installs the flow and the shared core
bin/flow daily_log python bin/init_db.py                # creates the database, collections and indexes (idempotent)
```

Tab completion for `bin/flow` and `bin/deploy` (fish) lives in `bin/completions/`, one file per script. fish only autoloads completions for commands on its PATH, so load the folder at startup, once:

```fish
printf 'for file in %s/bin/completions/*.fish\n    source $file\nend\n' (pwd) > ~/.config/fish/conf.d/fde-activity-log.fish
```

uv has no task runner, so `bin/flow <name> <command>` is the entry point for every flow: it runs the command inside `flows/<name>`, with that flow's environment and `.env`, from anywhere in the repository. `bin/flow` alone lists the flows; `bin/flow --help` shows examples.

FDE profiles live in `flows/daily_log/src/daily_log/profiles/<fde_id>.yaml` and ship with the code, so a profile change takes effect on the next deploy. To add an FDE, copy `example.yaml`. An owned account needs only its `key`, `name`, `aliases` and `email_domains`, plus an optional `description`. Channels, chats and repos are discovered by member domains and names, so new channels need no configuration change. The explicit lists are optional overrides. An invalid or missing profile fails the run before anything is collected, and the error names the field.

The integration token must be the FDE's own Enterprise token (app.crewai.com > Settings > Account). The apps also have to be connected in AMP under that FDE's user. Every action runs against those `@private` connections.

## Deploying

Each flow runs as its own AMP deployment. The daily log flow is **FDE Daily Log** (UUID `4ac2579d-b286-4003-a5f0-409124a6f7c7`; `bin/deploy` uses the UUID, since names can be changed in AMP). AMP builds it from GitHub (`origin`), so only pushed commits reach it. The review app runs on Heroku (see `ui/README.md`).

**Once per flow, in AMP.** The account report flow needs its own deployment, created once in AMP's web interface (the CLI cannot set a working directory): from this GitHub repository, with **Working directory** `flows/account_report` and auto-deploy off, and the environment variables in `flows/account_report/.env.example` (Mongo, `ANTHROPIC_API_KEY`, optionally `TODOIST_API_KEY`). Then:

1. Put its UUID in the `FLOWS` table at the top of `bin/deploy`.
2. On Heroku, set `AMP_REPORT_URL` and `AMP_REPORT_TOKEN` (the deployment's Status tab), so the review app starts reports there. Until then, the app saves a report and shows the command to run it locally.

For an existing deployment (the daily log flow), in its Settings:

1. Turn auto-deploy off: AMP does not allow it together with a working directory.
2. Set the working directory to `flows/daily_log`. AMP pulls the whole repository but builds and runs the flow from that folder, which is how it reaches the shared core in `packages/fde_core`.

**Every deploy**, from the repository root, after committing and pushing. `bin/deploy` ships both parts at once, the flow to AMP and the review app to Heroku, side by side with each line prefixed `[amp]` or `[heroku]` and a summary at the end (`bin/deploy --help` lists every option; fish completes them):

```bash
bin/deploy                    # both (same as --all)
bin/deploy --all --force      # both; --force replaces Heroku's history after amending a deployed commit
bin/deploy --amp              # only the flow: validate, deploy FDE Daily Log, follow it until Online
bin/deploy --heroku           # only the review app: check the committed ui/ builds, push it, wait until it answers
bin/deploy --dry-run          # both, checks only
bin/deploy --status-only      # only follow AMP's status (also --every <s>, --timeout <s>, --uuid <id>)
```

The CrewAI CLI must be signed in (`bin/flow daily_log crewai login`); `bin/deploy` says so when it is not.

Run the CrewAI CLI inside the flow's project, where its `pyproject.toml` and `.env` are: `bin/flow daily_log crewai run`, `bin/flow daily_log crewai eval` (or `cd flows/daily_log` first). Pass the UUID to `crewai deploy status`, `logs` and `push` (`-u 4ac2579d-…`): without it the CLI looks the deployment up by the project's name, `daily_log`, which is not the deployment's name. Do not run `crewai deploy create`: it would make a second deployment.

`bin/deploy` refuses to deploy the flow while `flows/daily_log/`, `packages/` or the root `pyproject.toml` have uncommitted changes, or while their latest change is not on GitHub, since AMP would build older code; and the review app while `ui/` has uncommitted changes, since Heroku only gets committed files.

## Running

Kickoff inputs are all optional, so a scheduler sends `{}`. Schedule it on weekday mornings (Monday to Friday): each run covers the previous workday, so Monday's covers Friday.

| Input | Default | Meaning |
|---|---|---|
| `fde_id` | `daniel-barreto` | The FDE profile (`flows/daily_log/src/daily_log/profiles/<fde_id>.yaml`); another FDE's schedule passes theirs |
| `date` | `"previous workday"` | `"previous workday"` in the FDE's timezone (on Monday, Friday), `"yesterday"`, `"2026-10-13"` for one day. A run covers one day; run each day separately |

```bash
bin/flow daily_log kickoff                            # the previous workday in the FDE's timezone
bin/flow daily_log kickoff '{"date": "2026-10-13"}'   # one day
```

What each source returned for a day is stored. A run reuses a source's stored data when it was collected `ok` at least `COLLECTION_SETTLE_HOURS` (2) after the day ended, since a past day does not change; it fetches the source again when it was never collected, failed, was partial, or was collected before the day ended. Matching, filtering and topics always run again, so profile or rule changes apply to stored days without calling any source. `FORCE_RECOLLECT=true` fetches everything again (local use, e.g. after edits or deletions in a source).

## Output and review

The run's result is the report (Markdown): the day's log. It shows on the AMP execution page and in `GET /status/{kickoff_id}`.

Collection, matching and filtering are plain Python. LLMs only group, write and edit:

| Step | Model setting | Default |
|---|---|---|
| Topic grouping (one call per day; `GROUPING_MAX_CHARS` caps its input) | `LLM_MODEL` | `anthropic/claude-sonnet-5-5` |
| Topic writing (one call per account) | `LLM_MODEL` | `anthropic/claude-sonnet-5-5` |
| Deep review (your requests in review) | `REVIEW_LLM_MODEL` | `anthropic/claude-opus-5-5` |
| Action points (relative due dates, multi-section accounts) | `LLM_MODEL` | `anthropic/claude-sonnet-5-5` |

### Review

Daily runs pause once, at `review_day`. You review the day in the **review app** (`ui/`, see `ui/README.md`), item by item; every change is saved right away, with no LLM and no round trip through the flow:

- topics: mark reviewed, edit any field, remove or restore, move to another account (its events follow);
- follow-ups: mark reviewed, edit what / owner / due, add, remove, drag to another topic;
- accounts: edit the headline, add your comment (context such as "public holiday, the call did not happen");
- offline work: add, edit or remove what no tool recorded.

Then you answer the pause from the app:

- **Finalize the day**: the day becomes final (read-only from then on: the record of what happened); its follow-ups go to Todoist.
- **Send to agent**: requests for the deep review agent, scoped to the day, an account or a topic ("look deeper", "I had a <CUSTOMER> call Tuesday, find it", "rework the <CUSTOMER> topics"). It reads the day's stored events again (nothing is fetched), changes only what the requests are about, flags new topics, and leaves notes on what it could not find. The day comes back for another round.

The app learns about each pause from AMP's Human in the Loop webhook (set it under Deployment > Settings > Human in the Loop > Webhooks, with `REVIEW_UI_URL` on the deployment so the review message links to the app). Email and the AMP dashboard keep working as a fallback: an empty reply finalizes, any text is a day-wide request for the agent. Locally the pause is in the terminal, with the same replies; the app can still be used for the edits.

Additions and comments are kept per day in `day_overrides` and re-added whenever the day is rebuilt. A `final` day is the record of what happened: later runs of that date leave it as is (nothing is collected or rebuilt), and the review app does not edit it. To redo a day, delete it in the review app (the day's Settings) and run it again.

## Action points in Todoist

When a day is final (after you finalize it, or when a final day is run again), every follow-up in it becomes a Todoist task, labeled `fde-log`:

- Yours (no owner, or you) as written; other people's as "Waiting on <owner>: ...".
- Where: the `todoist` row of the account in the profile, `Project / Section` (or just `Project`). With several (AB InBev's general and SAZ sections), an agent picks by content and the first is the default. Internal items go to `internal.todoist`; unassigned ones, and accounts without a usable row, go to the Inbox with the account in the title.
- Due date, always: the stated one, read against the log's date ("tomorrow" in the 13th's log is the 14th); otherwise the next working day after the log.
- Open tasks first: every open task where a follow-up would land (the account's targets and the Inbox, made by hand or not) goes to one LLM call, which decides whether one already covers the follow-up. If so, the follow-up is added to that task as a comment instead of becoming a new task. Only that follow-up's own candidates are accepted; if the call fails, every follow-up becomes a new task.
- Each task or comment is recorded in `action_items` with the follow-ups it came from, so nothing is sent twice and the review app links each follow-up to its task. Tasks are never edited or deleted. A Todoist problem never fails the run: the run's result ends with a line saying what was sent.

Needs `TODOIST_API_KEY` (the FDE's own token). Without it, or without any `todoist` row, nothing is sent.

## Account reports

In the review app's Reports page: pick accounts (owned accounts only), a date range, a language (English, Portuguese or Spanish) and, optionally, a goal for the agent ("for my manager's weekly review: what is at risk and where I need help"). The app saves the request in `reports` and starts the account report flow with the report's id; the flow reads only the final days in the range and writes, per account, a summary, key developments by date (each citing its topics, whose evidence becomes the links), decisions, risks and open issues, and the follow-ups with their Todoist status (code, not the LLM), then a title and an overview across the accounts. Weekdays in the range without a final day are listed as not covered. The app shows the report as a page and draws the PDF from the same HTML (WeasyPrint, CrewAI's fonts bundled in `ui/server/review_api/report_assets`), so the design can change without running the LLM again.

```bash
bin/flow account_report kickoff '{"report_id": "<id>"}'   # run a saved report locally (the app shows this command)
```

## Project builder

In the review app's Builds tab: build a CrewAI project with spec-driven development, by conversation. Stages go spec, design, code, tests; each stage's artifact is versioned and waits at a gate for your approval, and changing an approved spec (just ask for the change) reopens it and makes the later stages stale until they are reworked. Explicitly going back to an earlier stage is parked (see TODO.md). Every message is one turn of the conversational flow: a router picks the route (gate buttons route without an LLM; free text goes to a router LLM whose reasoning is in the Router log), and a specialist agent answers with the CrewAI skills its job needs (`src/project_builder/skills`, from github.com/crewaiinc/skills; `bin/update_skills` refreshes them). Every agent asks instead of guessing: when unsure, it halts and asks a form of questions (suggested options, one recommended, and always room for your own answer), and the build takes nothing else until every question is answered; the answers resume the same agent at the same step. Builds run side by side, one turn at a time each. Spec and design are built; code (in an E2B sandbox) and tests come next.

The design lists every environment variable the generated project needs (its tools' keys, each agent's LLM key, config values). You set them on the design's card, as the first step of its gate (Approve unlocks once every required one is set), or later with *Environment variables* in the stage bar; never in the chat. The review app stores them encrypted with `BUILD_ENV_KEY` (collection `build_env`, never returned once saved), and the flow, which has the same key, decrypts them only for the build's sandbox. A value set in one build can be reused in another.

With `AMP_BUILDER_URL` and `AMP_BUILDER_TOKEN` set, the review app sends each message to the flow's AMP deployment as a chat turn; without them, messages wait for a local worker:

```bash
bin/flow project_builder worker                                    # runs the queued turns, several builds at once
bin/flow project_builder kickoff "Page summarizer"                 # or chat with a new build in this terminal
bin/flow project_builder python flows/project_builder/sandbox/template.py   # build the E2B sandbox template
cd flows/project_builder && uv run pytest tests ../../packages/fde_core/tests   # offline tests
cd ui/server && uv run --with pytest --with mongomock pytest -q tests          # the review app's API tests
```

## Storage (database `fde_activity_log`)

| Collection | Indexes |
|---|---|
| `events` | unique `(fde_id, id)`; `(fde_id, date, source)`; TTL on `expires_at` |
| `daily_logs` | unique `(fde_id, date)` |
| `runs` | unique `run_id`; `(fde_id, started_at desc)` |
| `reference_cache` | unique `(fde_id, kind)` |
| `day_overrides` | unique `(fde_id, date)` |
| `collected_events` | unique `(fde_id, id)`; `(fde_id, date, source)`; TTL on `expires_at` |
| `source_snapshots` | unique `(fde_id, date, source)`; TTL on `expires_at` |
| `action_items` | unique `(fde_id, key)`; `(fde_id, date)` |
| `review_requests` | unique `id`; `(fde_id, date, round desc)`; `(run_id, status)` |
| `learnings` | unique `(fde_id, id)`; `(fde_id, status)` |
| `reports` | unique `id`; `(fde_id, created_at desc)` |
| `builds` | unique `id`; unique `session_id`; `(fde_id, updated_at desc)` |
| `build_artifacts` | unique `(build_id, stage, version)` |
| `build_messages` | unique `(build_id, seq)`; `(fde_id, delivery)` |
| `build_activity` | unique `(build_id, seq)` |
