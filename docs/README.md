# Beacon engineering documentation

These documents describe the code currently present in this repository. When a
document and the implementation disagree, the implementation and its tests are
the source of truth. Historical notes and future ideas are labeled explicitly.

## Start here

Choose the path that matches what you are trying to do:

- **Use Beacon from a terminal:** [CLI usage](../CLI_USAGE.md)
- **Run or develop Beacon:** [Development](development.md)
- **Call Beacon over HTTP:** [API reference](api-reference.md)
- **Consume the native Today snapshot:** [Today dashboard](today-dashboard.md)
- **Understand the system:** [Architecture](architecture.md)
- **Understand natural-language intake:** [Interaction](interaction.md) and
  [Intake architecture](../INTAKE_ARCHITECTURE.md)
- **Use persistent model-mediated text sessions:** [Text conversation](conversation.md)
- **Understand persistent user context:** [Context Registry](context-registry.md)
- **Understand scheduling behavior:** [Scheduling](scheduling.md) and
  [Availability engine](availability-engine.md)
- **Operate or troubleshoot Beacon:** [Debugging notes](debugging-notes.md)

## Reference map

| Document | Scope |
|---|---|
| [Architecture](architecture.md) | Runtime components, request paths, state ownership, security boundaries, and deployment shape. |
| [Interaction](interaction.md) | `/interact`, the rules and Gemini interpreters, deterministic planning, execution, and clarification. |
| [Text conversation](conversation.md) | Bidirectional Gemini function calling, local sessions, idempotency, safety, and degraded responses. |
| [Context Registry](context-registry.md) | Explicit persistent context, resolution, forgetting, migrations, backups, and privacy. |
| [Scheduling](scheduling.md) | Exact Vikunja-task-to-Nextcloud-work-block lifecycle. |
| [Availability engine](availability-engine.md) | Interval normalization, daily windows, candidate generation, scoring, and limitations. |
| [Daily Brief](daily-brief.md) | Read-only calendar/task/weather/travel aggregation, warnings, conflicts, and summaries. |
| [Today dashboard](today-dashboard.md) | Authenticated native-client schema, supported data, and null semantics. |
| [Integrations](integrations.md) | Vikunja, Nextcloud/CalDAV, Waze, Home Assistant, and Gemini adapter contracts. |
| [API reference](api-reference.md) | Authentication, endpoint payloads, success responses, and error mapping. |
| [Data models](data-models.md) | Current Pydantic request/response fields and validation rules. |
| [Development](development.md) | Python/Docker setup, every runtime setting, commands, and safe change workflow. |
| [Debugging notes](debugging-notes.md) | Common failure modes, diagnostic order, and important traps. |
| [Architecture decisions](decisions.md) | Accepted design decisions and their tradeoffs. |
| [Roadmap](roadmap.md) | Implemented capabilities, confirmed limitations, planned work, and speculative ideas. |

## Current system at a glance

Beacon is a self-hosted FastAPI service with a replaceable terminal client. The
CLI sends requests only to the HTTP API. The API accepts natural language or a
validated structured intent, builds a deterministic action plan, and delegates
execution to services. Vikunja remains the task source of truth and Nextcloud
remains the calendar source of truth.

Implemented external adapters are:

- Vikunja task reads, lists, and task creation;
- Nextcloud CalDAV reads, work-block creation, and in-place updates;
- optional Waze travel estimates for Daily Brief;
- optional Home Assistant weather reads for Daily Brief;
- optional Gemini structured-intent interpretation;
- optional bidirectional Gemini text conversation through Beacon tools.

Beacon has one internal SQLite database for explicitly taught Context Registry
data and persistent conversation sessions. It has no background worker,
automatic rescheduling daemon, reminder dispatcher, general action audit log, or
n8n workflow. Daily Brief
generation and scheduling are request-driven. The local rule interpreter is the
default; Gemini is optional and is limited to producing validated intent data.
