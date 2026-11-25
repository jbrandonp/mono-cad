from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mono.domain.models import (
    ExecutionStatus,
    Quantity,
    SimulationStudy,
    SolverDescriptor,
    SolverExecution,
    sha256_hex,
)


def _solver() -> SolverDescriptor:
    return SolverDescriptor(
        solver_id="example-fea",
        version="1.0.0",
        supported_physics=frozenset({"solid_mechanics"}),
        input_formats=frozenset({"step", "msh"}),
        output_formats=frozenset({"frd"}),
    )


def test_quantity_requires_explicit_dimension_and_unit() -> None:
    q = Quantity(value=25.0, unit="mm", dimension="length")
    assert q.value == 25.0
    assert q.unit == "mm"
    with pytest.raises(ValidationError):
        Quantity(value=25.0, unit="mm")  # type: ignore[call-arg]


def test_quantity_rejects_non_finite_values() -> None:
    with pytest.raises(ValidationError):
        Quantity(value=float("nan"), unit="m", dimension="length")


def test_study_rejects_solver_physics_mismatch() -> None:
    with pytest.raises(ValidationError):
        SimulationStudy(
            project_id=uuid4(),
            study_type="cfd",
            title="Unsupported CFD study",
            physics=frozenset({"fluid_dynamics"}),
            solver=_solver(),
        )


def test_execution_state_machine_rejects_skipped_convergence() -> None:
    execution = SolverExecution(
        study_id=uuid4(), solver_id="example-fea", solver_version="1.0.0"
    )
    queued = execution.transition(ExecutionStatus.VALIDATED_INPUTS)
    prepared = queued.transition(ExecutionStatus.PREPARED)
    running = prepared.transition(ExecutionStatus.QUEUED).transition(ExecutionStatus.RUNNING)
    finished = running.transition(ExecutionStatus.SOLVER_FINISHED)
    with pytest.raises(ValueError):
        finished.transition(ExecutionStatus.VERIFIED)


def test_execution_requires_explicit_convergence_before_verification() -> None:
    execution = SolverExecution(
        study_id=uuid4(), solver_id="example-fea", solver_version="1.0.0"
    )
    execution = execution.transition(ExecutionStatus.VALIDATED_INPUTS)
    execution = execution.transition(ExecutionStatus.PREPARED)
    execution = execution.transition(ExecutionStatus.QUEUED)
    execution = execution.transition(ExecutionStatus.RUNNING)
    execution = execution.transition(ExecutionStatus.SOLVER_FINISHED)
    execution = execution.transition(ExecutionStatus.NUMERICALLY_CONVERGED)
    assert execution.transition(ExecutionStatus.VERIFIED).status == ExecutionStatus.VERIFIED


def test_sha256_is_deterministic() -> None:
    assert sha256_hex(b"mono") == sha256_hex(b"mono")
    assert len(sha256_hex(b"mono")) == 64
