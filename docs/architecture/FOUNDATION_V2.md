> **Note de statut — 26 novembre 2026 :** ce document est une architecture cible V2 historique, pas la source de vérité active. Le référentiel de consolidation actuel est MONO_V2_INDEX_MAITRE_v2_41.md et MONO_V2_ARCHITECTURE_BASELINE_v3_1.md dans la Library. Les choix ci-dessous restent soumis aux ADR et portes E71. PostgreSQL est une cible recommandée par E04 V1.1, non ratifiée et non implémentée. E23–E27 restent non gelés; les solveurs ne sont pas qualifiés.

---

# Mono — Architecture de référence V2 reconstruite depuis les invariants
Statut : architecture cible; seule la première tranche de contrats de domaine est codée.

## Mission
Mono relie ingénierie système, conception, simulation, optimisation, fabrication, PLM, science computationnelle et données. Le produit central est le fil numérique : exigence → système → définition produit → géométrie/matériaux → étude → exécution → résultats → preuves → décision → fabrication → inspection → révision/release.

## Architecture
Démarrer par un monolithe modulaire local-first avec processus de calcul isolés. Ne pas créer des microservices avant que les frontières de domaines et contrats soient stables.

Couches :
1. Experience : workspaces, viewport, inspecteurs, visualisation de résultats.
2. Application : commandes, workflows, jobs, permissions, orchestration.
3. Domain : systems engineering, CAD, materials, CAE, optimization, manufacturing, robotics, PLM, molecular science, intelligence, data, natural systems.
4. Infrastructure : PostgreSQL, artefacts, outbox, queue, workers, identité, observabilité, sauvegardes.
5. Adapters : OpenCASCADE/build123d, Gmsh, CalculiX, OpenFOAM, OpenMM, IfcOpenShell, Taichi, slicers et moteurs futurs.

## Modèle canonique
### Gouvernance et systèmes
Workspace, Project, ProjectMember, Permission, ProjectRevision, Requirement, VerificationCase, Function, LogicalComponent, PhysicalComponent, Interface, Allocation, TraceLink.

### Conception
PartDefinition, AssemblyDefinition, AssemblyOccurrence, Feature, Sketch, Constraint, Parameter, CoordinateFrame, Datum, Tolerance, PMI, PersistentTopologyReference.

### Matériaux et durabilité
MaterialDefinition, MaterialRevision, MaterialModel, Property, PropertyCurve, TestDataset, Batch, ProcessCondition, EnvironmentCondition, FatigueModel, FractureModel, CreepModel, CorrosionModel, DegradationModel, LifePrediction.

### CAE et résultats
SimulationStudy, PhysicsModel, Domain, Mesh, BoundaryCondition, InitialCondition, LoadCase, Contact, SolverDescriptor, SolverExecution, ConvergenceReport, VerificationReport, ValidationReport, UQReport, Artifact, ResultSet, ScalarField, VectorField, TensorField, VolumeField, ParticleSet, TimeSeries, ModalResult, DerivedResult.

### Optimisation
OptimizationStudy, DesignVariable, Objective, Constraint, Candidate, CandidateEvaluation, Sensitivity, ParetoFront, RobustnessStudy.

### Fabrication et robotique
ManufacturingPlan, Operation, Resource, Machine, Tool, Fixture, Stock, Setup, Toolpath, MachineSimulation, PostProcessor, NCProgram, RobotCell, KinematicChain, Trajectory, CollisionReport, InspectionPlan, InspectionResult.

### PLM
ProductConfiguration, EBOM, MBOM, Revision, Effectivity, SupplierPart, NonConformance, ChangeRequest, ChangeOrder, Approval, Release.

### Science et données
MolecularSystem, Compound, Conformer, DockingStudy, MolecularDynamicsStudy, QuantumChemistryStudy, CrystalStructure, Microstructure, MaterialPrediction, ExperimentalDataset, Source, SourceSnapshot, Evidence, Claim, ResearchBrief, Alert, Dataset, DatasetVersion, Schema, IngestionRun, DataQualityReport, LineageEdge, CatalogEntry.

### Systèmes naturels
ModelDomain, SpatialReference, Grid, BoundaryForcing, Scenario, Observation, Calibration, ValidationDataset, SimulationRun.

## Unités, coordonnées et artefacts
SI cohérent recommandé en interne; unité d'origine conservée à l'import; conversion explicite et testée. Champs vectoriels/tensoriels : repère, base, localisation, convention de signe et unité. Artefact : ID, type, schema version, media type, hash, taille, URI, source revision, unités, repère, producteur/version, provenance et rétention. Les gros binaires restent hors des tables relationnelles.

## Persistance
PostgreSQL est la cible proposée pour la source de vérité pour métadonnées, identités, révisions, relations, permissions, événements et jobs. Artefacts sur filesystem local content-addressed au départ, puis object storage S3-compatible pour distribution distante. Outbox transactionnelle pour les événements.

Le desktop doit rester local-first. Une instance PostgreSQL locale gérée par l'installateur est l'option cible pour l'ambition long terme; SQLite reste utile pour caches/préférences/tests. Un prototype d'installation, mise à niveau et sauvegarde Windows doit valider cette décision avant gel architectural.

## Exécution scientifique
Étude versionnée → validation des entrées/unités/matériaux/conditions → préparation → admission et réservation ressources → worker isolé → collecte logs/métriques/artefacts → parsing strict → convergence et bilans → vérification numérique → validation expérimentale → UQ → qualification dans un contexte d'usage.

États séparés : draft, inputs_validated, prepared, queued, running, solver_finished, numerically_converged, verified, validated, qualified, failed, cancelled, timed_out. Aucun statut ne se déduit du seul code retour du processus.

## Contrats spécialisés
Ports distincts : GeometryKernel, GeometryExchange, Tessellation, SketchSolver, AssemblySolver, MeshGenerator, MeshQualityEvaluator, StructuralSolver, ThermalSolver, FluidSolver, ElectromagneticSolver, MultibodySolver, OptimizationEngine, MolecularSimulation, Docking, QuantumChemistry, ManufacturingPlanner, CAM, Slicer, MachineSimulator, RobotPlanner, FieldReader, VisualizationAdapter, DataConnector.

Les seules opérations communes sont validation, préparation, exécution, annulation, collecte, diagnostics, provenance et statut. Un solveur n'est pas obligé de tesseller ou sélectionner des faces.

## Sécurité et plugins
Scripts et plugins s'exécutent dans des processus isolés, permissions minimales, répertoire temporaire, réseau désactivé par défaut, quotas CPU/RAM/durée, hash/version/licence vérifiés, logs et annulation. AST n'est qu'une prévalidation syntaxique.

Tauri/backend : port dynamique, token local de session, handshake, identité PID/processus, CSP restrictive, origine contrôlée, schémas API stricts, taille de messages et quotas.

## Agents et auto-évolution
Agents simultanés sur tâches distinctes d'un même projet via DAG, contrats d'entrée/sortie, budgets, locks et artefacts versionnés. Le coordinateur fusionne les propositions et arbitre les conflits; pas d'écriture concurrente non contrôlée dans le même état mutable.

Mémoire locale du projet : décision à préserver. Mémoire versionnée, sourcée, consultable et soumise aux permissions. Auto-évolution maximale signifie proposer, coder et tester en branches/sandboxes, jamais modifier silencieusement le noyau ou publier un résultat non validé.

Extensions hybrides locales/distantes, avec manifeste de capacités, permissions, version, licence, provenance et sandbox.

## Interface
Workspaces : Requirements/System, CAD 2D/3D, Assemblies, Materials, Simulation, Optimization, Manufacturing, Robotics, PLM, Molecular Science, Data/Research. Scène partagée mais visualiseurs spécialisés pour B-Rep, mesh, volume, champs, particules, molécules, IFC et trajectoires.

## Échanges
Prévoir des adaptateurs testés pour STEP AP242, IGES, DXF/DWG via connecteurs licenciés, IFC4.3, glTF, USD, STL/3MF, G-code/STEP-NC, formats maillage/solveur, mmCIF/PDB, CSV/Parquet/NetCDF/Zarr. Support déclaré par capacité et version, jamais par simple mention README.

## Qualité
Tests unitaires et property-based; cas analytiques; régressions moteur; convergence mesh/temps; comparaison expérimentale; UQ; benchmarks reproductibles; SBOM/licences; sécurité; migrations/restauration; CI sur OS réellement supportés.

## Gouvernance
NASA-STD-7009B / NASA-HDBK-7009B et ASME VVUQ pertinents sont des guides de crédibilité, pas une certification automatique de Mono.
