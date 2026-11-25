# Mono Library Re-audit and E71 Rebaseline Implementation Plan

> **For agentic workers:** Execute natively in this session. Use focused changes, preserve all source archives, and verify each deliverable independently.

**Goal:** Reconcile every inspectable Mono E-series source (E01–E51 and E70), record the verified but currently unreadable E52–E69 bundles, correct the master Library structure, and align the repository CI/docs with evidence available on 26 November 2026.

**Architecture:** The Library remains an append-only documentary history. A single active V3.1 baseline and an E71 transverse audit become the navigation/decision authority; earlier E-series remain source records and are not silently overwritten. The code repository remains the authority for implementation status, and CI is the authority for the tested branch/commit.

**Tech Stack:** Markdown, semicolon/comma-delimited CSV as declared per file, JSON manifests, ZIP/SHA-256 verification, GitHub Actions, Python 3.12, uv, Bun/Vite.

**Spec:** E01–E70 Library source files, repository PR #1 / branch foundation-reset-2026-11, current user requirements in the Mono project conversation.

## Global Constraints

- Preserve every historical file and archive; do not permanently delete or overwrite historical source material.
- Never infer the content of E52–E69 from ZIP filenames.
- Keep explicit distinctions: proposed, implemented, tested, converged, verified, validated, qualified, released, operational.
- Do not declare industrial or scientific qualification from documentation or skipped CI tests.
- Keep local-first/offline-first, local project memory, both local/remote agents and extensions, and maximum self-evolution only under isolation, version control, test, permission and promotion gates.
- PostgreSQL remains a target recommendation pending an explicit ADR and desktop packaging evidence.
- No code execution sandbox claim unless OS-level isolation is implemented and tested.
- CSV delimiter and encoding must be explicit and validated; do not assume commas for all historical files.
- Every new package includes a manifest with file sizes and SHA-256 hashes; verify the ZIP and manifest before Library upload.
- Do not merge the draft PR without review and explicit authorization.

## Review Focus

- E23–E27 transactional consistency, idempotency, revocation, fencing, partial effects, restore and effect-unknown recovery.
- E28–E35 physical integration/Marlin evidence and the fact that E29 has no standalone gate CSV.
- E36–E51 assurance, safety, data governance, deployment, observability and recovery; validate the CSV schemas and count gates from their actual delimiters.
- E52–E69 are present as archive objects but content is unavailable; keep this as a release-blocking audit gap.
- E70 has 20 OPEN gates and must be tied to the implemented services, not treated as a production operations system.
- CI action versions, skipped external-engine tests, frontend Node-only module warnings and oversized output chunks.
- Legacy archive .mono sidecar/member coverage and provider-specific state restoration.

---

### Task 1: Establish the complete source inventory

**Files:** Library inventory, extracted E01–E51 bundles, E70 bundle, E52–E69 file metadata.

- [ ] Verify all 34 accessible E21–E51 / architecture ZIPs and the full documentary archive.
- [ ] Verify all 16 E36–E51 manifests, referenced file sizes and SHA-256 digests.
- [ ] Verify E70 ZIP integrity and all 12 listed artifacts against its manifest.
- [ ] Record the 18 E52–E69 ZIP objects as BUNDLE_PRESENT_CONTENT_UNREADABLE; do not fabricate their contents.
- [ ] Build a 70-row E-series register with per-number source availability, deliverables, gate status and disposition.

### Task 2: Reconcile gate states and document versions

**Outputs:** E71 gate register and series register.

- [ ] Parse each gate CSV using its actual delimiter and status column.
- [ ] Reconcile E22–E51 and E70 gate counts, retaining each gate ID and source.
- [ ] Record E29 as a pilot strategy without a standalone gate CSV; do not invent a gate count.
- [ ] Cross-check E23 V1.1, E25 corrective clauses, E26 adversarial findings and E27 composition/fencing contracts.
- [ ] Cross-check E31 V1.1 proposed through E32, then E33 differential protocol, E34 reference build and E35 physical bench.
- [ ] Cross-check E36–E51 and E70 against the actual repository and CI evidence.
- [ ] Record every contradictory path, stale index pointer, delimiter/schema anomaly and source gap.

### Task 3: Publish E71 transverse audit and V3.1 baseline

**Outputs:** a versioned E71 package plus active V3.1 index, architecture, capability matrix, decision register, document disposition, gate summary and reconstruction roadmap.

- [ ] Establish the authority hierarchy: repository code/tests, explicit user decisions, ratified ADRs, proposed E-series, adversarial reviews, package metadata.
- [ ] Add E70 to the active index and correct its actual Library path.
- [ ] Record E52–E69 as a release-blocking content gap.
- [ ] Recalculate the gate total; do not retain the previous incorrect 506 count.
- [ ] Update capability statuses to prototype/not demonstrated/unqualified where evidence is insufficient.
- [ ] Separate requirements for CAD, CAE, CFD, EM, multibody, materials/lifetime, topology optimization, CAM, robotics, PLM, molecules, data, intelligence and natural systems.
- [ ] Preserve V3.0 and V2.40 as history; make V3.1 the proposed current navigation layer only after integrity checks.

### Task 4: Reorganize the Library without loss

- [ ] Move the V3.0 draft folder into historical archives.
- [ ] Place the active V3.1 master index and supporting records under 00 - Index & Pilotage.
- [ ] Place E71 source audit, gate register and evidence under a single numbered audit folder.
- [ ] Keep E70's service-operations package in its own domain folder and ZIP in 99 - Archives & bundles.
- [ ] Check for duplicate names and redundant ZIPs; do not delete history.
- [ ] Verify the final folder tree and every uploaded file by listing it again.

### Task 5: Repository CI and status corrections

- [ ] Upgrade GitHub Actions to current Node 24-compatible, SHA-pinned releases.
- [ ] Include explicit skip reasons in the pytest log.
- [ ] Keep external solver tests skipped rather than substituting mocked physics outputs.
- [ ] Update README date, branch, exact CI run, test counts and remaining limitations.
- [ ] Record frontend warnings: fs/crypto externalization and ~6 MB main bundle; investigate before applying any untested workaround.
- [ ] Do not merge PR #1; keep it draft pending independent review and unresolved product architecture gates.

### Task 6: Recheck and close what is genuinely closable

- [ ] Re-run CI after workflow changes and record exact run/commit.
- [ ] Verify the Library package manifests, ZIP CRCs and uploaded paths.
- [ ] Re-open the published index, audit, architecture and registers to confirm their contents are readable.
- [ ] Re-read this plan and mark each task done, blocked or not yet implemented with evidence.
- [ ] Produce a final unresolved backlog ranked by safety/integrity blockers, core platform foundations, domain engines and production operations.

### Explicit work that cannot be completed by this documentary/code slice

- PostgreSQL schema, migrations, installer, backup/restore and transaction/outbox.
- Durable queues, scheduler, workers, checkpoints and multi-agent conflict arbitration.
- Real OS-level ECIP sandbox.
- Full archive preservation of provider-specific state and lossless sidecar mapping.
- Qualified FEA/CFD/EM/multibody/materials/topology/CAM/robotics/PLM/molecular/data/natural-science workflows.
- Physical Marlin hardware bench, firmware simulator and independent safety qualification.
- E52–E69 content review until authorized file bytes or readable source documents are available.

These are retained as work items, not represented as completed by E71.