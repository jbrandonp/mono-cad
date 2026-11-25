# Mono — Index de pilotage de la refonte
Version 2.0 — 25 novembre 2026
Statut : index documentaire, les décisions restent proposées et non ratifiées.

## Ordre de lecture
1. [Audit des fondations](FOUNDATION_AUDIT_V2.md) — constats statiques et risques du code actuel.
2. [Modèle de domaine canonique](DOMAIN_MODEL_V2.md) — objets, relations, unités, artefacts et contrats.
3. [Crédibilité des solveurs](SOLVER_CREDIBILITY_V2.md) — cycle d'étude, exécution, convergence et VVUQ.
4. [Architecture cible](ENGINEERING_PLATFORM_REBASELINE.md) — couches, domaines et décisions structurantes.
5. [Feuille de route de migration](FOUNDATION_MIGRATION_ROADMAP_V2.md) — phases et critères de sortie.
6. [Audit initial des capacités métier](ENGINEERING_CAPABILITY_AUDIT_2026-11-25.md) — matrice des fonctions déjà présentes ou manquantes.

## Ordre de priorité
P0 : sécurité, vérité des résultats, persistance et restauration.
P1 : modèle de domaine, unités, contrats, artefacts et références persistantes.
P2 : exécution durable des jobs, workers isolés, VVUQ et observabilité.
P3 : migration des moteurs actuels comme adaptateurs.
P4 : extension progressive des domaines CAE, optimisation, fabrication, robotique, PLM et sciences.

## Règle de gouvernance
Aucun domaine n'est réputé implémenté parce qu'il figure dans une spécification. Une phase ne ferme qu'après code intégré, tests exécutés, preuves, documentation, migration et revue technique.
