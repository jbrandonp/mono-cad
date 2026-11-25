"""Canonical, versioned contracts shared by Mono engineering domains.

These models describe persisted meaning. They deliberately do not import CAD,
meshing, solver, visualization, or manufacturing implementations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mono.domain.units import convert_value, get_unit_definition, validate_unit_dimension


SCHEMA_VERSION = "2.0"


class ContractModel(BaseModel):
    """Strict immutable base for versioned domain contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class ExecutionStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED_INPUTS = "validated_inputs"
    PREPARED = "prepared"
    QUEUED = "queued"
    RUNNING = "running"
    SOLVER_FINISHED = "solver_finished"
    NUMERICALLY_CONVERGED = "numerically_converged"
    VERIFIED = "verified"
    VALIDATED = "validated"
    QUALIFIED = "qualified"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


_ALLOWED_TRANSITIONS: dict[ExecutionStatus, frozenset[ExecutionStatus]] = {
    ExecutionStatus.DRAFT: frozenset({ExecutionStatus.VALIDATED_INPUTS, ExecutionStatus.FAILED}),
    ExecutionStatus.VALIDATED_INPUTS: frozenset({ExecutionStatus.PREPARED, ExecutionStatus.FAILED}),
    ExecutionStatus.PREPARED: frozenset({ExecutionStatus.QUEUED, ExecutionStatus.FAILED}),
    ExecutionStatus.QUEUED: frozenset({ExecutionStatus.RUNNING, ExecutionStatus.CANCELLED, ExecutionStatus.FAILED}),
    ExecutionStatus.RUNNING: frozenset({
        ExecutionStatus.SOLVER_FINISHED, ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED, ExecutionStatus.TIMED_OUT,
    }),
    ExecutionStatus.SOLVER_FINISHED: frozenset({
        ExecutionStatus.NUMERICALLY_CONVERGED, ExecutionStatus.FAILED,
    }),
    ExecutionStatus.NUMERICALLY_CONVERGED: frozenset({ExecutionStatus.VERIFIED, ExecutionStatus.FAILED}),
    ExecutionStatus.VERIFIED: frozenset({ExecutionStatus.VALIDATED, ExecutionStatus.FAILED}),
    ExecutionStatus.VALIDATED: frozenset({ExecutionStatus.QUALIFIED, ExecutionStatus.FAILED}),
    ExecutionStatus.QUALIFIED: frozenset(),
    ExecutionStatus.FAILED: frozenset(),
    ExecutionStatus.CANCELLED: frozenset(),
    ExecutionStatus.TIMED_OUT: frozenset(),
}


class EngineeringObjectRef(ContractModel):
    object_id: UUID = Field(default_factory=uuid4)
    object_type: str = Field(min_length=1, max_length=100)
    project_id: UUID
    schema_version: str = SCHEMA_VERSION


class RevisionRef(ContractModel):
    object_id: UUID
    revision_id: UUID = Field(default_factory=uuid4)
    revision_label: str = Field(min_length=1, max_length=80)
    content_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    is_released: bool = False


class Quantity(ContractModel):
    """A numeric value with an explicit unit and physical dimension."""

    value: float
    unit: str = Field(min_length=1, max_length=32)
    dimension: str = Field(min_length=1, max_length=80)
    uncertainty: float | None = Field(default=None, ge=0)

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float) -> float:
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("Quantity value must be finite")
        return value

    @model_validator(mode="after")
    def unit_matches_dimension(self) -> "Quantity":
        validate_unit_dimension(self.unit, self.dimension)
        return self

    def to(self, target_unit: str) -> "Quantity":
        source = get_unit_definition(self.unit)
        target = get_unit_definition(target_unit)
        converted_uncertainty = (
            None if self.uncertainty is None
            else self.uncertainty * source.scale_to_base / target.scale_to_base
        )
        return type(self).model_validate({
            **self.model_dump(),
            "value": convert_value(self.value, self.unit, target_unit),
            "unit": target_unit,
            "uncertainty": converted_uncertainty,
        })


class ArtifactRef(ContractModel):
    artifact_id: UUID
    artifact_type: str = Field(min_length=1, max_length=100)
    schema_version: str = SCHEMA_VERSION
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_type: str = Field(min_length=1, max_length=120)
    byte_size: int = Field(ge=0)
    storage_uri: str = Field(min_length=1, max_length=2048)


class ArtifactRecord(ArtifactRef):
    producer_id: str = Field(min_length=1, max_length=160)
    producer_version: str = Field(min_length=1, max_length=80)
    source_revision: RevisionRef | None = None
    units: dict[str, str] = Field(default_factory=dict)
    coordinate_frame: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class SolverDescriptor(ContractModel):
    solver_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    vendor: str | None = None
    executable_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    license_id: str | None = None
    supported_physics: frozenset[str]
    supported_dimensions: frozenset[int] = frozenset({2, 3})
    input_formats: frozenset[str]
    output_formats: frozenset[str]
    verification_suite_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class SimulationStudy(ContractModel):
    study_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    study_type: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    physics: frozenset[str] = Field(min_length=1)
    inputs: tuple[ArtifactRef, ...] = ()
    solver: SolverDescriptor
    parameters: dict[str, Any] = Field(default_factory=dict)
    assumptions: tuple[str, ...] = ()
    acceptance_criteria: dict[str, Any] = Field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def solver_supports_physics(self) -> "SimulationStudy":
        unsupported = self.physics - self.solver.supported_physics
        if unsupported:
            raise ValueError(f"Solver does not declare support for physics: {sorted(unsupported)}")
        return self


class FieldDescriptor(ContractModel):
    name: str = Field(min_length=1, max_length=120)
    field_type: str
    unit: str = Field(min_length=1, max_length=32)
    dimension: str = Field(min_length=1, max_length=80)
    location: str
    coordinate_frame: str | None = None
    time_unit: str | None = None


class SolverExecution(ContractModel):
    execution_id: UUID = Field(default_factory=uuid4)
    study_id: UUID
    solver_id: str
    solver_version: str
    status: ExecutionStatus = ExecutionStatus.DRAFT
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    worker_id: str | None = None
    exit_code: int | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    logs: tuple[ArtifactRef, ...] = ()
    outputs: tuple[ArtifactRef, ...] = ()
    convergence_report: dict[str, Any] | None = None
    verification_report: dict[str, Any] | None = None
    validation_report: dict[str, Any] | None = None
    qualification_context: str | None = None

    def transition(
        self,
        target: ExecutionStatus,
        *,
        at: datetime | None = None,
        evidence: dict[str, Any] | None = None,
        qualification_context: str | None = None,
    ) -> "SolverExecution":
        """Return a new state and require evidence at each credibility gate."""
        if target not in _ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(f"Illegal execution transition: {self.status.value} -> {target.value}")
        timestamp = at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Transition timestamp must be timezone-aware")
        changes: dict[str, Any] = {"status": target}
        if target == ExecutionStatus.RUNNING:
            changes["started_at"] = timestamp
        evidence_field = {
            ExecutionStatus.NUMERICALLY_CONVERGED: "convergence_report",
            ExecutionStatus.VERIFIED: "verification_report",
            ExecutionStatus.VALIDATED: "validation_report",
        }.get(target)
        if evidence_field is not None:
            if not evidence:
                raise ValueError(f"{target.value} requires a non-empty {evidence_field}")
            changes[evidence_field] = evidence
        if target == ExecutionStatus.QUALIFIED:
            if not qualification_context or not qualification_context.strip():
                raise ValueError("qualified status requires a named qualification_context")
            if self.validation_report is None:
                raise ValueError("qualified status requires a prior validation report")
            changes["qualification_context"] = qualification_context
        if target in {
            ExecutionStatus.SOLVER_FINISHED, ExecutionStatus.NUMERICALLY_CONVERGED,
            ExecutionStatus.VERIFIED, ExecutionStatus.VALIDATED, ExecutionStatus.QUALIFIED,
            ExecutionStatus.FAILED, ExecutionStatus.CANCELLED, ExecutionStatus.TIMED_OUT,
        }:
            changes["finished_at"] = timestamp
        return type(self).model_validate({**self.model_dump(), **changes})


class DomainEvent(ContractModel):
    event_id: UUID = Field(default_factory=uuid4)
    aggregate_id: UUID
    aggregate_type: str = Field(min_length=1, max_length=100)
    event_type: str = Field(min_length=1, max_length=160)
    schema_version: str = SCHEMA_VERSION
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    actor_id: str = Field(min_length=1, max_length=160)
    correlation_id: UUID
    causation_id: UUID | None = None
    payload: dict[str, Any]

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value


def sha256_hex(data: bytes) -> str:
    """Return a lowercase SHA-256 digest for immutable artifact content."""
    return sha256(data).hexdigest()
