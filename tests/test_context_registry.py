import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.context.database import ContextDatabase
from app.context.domain import (
    ContextOperation,
    EntityInput,
    EntityType,
    MutationStatus,
    Provenance,
    ResolutionStatus,
)
from app.context.normalization import normalize_reference
from app.context.service import ContextRegistryService
from app.intake.executor import ActionExecutor
from app.intake.planner import ActionPlanner
from app.intake.rules import RuleBasedIntentInterpreter
from app.models import ActionType, InteractRequest, IntentType, StructuredIntent
from app.services.interaction import InteractionService


NOW = datetime(2026, 8, 4, 9, 0, tzinfo=ZoneInfo("America/Chicago"))


def settings(path) -> Settings:
    return Settings(
        beacon_api_key="test",
        nextcloud_caldav_url="https://example.invalid/caldav",
        nextcloud_username="test",
        nextcloud_app_password="test",
        vikunja_api_url="https://example.invalid/api/v1",
        vikunja_api_token="test",
        beacon_calendars="personal",
        beacon_interpreter="rules",
        context_database_path=str(path),
    )


@pytest.fixture
def registry(tmp_path):
    return ContextRegistryService.from_path(str(tmp_path / "context.db"))


@pytest.mark.parametrize(
    ("entity_type", "name"),
    [
        (EntityType.PERSON, "Dr. Rivera"),
        (EntityType.ORGANIZATION, "Sample Players"),
        (EntityType.VENUE, "Example Hall"),
    ],
)
def test_create_supported_entities_idempotently(registry, entity_type, name):
    entity = EntityInput(type=entity_type, canonical_name=name)
    first = registry.create_entity(entity)
    second = registry.create_entity(entity)
    assert first.status is MutationStatus.CREATED
    assert second.status is MutationStatus.UNCHANGED
    assert first.record_id == second.record_id
    assert first.entity.created_at and first.entity.updated_at


def test_empty_entity_names_and_values_are_rejected(registry):
    with pytest.raises(ValidationError):
        EntityInput(type=EntityType.PERSON, canonical_name="")
    with pytest.raises(ValueError, match="letters or numbers"):
        registry.create_entity(EntityInput(type=EntityType.PERSON, canonical_name="..."))
    with pytest.raises(ValueError, match="must not be empty"):
        registry.add_fact(
            EntityInput(type=EntityType.PERSON, canonical_name="Test Person"),
            "note",
            "...",
        )


def test_normalization_is_central_and_punctuation_consistent():
    assert normalize_reference("  A.D.   Players ") == "ad players"
    assert normalize_reference("AD Players") == "ad players"
    assert normalize_reference("T.S.T.") == normalize_reference("tst")


def test_entities_and_facts_survive_new_database_sessions(tmp_path):
    path = tmp_path / "persistent.db"
    first = ContextRegistryService.from_path(str(path))
    first.add_fact(
        EntityInput(type=EntityType.PERSON, canonical_name="Alex Example"),
        "favorite_color",
        "blue",
        source_reference="request-test-1",
    )
    del first
    second = ContextRegistryService.from_path(str(path))
    result = second.query_entity("alex example")
    assert result.status is ResolutionStatus.RESOLVED
    assert result.facts[0].value == "blue"
    assert result.facts[0].source_reference == "request-test-1"
    assert result.facts[0].provenance is Provenance.EXPLICIT_USER_STATEMENT
    assert result.facts[0].created_at and result.facts[0].updated_at


def test_alias_resolution_idempotency_case_and_deprecation(registry):
    entity = EntityInput(type=EntityType.ORGANIZATION, canonical_name="Test Stage Theatre")
    first = registry.add_alias(entity, "T.S.T.")
    second = registry.add_alias(entity, "tst")
    assert first.record_id == second.record_id
    assert second.status is MutationStatus.UNCHANGED
    assert registry.query_entity("Tst").entity.id == first.entity.id
    assert registry.deprecate_alias("Test Stage Theatre", "TST").status is MutationStatus.DEPRECATED
    assert registry.query_entity("TST").status is ResolutionStatus.NOT_FOUND
    assert registry.deprecate_alias("Test Stage Theatre", "TST").status is MutationStatus.NOT_FOUND


def test_alias_cannot_silently_move(registry):
    registry.add_alias(
        EntityInput(type=EntityType.ORGANIZATION, canonical_name="First Company"),
        "FC",
    )
    result = registry.add_alias(
        EntityInput(type=EntityType.ORGANIZATION, canonical_name="Fresh Company"),
        "FC",
    )
    assert result.status is MutationStatus.CONFLICT
    assert registry.query_entity("FC").entity.canonical_name == "First Company"
    assert registry.query_entity("Fresh Company").status is ResolutionStatus.NOT_FOUND


def test_ambiguous_canonical_and_alias_resolution_is_deterministic(registry):
    one = registry.create_entity(
        EntityInput(type=EntityType.PERSON, canonical_name="Shared Name")
    ).entity
    db = registry.repository.database
    with db.transaction() as connection:
        stamp = "2026-08-04T00:00:00+00:00"
        connection.execute(
            "INSERT INTO context_entities (id, entity_type, canonical_name, normalized_canonical_name, active, created_at, updated_at) VALUES ('legacy-entity', 'organization', 'Shared Name', 'shared name', 1, ?, ?)",
            (stamp, stamp),
        )
        two = registry.repository._entity(
            connection.execute(
                "SELECT * FROM context_entities WHERE id = 'legacy-entity'"
            ).fetchone()
        )
        for index, entity in enumerate((one, two)):
            connection.execute(
                "INSERT INTO context_aliases (id, entity_id, alias, normalized_alias, provenance, active, created_at, updated_at) VALUES (?, ?, 'Legacy', 'legacy', 'system_seed', 1, ?, ?)",
                (f"legacy-{index}", entity.id, stamp, stamp),
            )
    assert registry.query_entity("Shared Name").status is ResolutionStatus.AMBIGUOUS
    assert registry.query_entity("Legacy").status is ResolutionStatus.AMBIGUOUS


def test_store_reuses_one_exact_canonical_despite_model_type_drift(registry):
    first = registry.add_alias(
        EntityInput(type=EntityType.CONCEPT, canonical_name="Synthetic Players"),
        "SP",
    )
    second = registry.add_fact(
        EntityInput(type=EntityType.ORGANIZATION, canonical_name="Synthetic Players"),
        "note",
        "test",
    )
    assert second.entity.id == first.entity.id
    assert registry.query_entity("SP").facts[0].value == "test"


def test_facts_are_idempotent_queryable_and_soft_deprecated(registry):
    entity = EntityInput(type=EntityType.VENUE, canonical_name="Example Hall")
    first = registry.add_fact(entity, "parking_note", "Use rear garage")
    second = registry.add_fact(entity, "parking_note", "Use rear garage")
    assert first.record_id == second.record_id
    assert registry.query_entity("Example Hall").facts[0].value == "Use rear garage"
    forgotten = registry.deprecate_fact("Example Hall", "parking_note")
    assert forgotten.status is MutationStatus.DEPRECATED
    assert registry.query_entity("Example Hall").facts == []
    assert registry.deprecate_fact("Example Hall", "parking_note").status is MutationStatus.NOT_FOUND


def test_fact_forget_is_safe_when_multiple_values_match(registry):
    entity = EntityInput(type=EntityType.PERSON, canonical_name="Casey Example")
    registry.add_fact(entity, "phone", "555-0101")
    registry.add_fact(entity, "phone", "555-0102")
    result = registry.deprecate_fact("Casey Example", "phone")
    assert result.status is MutationStatus.AMBIGUOUS
    assert len(registry.query_entity("Casey Example").facts) == 2


def test_explicit_correction_replaces_active_fact(registry):
    entity = EntityInput(type=EntityType.PERSON, canonical_name="Jordan Example")
    registry.add_fact(entity, "office_address", "100 Old Road")
    registry.add_fact(entity, "office_address", "200 New Road", replace_existing=True)
    result = registry.query_entity("Jordan Example")
    assert [fact.value for fact in result.facts] == ["200 New Road"]


def test_relationships_are_independent_idempotent_bidirectional_and_persistent(tmp_path):
    path = tmp_path / "relationships.db"
    registry = ContextRegistryService.from_path(str(path))
    source = EntityInput(type=EntityType.ORGANIZATION, canonical_name="Sample Players")
    target = EntityInput(type=EntityType.VENUE, canonical_name="Example Hall")
    first = registry.add_relationship(source, "normally_operates_at", target)
    second = registry.add_relationship(source, "normally_operates_at", target)
    assert first.record_id == second.record_id
    outgoing = registry.query_entity("Sample Players").outgoing_relationships
    incoming = registry.query_entity("Example Hall").incoming_relationships
    assert outgoing[0].target.id != outgoing[0].source.id
    assert incoming[0].source.canonical_name == "Sample Players"
    del registry
    reopened = ContextRegistryService.from_path(str(path))
    assert len(reopened.query_entity("Sample Players").outgoing_relationships) == 1
    assert reopened.deprecate_relationship(
        "Sample Players", "normally_operates_at", "Example Hall"
    ).status is MutationStatus.DEPRECATED
    assert reopened.query_entity("Sample Players").outgoing_relationships == []


@pytest.mark.parametrize(
    ("message", "intent_type", "operation"),
    [
        ("Remember that TST means Test Stage Theatre.", IntentType.STORE_CONTEXT, ContextOperation.ADD_ALIAS),
        ("Dr. Rivera is my physician.", IntentType.STORE_CONTEXT, ContextOperation.ADD_FACT),
        ("Sample Players normally operates at Example Hall.", IntentType.STORE_CONTEXT, ContextOperation.ADD_RELATIONSHIP),
        ("What does Beacon know about Dr. Rivera?", IntentType.QUERY_CONTEXT, ContextOperation.QUERY_ENTITY),
        ("Forget Dr. Rivera's old office address.", IntentType.FORGET_CONTEXT, ContextOperation.DEPRECATE_FACT),
    ],
)
def test_rules_interpreter_context_commands(tmp_path, message, intent_type, operation):
    intent = RuleBasedIntentInterpreter(settings(tmp_path / "unused.db")).interpret(message, date(2026, 8, 4))
    assert intent.intent is intent_type
    assert intent.operation is operation


def test_rules_interpreter_does_not_passively_learn_or_break_existing_intents(tmp_path):
    interpreter = RuleBasedIntentInterpreter(settings(tmp_path / "unused.db"))
    assert interpreter.interpret("Remember to buy milk tomorrow").intent is IntentType.CREATE_TASK
    assert interpreter.interpret("Schedule rehearsal tomorrow").intent is IntentType.SCHEDULE_TASK


def test_structured_context_rejects_incomplete_or_database_commands():
    with pytest.raises(ValidationError):
        StructuredIntent(intent=IntentType.STORE_CONTEXT, operation=ContextOperation.ADD_FACT)
    with pytest.raises(ValidationError):
        StructuredIntent.model_validate(
            {"intent": "QUERY_CONTEXT", "entity_reference": "Test", "sql": "DROP TABLE context_entities"}
        )


def test_planner_marks_context_queries_read_only_and_mutations_executable(tmp_path):
    planner = ActionPlanner(settings(tmp_path / "unused.db"))
    query = planner.plan(
        StructuredIntent(intent=IntentType.QUERY_CONTEXT, entity_reference="TST"),
        NOW.date(),
    )
    assert query.actions[0].action is ActionType.QUERY_CONTEXT
    store = planner.plan(
        StructuredIntent(
            intent=IntentType.STORE_CONTEXT,
            operation=ContextOperation.CREATE_ENTITY,
            entity=EntityInput(type=EntityType.PROJECT, canonical_name="Project Example"),
        ),
        NOW.date(),
    )
    assert store.actions[0].action is ActionType.MUTATE_CONTEXT


class NeverCalled:
    def __getattr__(self, name):
        raise AssertionError(f"external service called: {name}")


def test_executor_uses_registry_and_duplicate_execution_is_idempotent(tmp_path):
    configured = settings(tmp_path / "executor.db")
    registry = ContextRegistryService.from_path(configured.context_database_path)
    executor = ActionExecutor(
        vikunja=NeverCalled(), scheduler=NeverCalled(), daily_brief=NeverCalled(), context_registry=registry
    )
    intent = StructuredIntent(
        intent=IntentType.STORE_CONTEXT,
        operation=ContextOperation.ADD_ALIAS,
        entity=EntityInput(type=EntityType.ORGANIZATION, canonical_name="Test Stage Theatre"),
        alias="TST",
    )
    plan = ActionPlanner(configured).plan(intent, NOW.date())
    first = executor.execute(plan, NOW, NOW.tzinfo)
    second = executor.execute(plan, NOW, NOW.tzinfo)
    assert first.context.status is MutationStatus.CREATED
    assert second.context.status is MutationStatus.UNCHANGED


def test_normal_intake_boundary_teaches_queries_and_forgets(tmp_path):
    configured = settings(tmp_path / "intake.db")
    service = InteractionService(
        vikunja=NeverCalled(),
        scheduler=NeverCalled(),
        daily_brief=NeverCalled(),
        settings=configured,
        clock=lambda zone: NOW,
    )
    service.interact(InteractRequest(message="Remember that TST means Test Stage Theatre."))
    service.interact(InteractRequest(message="Test Stage Theatre normally operates at Example Hall."))
    service.interact(InteractRequest(message='Test Stage Theatre has the note "synthetic note".'))
    queried = service.interact(InteractRequest(message="What does Beacon know about TST?"))
    assert queried.context.entity.canonical_name == "Test Stage Theatre"
    assert queried.context.aliases[0].alias == "TST"
    assert queried.context.facts[0].value == "synthetic note"
    assert queried.context.outgoing_relationships[0].target.canonical_name == "Example Hall"
    service.interact(InteractRequest(message="Forget Test Stage Theatre's note."))
    assert service.interact(
        InteractRequest(message="What does Beacon know about TST?")
    ).context.facts == []


def test_migration_upgrade_is_repeatable_and_downgrade_reupgrade_works(tmp_path):
    database = ContextDatabase(str(tmp_path / "migration.db"))
    database.upgrade()
    database.upgrade()
    with database.connect() as connection:
        assert [
            row[0]
            for row in connection.execute("SELECT version FROM schema_migrations")
        ] == [1]
    database.downgrade()
    with database.connect() as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "context_entities" not in tables
    database.upgrade()
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM context_entities").fetchone()[0] == 0
