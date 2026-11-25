# Mono — Refoundation Implementation Plan

**Baseline:** `master` commit `4a2d76fea78450cb90cc497343f65bd5a9d3a1d3`  
**Branch:** `rebuild/mono-platform-foundations-2026-11`  
**Status:** plan only; implementation and verification remain separate.

## Delivery rule

Do not build every advertised discipline at once. Complete the platform foundation and prove two vertical slices: (1) parametric CAD design and revision; (2) one narrowly scoped linear-static FEA workflow. Only then expand.

## Phase 0 — Truth, inventory and reproducibility
**Goal:** know exactly what exists and what can be built.

- Correct README claims and remove stale test/coverage badges.
- Add capability registry with statuses: `EXPERIMENTAL`, `PROTOTYPE`, `INTEGRATION_ONLY`, `VERIFIED_FOR_SCOPE`, `QUALIFIED_FOR_CONTEXT`.
- Create test tiers: unit, contract, integration, benchmark, security and VVUQ.
- Define supported OS/Python/Node/Rust/solver matrix and exact tool versions.
- Capture CI evidence; no green badge without current run.
- Generate SBOM and license inventory.

**Exit:** clean reproducible baseline report; no undocumented claim of test or solver validation.

## Phase 1 — Platform domain kernel
**Goal:** remove semantic coupling.

- Introduce typed IDs and schema-versioned domain entities.
- Split `DesignGeometry`, `AnalysisDefinition`, `ComputeJob`, `ResultSet` and `EvidenceRecord`.
- Add dimensioned quantities and coordinate frames.
- Introduce immutable revisions and dependency graph.
- Replace the single generic provider with narrow capability interfaces.
- Add explicit domain capability registry.

**Exit:** a CAD design can have multiple studies and result sets without replacing its geometry provider; unsupported capabilities fail explicitly.

## Phase 2 — Persistence and project lifecycle
**Goal:** projects survive restarts and can be migrated.

- Local relational repository (SQLite first if appropriate).
- Artifact store with content hashes and metadata.
- Transactional project/revision writes.
- Schema migrations and safe import/export.
- Strict archive schema and safe member validation.
- Event log and durable audit trail.
- Round-trip tests for all supported artefacts.

**Exit:** close/reopen preserves domain objects, revisions, dependencies, source artefacts and analysis history; malformed archives are rejected safely.

## Phase 3 — Execution and security
**Goal:** compute is safe, observable and recoverable.

- Durable job state machine and job repository.
- Worker registry, quotas, heartbeat, cancellation and scratch isolation.
- OS-level sandbox for arbitrary/generated code.
- Remove false assumption that AST validation isolates Python.
- Backend identity handshake, dynamic port and local session token.
- Restrictive CSP/CORS/Host policy and least-privilege Tauri capabilities.
- API/WebSocket schemas, payload limits, authentication and rate limits.

**Exit:** malicious or runaway job cannot access host secrets/files/network beyond declared policy; restart recovers job state; cancellation is observable.

## Phase 4 — CAD vertical slice
**Goal:** trustworthy design authoring.

- Declarative operation graph for common CAD tasks.
- Stable semantic references and topology remapping.
- Correct constraint solver semantics or integration with a validated solver.
- Geometry validation and explicit units.
- STEP import/export round trip and native document persistence.
- Revision compare and rollback.
- UI editing and provenance.

**Exit:** documented test corpus passes across supported platforms; operations are deterministic within defined tolerances.

## Phase 5 — FEA vertical slice
**Goal:** a bounded, credible structural workflow.

Scope initially: linear static, isotropic linear elastic material, small displacement, defined element family and documented load/support cases.

- Explicit material and boundary-condition schema.
- No arbitrary face fallback; ambiguous selections block.
- Mesh quality and element formulation checks.
- CalculiX deck validation and solver log capture.
- FRD parser fixtures from known versions.
- Reaction force equilibrium and unit checks.
- Analytical and independent benchmark cases.
- Mesh convergence study and uncertainty/limitation report.

**Exit:** verification evidence for declared scope, with known limitations. Do not claim fatigue, nonlinear plasticity, fracture or life prediction.

## Phase 6 — Engineering evidence and VVUQ
**Goal:** make numerical trust inspectable.

- Solver registry with version, build, license and capability manifest.
- Verification plan per solver/domain.
- Code verification and solution verification.
- Validation against experimental/benchmark reference.
- Sensitivity and uncertainty quantification.
- Credibility/qualification labels with scope and expiration.
- Immutable evidence bundle per result.

**Exit:** every published simulation result shows its evidence, assumptions and validity envelope.

## Phase 7 — Additional disciplines
Add one bounded context at a time:
1. thermal analysis;
2. CFD;
3. electromagnetics;
4. multibody dynamics;
5. topology/shape and robust optimization;
6. manufacturing process planning and machine simulation;
7. robotics;
8. BIM/infrastructure/GIS;
9. materials informatics and molecular discovery;
10. PLM and change control;
11. natural systems and large-scale scientific data.

Each requires a domain brief, adapter evaluation, benchmark corpus, validation strategy and owner before implementation.

## Phase 8 — Collaboration and scale
- Multi-user access control and collaboration.
- Remote workers and resource scheduler.
- Object storage and cache.
- Workflow automation, review and approvals.
- Observability, support diagnostics and release engineering.

Only start when product requirements justify it.

## Stop conditions

Pause a phase if:
- required domain assumptions are unknown;
- no accepted benchmark exists;
- units/frames cannot be reconciled;
- results cannot be reproduced or traced;
- execution is not isolated enough for its threat model;
- archive migration is lossy without explicit user consent;
- tests only pass by substituting mocked or simplified physics;
- product claims exceed evidence.

## Definition of done for the platform

Mono is ready to be called a multidisciplinary engineering platform only when it can preserve projects and revisions, safely orchestrate domain tools, retain reproducible results and evidence, and support multiple specialized workbenches without flattening their scientific meaning. Breadth of menu items is not the acceptance criterion.
