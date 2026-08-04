# Text conversation layer

Beacon's optional text conversation layer translates natural language in both
directions without making the model an execution engine:

```text
human text
  -> ConversationService
  -> provider-neutral ConversationModelProvider
  -> Gemini requests one allowlisted Beacon function
  -> local Pydantic validation and StructuredIntent mapping
  -> existing ActionPlanner and ActionExecutor
  -> authoritative InteractResponse
  -> same Gemini interaction receives function-result data
  -> natural-language reply plus authoritative result
```

The LLM never receives CalDAV, Vikunja, SQLite, HTTP, filesystem, or credential
tools. It cannot write directly to an external system. The legacy `/interact`
route remains available, but a conversation tool call enters Beacon at
`InteractionService.execute_structured_intent`; it is not sent through the old
natural-language interpreter a second time.

## Scope and provider boundary

This phase is text only. There is no microphone, speech recognition, speech
synthesis, streaming audio, wake word, GUI, web search, general-purpose question
answering, or autonomous decision support.

`ConversationModelProvider` and its message, turn, tool-call, tool-result, usage,
and error models are provider-neutral. The Gemini adapter is the only module that
imports `google-genai`. It uses the Gemini Interactions API, function tools in
automatic selection mode, server-side interaction storage, and
`previous_interaction_id` continuation when available. The provider interaction
ID is only an optimization: normalized local history remains authoritative, and
the adapter can reconstruct a bounded interaction when an ID is absent. Another
text provider can implement the same two asynchronous methods—begin a turn and
continue with a function result—without changing planning or execution.

A future voice adapter should produce the same text/tool turns and reuse the same
tool registry and `StructuredIntent` boundary. It must not receive direct service
access.

## Configuration

The feature defaults off. It reuses `GEMINI_API_KEY`; health checks never call
Gemini. Set:

```dotenv
CONVERSATION_ENABLED=true
CONVERSATION_PROVIDER=gemini
CONVERSATION_MODEL=gemini-3.6-flash
CONVERSATION_PROVIDER_TIMEOUT_SECONDS=30
CONVERSATION_PROVIDER_MAX_RETRIES=1
CONVERSATION_MAX_HISTORY_MESSAGES=24
CONVERSATION_MAX_TOOL_ROUNDS=2
CONVERSATION_MAX_SIDE_EFFECT_INTENTS=1
CONVERSATION_MAX_MALFORMED_REPAIRS=1
CONVERSATION_MAX_INPUT_LENGTH=4000
CONVERSATION_MAX_OUTPUT_LENGTH=4000
CONVERSATION_MAX_OUTPUT_TOKENS=1024
```

`GEMINI_API_KEY` is required when the feature is enabled. Application startup
remains independent of Gemini when the feature is disabled.

## API and sessions

`POST /v1/conversation` requires `X-Beacon-API-Key` and:

```json
{
  "message": "Add rehearsal on August 17 at 9 AM for three hours.",
  "client_message_id": "mobile-0187",
  "session_id": "optional-id-returned-by-an-earlier-turn"
}
```

Omit `session_id` to create a session. A success response contains `session_id`,
`turn_id`, status, natural `reply`, optional authoritative `beacon_result`,
degraded state, safe provider/model/usage metadata, and `correlation_id`. It
never includes provider response objects or hidden reasoning.

The client message ID is unique within a session. Repeating the same ID and
normalized content returns the stored response without calling Gemini or Beacon
again. Reusing it with different content returns `409`. A session accepts only
one active turn; a concurrent turn returns `409`. A client-supplied unknown
session ID returns `404`.

SQLite tables `conversation_sessions`, `conversation_turns`, and
`conversation_messages` are added by migration 002 to the existing Beacon
database. Compose stores `/data/beacon.db` in the durable `beacon_data` named
volume. Each repository operation opens its own SQLite connection; mutations
use `BEGIN IMMEDIATE`, foreign keys, the existing ten-second busy timeout, WAL,
unique message IDs, and sequence constraints. All history remains stored, while
only the most recent configured number of messages is sent to the provider.

Conversation text and authoritative results may contain sensitive data. Include
the database in Beacon's backup and access-control plan. Logs contain IDs,
provider/model, latency, requested tool name, status, and degraded state—not
complete messages, prompts, results, credentials, or raw provider payloads.

## Tool loop and failures

The capability manifest is generated from the registered tools. Current tools
map only to implemented intents: task creation/scheduling, bounded daily calendar
events, briefs, explicit context alias/fact/relationship/query/forget operations,
and deterministic clarification.

Every model argument object is validated locally with extra fields forbidden.
Unknown tools and multiple parallel tool calls execute nothing. A turn permits
at most two model/tool rounds, one side-effecting intent submission, and one
malformed-call repair. One transient pre-execution timeout, 429, or eligible 5xx
may be retried. Beacon execution is never automatically retried.

If final rendering fails after execution, Beacon does not replay the action. The
API returns the authoritative result, a terse deterministic fallback, and
`degraded: true`. Partial execution stays partial. Tool-result content is passed
as function-result data and treated as untrusted; instructions embedded in an
event or task title cannot trigger another execution.

## CLI

Use conversation mode for one turn:

```bash
python -m app.cli --conversation \
  --client-message-id shell-001 \
  "Add rehearsal on August 17 at 9 AM for three hours"
```

The response prints the session ID. Continue it with:

```bash
python -m app.cli --conversation \
  --session-id SESSION_ID \
  --client-message-id shell-002 \
  "Make that three and a half hours"
```

Add `--debug` to inspect the complete safe structured response. Existing CLI
behavior without `--conversation` continues to call `/interact`.

## Testing

Automated tests use a scripted provider, mocked deterministic/external service
boundaries, and isolated temporary SQLite databases; they do not call Gemini or
life-management services:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/test_conversation.py \
  tests/test_conversation_provider.py \
  tests/test_conversation_repository.py \
  tests/test_conversation_api.py
```

No live Gemini evaluation harness is included in this phase. Creative wording
is not asserted; structural statuses, execution counts, idempotency, persistence,
and safety invariants are.
