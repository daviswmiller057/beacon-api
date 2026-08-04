from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EntityType(StrEnum):
    PERSON = "person"
    ORGANIZATION = "organization"
    VENUE = "venue"
    LOCATION = "location"
    PROJECT = "project"
    ROUTINE = "routine"
    CONCEPT = "concept"


class Provenance(StrEnum):
    EXPLICIT_USER_STATEMENT = "explicit_user_statement"
    SYSTEM_SEED = "system_seed"
    MODEL_INFERENCE = "model_inference"


class ContextOperation(StrEnum):
    CREATE_ENTITY = "CREATE_ENTITY"
    ADD_ALIAS = "ADD_ALIAS"
    ADD_FACT = "ADD_FACT"
    ADD_RELATIONSHIP = "ADD_RELATIONSHIP"
    QUERY_ENTITY = "QUERY_ENTITY"
    DEPRECATE_ALIAS = "DEPRECATE_ALIAS"
    DEPRECATE_FACT = "DEPRECATE_FACT"
    DEPRECATE_RELATIONSHIP = "DEPRECATE_RELATIONSHIP"


class EntityInput(BaseModel):
    type: EntityType
    canonical_name: str = Field(min_length=1, max_length=500)


class ContextEntity(BaseModel):
    id: str
    type: EntityType
    canonical_name: str
    normalized_canonical_name: str
    active: bool
    created_at: datetime
    updated_at: datetime


class ContextAlias(BaseModel):
    id: str
    entity_id: str
    alias: str
    normalized_alias: str
    provenance: Provenance
    source_reference: str | None = None
    active: bool
    created_at: datetime
    updated_at: datetime
    deprecated_at: datetime | None = None


class ContextFact(BaseModel):
    id: str
    subject_entity_id: str
    predicate: str
    value: Any
    provenance: Provenance
    source_reference: str | None = None
    active: bool
    created_at: datetime
    updated_at: datetime
    deprecated_at: datetime | None = None


class RelationshipView(BaseModel):
    id: str
    source: ContextEntity
    relationship: str
    target: ContextEntity
    provenance: Provenance
    source_reference: str | None = None
    active: bool
    created_at: datetime
    updated_at: datetime
    deprecated_at: datetime | None = None


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"


class EntityResolution(BaseModel):
    status: ResolutionStatus
    entity: ContextEntity | None = None
    candidates: list[ContextEntity] = Field(default_factory=list)


class EntityContextResult(BaseModel):
    status: ResolutionStatus
    entity: ContextEntity | None = None
    candidates: list[ContextEntity] = Field(default_factory=list)
    aliases: list[ContextAlias] = Field(default_factory=list)
    facts: list[ContextFact] = Field(default_factory=list)
    outgoing_relationships: list[RelationshipView] = Field(default_factory=list)
    incoming_relationships: list[RelationshipView] = Field(default_factory=list)


class MutationStatus(StrEnum):
    CREATED = "created"
    UNCHANGED = "unchanged"
    DEPRECATED = "deprecated"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    CONFLICT = "conflict"


class ContextMutationResult(BaseModel):
    status: MutationStatus
    entity: ContextEntity | None = None
    record_id: str | None = None
    message: str
