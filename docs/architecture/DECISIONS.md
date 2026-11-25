# Mono — Décisions d'architecture (ADR)
Date : 25 novembre 2026
Statut : décisions de conception proposées; les ADR qui nécessitent un prototype restent conditionnels.

## ADR-001 — Monolithe modulaire local-first
**Décision :** commencer avec un monolithe modulaire à frontières de domaines explicites et des workers de calcul séparés.

**Pourquoi :** Mono est encore un prototype compact. Des microservices maintenant multiplieraient les coûts d'exploitation et les problèmes de cohérence avant la stabilisation des contrats. Les workers séparés protègent l'API/UI et permettent plus tard l'exécution distante.

**Conséquence :** chaque domaine expose des ports et contrats indépendants; pas d'import transversal des providers concrets dans les règles métier.

## ADR-002 — PostgreSQL comme source de vérité transactionnelle
**Décision cible :** PostgreSQL pour projets, objets, révisions, relations, permissions, événements, jobs et métadonnées d'artefacts. SQLite peut rester cache/préférences/tests.

**Pourquoi :** cohérence transactionnelle, concurrence, contraintes relationnelles, JSONB, outbox et trajectoire vers plusieurs utilisateurs/serveurs. Le stockage des gros binaires est séparé.

**Condition avant gel :** prototype d'installation, démarrage, mise à niveau, sauvegarde et restauration PostgreSQL sur Windows desktop. Si l'expérience locale est trop lourde, comparer une distribution PostgreSQL embarquée/gérée à un serveur local; ne pas réintroduire SQLite comme source unique par facilité.

## ADR-003 — Artefacts binaires hors base relationnelle
**Décision :** stocker les artefacts dans un magasin content-addressed, avec métadonnées relationnelles (hash, type, taille, URI, producteur, version, provenance, révision source).

**Pourquoi :** maillages, géométries, champs, résultats temporels, trajectoires, données moléculaires et datasets peuvent devenir très volumineux.

**Évolution :** filesystem local au départ; stockage objet S3-compatible pour calcul distant et partage.

## ADR-004 — Solveurs spécialisés et crédibilité explicite
**Décision :** un solveur déclare physique, formulation, formats, versions, limites, ressources, licence, benchmarks et niveaux de validation. Le cycle sépare fin du processus, convergence, vérification, validation et qualification.

**Pourquoi :** un code retour nul ou un résultat plausible ne garantit pas une réponse physique correcte. Aucun fallback de modèle ou condition aux limites implicite.

## ADR-005 — Agents parallèles avec état contrôlé
**Décision :** agents spécialisés simultanés autorisés, mais chaque tâche reçoit un périmètre, un contrat, une branche/zone de travail, des dépendances, un budget et des verrous. Un coordinateur résout les conflits avant fusion.

**Pourquoi :** le parallélisme ne doit pas créer de mutations concurrentes indéterministes sur un même document.

**Mémoire :** mémoire de projet locale, versionnée et sourcée, conformément à la préférence exprimée. Les secrets et données sensibles sont exclus des contextes non autorisés.

## ADR-006 — Auto-évolution maximale sous contrôle
**Décision :** Mono peut analyser, proposer, coder, tester et optimiser automatiquement dans des branches et environnements isolés. Les changements de noyau, releases, actions physiques, calculs qualifiés et programmes machines exigent des portes d'approbation explicites.

**Pourquoi :** autonomie maximale ne signifie pas auto-autorisation de changements irréversibles ou de décisions scientifiques non vérifiées.

## ADR-007 — Extensions locales et distantes
**Décision :** modèle hybride : extensions locales et distantes, découvertes via manifeste de capacités, version, licence, provenance, permissions et limites de ressources.

**Pourquoi :** garder les outils desktop et les moteurs locaux, tout en permettant plugins, services spécialisés et calcul HPC.

## ADR-008 — Format de projet portable versionné
**Décision :** le format .mono est un paquet d'échange/sauvegarde, pas la base transactionnelle en cours d'édition. Il contient manifeste, révisions, artefacts, hashes, provenance et migrations de schéma. La restauration est fail-closed.

**Pourquoi :** le fichier projet doit être portable et vérifiable; la base transactionnelle doit gérer les mutations et la concurrence.

## ADR-009 — Interopérabilité par adaptateurs testés
**Décision :** chaque format et moteur expose une capacité déclarée avec versions et limitations. Les formats ne sont pas annoncés supportés sur la seule présence d'un import/export.

**Pourquoi :** STEP, IFC, DXF, glTF, USD, 3MF, G-code, mmCIF, Parquet, NetCDF et autres ont des sémantiques différentes.

## ADR-010 — Qualification par cas d'usage
**Décision :** aucun statut général « certifié industriel ». La qualification s'applique à un couple domaine/version moteur/cas d'usage/environnement, avec preuves et limites.

**Pourquoi :** la crédibilité d'un solveur structurel ne se transfère pas automatiquement à la CFD, à la robotique, à la durée de vie ou aux systèmes naturels.
