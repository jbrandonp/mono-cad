"""Ports (interfaces) between Mono's domain and replaceable infrastructure.

Domain services depend on these protocols, never on a concrete CAD kernel,
solver, database driver, object store, UI framework, or LLM vendor.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from mono.domain.models import (
    ArtifactRecord,
    ArtifactRef,
    DomainEvent,
    EngineeringObjectRef,
    RevisionRef,
    SimulationStudy,
    SolverDescriptor,
    SolverExecution,
)


@runtime_checkable
class ArtifactStore(Protocol):
    """Durable, content-addressed storage for large immutable artifacts."""

    async def put(
        self,
        content: bytes,
        *,
        artifact_type: str,
        media_type: str,
        producer_id: str,
        producer_version: str,
        source_revision: RevisionRef | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRecord: ...

    async def get(self, artifact: ArtifactRef) -> bytes: ...

    async def delete_unreferenced(self, *, older_than_seconds: int) -> int: ...


@runtime_checkable
class ProjectRepository(Protocol):
    """Repository for project and engineering object metadata."""

    async def get_project(self, project_id: UUID) -> dict[str, Any] | None: ...

    async def get_object(self, ref: EngineeringObjectRef) -> dict[str, Any] | None: ...

    async def create_revision(
        self,
        object_ref: EngineeringObjectRef,
        payload: dict[str, Any],
        *,
        expected_revision: UUID | None = None,
    ) -> RevisionRef: ...


@runtime_checkable
class EventStore(Protocol):
    """Append-only domain event storage; event publication is handled by an outbox."""

    async def append(self, event: DomainEvent) -> None: ...

    async def read(
        self,
        aggregate_id: UUID,
        *,
        after_event_id: UUID | None = None,
        limit: int = 1000,
    ) -> list[DomainEvent]: ...


@runtime_checkable
class UnitOfWork(Protocol):
    """A transaction boundary that commits metadata and outbox records atomically."""

    async def __aenter__(self) -> "UnitOfWork": ...

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...

    @property
    def projects(self) -> ProjectRepository: ...

    @property
    def events(self) -> EventStore: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


@runtime_checkable
class SolverAdapter(Protocol):
    """Lifecycle contract for a specific physics solver, not a geometry provider."""

    @property
    def descriptor(self) -> SolverDescriptor: ...

    async def validate_inputs(self, study: SimulationStudy) -> dict[str, Any]: ...

    async def prepare(
        self, study: SimulationStudy, workspace: Path
    ) -> dict[str, Any]: ...

    async def execute(
        self,
        execution: SolverExecution,
        prepared_case: dict[str, Any],
        *,
        cancellation: Any,
        resource_limits: dict[str, Any],
    ) -> SolverExecution: ...

    async def collect_outputs(
        self, execution: SolverExecution
    ) -> Iterable[ArtifactRef]: ...

    async def parse_results(
        self, execution: SolverExecution, artifacts: Iterable[ArtifactRef]
    ) -> dict[str, Any]: ...


@runtime_checkable
class GeometryKernel(Protocol):
    """Exact geometry operations, independent of simulation and rendering."""

    @property
    def kernel_id(self) -> str: ...

    @property
    def kernel_version(self) -> str: ...

    def import_exchange(self, content: bytes, format_id: str) -> Any: ...

    def export_exchange(self, geometry: Any, format_id: str) -> bytes: ...

    def validate_topology(self, geometry: Any) -> dict[str, Any]: ...

    def apply_feature(self, geometry: Any, feature: dict[str, Any]) -> Any: ...


@runtime_checkable
class MeshGenerator(Protocol):
    """Mesh generation and quality assessment as a separate capability."""

    @property
    def generator_id(self) -> str: ...

    async def generate(self, geometry: ArtifactRef, settings: dict[str, Any]) -> ArtifactRef: ...

    async def assess_quality(self, mesh: ArtifactRef) -> dict[str, Any]: ...


@runtime_checkable
class JobQueue(Protocol):
    """Durable job admission and lifecycle; not an in-memory task list."""

    async def enqueue(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        project_id: UUID,
        idempotency_key: str,
    ) -> UUID: ...

    async def cancel(self, job_id: UUID, *, requested_by: str) -> bool: ...

    async def claim(self, worker_id: str, capabilities: set[str]) -> dict[str, Any] | None: ...

    async def heartbeat(self, job_id: UUID, worker_id: str) -> None: ...


@runtime_checkable
class ExtensionRegistry(Protocol):
    """Capability and permission registry for local or remote extensions."""

    async def resolve(self, extension_id: str, version: str | None = None) -> dict[str, Any]: ...

    async def authorize(
        self, extension_id: str, *, project_id: UUID, requested_capabilities: set[str]
    ) -> bool: ...
