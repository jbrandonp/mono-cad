# MONO — Audit de fondation et de cohérence architecturale
Version 2.0 — 25 novembre 2026
Référence : branche rebaseline/engineering-platform-2026-11
Statut : audit statique approfondi, propositions non ratifiées

## 1. Périmètre et méthode
Examen de l'arborescence du dépôt, des modules Python, des contrats de données, du backend FastAPI/WebSocket, du frontend TypeScript/Three.js/Mol*, du wrapper Tauri/Rust, des dépendances, des fournisseurs métier et d'échantillons de tests. L'arborescence de la branche contient 117 entrées. Le dépôt source reste un prototype compact. Aucun build, test, benchmark, solveur ou test de pénétration n'a été exécuté pendant cet audit. Les constats ci-dessous sont des observations statiques et des risques à confirmer par exécution.

## 2. Diagnostic exécutif
La base actuelle est une application desktop mono-processus qui réunit plusieurs prototypes métier. Elle n'est pas encore un noyau de plateforme d'ingénierie multidisciplinaire. Le principal défaut n'est pas le nombre de moteurs manquants : c'est l'absence de contrats transverses suffisamment riches pour que des domaines scientifiques différents puissent partager des données, des jobs, des résultats, des preuves, des révisions et des règles de qualité.

Décision recommandée : refondre d'abord le modèle de domaine, le graphe d'artefacts, le système d'unités, le contrat des études et solveurs, l'exécution des jobs, la provenance scientifique, le stockage et la sécurité. Conserver les fournisseurs existants derrière des adaptateurs pendant la migration. Ne pas réécrire immédiatement les algorithmes géométriques qui ont une valeur démontrable.

## 3. Constatations par fondation

### F-01 — Modèle de document et de données
Fichiers : `src/mono/core/document.py`, `src/mono/core/geometry_provider.py`.
- `MonoDocument` contient principalement `units`, `parameters`, `entities` et un historique en mémoire.
- `EntityRecord` mélange identité, fournisseur, échelle, transformation, script et paramètres.
- Pas de modèle de projet, exigences, système, produit, configuration, assemblage, matériau versionné, étude, solveur, job, résultat, preuve, révision, changement, nomenclature, procédé ou ressource de fabrication.
- Les fournisseurs sont stockés comme objets Python vivants; le document n'est donc pas un modèle de domaine sérialisable indépendant du runtime.
- `run_fea_simulation` remplace le fournisseur géométrique de la pièce par un fournisseur FEA : la géométrie et l'analyse ne sont pas des objets distincts liés par provenance.
- `update_parameter` modifie le paramètre puis tente de reconstruire des pièces; une exception est ignorée silencieusement, ce qui peut laisser le paramètre et la géométrie désynchronisés.
- Les unités sont déclarées par défaut en SI, mais chaque fournisseur emploie des conventions locales (mm, m, GPa, MPa, Å, nm) et des facteurs d'échelle ad hoc.

Conclusion : créer un modèle de domaine versionné et indépendant des moteurs; distinguer définition de conception, occurrence d'assemblage, géométrie, maillage, matériau, étude, exécution, résultat et preuve.

### F-02 — Contrat de fournisseur trop général
Fichier : `src/mono/core/geometry_provider.py`.
- `GeometryProvider` exige tessellation, introspection, sélecteur topologique et sérialisation native de tous les domaines.
- Un modèle moléculaire, un textile, un solide B-Rep, un résultat de champ CFD et une étude PLM n'ont pas les mêmes opérations.
- Le transport `BinaryMeshPayload` ne transporte que géométrie triangulée et un champ scalaire optionnel; il ne représente pas les champs vectoriels, tenseurs, volumes, maillages FEM, séries temporelles, trajectoires, cartes de particules ou métadonnées d'unités.
- Le format binaire concatène les buffers sans version, endian explicite, offsets, alignement, compression, unités, bornes ni checksum interne; le décodage n'impose pas une taille exacte.

Conclusion : décomposer les interfaces en capacités spécialisées et définir un format d'artefact versionné.

### F-03 — Géométrie et CAO
Fichiers : `brep_build123d.py`, `planegcs_solver.py`, `techdraw_drafting.py`, `bim_ifc.py`.
- La CAO B-Rep, les esquisses et les exports 2D sont de bonnes bases de prototype.
- Les identifiants de faces/arêtes reposent sur des indices topologiques; ils peuvent changer après une opération B-Rep. Il manque une stratégie de références persistantes et de résolution des références perdues.
- Le solveur d'esquisse déclare davantage de types de contraintes que ceux effectivement évalués; les types POINT_ON_LINE, POINT_ON_CIRCLE, ANGLE et EQUAL_LENGTH ne sont pas tous calculés dans le résidu observé. Le statut de convergence et les DOF doivent être recalculés depuis le Jacobien complet, pas uniquement le nombre de contraintes.
- Le solveur observé est une boucle maison de type Levenberg-Marquardt régularisé; il ne faut pas le présenter comme une intégration validée de PlaneGCS sans preuve.
- Les dessins techniques doivent séparer géométrie projetée, cotation, tolérancement, référentiels et normes de dessin.

Conclusion : introduire géométrie exacte, topologie, assemblages, contraintes, PMI/GD&T, tolérances, références persistantes et système de coordonnées explicites.

### F-04 — FEA et science des matériaux
Fichier : `fea_gmsh.py`.
- La chaîne Gmsh/CalculiX existe : import STEP, génération de maillage, deck CalculiX, exécution de ccx, lecture FRD.
- Les conditions aux limites peuvent être sélectionnées par extrêmes de bounding box; si elles ne sont pas trouvées, le code sélectionne arbitrairement des faces de remplacement. Pour un calcul de structure, cela peut produire une étude mathématiquement exécutable mais physiquement fausse.
- La sélection par bbox ne remplace pas des surfaces nommées et persistantes.
- Les hypothèses observées sont très limitées : matériau isotrope linéaire, charge simplifiée, calcul statique. Le calcul du facteur de sécurité à partir de la limite d'élasticité ne prouve ni la tenue en fatigue ni la durée de vie.
- Le parsing FRD, la correspondance des nœuds et les unités doivent être vérifiés sur jeux de référence indépendants.
- La défeaturisation automatique peut altérer les caractéristiques physiques importantes; elle doit être opt-in, justifiée, comparée à la géométrie d'origine et tracée.

Conclusion : les conditions aux limites invalides doivent bloquer l'étude, jamais déclencher un repli silencieux. Introduire matériaux versionnés, lois constitutives, contacts, charges, maillage qualifié, convergence, sensibilité et VVUQ.

### F-05 — CFD
Fichier : `cfd_openfoam.py`.
- Le fournisseur prépare un cas et appelle des exécutables externes; le premier audit a révélé un état de convergence faux positif, corrigé dans la branche.
- La correction présente maintenant des états distincts et refuse de publier des valeurs de force avant post-traitement vérifié.
- La chaîne doit encore être démontrée sur un cas de référence : géométrie fluide, domaine, maillage, qualité, conditions aux limites, modèle physique, solveur, résidus, bilans de conservation, indépendance au maillage et extraction des efforts.
- L'exécution de commandes externes doit être isolée, versionnée et traçable.

Conclusion : aucune métrique CFD ne doit être considérée fiable avant vérification du maillage, du solveur, de la convergence et de la validation physique.

### F-06 — Fabrication et robotique
Fichiers : `cam_machining.py`, `additive_slicer.py`, `workers.py`.
- Le CAM observé génère des trajectoires nominales simplifiées; la boîte englobante et les stratégies géométriques ne constituent pas une simulation d'enlèvement de matière complète.
- Le générateur additif interne par bounding box a été isolé comme démo non fabricable; l'API exige désormais OrcaSlicer pour le tranchage de production.
- Un G-code analysé n'est pas validé pour une machine, un contrôleur, un matériau ou une configuration thermique.
- Aucun modèle complet de cellule robot, cinématique, singularité, collision, post-processeur robot ou validation de trajectoire n'est présent.
- Les workers vérifient l'existence de binaires, pas leur identité, signature, version, hash, licence ni compatibilité.

Conclusion : séparer plan de fabrication, ressource, outillage, brut, trajectoire, simulation machine, post-processeur et programme NC; bloquer toute qualification machine sans preuve.

### F-07 — Molécules et matériaux
Fichiers : `biomolecule_gemmi.py`, `biomolecule_openmm.py`.
- Gemmi fournit une ingestion structurale et des analyses de base; OpenMM peut tenter Amber tout-atome.
- Le repli ENM est un modèle physique différent. Il ne doit jamais être silencieux ou présenté comme Amber tout-atome; la méthode est rapportée, mais le produit doit exiger un consentement explicite au changement de modèle et une qualification du workflow.
- Le workflow observé ne constitue pas une plateforme de découverte moléculaire : docking, génération de candidats, scoring, chimie quantique, incertitudes et validation expérimentale manquent.
- Les données matériaux ne sont pas une base de matériaux d'ingénierie versionnée.

Conclusion : traiter molécules et matériaux comme domaines scientifiques distincts, avec unités, modèles, provenance, bases de données et protocoles propres.

### F-08 — Orchestration et IA
Fichiers : `mac_orchestrator.py`, `server.py`.
- Le serveur instancie explicitement `MockLLMProvider`; l'agent connecté au backend n'est pas un service LLM de production.
- Le MAC est centré sur les tâches CAO actuelles et ne peut pas orchestrer proprement des workflows multi-physiques, PLM, calcul distribué et données scientifiques.
- Les sorties IA doivent être des propositions structurées et validées par contrats; l'IA ne doit pas décider seule des conditions aux limites ou lancer une fabrication.

Conclusion : l'IA devient un assistant de spécification, orchestration et analyse; les calculs sont exécutés par des moteurs déterministes, avec validation humaine pour les opérations à risque.

### F-09 — Exécution, API et état
Fichiers : `server.py`, `websocket_manager.py`, `main.py`.
- Le backend conserve un document global en mémoire, sans persistance transactionnelle durable ni isolation multi-projet.
- Le WebSocket reçoit des messages JSON sans schéma Pydantic strict, quotas, authentification applicative ou limites robustes de charge visibles dans la route examinée.
- Les tâches lourdes peuvent être exécutées dans le chemin API synchrone; pas de véritable gestionnaire durable de jobs, files, workers concurrents, annulation, checkpoints, quotas ou reprise.
- Le cycle de vie FastAPI ne fait pratiquement aucun initialisation/arrêt de services.
- Le port loopback limite l'exposition réseau par défaut, mais n'est pas une frontière d'authentification contre les autres processus locaux ou les pages web malveillantes.

Conclusion : API de commande versionnée, gestionnaire de jobs durable, workers isolés, événements, authentification locale et gestion de ressources.

### F-10 — Sécurité du code et des plugins
Fichiers : `ast_validator.py`, `ecip_executor.py`.
- Le validateur AST interdit quelques imports et noms; l'exécution finale utilise `exec` dans le même processus Python.
- Une liste blanche AST n'est pas un bac à sable de sécurité. Les objets Python et les bibliothèques natives peuvent exposer des surfaces d'attaque complexes.
- Pas de limite CPU/mémoire, quota, système de fichiers dédié, réseau désactivé au niveau OS, profil de processus ou isolation noyau visible.
- Les plugins futurs ne doivent pas partager le processus de l'interface ou du backend privilégié.

Conclusion : code généré et plugins dans des workers isolés avec permissions explicites, répertoires éphémères, limites OS, absence de réseau par défaut, timeout, quotas et journalisation.

### F-11 — Archive et persistance
Fichier : `archive_mono.py`.
- L'archive est un tar compressé Zstandard avec manifest JSON et hash SHA-256 par artefact.
- Les données restaurées sont incomplètes pour plusieurs fournisseurs : TechDraw, CAM, slicer, FEA et textile sont réinstanciés avec des objets par défaut au lieu de reconstituer l'état complet.
- Le manifeste n'est pas validé par schéma strict avant restauration.
- L'identifiant d'entité est interpolé dans un chemin temporaire lors de la restauration STEP; un identifiant malveillant peut introduire un chemin relatif hors du répertoire temporaire.
- Les tailles d'archives, nombre d'entrées, ratio de décompression, noms de fichiers, chemins, doublons, types de membres et taille du manifeste doivent être bornés.
- SHA-256 vérifie l'intégrité des artefacts mais pas leur authenticité ni l'identité de l'auteur.

Conclusion : archive v2 avec schéma strict, chemins normalisés, limites de ressources, signatures facultatives, migrations et tests adversariaux.

### F-12 — Historique transactionnel
Fichier : `command.py`.
- La pile Undo/Redo est en mémoire et limitée en nombre de commandes, sans transaction persistante, isolation ni journal d'événements.
- La commande de paramètre accepte des cibles dynamiques via introspection; ce n'est pas un modèle d'événements robuste pour PLM.
- La reconstruction géométrique et les jobs de calcul ne doivent pas être confondus avec les mutations de conception.

Conclusion : événements de domaine, transactions atomiques, snapshots, journaux de changement et versions immuables.

### F-13 — Frontend et visualisation
Fichiers : `frontend/src/main.ts`, `viewport.ts`, `molstar_viewer.ts`, `index.html`.
- L'interface est principalement une grande classe impérative et une page unique avec panneaux spécialisés.
- Elle est fortement couplée à des identifiants d'entités fixes et à des URL locales codées en dur.
- Le viewport ne gère qu'un modèle de mesh générique et un champ scalaire; les champs vectoriels/tenseurs, volumes, séries temporelles, coupes, iso-surfaces, lignes de courant, particules et grands ensembles de résultats ne sont pas représentés.
- Le frontend importe React mais le code observé utilise principalement le DOM impératif.
- Les flux de simulation, la sélection de version, les unités, l'état de job, les diagnostics et les preuves ne forment pas encore une UX cohérente.

Conclusion : architecture d'interface par espaces de travail et visualiseurs spécialisés partageant un modèle de scène/artefact commun.

### F-14 — Desktop et packaging
Fichiers : `src-tauri/src/lib.rs`, `tauri.conf.json`, `capabilities/default.json`.
- Tauri lance le backend en recherchant des chemins candidats et en utilisant un port fixe; l'identité du backend déjà présent sur le port n'est pas vérifiée.
- La CSP est configurée à null.
- Le manifeste Tauri n'accorde actuellement que `core:default`, mais la configuration CSP et la politique d'origine doivent être explicitement durcies.
- La gestion de l'arrêt, des redémarrages, du PID et de la détection d'un backend externe doit être rendue déterministe.
- Le packaging Windows/Linux/macOS et les dépendances natives des solveurs ne sont pas qualifiés.

Conclusion : service local lié à un token aléatoire, handshake entre desktop et backend, port dynamique, CSP restrictive, permissions minimales et packaging reproductible par OS.

### F-15 — Dépendances et chaîne logicielle
Fichier : `pyproject.toml`.
- Des imports directs observés comme `scipy` et `ezdxf` ne sont pas déclarés dans la liste directe du projet.
- `python-solvespace` est déclaré mais aucun usage direct n'a été confirmé dans les fichiers examinés.
- Les solveurs externes ne sont pas gérés comme un catalogue d'outils avec versions, licences, hashes, provenance et profils de compatibilité.
- Les contraintes de versions très ouvertes peuvent produire des changements incompatibles; les lockfiles doivent être reproduits et testés par matrice de plateforme.

Conclusion : dépendances directes explicites, groupes optionnels par domaine, lockfiles vérifiés, SBOM, provenance, licences et signatures.

## 4. Invariants à imposer à toute nouvelle architecture
1. Unités dimensionnelles explicites et conversions centralisées.
2. Identifiants stables et références persistantes aux objets.
3. Séparation entre définition, occurrence, artefact, étude, job, résultat et preuve.
4. Aucun fallback scientifique silencieux.
5. Aucune condition aux limites arbitraire.
6. Aucune sortie de fabrication qualifiée sans validation machine/contrôleur.
7. Toute simulation possède une hypothèse, un modèle, une version, une provenance, des critères d'acceptation et des limites.
8. Les résultats bruts sont immuables; les post-traitements sont versionnés.
9. Les calculs lourds s'exécutent hors du processus API/UI.
10. Les données et les résultats survivent au redémarrage et aux migrations.
11. La sécurité est assurée par isolation OS, pas par AST seul.
12. L'IA propose et explique; les moteurs déterministes calculent; l'humain autorise les actions critiques.

## 5. Référentiel VVUQ
Adopter une gouvernance de crédibilité des modèles inspirée de NASA-STD-7009B et des familles ASME VVUQ :
- vérification du code ;
- vérification de la solution numérique ;
- validation par rapport à des données de référence/expérimentales ;
- quantification des incertitudes ;
- domaine d'applicabilité ;
- niveau de crédibilité adapté au risque et à l'usage.
Références publiques : NASA-STD-7009B (https://standards.nasa.gov/standard/nasa/nasa-std-7009), NASA-HDBK-7009B (https://standards.nasa.gov/standard/nasa/nasa-hdbk-7009), ASME V&V 10-2019 (R2025), ASME V&V 20 et ASME VVUQ 1-2022 (https://www.asme.org/codes-standards/publications-information/verification-validation-uncertainty).

## 6. Décision d'architecture
Le dépôt doit devenir un monolithe modulaire à frontières de domaines strictes dans un premier temps, avec workers de calcul isolés et interfaces de solveurs. Ne pas passer directement à des microservices distribués : cela augmenterait la complexité avant que les contrats de domaine soient stables. Le découpage en services distants/HPC pourra être ajouté derrière les mêmes contrats.

## 7. Statut
Audit statique approfondi, pas certification. Les défauts et risques doivent être transformés en tickets, corrigés et validés par tests. Aucun moteur avancé n'est déclaré implémenté par ce document.
