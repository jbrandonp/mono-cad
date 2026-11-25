# MONO — Refonte des fondations et feuille de route de migration
Version 2.0 — 25 novembre 2026
Branche : rebaseline/engineering-platform-2026-11
Statut : plan proposé, aucune phase non explicitement committée ne doit être considérée réalisée.

## Stratégie
Ne pas ajouter immédiatement les domaines manquants au monolithe existant. Stabiliser d'abord le langage commun de la plateforme, migrer les moteurs actuels comme adaptateurs, puis étendre par verticales. Garder un monolithe modulaire tant que les contrats et les limites de ressources ne sont pas prouvés; isoler les solveurs dans des processus workers distincts.

## Phase 0 — Contrôle de risque immédiat
- Geler toute revendication de certification, de fiabilité industrielle et de couverture de production.
- Sécuriser le bac à sable ECIP : l'AST ne doit plus être présenté comme une isolation. Exécuter dans un worker isolé avec limites OS et accès réseau/fichiers désactivés par défaut.
- Corriger restauration d'archives : identifiants, chemins, manifestes, limites de décompression et reconstruction sans perte.
- Ajouter validation stricte des messages WebSocket, authentification locale, quotas, limites de taille et autorisations.
- Supprimer les substitutions automatiques de faces pour FEA.
- Vérifier le démarrage Tauri, identité du backend, port dynamique et CSP.
- Maintenir les correctifs CFD et slicer en place, mais ajouter preuves d'exécution et tests réels.

## Phase 1 — Contrats et modèle de domaine
- Introduire Workspace/Project/Revision/Artifact.
- Séparer PartDefinition, AssemblyOccurrence, GeometryRepresentation et Mesh.
- Introduire MaterialRevision, StudyDefinition, SolverExecution, ResultSet, Evidence.
- Définir les contrats de unités/dimensions et coordonnées.
- Remplacer GeometryProvider universel par interfaces spécialisées.
- Définir les identifiants stables et la gestion des références topologiques.
- Définir schémas JSON/MsgPack versionnés et format de trames binaires versionné.
- Ajouter migration V1→V2 et fixtures de données.

## Phase 2 — Persistance, jobs et événements
- Base relationnelle pour métadonnées, transactions, révisions et relations.
- Stockage artefacts séparé pour STEP, IFC, maillages, champs, trajectoires, datasets et résultats.
- Gestion durable des jobs, files, workers, quotas, annulation, reprises, checkpoints et observabilité.
- Provenance et reproductibilité des calculs.
- Outbox/event log transactionnel; WebSocket devient canal de notification et streaming, pas source de vérité.

## Phase 3 — Sécurité et distribution
- Workers isolés par domaine/solveur, profils de permission, sandbox OS.
- Authentification locale à jeton, origines, CSP, permissions Tauri minimales.
- Vérification des binaires externes, hash, signature, versions et licences.
- SBOM, lockfiles, builds reproductibles, matrices Windows/Linux/macOS.
- Tests adversariaux d'archives, scripts, plugins, WebSocket et API.

## Phase 4 — Verticales d'ingénierie
A. CAD : esquisses complètes, topologie persistante, assemblages, PMI/GD&T.
B. CAE : contrat solveur, matériaux, maillages, FEA linéaire/non linéaire, thermique.
C. Durabilité : fatigue, fracture, fluage, corrosion, vieillissement, UQ.
D. Optimisation : paramétrique, forme, topologie, DOE, Pareto, robustesse.
E. CFD : maillage et validation, turbulence, thermique, couplages.
F. Electromagnétisme et multibody.
G. Manufacturing : process planning, CAM vérifié, simulation machine, additive.
H. Robotics : modèles, cinématique, planification, simulation et post-processeurs.
I. PLM : exigences, EBOM/MBOM, configuration, ECO/ECN, approbations, release.
J. Molecular/materials science.
K. Intelligence, veille, Big Data et systèmes naturels.

## Phase 5 — Interface et expérience
- Espaces de travail par tâche : conception, simulation, résultats, fabrication, PLM, recherche.
- Visualiseurs spécialisés : B-Rep, mesh, champ scalaire/vectoriel/tensoriel, volume, particules, molécules, séries temporelles.
- Inspecteur d'étude et de provenance.
- Comparaison de révisions et de résultats.
- Tableau de jobs, ressources, logs, diagnostics et statuts.
- Avertissements de limites et incertitudes visibles.

## Phase 6 — Qualification et montée en échelle
- Tests de référence par domaine, jeux analytiques et expérimentaux.
- Tests de convergence et indépendance au maillage.
- Benchmarks reproductibles.
- Qualification par cas d'usage et niveau de risque.
- Calcul distribué/HPC uniquement après stabilisation des contrats de jobs et d'artefacts.

## Conditions de sortie par phase
Une phase n'est considérée terminée qu'avec : code intégré, tests automatisés exécutés, build, preuve d'exécution, documentation, migrations, sécurité, limites connues et revue technique. Une spécification seule ne ferme pas la phase.

## Ce qu'il faut éviter
- Microservices prématurés.
- Une interface universelle qui force tous les solveurs dans le même contrat.
- Unité implicite ou conversion magique.
- Résultat IA présenté comme calcul physique.
- Résultat de solveur sans provenance et statut.
- G-code ou trajectoire robot sans simulation et validation machine.
- Fallback silencieux vers un modèle de physique différent.
- Recréation complète d'un modèle en perdant la filiation PLM.
- Dépendances massives toutes obligatoires pour l'installation de base.
