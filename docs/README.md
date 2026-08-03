# Beacon engineering documentation

This directory records the current implementation. Code is the source of truth. Deployment history identified as such is maintainer-provided context, not behavior inferred from code.

- [Architecture](architecture.md): components and runtime boundaries.
- [Scheduling](scheduling.md): exact task-to-calendar lifecycle.
- [Daily Brief](daily-brief.md): read-only daily orchestration, prioritization, travel, weather, conflicts, and summaries.
- [Interaction](interaction.md): natural-language minimum and structured-intent boundary.
- [Availability engine](availability-engine.md): intervals, windows, scoring, sorting.
- [Integrations](integrations.md): Vikunja and Nextcloud/CalDAV contracts.
- [API reference](api-reference.md): endpoints, authentication, successes, errors.
- [Data models](data-models.md): every current Pydantic field.
- [Decisions](decisions.md): architecture decision records.
- [Development](development.md): setup, settings, commands, safe workflow.
- [Debugging notes](debugging-notes.md): failure behavior and traps.
- [Roadmap](roadmap.md): implemented, limited, planned, and speculative work.

There is no internal database, background worker, hosted AI runtime, n8n workflow,
rescheduling daemon, or persistent linkage table in this repository. A narrow
rule-based interpreter provides the minimum local interaction path, while
pre-structured intent provides the boundary for a future Gemini/n8n adapter.
