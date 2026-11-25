# Mono — Séquence de reconstruction et portes de sortie V3.1

**État au 26 novembre 2026.** Une phase n'est fermée qu'avec code intégré, tests exécutés sur les OS ciblés, build reproductible, preuves, revue et mise à jour du registre d'écarts. Une spécification ou un ZIP ne ferme pas une phase.

## État de la branche de reconstruction

La branche foundation-reset-2026-11 contient une première tranche de Domain Core, ports d'infrastructure, unités, transitions de crédibilité, mise à jour paramétrique atomique, ECIP fail-closed par défaut et durcissement du format .mono.

Le run CI 45723972613 (commit 99624cc) a réussi sur Windows, Linux et macOS : 111 tests passés, 12 ignorés et 3 avertissements par environnement; build frontend réussi. Les 12 tests ignorés nécessitent CalculiX/OrcaSlicer ou une archive de démonstration absente. Ces moteurs ne sont donc pas validés par cette CI.

## P0 — Sécurité, intégrité et crédibilité (bloquant)

- Sandbox OS pour ECIP et extensions; aucun code non fiable dans le processus API.
- Contrat E23–E27 unique, ratifié et testé : invocation/effect, effect_unknown, fencing, révocation, budgets, replay, compensation et anti-rollback.
- FEA fail-closed : aucun matériau ou BC implicite; étude distincte de l'entité géométrique.
- Qualification avec attestation indépendante, portée, approbateur, validité et preuves.
- Format .mono : tous les artefacts primaires et sidecars manifestés/restaurés; migrations et compatibilité arrière testées.

**Sortie P0 :** tests adversariaux indépendants, aucun retry aveugle, round-trip sans perte et aucune exécution in-process non isolée.

## P1 — Domain Core et persistance durable

- ADR formelle de stockage relationnel et modèle canonique.
- Projets, exigences, systèmes, pièces, assemblages, révisions, matériaux, études, exécutions, résultats, preuves et configuration PLM.
- PostgreSQL local si l'ADR est ratifiée, avec migrations, UoW, outbox, journal et contrôle de concurrence.
- ArtifactStore content-addressed, rétention, sauvegarde et restauration testées.

## P2 — Jobs, workers et agents

- Queue durable, scheduler, leases/fencing, timeout, cancellation, checkpoint et reprise.
- Workers isolés par moteur, budgets CPU/RAM/GPU/IO et filesystem/network allowlist.
- Orchestration multi-agent sur DAG, projets/branches isolés, locks, arbitration et revue de patch.

## P3 — Verticale mécanique end-to-end

- Exigences → CAD 2D/3D → matériaux/load case → mesh/FEA → vérification/validation → optimisation → CAM/process → inspection → PLM release.
- Références analytiques, benchmarks externes, STEP/2D round-trip, topological naming et assemblages.

**Sortie P3 :** un cas mécanique complet avec preuves numériques et physiques adaptées; aucune certification générale n'est revendiquée.

## P4 — Autres moteurs CAE et durabilité

Thermique, CFD, EM, multibody, multiphysique, fatigue, fluage, fracture, corrosion, vieillissement et durée de vie. Chaque domaine exige formulation, unités, repères, maillage, convergence, conservation, sensibilité, UQ et VVUQ adaptés.

## P5 — Manufacturing et robotique

Process planning, ressources/outils, CAM, simulation machine, enlèvement matière, collision/gouge, additive, robot cell, cinématique, singularités, OLP et post-processeurs. Toute action physique reste soumise à un safety case et une autorisation distincte.

## P6 — PLM, molécules, matériaux, données et nature

EBOM/MBOM, effectivity, change workflow, approvals, release et fournisseurs; Gemmi/OpenMM et workflows moléculaires; science des matériaux et microstructure; veille sourcée; data catalog/lineage/quality; Big Data; climat/hydrologie/géosciences/écologie avec calibration sur observations.

## P7 — Exploitation et industrialisation (E70)

Instrumentation E50/E70, SLI calculés, baselines, SLO approuvés, runbooks exercés, support, EOL, capacité/coûts; packaging Tauri, install/update, matrice OS, SBOM, licences, sécurité, sauvegardes et RPO/RTO mesurés.

## Blocages documentaires et de gouvernance

- 546 gates OPEN E22–E51 et 20 gates OPEN E70 (566 connus); E13 a 7 critères BLOQUANTS sur 15; E71 ajoute 18 gates OPEN.
- E52–E69 : 18 ZIP visibles par métadonnées mais contenus non lisibles/materialisables dans le contexte Project; aucun statut de gate n'est supposé.
- E23–E27 restent proposés/non ratifiés; E04 PostgreSQL reste une recommandation non ratifiée.
- E70 catalogue de services et SLI/SLO ne sont pas encore activés.

**Ordre recommandé :** fermer P0, ratifier les ADR, implémenter P1–P2, livrer une verticale mécanique P3, puis seulement élargir les autres domaines.