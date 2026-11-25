# Mono — Séquence de reconstruction et portes de sortie

Une phase n'est fermée qu'avec code intégré, tests exécutés, build, documentation et preuve reproductible.

## État de départ de la branche foundation-reset-2026-11
Première tranche réalisée :
- Contrats de domaine initiaux, ports d'infrastructure et registre d'unités.
- Transitions de cycle de vie avec preuves requises aux portes convergence/vérification/validation/qualification.
- Filtrage ECIP fail-closed par défaut; mode in-process legacy uniquement sur opt-in explicite pour tests/environnements jetables.
- Mises à jour de paramètres atomiques : rollback intégral si reconstruction dépendante échoue.
- Validation et limites de ressources initiales du format .mono.
- CI Windows Python + build frontend Bun verrouillé.

Non clos :
- Les tests d'intégration CalculiX et l'archive démo sont ignorés lorsqu'ils sont absents; la qualification des moteurs reste à exécuter.
- PostgreSQL, migrations, outbox, queue durable et sandbox OS restent à implémenter.
- Aucun domaine avancé n'est qualifié industriellement.

## Gate 0 — Rebaseline et contrôle du risque
- Corriger les revendications non prouvées du README.
- CI Python, TypeScript et Tauri.
- SBOM, licences et inventaire des binaires.
- Isolation OS du code ECIP; AST uniquement en prévalidation.
- Durcissement .mono : traversal, liens, archive bombs, manifestes et restauration.
- Blocage FEA si BC absentes/ambiguës.
- Authentification locale, CSP, origine, quotas et limites IPC.

## Gate 1 — Domaine canonique
- Projets, systèmes, exigences, composants, assemblages, matériaux, études, résultats, preuves et PLM.
- Unités/dimensions, coordonnées, identifiants et références persistantes.
- Schémas versionnés, migrations V1→V2 et graphe d'artefacts.

## Gate 2 — Persistance
- PostgreSQL transactionnel, outbox, event log, audit et snapshots.
- Artefacts séparés, content-addressed, rétention, sauvegarde et restauration.
- Desktop local-first et prototype installateur Windows.

## Gate 3 — Exécution
- Queue durable, workers isolés, quotas, timeout, annulation, reprise, checkpoints.
- Registry solveurs, versions, hashes, licences, capacités et limitations.
- Contrats préparation/exécution/collecte/parsing.
- Multi-agents via DAG, locks, contrats, budgets et arbitrage.

## Gate 4 — CAO et ingénierie système
- Solveur d'esquisse complet, DOF, conflits.
- B-Rep, feature graph, assemblages, références persistantes, PMI/GD&T.
- STEP AP242, IFC, DXF et tests d'échange.
- Exigences, allocation et traçabilité.

## Gate 5 — CAE et matériaux
- FEA linéaire/non linéaire, contacts, thermique.
- CFD, électromagnétisme, multibody selon moteur retenu.
- Qualité mesh, convergence, conservation, VVUQ.
- Fatigue, fracture, fluage, corrosion, vieillissement et durée de vie uniquement avec modèles et données appropriés.

## Gate 6 — Optimisation
- Paramétrique, forme, topologie, DOE, sensibilité, Pareto, robustesse.
- Boucles solveur/optimiseur versionnées et contraintes DFM.

## Gate 7 — Fabrication et robotique
- DFM/DFA, process planning, ressources, temps et coûts.
- CAM 3/5 axes, simulation matière/collisions/gouges, post-processeurs.
- Additive, slicers et profils.
- Robot cells, cinématique, trajectoires, collisions, OLP.

## Gate 8 — PLM
- Configuration produit, EBOM/MBOM, révisions, effectivity.
- Change request/order, approbation, release, fournisseurs, non-conformités.
- Traçabilité exigence → conception → analyse → fabrication → inspection.

## Gate 9 — Sciences, données et systèmes naturels
- MD, docking, chimie quantique, science matériaux.
- Veille sourcée, data catalog, lineage, qualité, Big Data.
- Climat, hydrologie, géosciences et autres domaines avec calibration/validation.

## Gate 10 — Industrialisation
- Packaging, install, update, migrations et matrice OS.
- Performance, grandes scènes, calcul distant/HPC.
- SBOM, sécurité, support, observabilité et qualification par cas d'usage.

## Ordre produit recommandé
Ne pas construire tous les domaines en parallèle. Stabiliser Gate 0–3, puis livrer une verticale mécanique de bout en bout : exigences → CAO → matériaux → FEA → optimisation → fabrication → inspection → PLM. Les autres disciplines réutilisent les contrats éprouvés.
