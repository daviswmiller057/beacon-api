from typing import Any

from app.context.database import ContextDatabase
from app.context.domain import (
    ContextMutationResult,
    EntityContextResult,
    EntityInput,
    MutationStatus,
    Provenance,
    ResolutionStatus,
)
from app.context.repository import (
    ContextAmbiguityError,
    ContextConflictError,
    ContextRepository,
)


class ContextRegistryService:
    """Deterministic transaction and safety policy for persistent context."""

    def __init__(self, repository: ContextRepository) -> None:
        self.repository = repository

    @classmethod
    def from_path(cls, path: str, *, migrate: bool = True) -> "ContextRegistryService":
        database = ContextDatabase(path)
        if migrate:
            database.upgrade()
        return cls(ContextRepository(database))

    def create_entity(self, entity: EntityInput) -> ContextMutationResult:
        try:
            with self.repository.database.transaction() as connection:
                stored, created = self.repository.create_or_get_entity(connection, entity)
                return ContextMutationResult(
                    status=MutationStatus.CREATED if created else MutationStatus.UNCHANGED,
                    entity=stored,
                    record_id=stored.id,
                    message=("Created entity." if created else "Entity already exists."),
                )
        except ContextAmbiguityError as exc:
            return self._store_ambiguity(str(exc))

    def add_alias(
        self,
        entity: EntityInput,
        alias: str,
        provenance: Provenance = Provenance.EXPLICIT_USER_STATEMENT,
        source_reference: str | None = None,
    ) -> ContextMutationResult:
        try:
            with self.repository.database.transaction() as connection:
                stored, _ = self.repository.create_or_get_entity(connection, entity)
                record, created = self.repository.add_alias(
                    connection, stored, alias, provenance, source_reference
                )
                return ContextMutationResult(
                    status=MutationStatus.CREATED if created else MutationStatus.UNCHANGED,
                    entity=stored,
                    record_id=record.id,
                    message=("Stored alias." if created else "Alias already active."),
                )
        except ContextAmbiguityError as exc:
            return self._store_ambiguity(str(exc))
        except ContextConflictError as exc:
            return ContextMutationResult(status=MutationStatus.CONFLICT, message=str(exc))

    def add_fact(
        self,
        entity: EntityInput,
        predicate: str,
        value: Any,
        provenance: Provenance = Provenance.EXPLICIT_USER_STATEMENT,
        source_reference: str | None = None,
        *,
        replace_existing: bool = False,
    ) -> ContextMutationResult:
        try:
            with self.repository.database.transaction() as connection:
                stored, _ = self.repository.create_or_get_entity(connection, entity)
                record, created = self.repository.add_fact(
                    connection,
                    stored,
                    predicate,
                    value,
                    provenance,
                    source_reference,
                    replace_existing,
                )
                return ContextMutationResult(
                    status=MutationStatus.CREATED if created else MutationStatus.UNCHANGED,
                    entity=stored,
                    record_id=record.id,
                    message=("Stored fact." if created else "Fact already active."),
                )
        except ContextAmbiguityError as exc:
            return self._store_ambiguity(str(exc))

    def add_relationship(
        self,
        source: EntityInput,
        relationship: str,
        target: EntityInput,
        provenance: Provenance = Provenance.EXPLICIT_USER_STATEMENT,
        source_reference: str | None = None,
    ) -> ContextMutationResult:
        try:
            with self.repository.database.transaction() as connection:
                stored_source, _ = self.repository.create_or_get_entity(connection, source)
                stored_target, _ = self.repository.create_or_get_entity(connection, target)
                record, created = self.repository.add_relationship(
                    connection,
                    stored_source,
                    relationship,
                    stored_target,
                    provenance,
                    source_reference,
                )
                return ContextMutationResult(
                    status=MutationStatus.CREATED if created else MutationStatus.UNCHANGED,
                    entity=stored_source,
                    record_id=record.id,
                    message=("Stored relationship." if created else "Relationship already active."),
                )
        except ContextAmbiguityError as exc:
            return self._store_ambiguity(str(exc))

    def query_entity(self, reference: str) -> EntityContextResult:
        resolution = self.repository.resolve(reference)
        if resolution.status is not ResolutionStatus.RESOLVED:
            return EntityContextResult(
                status=resolution.status, candidates=resolution.candidates
            )
        entity = resolution.entity
        assert entity is not None
        return EntityContextResult(
            status=ResolutionStatus.RESOLVED,
            entity=entity,
            aliases=self.repository.aliases_for(entity.id),
            facts=self.repository.facts_for(entity.id),
            outgoing_relationships=self.repository.relationships_for(
                entity.id, incoming=False
            ),
            incoming_relationships=self.repository.relationships_for(
                entity.id, incoming=True
            ),
        )

    def deprecate_alias(self, reference: str, alias: str) -> ContextMutationResult:
        with self.repository.database.transaction() as connection:
            resolution = self.repository.resolve(reference, connection)
            failure = self._resolution_failure(resolution)
            if failure:
                return failure
            entity = resolution.entity
            assert entity is not None
            changed = self.repository.deprecate_alias(connection, entity.id, alias)
            return self._deprecation_result(entity, changed, "alias")

    def deprecate_fact(
        self, reference: str, predicate: str, value_reference: str | None = None
    ) -> ContextMutationResult:
        with self.repository.database.transaction() as connection:
            resolution = self.repository.resolve(reference, connection)
            if resolution.status is ResolutionStatus.NOT_FOUND:
                return self._resolution_failure(resolution)
            candidates = (
                resolution.candidates
                if resolution.status is ResolutionStatus.AMBIGUOUS
                else [resolution.entity]
            )
            matches = []
            for candidate in candidates:
                assert candidate is not None
                matches.extend(
                    self.repository.matching_facts(
                        connection, candidate.id, predicate, value_reference
                    )
                )
            if len(matches) > 1:
                return ContextMutationResult(
                    status=MutationStatus.AMBIGUOUS,
                    message="Multiple facts matched; nothing was changed.",
                )
            entity = next(
                (candidate for candidate in candidates if matches and candidate.id == matches[0].subject_entity_id),
                resolution.entity,
            )
            changed = self.repository.deprecate_ids(
                connection, "context_facts", [item.id for item in matches]
            )
            return self._deprecation_result(entity, changed, "fact")

    def deprecate_relationship(
        self,
        source_reference: str,
        relationship: str,
        target_reference: str | None = None,
    ) -> ContextMutationResult:
        with self.repository.database.transaction() as connection:
            source_resolution = self.repository.resolve(source_reference, connection)
            if source_resolution.status is ResolutionStatus.NOT_FOUND:
                return self._resolution_failure(source_resolution)
            target_id = None
            if target_reference:
                target_resolution = self.repository.resolve(target_reference, connection)
                failure = self._resolution_failure(target_resolution)
                if failure:
                    return failure
                assert target_resolution.entity is not None
                target_id = target_resolution.entity.id
            candidates = (
                source_resolution.candidates
                if source_resolution.status is ResolutionStatus.AMBIGUOUS
                else [source_resolution.entity]
            )
            matches = []
            for candidate in candidates:
                assert candidate is not None
                matches.extend(
                    self.repository.matching_relationships(
                        connection, candidate.id, relationship, target_id
                    )
                )
            if len(matches) > 1:
                return ContextMutationResult(
                    status=MutationStatus.AMBIGUOUS,
                    message="Multiple relationships matched; nothing was changed.",
                )
            source = next(
                (candidate for candidate in candidates if matches and candidate.id == matches[0].source.id),
                source_resolution.entity,
            )
            changed = self.repository.deprecate_ids(
                connection, "context_relationships", [item.id for item in matches]
            )
            return self._deprecation_result(source, changed, "relationship")

    @staticmethod
    def _resolution_failure(resolution) -> ContextMutationResult | None:
        if resolution.status is ResolutionStatus.AMBIGUOUS:
            return ContextMutationResult(
                status=MutationStatus.AMBIGUOUS,
                message="The entity reference is ambiguous; nothing was changed.",
            )
        if resolution.status is ResolutionStatus.NOT_FOUND:
            return ContextMutationResult(
                status=MutationStatus.NOT_FOUND,
                message="No matching entity was found; nothing was changed.",
            )
        return None

    @staticmethod
    def _deprecation_result(entity, changed: int, record_type: str):
        return ContextMutationResult(
            status=(MutationStatus.DEPRECATED if changed else MutationStatus.NOT_FOUND),
            entity=entity,
            message=(
                f"Deprecated {record_type}."
                if changed
                else f"No active matching {record_type} was found."
            ),
        )

    @staticmethod
    def _store_ambiguity(message: str) -> ContextMutationResult:
        return ContextMutationResult(
            status=MutationStatus.AMBIGUOUS,
            message=f"{message}; nothing was changed.",
        )
