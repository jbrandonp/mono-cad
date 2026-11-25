# Mono — Matrice des capacités observées et des écarts

**Dernière revue : 26 novembre 2026.** Cette matrice est un résumé du dépôt. La matrice complète avec périmètre, preuve, statut et travail requis est conservée dans la Library sous MONO_V2_CAPABILITY_MATRIX_v3_1.csv.

## Échelle de statut

- **Prototype** : code/provider présent, couverture limitée.
- **Partiel** : une partie du workflow est présente, sans chaîne complète.
- **Non démontré** : aucun workflow complet ou moteur validé n'a été observé.
- **Qualifié** : réservé à un périmètre/version/cas d'usage avec preuves indépendantes; aucun domaine ne reçoit ici ce statut global.

## Capacités demandées

| Domaine | Présence observée dans le dépôt | Statut de preuve | Lacune principale |
|---|---|---|---|
| CAO 2D / esquisses | Solveur d'esquisse interne, génération de dessins | Prototype | DOF/Jacobian, conflits, contraintes, cotation et régression géométrique indépendants |
| CAD/CAO 3D B-Rep | build123d/OCP, features et géométrie | Prototype | Références topologiques persistantes, feature graph complet, assemblages, PMI/GD&T, échanges qualifiés |
| Visualisation/expérience 3D | Three.js, viewport, Mol* | Prototype | Champs 3D et temporels, grandes scènes, streaming, interactions/UX et tests de performance |
| FEA | Gmsh/CalculiX provider | Prototype non validé | CalculiX externe absent du CI; preuves analytiques/expérimentales et BC/material contracts |
| CFD | Génération de cas OpenFOAM | Prototype non validé | Binaire et benchmarks réels, convergence et conservation |
| Électromagnétisme | Aucun solveur complet démontré | Non démontré | Choix FEM/BEM/FDTD et validation dédiée |
| Dynamique multicorps | Aucun solveur complet démontré | Non démontré | Kinematics, contact, intégration temporelle et conservation |
| Matériaux/durée de vie | Primitives matériaux/FEA | Non démontré pour la durée de vie | Fatigue, fluage, fracture, corrosion, vieillissement et UQ |
| Optimisation topologique/forme | Aucun pipeline complet démontré | Non démontré | Méthodes, contraintes, manufacturabilité et benchmarks |
| Automatisation de conception | ECIP, paramétrique, update atomique | Partiel | Sandbox OS, transactions durables, dépendances paramétriques et preuves de reproductibilité |
| Planification de procédés/CAM | Providers CAM, G-code, slicing | Prototype | Gammes, ressources, outils/fixtures, simulation machine et postprocesseurs qualifiés |
| Simulation fabrication | CAM/slicer partiels | Non démontré comme jumeau machine complet | Enlèvement matière, collision, gouge, cinématique machine et validation physique |
| Programmation robotique | Concepts d'intégration Marlin et E14 | Non démontré | Bench Marlin non construit, simulateur non développé, OLP/post-processeurs non qualifiés |
| PLM | Modèles et concepts documentaires | Non démontré | EBOM/MBOM, configuration, effectivity, ECO/ECN, approvals, release |
| BIM/IFC | IfcOpenShell | Prototype | IDS, coordination, conformité et échanges testés indépendamment |
| Molécules et matériaux computationnels | Gemmi/OpenMM | Prototype | Pipeline découverte, modèles explicites, reproductibilité et validation expérimentale |
| Intelligence/veille | Orchestration et MockLLMProvider | Prototype | Sources, droits, provenance, fraîcheur, claim-evidence et évaluation |
| Big Data/data engineering | Aucun système distribué démontré | Non démontré | Catalogue, lineage, qualité et ingestion/traitement à l'échelle |
| Modélisation naturelle | Aucun domaine validé démontré | Non démontré | CRS/temps, observations, calibration et validation pour climat/hydrologie/géosciences/écologie |
| Agents multi-spécialisés | Orchestration orientée CAD | Partiel | Isolation concurrente, propriété des objets, arbitrage et remote agents |
| Mémoire projet locale | Pas de persistance complète démontrée | Non démontré | Versioning, provenance, recherche, ACL, backup et rétention |
| Auto-évolution | Outillage de code/agents partiel | Non démontré comme évolution autonome sûre | Worker isolé, branche, tests, SBOM, promotion/rollback contrôlés |
| Extensions locales/distantes | Abstraction provider/plugin | Partiel | Manifest signé, capacités, licences, révocation et sandbox |
| Persistance et jobs | MonoDocument in-memory et .mono archive | Non démontré pour usage durable | PostgreSQL non intégré, outbox, queue, checkpoints et reprise |

## Preuve d'exécution actuelle

La dernière matrice CI entièrement verte (run 45723972613, commit 99624cc) exécute 111 tests Python réussis sur Windows, Linux et macOS; 12 tests sont ignorés par environnement parce que les binaires CalculiX/OrcaSlicer et l'archive de démo manquent. Le build frontend réussit, mais avertissements de modules Node externalisés et de taille de bundle restent ouverts.

**Conclusion :** Mono est un ensemble de prototypes et de contrats de reconstruction. Aucune capacité n'est déclarée industriellement qualifiée par la simple présence d'un provider.
