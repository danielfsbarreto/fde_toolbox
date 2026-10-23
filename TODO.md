# TODO

What's next, in order. The project builder's plan is in more detail in the approved plan (stages, agents, sandbox,
parallel builds); this list tracks what is left.

## Now

- **Try the Design stage on AMP.** It is built: the architect, with the whole crewai_tools and AMP catalog in its
  prompt, `find_integrations`, and web search (Exa) for external APIs. The design covers state and how each step
  changes it, structured outputs, tools (why a custom one is custom, its packages), how a run starts, and coverage.
  After deploying, set `EXA_API_KEY` and a rotated `CREWAI_PLATFORM_INTEGRATION_TOKEN` in the deployment's env vars.

## Project builder, next phases

1. **Code stage.** The engineer agent (check-crewai-api, build-flow, design-agent, design-task,
   connect-tools-and-mcp) in an E2B sandbox:
   - **Template:** `crewai-builder`, built by `flows/project_builder/sandbox/template.py`; CrewAI is never pinned.
   - **The project:** scaffolded with `crewai create flow` and stored per file in MongoDB, each file with the design
     elements it implements and its author (agent or you).
   - **The code panel:** file tree, file view, diffs; ask about selected lines.
   - **The actual graph:** the real flow drawn from `build_flow_structure()`, next to the design.
   - **An `ask_user` tool** for tool-using agents: it halts the agent and opens the same question form.
   - **Sandbox resilience:** retry and reconnect when a command's output stream drops (seen once in the spike).
2. **Test stage.** The tester agent (test-crewai-project): a test per acceptance criterion, offline with the skill's
   `StubLLM`, run in the sandbox; criteria → tests → results; failures routed back to Code.
3. **Later:** Run (real LLM calls with keys held encrypted, given only to the sandbox), Deploy (to AMP from the
   sandbox), downloading the project as a ZIP.

## Project builder, open decisions and parked work

- **Going back to an earlier stage (parked).** The explicit "go back" was removed: it was confusing, and the spec
  writer then offered to approve or move stages itself. Asking for a change to an approved spec still reopens it and
  makes the later stages stale. Options to revisit once Design exists:
  1. *(recommended)* "Request changes" on the latest approved version (its card and the artifact panel), with the
     impact stated before reopening ("this reopens the Spec; Design will need rework").
  2. A "⋯" menu on each approved stage in the stage bar.
  3. Editing an artifact by hand (a new version authored by you, then reviewed by its agent); goes with editing code
     by hand.
- **No open questions in an approved spec (decide).** Today it's only an instruction: the agent can still write a
  spec that lists open questions instead of asking them. Proposed hard rule: a spec with open questions is sent back
  to be asked as a form (code falls back to free-text questions), and approval is blocked while any remain.
- **Kickoff inputs and `{placeholders}` in designs (parked).** The architect knows interpolation only from skills it
  may not load, and nothing checks it. Proposed: a prompt rule (a crew uses every input as `{name}` in agent or task
  text; a flow gets a state field per input), a `tidy_design` check (placeholders that are not inputs or state fields,
  inputs never used), and placeholders highlighted in the Design tab with where each input is used.
- **Rename routes ASK → QUESTION and ANSWER → RESUME** (decide): the names read alike in the router log.
- **The CrewAI docs MCP for the agents** (`MCPServerHTTP("https://docs.crewai.com/mcp")`): planned for every agent,
  not added yet; most useful to the architect and the engineer.
- **Editing code by hand:** an Edit mode in the code panel that saves a version authored by you, marks the tests
  that cover it stale, and tells the next agent turn what changed.
- **AG-UI (parking lot).** Not adopted: no support in crewai 1.15.28 or on AMP, and the CrewAI bridge maps one
  question per pause. The question forms already use a similar structure (a request with questions, answered by
  the next turn), so a later move is a mapping.

## Learnings (from the previous TODO)

- **Phase 2, capturing corrections:** an edit log in the review app (each edit with before and after) and a snapshot
  of each day's first draft (the flow keeps none today).
- **Phase 3, proposals:** a step after finalize where an LLM reads the day's corrections and the existing learnings
  (denied ones too) and proposes a few; Approve / Deny / Edit in the Learnings page.
