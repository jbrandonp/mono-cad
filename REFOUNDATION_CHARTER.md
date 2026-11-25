# Mono — Refoundation Charter

**Date:** 2026-11-25  
**Baseline:** `master` at `4a2d76fea78450cb90cc497343f65bd5a9d3a1d3`  
**Status:** Proposed target and governance baseline; not implemented or validated.

## 1. Product thesis

Mono is not fundamentally a CAD application with extra simulation buttons. The intended product is a **multidisciplinary engineering and scientific workbench**: one environment in which teams can define systems, create domain-native models, execute analyses, compare alternatives, plan manufacturing or deployment, and preserve the evidence behind decisions.

The platform should unify the *workflow, identity, provenance, data exchange, orchestration, visualization and lifecycle* of engineering work. It must not pretend that all disciplines share one geometry model, one solver, one physical ontology, or one definition of correctness.

The ambition spans several families:

1. **Product and system engineering:** requirements, architecture, interfaces, traceability, trade studies.
2. **Geometry and design:** 2D sketching, parametric B-Rep, mesh and field-based design, assemblies, drawings, BIM.
3. **Physical simulation:** structural mechanics, fatigue, thermal, fluids/CFD, electromagnetics, acoustics, multibody dynamics and coupled multiphysics.
4. **Optimization and uncertainty:** parameter studies, sensitivity, design-space exploration, robust and topology optimization, uncertainty quantification.
5. **Materials and molecular science:** material models, microstructure, molecular structures, molecular dynamics, candidate screening and discovery workflows.
6. **Manufacturing and operations:** process planning, CAM, additive manufacturing, machine simulation, robotics, inspection and digital thread.
7. **PLM and evidence:** revisions, baselines, approvals, change impact, configuration, compliance evidence and release packages.
8. **Data, intelligence and natural systems:** scientific datasets, geospatial/environmental models, time-series, agent-assisted research and model orchestration.

These are **long-term product domains**, not a claim that the present repository already implements them.

## 2. The central architectural decision

**Unify the engineering lifecycle; specialize the domain models and numerical engines.**

The shared platform owns:
- identity, tenancy/workspaces, projects and permissions;
- stable object IDs, revisions, references and dependency graphs;
- typed quantities, units, coordinate frames and conversion policies;
- immutable source artefacts and derived artefacts;
- analysis definitions, compute jobs, execution records and result provenance;
- workflow, review, approval, audit and release;
- adapters, capability discovery, API contracts and visualization transport.

Each domain owns:
- its own canonical data model and invariants;
- its own mathematical and physical assumptions;
- its own solver or external-tool adapters;
- domain-specific validation and verification;
- domain-specific serialization and visualization semantics.

A CAD solid, a finite-element mesh, a CFD field, a robot trajectory, a molecular structure, an IFC building model and a geospatial raster are related engineering artefacts. They are **not interchangeable instances of a universal GeometryProvider**.

## 3. Non-negotiable principles

### P1 — No false capability claims
A generated input deck is not a simulation. A process exit code is not numerical convergence. Numerical convergence is not validation. A passing unit test is not scientific credibility. A prototype must be labelled as such.

### P2 — Domain truth before visual unity
The interface may present several disciplines in one workspace, but it must preserve native meaning, units, topology, coordinate systems, temporal semantics and uncertainty.

### P3 — Explicit provenance
Every result must be traceable to immutable input revisions, solver/tool versions, configuration, environment, execution logs, post-processing version and validation evidence.

### P4 — Fail closed on ambiguous engineering intent
No arbitrary boundary-condition fallback, silent solver substitution, silent model downgrade, invented physical property, or implicit unit conversion that changes meaning.

### P5 — Reproducibility by design
A calculation must be replayable or its non-reproducible factors explicitly recorded (random seed, hardware/backend, non-deterministic kernels, remote service version).

### P6 — Human authority for consequential actions
AI can interpret, propose, explain, generate drafts and orchestrate approved tools. It cannot silently release a design, certify a simulation, authorize manufacturing, or override engineering constraints.

### P7 — Security is an execution boundary, not a syntax filter
AST inspection may be useful for linting or policy checks. It is not a sandbox. Untrusted code and solver inputs must execute in constrained worker environments.

### P8 — Durable state
Projects, revisions, job states, events, audit records and artefacts must survive process restart. In-memory state is a cache or working session, not the system of record.

### P9 — Interoperability without semantic flattening
Import/export adapters preserve source metadata and report unsupported/lossy conversions. STEP, IFC, mesh, G-code, PDB/mmCIF and scientific formats remain first-class domain artefacts.

### P10 — Modular monolith first
Start with clear package boundaries and stable contracts in one deployable backend. Extract workers and services only where isolation, scaling, licensing or deployment requires it.

## 4. Explicit non-goals for the first foundation release

- Reimplementing OpenCASCADE, Gmsh, CalculiX, OpenFOAM, OpenMM or a full PLM system.
- A single universal scene graph as the source of truth for all disciplines.
- Premature microservices, distributed consensus or custom workflow DSL.
- Claiming aerospace, medical, nuclear, automotive or other regulated qualification.
- Building every solver before persistence, provenance, execution security and validation infrastructure exist.
- Treating a visual preview, AI critique or heuristic score as proof of physical correctness.

## 5. Delivery philosophy

The platform is built through vertical slices. Each slice must include:
1. user workflow and domain requirement;
2. canonical domain data and invariants;
3. import/export and migration story;
4. deterministic tests;
5. solver or tool verification evidence where applicable;
6. security and failure handling;
7. user-visible status and provenance;
8. measurable acceptance criteria.

No domain is declared complete because a provider class or API endpoint exists.

## 6. Governance of this refoundation

This document is the new proposed charter for the refoundation branch. It supersedes earlier architectural proposals only on this branch. It does not delete or rewrite prior history, does not modify `master`, and does not prove that the target architecture has been implemented.

All findings in the audit are classified as:
- **Observed:** directly visible in repository code/config/tests.
- **Inferred:** a consequence or risk derived from observed design.
- **Unknown:** requires execution, external solver inspection, benchmark or domain-expert review.
- **Target:** proposed future design, not an existing feature.
