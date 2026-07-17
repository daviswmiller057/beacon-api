# Beacon

## Purpose

Beacon is my attempt to build a self-hosted executive function operating system.

The goal is to reduce the amount of mental bookkeeping required to manage everyday life by coordinating tasks, calendars, reminders, and context.

Beacon is not intended to make decisions for me.

Instead, it should surface information, automate deterministic workflows, and reduce cognitive load while leaving important decisions to me.

---

# Philosophy

## AI interprets.

LLMs are used to understand natural language and convert it into structured data.

## Deterministic systems execute.

Scheduling, prioritization, conflict detection, reminders, and business logic should be deterministic and testable.

---

# Current Architecture

User
    ↓
Gemini
    ↓
Structured JSON
    ↓
Beacon API (FastAPI)
    ↓
Services
    • Availability
    • CalDAV
    • Scheduler (planned)
    ↓
n8n
    ↓
Vikunja
Nextcloud
Home Assistant

---

# Current Status

## Completed

- [x] FastAPI backend
- [x] Docker deployment
- [x] API authentication
- [x] CalDAV integration
- [x] Availability engine
- [x] Ranked availability options

## In Progress

- [ ] Intelligent scheduling

## Planned

- [ ] Vikunja integration
- [ ] Automatic rescheduling
- [ ] Context registry
- [ ] Daily brief
- [ ] Home Assistant integration

---

# Repository Structure

app/
    api/
        API routes

    services/
        Business logic

    models.py
        Shared Pydantic models

    config.py
        Configuration

---

# Design Rules

- Services should have one responsibility.
- AI should never directly modify user data.
- Beacon should remain provider-agnostic.
- Everything should be self-hostable when practical.
- Business logic belongs in Python, not n8n.

---

# Development Notes

Availability Engine
- Reads calendars through CalDAV.
- Produces busy intervals.
- Computes available openings.
- Returns ranked availability.

Next Feature
- Scheduler service.