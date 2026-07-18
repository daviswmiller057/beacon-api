# Beacon engineering documentation

This directory records the current implementation. Code is the source of truth. Deployment history identified as such is maintainer-provided context, not behavior inferred from code.

- [Architecture](architecture.md): components and runtime boundaries.
- [Scheduling](scheduling.md): exact task-to-calendar lifecycle.
- [Daily Brief](daily-brief.md): read-only daily orchestration, prioritization, travel, weather, conflicts, and summaries.
- [Availability engine](availability-engine.md): intervals, windows, scoring, sorting.
- [Integrations](integrations.md): Vikunja and Nextcloud/CalDAV contracts.
- [API reference](api-reference.md): endpoints, authentication, successes, errors.
- [Data models](data-models.md): every current Pydantic field.
- [Decisions](decisions.md): architecture decision records.
- [Development](development.md): setup, settings, commands, safe workflow.
- [Debugging notes](debugging-notes.md): failure behavior and traps.
- [Roadmap](roadmap.md): implemented, limited, planned, and speculative work.

There is no internal database, background worker, AI runtime, n8n workflow, Home Assistant adapter, rescheduling engine, or persistent linkage table in this repository. The project philosophy is: AI interprets; deterministic systems execute; important decisions remain with the user; self-host where practical; reduce executive-function load; optimize for usefulness over novelty.
