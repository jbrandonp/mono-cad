# Mono — Evidence-based Baseline Audit

**Audit date:** 2026-11-25  
**Audited baseline:** `master`, commit `4a2d76fea78450cb90cc497343f65bd5a9d3a1d3`  
**Method:** static inspection of repository tree, README, Python core/providers, execution/security code, Tauri shell, frontend package and selected tests.  
**Not performed:** no local checkout, dependency installation, test run, build, solver run, performance benchmark, fuzzing, penetration test or scientific benchmark in this audit.

## 1. Executive assessment

The repository is a promising multi-engine prototype with useful integrations and demonstrations. It is not yet a reliable unified engineering platform. The main gap is not the number of missing modules. It is the absence of a robust platform kernel that can preserve domain-specific meaning, state, provenance, execution safety and evidence across those modules.

The README materially overstates maturity. It describes industrial or complete capabilities, asserts test and coverage figures, claims an AST sandbox and strong physical guarantees, and describes a unified scene as source of truth. Those claims are not established by the code inspected here.

**Recommended disposition:** preserve existing domain prototypes as experiments/adapters, but do not extend the current shared abstractions blindly. Establish a new platform core and migrate one validated vertical slice at a time.

## 2. Repository shape observed

The repository includes:
- Python package `src/mono` with core document/command/archive/provider abstractions;
- providers for B-Rep, FEA, CFD, CAM, BIM/IFC, textile XPBD, biomolecular structures/OpenMM and technical drawings;
- ECIP script execution and a multi-agent CAD orchestrator;
- FastAPI/WebSocket backend;
- TypeScript/Three.js frontend and Mol* viewer;
- Tauri/Rust desktop shell;
- tests for archive, CAD, FEA, CFD, CAM, BIM, biomolecules, textiles, API and orchestration;
- `pyproject.toml`, `uv.lock`, frontend package and Cargo lock/configuration.

This is evidence of breadth of experimentation, not proof of production completeness.

## 3. Critical structural findings

### A-01 — The central document conflates unrelated responsibilities
**Observed:** `MonoDocument` stores units, parameters, entities and in-memory command history. `EntityRecord` combines provider instance, scale domain, position, rotation, script, parameter bindings and dirty flag. `run_fea_simulation` creates an FEA provider and replaces the entity's geometry provider with it.

**Risk:** geometry identity is overwritten by analysis state; one object cannot safely retain multiple studies/results; domain state and UI scene state are coupled; persistence and collaboration become difficult.

**Required change:** separate project/product/design/geometry/assembly/material/analysis/job/result/evidence objects. Geometry references must be immutable revisioned inputs to studies, not replaced by study providers.

### A-02 — The universal GeometryProvider is semantically incorrect
**Observed:** the abstract provider requires tessellation, geometry introspection, topological selectors and native serialization. It is implemented by domains including BIM, CAM, molecular and textile providers.

**Risk:** providers fabricate irrelevant fields or return placeholder geometry/validity values. The common interface forces domain-specific concepts into a false shared abstraction.

**Required change:** split capabilities into interfaces: GeometryKernel, GeometryExchange, Tessellation, FieldData, MolecularStructure, Toolpath, BIMModel, Mesh, SimulationResult and Visualization. A provider advertises capabilities; it does not implement meaningless methods.

### A-03 — Binary mesh payload has no robust wire contract
**Observed:** payload is concatenated arrays (vertices, normals, indices, face IDs, optional scalar field) with implicit order and counts. Deserialization relies on external counts and permits trailing bytes as a scalar field.

**Risk:** incompatible clients, malformed payload ambiguity, missing units/frame/version, no bounds/offset/checksum, no tensor/vector/time-dependent fields, no safe schema evolution.

**Required change:** versioned envelope with explicit dtype, endianness, dimensions, offsets, units, coordinate frame, topology, field descriptors, checksums and strict validation. Consider glTF for visual geometry and VTK/VTU or XDMF/HDF5-style formats for scientific fields, with domain adapters.

### A-04 — ECIP AST validator is not a security sandbox
**Observed:** AST checks imports, direct calls to selected names and dunder attributes. Executor then compiles and executes Python using `exec` in the same interpreter with a custom builtins dictionary. build123d and NumPy objects are exposed.

**Risk:** Python object graphs and library internals are not contained by this denylist. CPU/memory exhaustion and interpreter escape risks remain. A script can destabilize or compromise the backend process.

**Required change:** treat generated scripts as untrusted. Prefer a constrained declarative DSL for common CAD operations. If Python is retained, run it in an OS-isolated worker/container/VM with no ambient credentials, restricted filesystem/network, resource limits, timeout, process kill, read-only runtime and narrow RPC. AST validation is only preflight policy.

### A-05 — FEA boundary selection is unsafe
**Observed:** when fixed/load face selection returns empty, `_select_boundary_surfaces` falls back to first/last surfaces. Physical groups are then assembled from these tags.

**Risk:** solver can run on unintended supports/loads and return plausible but meaningless results. Geometric extrema are also not robust semantic references after topology changes.

**Required change:** explicit boundary sets with stable semantic/geometric selection criteria, ambiguity reports, user confirmation for unresolved selections, minimum cardinality and disjointness checks. Empty or ambiguous sets must block the solve.

### A-06 — FEA result credibility is not established
**Observed:** one linear elastic material model and static deck generation are visible; parser uses regular-expression extraction of FRD records; defaults and units are encoded in provider logic. Tests assert a bar benchmark and structural safety.

**Risks/investigations:** validate parser against real CalculiX output variants; verify units and nodal/element stress semantics; check force distribution, constraints, mesh convergence, singularities, stress averaging, material validity, solver warnings, element quality and reaction-force balance. A yield-based static factor is not fatigue life, fracture, buckling or durability.

**Required change:** analysis specification schema, mesh/BC validation, solver run record, parser qualification, numerical checks, benchmark suite and explicit VVUQ evidence.

### A-07 — CFD setup is heuristic and incomplete
**Observed:** generated domain is based on a bounding box multiplier; the case hardcodes mesh settings, a fixed `locationInMesh`, boundary names and turbulence assumptions. The master implementation analytically estimates drag/lift and initializes `converged=True`; if `simpleFoam` exists it runs `blockMesh` and `simpleFoam`, without the full mesh/quality/post-processing chain.

**Risk:** analytic estimates can be mistaken for solver results; mesh/patch setup may not represent the geometry; convergence is not established by process completion.

**Required change:** separate estimate, prepared case, solver execution, numerical convergence, physical verification and validated result. Build a full reproducible pipeline with mesh checks, residuals, conservation, force history, grid/time-step independence and documented model assumptions.

### A-08 — Molecular dynamics fallback changes the physical model
**Observed:** OpenMM first attempts Amber all-atom setup, catches any exception and silently switches to an elastic network model. The ENM constructs harmonic pair interactions by distance cutoff. Result reports method but setup errors are not retained.

**Risk:** chemically distinct models may be interpreted as equivalent; broad exception hides data/force-field failures. The ENM is not an Amber replacement and does not support chemical discovery claims.

**Required change:** explicit model selection, explicit fallback opt-in, retained error diagnostics, model/parameter provenance, validation against appropriate references and separate pipelines for docking, scoring, free-energy and experimental evidence.

### A-09 — Archive format is not a complete project persistence model
**Observed:** tar+Zstandard archive with manifest and SHA-256; unpack reconstructs some providers with empty/default constructors (e.g. technical drawing, CAM, slicer, textile, FEA), while only some source artefacts are restored. Manifest validation is permissive. Temporary STEP path includes entity ID. Tar members are read without a complete strict policy for path traversal, duplicate entries, count, compressed/decompressed size or compression ratio.

**Risk:** lossy project restoration, malformed archive attacks, path manipulation, silent default state and incompatible schema evolution. SHA-256 detects accidental/tampered content relative to manifest but is not authenticity/signature.

**Required change:** schema-versioned project package, strict JSON schema, content-addressed immutable artefacts, safe member allowlist, path normalization, quotas, duplicate rejection, streaming limits, migration functions and signed manifests when trust/authenticity is needed.

### A-10 — Compute workers are subprocess wrappers, not a job platform
**Observed:** worker helpers invoke CalculiX/OrcaSlicer with `subprocess.run`, timeouts and working directories. No durable queue, job registry, resource scheduler, cancellation protocol, checkpointing, retry policy, artifact staging or sandbox contract is visible in these helpers.

**Risk:** long-running jobs are hard to recover, monitor, cancel, parallelize or reproduce; concurrent calls may collide or exhaust resources.

**Required change:** persistent job state machine, unique workspace, quotas, scheduler, worker isolation, structured logs, output capture, cancellation and retry semantics.

### A-11 — Tauri/backend trust boundary is weak
**Observed:** Tauri CSP is null. Desktop shell probes fixed `127.0.0.1:8000`, treats any listener as the backend, and launches candidates/fallbacks based on paths/PATH. Status exposes fixed URL and port.

**Risk:** port squatting, wrong-process trust, cross-origin access and excessive webview capabilities. Startup path and process ownership are not strongly verified.

**Required change:** dynamic port, random per-session secret, backend handshake bound to spawned process, strict CORS/Host validation, restrictive CSP, least-privilege Tauri capabilities, controlled executable resolution and lifecycle supervision.

### A-12 — AI orchestration is a mock prototype
**Observed:** MAC orchestrator accepts an injected `LLMProvider`; repository includes `MockLLMProvider` with heuristic dimension extraction and deterministic code generation. Visual critique may be simulated.

**Risk:** demo success may be misread as real model integration or engineering validation.

**Required change:** provider registry for real model APIs, tool permissions, prompt/input provenance, schema-constrained outputs, cost/time budgets, human approval gates and explicit separation between AI critique and deterministic/physical validation.

### A-13 — Test labels and evidence are not trustworthy as presented
**Observed:** README badges claim 106 passed and 83% coverage. The audit did not execute tests. Some tests invoke external tools (Gmsh, CalculiX, OrcaSlicer/OpenFOAM) and assert particular physical results.

**Risk:** stale badges, environment-dependent tests, mocks mixed with integration tests and unqualified analytical benchmarks.

**Required change:** CI matrix, test tiers (unit/contract/integration/benchmark/qualification), captured tool versions, deterministic fixtures, explicit skip reasons and generated evidence reports. Only publish numbers from current CI runs.

### A-14 — Dependencies and licensing need formal governance
**Observed:** Python, Rust and frontend dependencies are locked, but the project declares a mixture of scientific engines and integrations. README mentions anti-GPL isolation and permissive licensing.

**Unknown:** actual transitive license inventory, binary redistribution rights, runtime linkage, platform-specific packaging and legal effect of subprocess boundaries.

**Required change:** SBOM, SPDX license inventory, notices, binary provenance, dependency vulnerability monitoring and legal review. Do not assume subprocess isolation automatically resolves every licensing obligation.

### A-15 — Frontend is a prototype, not a scalable domain workbench
**Observed:** large imperative TypeScript entry point, Three.js viewport and separate Mol* viewer. Shared mesh/scalar field model is limited.

**Risk:** UI complexity grows faster than domain capabilities; scientific data visualization and CAD interaction require different interaction models.

**Required change:** feature/module boundaries, typed API clients, command/query separation, explicit viewport adapters and dedicated renderers for B-Rep, meshes, fields, molecules, BIM, toolpaths, time series and geospatial data.

## 4. Domain maturity matrix

The following is a static code-level assessment, not an execution or scientific validation.

| Domain | Evidence in repo | Current assessment | Missing proof |
|---|---|---|---|
| Parametric B-Rep CAD | build123d/OCP provider and ECIP | Prototype | robust feature history, constraints, topology stability, broad import/export, regression corpus |
| 2D sketch | custom solver and tests | Prototype | complete constraint semantics, DOF/rank validation, solver comparison |
| Drawings | SVG/DXF/PDF provider | Partial prototype | standards coverage, GD&T semantics, dimension correctness, drawing update consistency |
| FEA | Gmsh + CalculiX provider | Prototype | BC safety, parser qualification, convergence, verification, validation, uncertainty |
| CFD | OpenFOAM case generation | Early prototype | full mesh chain, BC completeness, convergence, conservation, grid independence |
| BIM | IfcOpenShell provider | Partial prototype | IFC schema coverage, coordination, exchange validation, model checking |
| CAM | toolpath/G-code provider | Prototype | machine kinematics, stock simulation, collision/gouge verification, postprocessor qualification |
| Additive | OrcaSlicer integration | Integration prototype | machine/material profiles, process qualification, build validation |
| Textile | XPBD/Taichi | Experimental prototype | constitutive calibration, validation, material tests |
| Molecular structures | Gemmi parsing/visualization | Prototype | chemistry completeness, topology, provenance, biological validation |
| Molecular dynamics | OpenMM Amber/ENM | Experimental prototype | explicit model handling, parameterization, stability/validation |
| Robotics | no complete engine observed | Not established | kinematics, dynamics, planning, collision, controller and hardware validation |
| Materials discovery | no complete discovery pipeline observed | Not established | chemistry, candidate generation, scoring, uncertainty, lab feedback |
| PLM | no lifecycle system observed | Not established | effectivity, change, configuration, approval, traceability |
| Big Data/natural systems | no platform-level pipeline observed | Not established | ingestion, storage, geospatial/time-series models, compute and validation |

## 5. README claims requiring correction

Claims such as “100% native”, “zero Docker”, “sandbox AST”, “zero-copy”, “transactional <5 ms”, “60 FPS”, “95% industrial components”, “106 tests passed”, “83% coverage”, “industrial” or “first platform” should be treated as unverified until backed by reproducible tests, benchmarks, deployment evidence or independent review.

The existence of a badge or test file is not evidence that a claim is currently true.

## 6. Recommended response

Do not attempt to fix every provider before the platform kernel. The correct sequence is:

1. Establish requirements and architecture decision records.
2. Implement typed domain identities, revisions, quantities, frames and artefacts.
3. Add durable persistence, events and migrations.
4. Create a secure, observable job platform.
5. Replace universal provider assumptions with capability contracts.
6. Migrate one narrow CAD vertical slice and one simulation vertical slice.
7. Build evidence/validation framework before adding more solver domains.
8. Expand disciplines through explicit adapters and acceptance gates.

## 7. Audit limitations

This audit is static and repository-scoped. It cannot determine whether binaries work, whether tests pass, whether solvers are installed, whether numerical outputs are correct, whether the desktop app launches, whether packaging is safe, or whether licenses permit intended distribution. Those require runtime, scientific, security and legal work.
