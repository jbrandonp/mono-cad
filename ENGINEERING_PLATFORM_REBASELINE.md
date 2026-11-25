# MONO — Rebaselining architectural global
Version 2.0 — 25 novembre 2026
Statut : proposition d'architecture de référence, non ratifiée

## Vision
Mono devient une plateforme intégrée d'ingénierie et de sciences computationnelles. Sa valeur repose sur un modèle de données et un fil numérique communs, et non sur une accumulation d'outils dans une interface unique.

## Décision structurante
Adopter un **monolithe modulaire à frontières de domaines**, avec :
- un modèle de domaine indépendant des bibliothèques ;
- un catalogue d'artefacts et un graphe de provenance ;
- des adaptateurs de géométrie, de simulation, de fabrication et de science ;
- un gestionnaire durable de jobs ;
- des workers de solveurs isolés ;
- un système VVUQ par domaine ;
- un frontend de workspaces et de visualiseurs spécialisés ;
- un PLM transversal comme gestion des configurations, révisions et changements.

L'architecture distribuée et le calcul HPC seront des extensions, pas des prérequis.

## Couches
1. **Experience** — UI desktop/web, visualiseurs, tableaux de bord, inspecteurs.
2. **Application** — commandes, workflows, études, permissions, orchestration et jobs.
3. **Domain** — CAD, systems engineering, materials, CAE, optimization, manufacturing, robotics, PLM, molecular science, intelligence, data et natural systems.
4. **Infrastructure** — persistance, stockage artefacts, événements, workers, secrets, observabilité, identité, packaging.
5. **Adapters** — OpenCASCADE/build123d, Gmsh, CalculiX, OpenFOAM, OpenMM, IfcOpenShell, OrcaSlicer, moteurs futurs.

## Domaines métier
- CAD & Geometry : esquisses, B-Rep, maillage, assemblages, PMI/GD&T, dessins.
- Systems Engineering : exigences, architecture, interfaces, allocations, trade studies.
- CAE & Physics : structure, thermique, CFD, électromagnétisme, acoustique, multibody, multiphysique.
- Materials & Durability : matériaux, modèles constitutifs, fatigue, fracture, fluage, corrosion, durée de vie.
- Optimization : paramètres, forme, topologie, multiobjectif, robustesse.
- Manufacturing : DFM/DFA, process planning, CAM, additive, simulation machine, métrologie.
- Robotics : cinématique, cellules, trajectoires, collisions, programmation hors ligne.
- PLM : configurations, EBOM/MBOM, révisions, changements, approbations, releases.
- Molecular & Materials Science : molécules, docking, dynamique moléculaire, chimie computationnelle, microstructures.
- Intelligence & Research : veille, sources, provenance, extraction, alertes.
- Data Platform : ingestion, catalogue, qualité, lineage, stockage et traitement massif.
- Natural Systems : climat, hydrologie, géosciences, écologie, systèmes physiques naturels.

## Fondations transverses
- Modèle canonique décrit dans `DOMAIN_MODEL_V2.md`.
- Contrat de calcul décrit dans `SOLVER_CREDIBILITY_V2.md`.
- Audit de la base actuelle décrit dans `FOUNDATION_AUDIT_V2.md`.
- Migration décrite dans `FOUNDATION_MIGRATION_ROADMAP_V2.md`.

## Modèle d'exécution
Un utilisateur définit une étude versionnée. Le système valide les entrées et unités, prépare les artefacts, place un job dans une file, sélectionne un worker et un solveur compatible, exécute dans une sandbox, collecte les sorties, parse les résultats typés, évalue la convergence, produit les preuves VVUQ, puis publie un résultat immuable.

## Architecture de données
Les métadonnées, identités, révisions, relations, états et permissions sont stockés transactionnellement. Les gros artefacts binaires et résultats de champs résident dans un stockage adapté. Un catalogue indexe les artefacts, unités, provenance et relations. Le format d'archive .mono devient un paquet portable de références, manifeste, artefacts et preuves, avec validation stricte.

## Système d'unités
SI cohérent en interne, dimensions typées, unités explicites, conversions centralisées, référentiels de coordonnées explicites et conventions de signe/documentation.

## VVUQ et confiance
Aucun calcul ne peut être qualifié par la seule présence d'un solveur ou par un code retour nul. Vérification du code, vérification de solution, validation empirique, quantification des incertitudes et domaine d'applicabilité sont séparés.

## Gouvernance des moteurs
Les fournisseurs existants restent utiles mais deviennent adaptateurs. Un moteur externe est installé, versionné, vérifié, licencié, exécuté et journalisé selon un contrat commun. Aucun fallback silencieux.

## Sécurité
Le code utilisateur et les plugins s'exécutent dans des workers isolés au niveau OS. Le backend local utilise authentification, contrôle d'origine, quotas, schémas stricts et permissions minimales. Les archives sont validées avant toute reconstruction. Tauri utilise CSP restrictive et identité vérifiée du backend.

## Compatibilité
- V1 reste importable en mode lecture/migration.
- Les projets migrés conservent IDs externes, artefacts et provenance disponible.
- Toute donnée perdue ou non reconstructible est signalée.
- Pas de migration destructive automatique.
- Les changements de schéma sont versionnés et réversibles autant que possible.

## Références de crédibilité scientifique
NASA-STD-7009B, NASA-HDBK-7009B, ASME VVUQ 1-2022, ASME V&V 10-2019 (R2025), ASME V&V 20. Ces références guident la gouvernance VVUQ; elles ne constituent pas une certification de Mono.

## Limites et statut
Cette architecture est une cible proposée. Elle ne signifie pas que les domaines avancés sont déjà implémentés, validés ou certifiés. Les validations doivent être réalisées par discipline, cas d'usage et niveau de risque.
