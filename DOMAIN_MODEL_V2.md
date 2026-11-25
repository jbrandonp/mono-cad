# MONO — Modèle de domaine cible et contrats fondamentaux
Version 2.0 — 25 novembre 2026
Statut : architecture cible proposée, non ratifiée

## 1. Principes
Le modèle canonique ne doit dépendre ni de build123d, ni de Gmsh, ni d'OpenFOAM, ni de CalculiX, ni d'OpenMM, ni de Three.js. Les bibliothèques sont des adaptateurs d'exécution. Le modèle persistant décrit le sens des objets et leurs relations; les moteurs produisent des artefacts versionnés.

## 2. Agrégats métier
### Workspace / Project
- Workspace, Project, ProjectMember, Role, Permission, ProjectSettings.
- Projet contient des révisions et des artefacts, pas des objets Python en mémoire.

### Product Definition / Systems Engineering
- Requirement, VerificationCase, Function, LogicalComponent, PhysicalComponent, Interface, Allocation, TradeStudy.
- RequirementTrace relie exigence → système → composant → analyse → preuve → acceptation.

### Design
- PartDefinition, AssemblyDefinition, AssemblyOccurrence, Feature, Sketch, Constraint, Parameter, CoordinateSystem, Datum, Tolerance, PMI.
- GeometryRepresentation décrit B-Rep, surface, facetté, implicite ou autre représentation.
- PersistentReference décrit une face/arête/vertex via stratégie de référence stable et historique de topologie, jamais seulement un index de face.

### Materials
- MaterialDefinition, MaterialRevision, MaterialModel, Property, PropertyCurve, TestDataset, EnvironmentCondition, ProcessCondition.
- Chaque propriété porte unité, température, orientation, méthode d'essai, source, lot, incertitude et domaine d'application.
- Séparer propriété nominale, courbe dépendante de température/vitesse, lot de production et valeur estimée.

### Simulation / CAE
- SimulationStudy, PhysicsModel, Domain, Mesh, BoundaryCondition, InitialCondition, LoadCase, Contact, SolverDefinition, SolverVersion, SolverExecution, ConvergenceRecord.
- ResultSet, Field, ScalarField, VectorField, TensorField, TimeSeries, ParticleSet, ModalSet, DerivedResult, UncertaintyResult.
- StudyDefinition immuable par version; exécution référence exactement la définition, les entrées, le solveur et l'environnement.

### Optimization
- OptimizationStudy, DesignVariable, Objective, Constraint, DesignCandidate, CandidateEvaluation, ParetoFront, SensitivityResult, RobustnessStudy.
- Les évaluations référencent les études et résultats, sans modifier les résultats d'origine.

### Manufacturing
- ManufacturingPlan, Operation, Resource, Machine, Tool, Fixture, Stock, Setup, Toolpath, PostProcessor, NCProgram, InspectionPlan, ManufacturingExecution.
- Distinction entre parcours théorique, simulation machine, post-traitement, vérification et programme libéré.

### PLM / Configuration
- ProductConfiguration, EBOM, MBOM, Revision, ChangeRequest, ChangeOrder, Release, Approval, SupplierPart, NonConformance, Effectivity.
- Les révisions publiées sont immuables; les changements produisent une nouvelle révision et conservent la filiation.

### Molecular & Materials Science
- MolecularSystem, MolecularStructure, Compound, Conformer, DockingRun, ScoringModel, MolecularDynamicsStudy, QuantumChemistryStudy, CrystalStructure, Microstructure, PropertyPrediction.
- Ne pas confondre analyse de structure macromoléculaire, simulation moléculaire, découverte de molécules et science des matériaux.

### Intelligence / Research
- Source, SourceSnapshot, Evidence, Claim, Extraction, Topic, Alert, ResearchBrief.
- Toute affirmation conserve URL/source, date de collecte, date de publication, version, extrait, confiance et historique de correction.

### Data Platform
- Dataset, DatasetVersion, Schema, DataContract, IngestionRun, Transformation, DataQualityReport, LineageEdge, CatalogEntry, RetentionPolicy.
- Les données volumineuses résident dans des stockages spécialisés; les métadonnées restent interrogeables dans le catalogue.

### Natural Systems
- ModelDomain, SpatialReference, Grid, Mesh, BoundaryForcing, Scenario, Observation, Calibration, ValidationDataset, SimulationRun.
- Les domaines (climat, hydrologie, géosciences, écologie) ont des modèles physiques et des jeux de validation propres.

## 3. Relations canoniques
- Project 1—N Revision
- Revision 1—N Artifact
- PartDefinition 1—N GeometryRepresentation
- AssemblyDefinition 1—N AssemblyOccurrence
- PartDefinition N—N MaterialRevision via MaterialAssignment
- SimulationStudy N—N Artifact via StudyInput
- SimulationStudy 1—N SolverExecution
- SolverExecution 1—N ResultSet
- ResultSet 1—N Field
- OptimizationStudy 1—N DesignCandidate
- DesignCandidate N—N SimulationStudy via CandidateEvaluation
- ProductConfiguration 1—N EBOM/MBOM
- ChangeOrder 1—N Revision/Release
- Requirement N—N Design/Study/Result via TraceLink
- ManufacturingPlan N—N Part/Assembly/Revision
- InspectionResult N—N Requirement/ManufacturingExecution

## 4. Identité, version et immutabilité
Chaque objet persistant possède :
- UUID global, type de domaine, numéro de révision, état de cycle de vie ;
- created_at, updated_at, created_by, source/provenance ;
- schema_version et migration history ;
- content_hash pour les artefacts immuables ;
- unités et référentiel spatial quand applicable ;
- liens de traçabilité.

Les IDs ne doivent jamais être dérivés d'un nom de fichier ou d'un indice topologique. Une révision publiée ne se modifie pas; on crée une nouvelle révision.

## 5. Système d'unités et coordonnées
- Canonique interne : SI cohérent, unités dimensionnelles typées, pas de floats sans dimension explicite.
- Représentation d'affichage et d'import/export peut utiliser mm, cm, Å, MPa, GPa, rpm, etc.
- Chaque champ porte sa dimension, unité, référentiel, convention de signe et système de coordonnées.
- Les conversions sont centralisées et testées; aucun facteur d'échelle caché au niveau d'un provider.
- Les grandeurs de champ vectorielles et tensorielles déclarent leur base, composantes, ordre, orientation et localisation (nœud, élément, intégration, cellule).

## 6. Contrats d'artefacts
Un artefact contient au minimum :
- artifact_id, artifact_type, schema_version, media_type ;
- content_hash, byte_size, storage_uri ;
- source_object_id et source_revision ;
- units, coordinate_frame, topology/mesh metadata ;
- producer_id/version, creation timestamp ;
- provenance et signature si requise ;
- classification, retention, access policy.

Les gros fichiers binaires ne sont pas placés dans les tables relationnelles; les tables contiennent les métadonnées et références vers stockage objet/fichier.

## 7. Interfaces par capacités
Remplacer GeometryProvider universel par interfaces indépendantes :
- GeometryKernel: opérations B-Rep, topologie, transformations.
- GeometryExchange: import/export STEP, IGES, STL, DXF, IFC.
- TessellationService: représentation d'affichage.
- MeshService: génération, qualité, adaptation, conversion.
- SolverAdapter: préparation, exécution, extraction, diagnostic.
- FieldDataAdapter: champs scalaires/vectoriels/tensoriels, temporels et volumiques.
- ManufacturingAdapter: opérations, parcours, simulation, post-processeur.
- MolecularAdapter: structures, préparation, simulation, scoring.
- LifecycleAdapter: configuration, révisions, changements et release.
- DataConnector: ingestion, catalogue, lineage.
- VisualizationAdapter: scène et rendu spécialisés.

Aucun contrat ne doit obliger un solveur à exposer un `resolve_selector` ou une tessellation.

## 8. Contrat de résultat de calcul
Tout résultat possède un statut strict :
- DRAFT_INPUT
- PREPARED
- QUEUED
- RUNNING
- FAILED
- CANCELLED
- SOLVER_FINISHED
- NUMERICALLY_CONVERGED
- VERIFIED
- VALIDATED
- QUALIFIED_FOR_CONTEXT

Ces statuts ne sont pas synonymes. Une étude peut finir sans converger; une convergence ne prouve pas la validation; une validation n'autorise que le contexte d'usage défini.

Chaque résultat doit exposer les hypothèses, limites, erreurs, résidus, conservation, incertitudes, logs, solveur, versions, paramètres, maillage, conditions et provenance.

## 9. Migration du modèle actuel
- V1 EntityRecord devient un LegacyGeometryEntity.
- Les providers existants sont encapsulés derrière des adaptateurs.
- La géométrie source et les études ne sont plus fusionnées dans un seul provider.
- Les IDs historiques sont conservés comme external_id.
- Les données perdues lors des archives V1 sont signalées; aucune reconstruction silencieuse n'est permise.
- Les projets V1 sont importés en mode compatibilité et ne deviennent pas automatiquement des révisions PLM publiées.
