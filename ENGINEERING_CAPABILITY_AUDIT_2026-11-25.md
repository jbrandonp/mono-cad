# Mono CAD Studio — audit des capacités d'ingénierie
Date de l'audit : 25 novembre 2026
Dépôt examiné : jbrandonp/mono-cad
Branche de travail : rebaseline/engineering-platform-2026-11
Référence examinée : master, commit 4a2d76fea78450cb90cc497343f65bd5a9d3a1d3

## Méthode et limites
Audit statique du README, de l'arborescence Git, du manifeste Python, des fournisseurs métier, du serveur API, de l'interface et des fichiers de tests. Le dépôt contient 114 entrées dans l'arbre récursif, 10 fournisseurs métier Python et 22 fichiers de tests. L'inventaire statique relève 104 fonctions de test nommées `test_*`; le README annonce 106 tests. Cette différence doit être réconciliée. Aucun clone local n'a pu être établi dans l'environnement de travail et aucune suite de tests, compilation, simulation ou validation de solveur n'a été exécutée pendant cet audit. Les conclusions sont donc des constats de code statique, pas une certification d'exécution.

## Matrice de capacités demandées

| Capacité | État constaté | Preuves statiques / limites |
|---|---|---|
| Conception et fabrication de produits complexes | PARTIEL | Plusieurs moteurs spécialisés existent, mais pas de chaîne numérique complète et validée du besoin au produit fabriqué. |
| CAO 3D | PRÉSENT — BASE | `brep_build123d.py`, build123d/OCP, scène et tessellation. Robustesse, assemblages avancés et couverture d'opérations à qualifier. |
| CAD 3D | PRÉSENT — BASE | Même noyau B-Rep et export STEP. CAD/CAO sont ici deux appellations de la même famille, pas deux moteurs indépendants. |
| CAO 2D | PARTIEL | `planegcs_solver.py`, TechDraw, DXF/SVG/PDF. L'énumération des contraintes est plus large que les résidus réellement calculés : POINT_ON_LINE, POINT_ON_CIRCLE, ANGLE et EQUAL_LENGTH ne sont pas traités dans le calcul de résidus examiné. |
| Visualisation et expérience 3D | PARTIEL | Three.js, viewport, Mol*. La couverture UX, sélection avancée, assemblages, rendu, annotations, grands modèles et tests d'ergonomie restent à établir. |
| Solveurs numériques | PARTIEL | FEA statique élastique via Gmsh/CalculiX, préparation CFD OpenFOAM, XPBD textile, OpenMM. Pas de plateforme multiphysique générique ni de validation croisée. |
| Prévision détaillée de durée de vie, matériaux et durabilité des pièces | ABSENT | Le matériau FEA contient notamment module d'Young, Poisson, limite d'élasticité et densité. Aucun workflow fatigue, courbe S-N, dommage cumulatif, fracture, fluage, corrosion, vieillissement ou durée de vie n'a été trouvé. |
| Optimisation topologique et optimisation de forme | ABSENT | Aucun fournisseur, solveur, endpoint ou test identifié. |
| CFD | PARTIEL / NON FIABLE EN L'ÉTAT | `cfd_openfoam.py` génère des fichiers de cas et tente blockMesh/simpleFoam, mais le rapport calcule des coefficients empiriques et initialise `converged=True` même si simpleFoam est absent. Il ne démontre donc pas la convergence du solveur ni l'extraction de résultats CFD. Aucun endpoint CFD dédié n'a été identifié dans `server.py`. |
| Simulation électromagnétique | ABSENT | Aucun moteur Maxwell, FEM électromagnétique, FDTD/FEM/BEM ou fournisseur dédié trouvé. |
| Dynamique multicorps | ABSENT | Aucun moteur de corps rigides articulés, cinématique de mécanismes, contact dynamique ou solveur multibody identifié. |
| Automatisation et optimisation des processus de conception | PARTIEL | ECIP et orchestrateur MAC existent, mais le serveur instancie `MockLLMProvider`. Aucun pipeline d'optimisation de conception robuste (variables, objectifs, contraintes, DOE, Pareto, convergence, reproductibilité) n'a été identifié. |
| Planification des processus de fabrication | PARTIEL / FAIBLE | Le module CAM génère des trajectoires nominales, mais plusieurs stratégies utilisent la boîte englobante et des parcours géométriques simplifiés. Pas de gamme opératoire complète, ordonnancement, choix outillage/bridage, post-processeur qualifié ni simulation machine validée. |
| Simulation des opérations de fabrication | ABSENT / PARTIEL | Audit de trajectoire et génération de G-code présents. Pas de simulation cinématique machine complète, jumeau machine, matière enlevée, collisions multi-axes ou validation contrôleur démontrée. |
| Programmation robotique | ABSENT | Aucun modèle robot, cinématique directe/inverse, planification de mouvement, post-processeur robot ou simulation de cellule trouvé. |
| PLM | ABSENT | Aucun modèle de configuration produit, EBOM/MBOM, révisions, ECO/ECN, gestion des exigences, changements, signatures ou traçabilité de cycle de vie identifié. |
| Découverte de molécules | ABSENT | Ingestion et analyse de structures macromoléculaires et OpenMM existent. Pas de découverte de candidats, génération, docking, scoring, chimie quantique ou boucle de conception moléculaire identifiée. |
| Science des matériaux | PARTIEL TRÈS LIMITÉ | Quelques propriétés mécaniques isotropes FEA et simulation moléculaire de protéines. Pas de base matériaux qualifiée, modèles constitutifs avancés, DFT, microstructure, thermodynamique ou prédiction de propriétés. |
| Intelligence informationnelle et veille | ABSENT | Aucun pipeline de sources, collecte, déduplication, extraction, provenance, alertes ou analyse de veille identifié. |
| Big Data | ABSENT | Pas de stockage analytique distribué, ingestion, orchestration de données volumineuses, catalogue, gouvernance ou moteur de traitement identifié. |
| Modélisation et simulation du monde naturel | ABSENT | Le textile XPBD et la biomolécule sont des domaines physiques ciblés, pas une plateforme de modélisation de systèmes naturels (climat, hydrologie, géosciences, écosystèmes, etc.). |

## Constatations techniques importantes

1. **CFD — faux état de convergence.** Dans `CfdOpenFoamProvider.simulate`, `converged` est initialisé à `True`. Si `simpleFoam` n'est pas trouvé, aucune résolution n'est exécutée et le rapport peut néanmoins annoncer la convergence. Les forces et coefficients proviennent d'estimations empiriques, pas de champs calculés extraits d'OpenFOAM. Ce comportement doit être corrigé avant tout usage d'ingénierie.
2. **CFD — chaîne incomplète.** Le code prépare blockMesh et snappyHexMesh dictionaries, mais la séquence de résolution observée ne lance que blockMesh et simpleFoam; pas de génération snappyHexMesh ni de checkMesh avant le solveur, ni de preuve de convergence par résidus.
3. **FAO additive — repli géométrique simplifié.** En l'absence d'OrcaSlicer, `_generate_analytical_gcode` trace un rectangle de boîte englobante par couche. Il ne s'agit pas d'un slicer géométrique général et ce résultat ne doit pas être présenté comme un parcours de fabrication qualifié.
4. **FAO soustractive — trajectoires nominales.** Plusieurs stratégies CAM génèrent des parcours à partir des bornes de la pièce; elles ne remplacent pas un calcul d'intersection outil/solide, une simulation d'enlèvement de matière ni une validation machine/contrôleur.
5. **Dynamique moléculaire — repli ENM.** OpenMM tente un champ de force tout-atome Amber, mais bascule vers un modèle élastique de réseau (ENM) en cas d'échec. Le rapport doit exposer clairement la méthode réellement utilisée; un ENM n'est pas équivalent à une simulation tout-atome Amber.
6. **Esquisse 2D — contrat de contraintes incomplet.** Certaines valeurs d'énumération de contrainte ne sont pas implémentées dans l'évaluation des résidus.
7. **Dépendances Python — manifeste à vérifier.** `planegcs_solver.py` importe `scipy.linalg` et `ezdxf`, qui ne sont pas déclarés explicitement dans `pyproject.toml`. Il faut vérifier les dépendances transitives puis déclarer directement celles utilisées.
8. **Tests — revendication à réconcilier.** Le README annonce 106 tests; 104 fonctions `test_*` sont visibles dans les 22 fichiers recensés. Les tests n'ont pas été exécutés lors de cet audit.
9. **Orchestration IA — mode simulé.** Le serveur crée `MockLLMProvider` pour l'exécution d'agent; la présence du MAC ne prouve pas une orchestration LLM de production.
10. **API et intégration.** Le backend expose plusieurs routes spécialisées, mais les capacités avancées demandées (topologie, électromagnétisme, multibody, robotique, PLM, veille, Big Data, sciences naturelles) ne disposent pas d'API/moteurs trouvés dans l'arborescence.

## Architecture cible à ajouter

Le produit doit évoluer d'une collection de fournisseurs spécialisés vers une plateforme d'ingénierie multiphysique et de gestion du cycle de vie, avec des domaines séparés et des contrats communs :

- `geometry`: B-Rep, maillage, esquisses, assemblages, tolérances, unités.
- `simulation`: orchestration de solveurs, modèles, conditions aux limites, maillages, convergence, incertitudes, résultats.
- `materials`: matériaux, modèles constitutifs, fatigue, fracture, fluage, corrosion, vieillissement et durabilité.
- `optimization`: topologie, forme, dimensionnement, DOE, optimisation multiobjectif et robustesse.
- `physics`: structure, thermique, CFD, électromagnétisme, acoustique, multibody et couplages multiphysiques.
- `manufacturing`: DFM/DFA, process planning, CAM, fabrication additive, simulation machine, robotique et métrologie.
- `plm`: exigences, configurations, EBOM/MBOM, révisions, changements, approbations, traçabilité et archivage.
- `molecular_materials`: chimie computationnelle, découverte moléculaire, matériaux, structures et workflows scientifiques.
- `intelligence`: veille, sources, provenance, recherche, extraction et alertes.
- `data_platform`: ingestion, stockage, traitement, catalogage, qualité et gouvernance Big Data.
- `natural_systems`: domaines de simulation naturels clairement séparés et validés scientifiquement.
- `platform`: jobs, événements, plugins, API, IAM, observabilité, UI, distribution et audit.

## Priorités d'implémentation proposées

### P0 — Fiabilité et vérité des résultats
- Corriger les états de convergence CFD et interdire les résultats simulés présentés comme solveur exécuté.
- Séparer explicitement résultats numériques, estimations analytiques, maquettes et sorties simulées.
- Réconcilier les contrats de contraintes 2D et les dépendances.
- Ajouter un registre des solveurs avec disponibilité, version, licence, paramètres, logs, convergence et provenance.
- Mettre en place CI et tests de contrats; exécuter une suite reproductible sur environnements contrôlés.

### P1 — Ingénierie mécanique avancée
- Assemblages et cinématique multibody.
- Matériaux avancés, fatigue et durée de vie.
- Optimisation paramétrique, forme et topologie.
- CFD réellement résolue avec maillage, contrôles de qualité, convergence et post-traitement.
- Couplage thermo-mécanique et modal/buckling selon validation.

### P2 — Fabrication avancée
- CAM basé sur géométrie et stock réel, simulation d'enlèvement de matière, cinématique machine et collisions.
- Slicing réel obligatoire pour les sorties destinées à fabrication.
- Planification des procédés et programmation robotique hors ligne.
- Qualification post-processeurs par machine et contrôleur.

### P3 — PLM et gestion du produit
- Exigences, EBOM/MBOM, révisions, ECO/ECN, configuration, approbations et fil numérique.

### P4 — Sciences et données
- Découverte moléculaire et science des matériaux avec workflows scientifiques vérifiables.
- Plateforme de veille et Big Data.
- Simulation de systèmes naturels par domaines (géosciences, climat, hydrologie, écologie), sans prétendre à une simulation universelle.

## Règles de vérité produit
- Un moteur simplement déclaré n'est pas une capacité implémentée.
- Un fichier de cas généré n'est pas une simulation résolue.
- Une sortie G-code synthétique n'est pas un parcours qualifié pour une machine.
- Une estimation empirique n'est pas un résultat de solveur.
- Un test présent dans le dépôt n'est pas un test exécuté.
- Une réussite de test logiciel ne constitue pas une validation scientifique ou une certification d'ingénierie.
- Aucune pièce destinée à la sécurité, à la fabrication ou à la mise en service ne doit être qualifiée sans revue indépendante, validation expérimentale et responsabilité d'ingénieur compétent.

## Statut
Audit statique initial. Aucune fonctionnalité nouvelle n'est déclarée implémentée par ce document. Les changements de code doivent être réalisés par étapes sur cette branche, avec tests et preuves. La branche master n'a pas été modifiée.
