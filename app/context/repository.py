import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.context.database import ContextDatabase
from app.context.domain import (
    ContextAlias,
    ContextEntity,
    ContextFact,
    EntityInput,
    EntityResolution,
    Provenance,
    RelationshipView,
    ResolutionStatus,
)
from app.context.normalization import normalize_key, normalize_reference


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ContextRepository:
    """SQLite persistence adapter; callers own mutation transaction boundaries."""

    def __init__(self, database: ContextDatabase) -> None:
        self.database = database

    def create_or_get_entity(
        self, connection: sqlite3.Connection, entity: EntityInput
    ) -> tuple[ContextEntity, bool]:
        name = " ".join(entity.canonical_name.strip().split())
        normalized = normalize_reference(name)
        if not normalized:
            raise ValueError("Entity name must contain letters or numbers")
        rows = connection.execute(
            "SELECT * FROM context_entities WHERE active = 1 "
            "AND normalized_canonical_name = ? ORDER BY canonical_name, id",
            (normalized,),
        ).fetchall()
        if len(rows) > 1:
            raise ContextAmbiguityError(
                f'Canonical name "{name}" matches multiple active entities'
            )
        row = rows[0] if rows else connection.execute(
            "SELECT * FROM context_entities WHERE entity_type = ? "
            "AND normalized_canonical_name = ?",
            (entity.type.value, normalized),
        ).fetchone()
        if row:
            if not row["active"]:
                connection.execute(
                    "UPDATE context_entities SET active = 1, updated_at = ? WHERE id = ?",
                    (_now(), row["id"]),
                )
                row = connection.execute(
                    "SELECT * FROM context_entities WHERE id = ?", (row["id"],)
                ).fetchone()
            return self._entity(row), False
        entity_id, timestamp = str(uuid4()), _now()
        connection.execute(
            "INSERT INTO context_entities "
            "(id, entity_type, canonical_name, normalized_canonical_name, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (entity_id, entity.type.value, name, normalized, timestamp, timestamp),
        )
        row = connection.execute(
            "SELECT * FROM context_entities WHERE id = ?", (entity_id,)
        ).fetchone()
        return self._entity(row), True

    def resolve(self, reference: str, connection=None) -> EntityResolution:
        normalized = normalize_reference(reference)
        if not normalized:
            return EntityResolution(status=ResolutionStatus.NOT_FOUND)
        owns_connection = connection is None
        connection = connection or self.database.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM context_entities WHERE active = 1 "
                "AND normalized_canonical_name = ? ORDER BY canonical_name, id",
                (normalized,),
            ).fetchall()
            if not rows:
                rows = connection.execute(
                    "SELECT e.* FROM context_aliases a JOIN context_entities e "
                    "ON e.id = a.entity_id WHERE a.active = 1 AND e.active = 1 "
                    "AND a.normalized_alias = ? ORDER BY e.canonical_name, e.id",
                    (normalized,),
                ).fetchall()
            candidates = [self._entity(row) for row in rows]
            if len(candidates) == 1:
                return EntityResolution(
                    status=ResolutionStatus.RESOLVED, entity=candidates[0]
                )
            return EntityResolution(
                status=(
                    ResolutionStatus.AMBIGUOUS
                    if candidates
                    else ResolutionStatus.NOT_FOUND
                ),
                candidates=candidates,
            )
        finally:
            if owns_connection:
                connection.close()

    def add_alias(
        self,
        connection: sqlite3.Connection,
        entity: ContextEntity,
        alias: str,
        provenance: Provenance,
        source_reference: str | None,
    ) -> tuple[ContextAlias, bool]:
        display = " ".join(alias.strip().split())
        normalized = normalize_reference(display)
        if not normalized:
            raise ValueError("Alias must contain letters or numbers")
        conflicts = connection.execute(
            "SELECT e.* FROM context_aliases a JOIN context_entities e ON e.id = a.entity_id "
            "WHERE a.normalized_alias = ? AND a.active = 1 AND e.active = 1 "
            "AND e.id != ?",
            (normalized, entity.id),
        ).fetchall()
        if conflicts:
            names = ", ".join(row["canonical_name"] for row in conflicts)
            raise ContextConflictError(f'Alias "{display}" already refers to {names}')
        row = connection.execute(
            "SELECT * FROM context_aliases WHERE entity_id = ? AND normalized_alias = ?",
            (entity.id, normalized),
        ).fetchone()
        if row:
            if not row["active"]:
                connection.execute(
                    "UPDATE context_aliases SET alias = ?, provenance = ?, source_reference = ?, "
                    "active = 1, updated_at = ?, deprecated_at = NULL WHERE id = ?",
                    (display, provenance.value, source_reference, _now(), row["id"]),
                )
                row = connection.execute(
                    "SELECT * FROM context_aliases WHERE id = ?", (row["id"],)
                ).fetchone()
                return self._alias(row), True
            return self._alias(row), False
        alias_id, timestamp = str(uuid4()), _now()
        connection.execute(
            "INSERT INTO context_aliases "
            "(id, entity_id, alias, normalized_alias, provenance, source_reference, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (alias_id, entity.id, display, normalized, provenance.value, source_reference, timestamp, timestamp),
        )
        row = connection.execute(
            "SELECT * FROM context_aliases WHERE id = ?", (alias_id,)
        ).fetchone()
        return self._alias(row), True

    def add_fact(
        self,
        connection: sqlite3.Connection,
        entity: ContextEntity,
        predicate: str,
        value: Any,
        provenance: Provenance,
        source_reference: str | None,
        replace_existing: bool = False,
    ) -> tuple[ContextFact, bool]:
        display_predicate = normalize_key(predicate)
        if not display_predicate:
            raise ValueError("Fact predicate must contain letters or numbers")
        value_json = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        normalized_value = normalize_reference(value) if isinstance(value, str) else value_json
        if value is None or (isinstance(value, str) and not normalized_value):
            raise ValueError("Fact value must not be empty")
        if replace_existing:
            timestamp = _now()
            connection.execute(
                "UPDATE context_facts SET active = 0, updated_at = ?, deprecated_at = ? "
                "WHERE subject_entity_id = ? AND normalized_predicate = ? AND active = 1 "
                "AND value_json != ?",
                (timestamp, timestamp, entity.id, display_predicate, value_json),
            )
        row = connection.execute(
            "SELECT * FROM context_facts WHERE subject_entity_id = ? "
            "AND normalized_predicate = ? AND value_json = ?",
            (entity.id, display_predicate, value_json),
        ).fetchone()
        if row:
            if not row["active"]:
                connection.execute(
                    "UPDATE context_facts SET predicate = ?, value_type = ?, normalized_value = ?, "
                    "provenance = ?, source_reference = ?, active = 1, updated_at = ?, "
                    "deprecated_at = NULL WHERE id = ?",
                    (predicate.strip(), type(value).__name__, normalized_value, provenance.value, source_reference, _now(), row["id"]),
                )
                row = connection.execute(
                    "SELECT * FROM context_facts WHERE id = ?", (row["id"],)
                ).fetchone()
                return self._fact(row), True
            return self._fact(row), False
        fact_id, timestamp = str(uuid4()), _now()
        connection.execute(
            "INSERT INTO context_facts "
            "(id, subject_entity_id, predicate, normalized_predicate, value_json, value_type, "
            "normalized_value, provenance, source_reference, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (fact_id, entity.id, predicate.strip(), display_predicate, value_json, type(value).__name__, normalized_value, provenance.value, source_reference, timestamp, timestamp),
        )
        row = connection.execute(
            "SELECT * FROM context_facts WHERE id = ?", (fact_id,)
        ).fetchone()
        return self._fact(row), True

    def add_relationship(
        self,
        connection: sqlite3.Connection,
        source: ContextEntity,
        relationship: str,
        target: ContextEntity,
        provenance: Provenance,
        source_reference: str | None,
    ) -> tuple[RelationshipView, bool]:
        normalized = normalize_key(relationship)
        if not normalized:
            raise ValueError("Relationship type must contain letters or numbers")
        row = connection.execute(
            "SELECT * FROM context_relationships WHERE source_entity_id = ? "
            "AND normalized_relationship = ? AND target_entity_id = ?",
            (source.id, normalized, target.id),
        ).fetchone()
        if row:
            if not row["active"]:
                connection.execute(
                    "UPDATE context_relationships SET relationship = ?, provenance = ?, "
                    "source_reference = ?, active = 1, updated_at = ?, deprecated_at = NULL WHERE id = ?",
                    (relationship.strip(), provenance.value, source_reference, _now(), row["id"]),
                )
                row = connection.execute(
                    "SELECT * FROM context_relationships WHERE id = ?", (row["id"],)
                ).fetchone()
                return self._relationship(connection, row), True
            return self._relationship(connection, row), False
        relationship_id, timestamp = str(uuid4()), _now()
        connection.execute(
            "INSERT INTO context_relationships "
            "(id, source_entity_id, relationship, normalized_relationship, target_entity_id, "
            "provenance, source_reference, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (relationship_id, source.id, relationship.strip(), normalized, target.id, provenance.value, source_reference, timestamp, timestamp),
        )
        row = connection.execute(
            "SELECT * FROM context_relationships WHERE id = ?", (relationship_id,)
        ).fetchone()
        return self._relationship(connection, row), True

    def aliases_for(self, entity_id: str, connection=None) -> list[ContextAlias]:
        return self._list_records(connection, "SELECT * FROM context_aliases WHERE entity_id = ? AND active = 1 ORDER BY alias", (entity_id,), self._alias)

    def facts_for(self, entity_id: str, connection=None) -> list[ContextFact]:
        return self._list_records(connection, "SELECT * FROM context_facts WHERE subject_entity_id = ? AND active = 1 ORDER BY predicate, created_at", (entity_id,), self._fact)

    def relationships_for(self, entity_id: str, incoming: bool, connection=None) -> list[RelationshipView]:
        owns = connection is None
        connection = connection or self.database.connect()
        column = "target_entity_id" if incoming else "source_entity_id"
        try:
            rows = connection.execute(
                f"SELECT * FROM context_relationships WHERE {column} = ? AND active = 1 ORDER BY relationship, id",
                (entity_id,),
            ).fetchall()
            return [self._relationship(connection, row) for row in rows]
        finally:
            if owns:
                connection.close()

    def deprecate_alias(self, connection, entity_id: str, alias: str) -> int:
        return self._deprecate(connection, "context_aliases", "entity_id = ? AND normalized_alias = ?", (entity_id, normalize_reference(alias)))

    def matching_facts(self, connection, entity_id: str, predicate: str, value_reference: str | None) -> list[ContextFact]:
        params: list[Any] = [entity_id, normalize_key(predicate)]
        sql = "SELECT * FROM context_facts WHERE subject_entity_id = ? AND normalized_predicate = ? AND active = 1"
        if value_reference:
            sql += " AND normalized_value = ?"
            params.append(normalize_reference(value_reference))
        return [self._fact(row) for row in connection.execute(sql, params).fetchall()]

    def matching_relationships(self, connection, source_id: str, relationship: str, target_id: str | None) -> list[RelationshipView]:
        params: list[Any] = [source_id, normalize_key(relationship)]
        sql = "SELECT * FROM context_relationships WHERE source_entity_id = ? AND normalized_relationship = ? AND active = 1"
        if target_id:
            sql += " AND target_entity_id = ?"
            params.append(target_id)
        return [self._relationship(connection, row) for row in connection.execute(sql, params).fetchall()]

    def deprecate_ids(self, connection, table: str, ids: list[str]) -> int:
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        return self._deprecate(connection, table, f"id IN ({placeholders})", tuple(ids))

    def _deprecate(self, connection, table: str, where: str, params: tuple) -> int:
        if table not in {"context_aliases", "context_facts", "context_relationships"}:
            raise ValueError("Invalid context table")
        timestamp = _now()
        cursor = connection.execute(
            f"UPDATE {table} SET active = 0, updated_at = ?, deprecated_at = ? WHERE active = 1 AND {where}",
            (timestamp, timestamp, *params),
        )
        return cursor.rowcount

    def _list_records(self, connection, sql, params, converter):
        owns = connection is None
        connection = connection or self.database.connect()
        try:
            return [converter(row) for row in connection.execute(sql, params).fetchall()]
        finally:
            if owns:
                connection.close()

    @staticmethod
    def _entity(row) -> ContextEntity:
        return ContextEntity(id=row["id"], type=row["entity_type"], canonical_name=row["canonical_name"], normalized_canonical_name=row["normalized_canonical_name"], active=bool(row["active"]), created_at=row["created_at"], updated_at=row["updated_at"])

    @staticmethod
    def _alias(row) -> ContextAlias:
        return ContextAlias(id=row["id"], entity_id=row["entity_id"], alias=row["alias"], normalized_alias=row["normalized_alias"], provenance=row["provenance"], source_reference=row["source_reference"], active=bool(row["active"]), created_at=row["created_at"], updated_at=row["updated_at"], deprecated_at=row["deprecated_at"])

    @staticmethod
    def _fact(row) -> ContextFact:
        return ContextFact(id=row["id"], subject_entity_id=row["subject_entity_id"], predicate=row["predicate"], value=json.loads(row["value_json"]), provenance=row["provenance"], source_reference=row["source_reference"], active=bool(row["active"]), created_at=row["created_at"], updated_at=row["updated_at"], deprecated_at=row["deprecated_at"])

    def _relationship(self, connection, row) -> RelationshipView:
        source = connection.execute("SELECT * FROM context_entities WHERE id = ?", (row["source_entity_id"],)).fetchone()
        target = connection.execute("SELECT * FROM context_entities WHERE id = ?", (row["target_entity_id"],)).fetchone()
        return RelationshipView(id=row["id"], source=self._entity(source), relationship=row["relationship"], target=self._entity(target), provenance=row["provenance"], source_reference=row["source_reference"], active=bool(row["active"]), created_at=row["created_at"], updated_at=row["updated_at"], deprecated_at=row["deprecated_at"])


class ContextConflictError(ValueError):
    pass


class ContextAmbiguityError(ValueError):
    pass
