"""The MongoDB documents this app shares with the AMP flow (flows/daily_log), and the rules both follow.

Collections and the fields this app reads or writes:

- daily_logs, one per (fde_id, date "YYYY-MM-DD"):
  accounts[] {account_key, account_name, headline, quiet, topics[]}, internal[], unassigned[], removed[]: topics.
  A topic: {id, reviewed, agent_added, title, workstream, what_happened, status_or_decision, risks[], people[],
  my_involvement, confidence, inferred, follow_ups[{id, reviewed, what, owner, due_if_stated}], evidence[event ids],
  account_key, attribution ("rules" | "content" | "review" | "manual"), attribution_reason}.
  review_status ("draft" | "working" | "final"), version (int, +1 on every save; a write names the version it was
  made on), comments {account: text}, unapplied_feedback[] (notes from the agent), activity {account: [lines with
  event_ids and url]}, reviewed_at.
  Topics with attribution "manual" come from day_overrides.manual_entries; they are edited as entries, not here.
- day_overrides, one per (fde_id, date): manual_entries[{id, account_key, title, what, workstream, time,
  duration_min, people[], follow_ups[]}], comments {account: text}. The source of truth for both; the flow turns
  entries into manual events and topics when it opens a review round and when it finalizes the day.
- events: {fde_id, id, account_key, match_reason, meta, source}. A topic moved to another account takes its
  evidence events with it (account_key, match_reason "review", meta.rule_account = the account the rules chose).
- review_requests: {id, fde_id, date, run_id, round, status ("waiting" | "answered" | "superseded"),
  callback_ciphertext, amp_request_id, answered_at, answered_from}. The flow creates one per review pause; this
  app attaches AMP's callback (encrypted) and marks it answered.
- learnings: {id, fde_id, scope ("general" | an account key), steps[] ("attribution" | "topics" | "follow_ups";
  empty means every step), text, status ("proposed" | "approved" | "denied" | "retired"), source ("manual" |
  "assessment"), evidence[], created_at, updated_at, decided_at}. This app writes them (by hand: approved at once);
  every run reads the approved ones into its LLM steps' prompts (fde_core.domain.learnings).

The project builder (flows/project_builder, models in fde_core.domain.builds) shares these:

- builds: {id, fde_id, title, account, session_id (the chat session: AMP's, or the build id for a local worker),
  stages {spec|design|code|test: {status ("not_started" | "draft" | "approved" | "stale"), version,
  approved_version}}, current (stage), turn ({id: the user message, started_at, kickoff_id} while one is answered,
  else null), pending_input ({message_id, request_id} while a form of questions waits for its answers), sandbox_id,
  seq (the last sequence number of its timeline), created_at, updated_at}. This app creates them and claims/ends
  turns on AMP; the flow changes the stages and opens and closes forms.
- build_messages: {id, fde_id, build_id, seq, role ("user" | "assistant"), author ("you" | an agent), text
  (Markdown), card ({stage, version, summary, changes[], gate} | null),
  input_request ({id, agent, stage, route, request, questions[{id, prompt, why, options[{value, label,
  description}], recommended}], status ("pending" | "answered"), answers {qid: {option, text}}, answered_at} | null:
  a form the agent asks instead of guessing; while pending, this app accepts only its complete answers), turn_id,
  delivery ("queued" for a local worker | "sending" to AMP | "sent" | "answered" | "failed", user messages),
  answers_for (the form whose answers a user message carries; the UI shows them on the form instead),
  envelope (user messages: the JSON sent as the turn, with the build's and the message's ids: how the flow finds
  both), created_at}. This app writes the user's; the flow writes the replies.
- build_activity: {fde_id, build_id, seq, turn_id, kind ("turn" | "route" | "agent" | "tool" | "output" |
  "artifact" | "error" | "done"), text, detail, at}. Written by the flow (route entries carry the router's
  reasoning); this app only adds an error when AMP reports a failed turn. Messages and activity share `seq`.
- build_artifacts: {id, fde_id, build_id, stage, version, content (a Spec: {kind, title, goal, users[], inputs[],
  outputs[], non_goals[], examples[], criteria[{id "AC-n", text, given, when, then}], open_questions[]}), summary,
  author, created_at}. Written by the flow.
- build_env: {fde_id, build_id, name, ciphertext, set_at}, unique per (fde_id, build_id, name). The values a build's
  generated project needs (API keys, IDs), Fernet-encrypted with BUILD_ENV_KEY (fde_core.vault): this app encrypts
  what the FDE enters and never returns a value; the flow decrypts them only to write the sandbox's .env.

The flow re-renders the stored Markdown and recomputes activity when it opens a round and when it finalizes, so
edits made here only need to keep the structured fields right.
"""

INTERNAL = "internal"
UNASSIGNED = "unassigned"
MANUAL = "manual"
REVIEW_MATCH = "review"
