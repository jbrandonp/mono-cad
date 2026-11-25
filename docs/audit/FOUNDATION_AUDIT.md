# Mono — Audit de reconstruction depuis la base
Date : 25 novembre 2026
Base : master de jbrandonp/mono-cad
Méthode : inspection statique des fichiers source, manifestes, tests et arborescences GitHub. Aucun test/build local ni aucun solveur n'a été exécuté pendant cet audit.

## Règle de vérité
Les statuts de ce rapport signifient :
- Observé : le code ou le fichier est présent.
- Partiel : une première version existe, sans preuve de couverture complète.
- Non démontré : le dépôt ne permet pas d'affirmer que la capacité est opérationnelle.
- Cible : architecture ou fonctionnalité à construire.

Les mentions historiques « certifié », « 100 % », « industriel », « incassable » et « prêt en production » ne sont pas acceptées comme preuves. Un skill n'est pas un moteur de calcul; un test unitaire ne vaut pas validation physique.

## 0 bis. Correctifs déjà introduits sur la branche de reconstruction
- Contrats de domaine V2 initiaux ajoutés dans src/mono/domain/models.py avec quantités explicites, références d'artefacts, études, solveurs et transitions de crédibilité exigeant des preuves.
- L'exécution ECIP dans le processus backend est maintenant désactivée par défaut. Le flag MONO_ENABLE_UNSAFE_INPROCESS_ECIP=1 ne doit servir qu'aux tests et environnements jetables; ce mode reste non isolé.
- Le lecteur .mono valide les noms de membres, refuse liens et types non réguliers, borne les tailles, contrôle le manifeste, les identifiants, les hashes et les tailles des artefacts, et borne les entrées compressées/gzip.
- Tests d'intégration dépendant de CalculiX ou de l'archive démo sont explicitement ignorés lorsque ces binaires/artefacts ne sont pas présents. Cela ne signifie pas que les domaines FEA ou démo sont validés.
- CI Windows Python et frontend Bun a été ajoutée. Les résultats sont à consulter sur les runs GitHub; la création du workflow ne constitue pas un test réussi.

## 1. État réel du dépôt
Le dépôt master compte 114 entrées Git. Il comprend :
- Backend Python 3.12 / FastAPI / Pydantic.
- Géométrie build123d / OCP.
- Maillage Gmsh, fournisseur CalculiX et fournisseur OpenFOAM.
- IFC/IfcOpenShell, Taichi/XPBD, Gemmi/OpenMM, CAM, slicer, TechDraw.
- Frontend TypeScript, Three.js, Mol*, Vite et dépendances React.
- Wrapper desktop Tauri 2 / Rust.
- Une suite de tests présente, mais aucun résultat de test actuel n'a été exécuté ici.

Le dépôt contient une collection de prototypes et de connecteurs métier. Il ne contient pas encore une plateforme PLM/CAE/optimisation/fabrication/sciences intégrée et qualifiée.

## 2. Défauts et écarts constatés

### 2.1 Modèle métier
Fichier : src/mono/core/document.py.
- MonoDocument contient paramètres, entités et historique; EntityRecord garde une instance Python de provider.
- Il n'existe pas de modèle canonique complet pour projet, révision, exigences, assemblage, matériaux versionnés, étude, exécution, résultats, preuves, changement et release.
- update_parameter masque une exception de reconstruction géométrique avec except Exception: pass.
- run_fea_simulation transforme le provider d'une entité géométrique en provider FEA au lieu de créer une étude distincte liée à une révision de conception.

Conséquence : le modèle actuel ne peut pas constituer seul la base durable d'un fil numérique industriel.

### 2.2 Contrat des providers
Fichier : src/mono/core/geometry_provider.py.
Le contrat GeometryProvider mélange tessellation, introspection, sélection topologique et sérialisation native. Ces opérations ne sont pas communes à un solveur CFD, une simulation moléculaire, un résultat de champ ou un système PLM.

Conséquence : séparer GeometryKernel, GeometryExchange, Tessellation, MeshGenerator, SolverAdapter, FieldDataAdapter, ManufacturingAdapter et MolecularAdapter.

### 2.3 Esquisses et topologie
Le solveur d'esquisse et les références topologiques doivent être comparés aux implémentations amont et vérifiés contrainte par contrainte. Un index de face/arête ne garantit pas une identité persistante après une opération B-Rep. La présence d'un skill PlaneGCS ne prouve pas que PlaneGCS upstream est intégré.

Conséquence : références persistantes résolues et vérifiées, graphe de features, tests de régression topologique, DOF calculés depuis le Jacobien et traitement explicite des conflits.

### 2.4 FEA et durabilité
Fichier : src/mono/providers/fea_gmsh.py.
Une chaîne Gmsh/CalculiX existe. Les groupes de faces de conditions aux limites sont déterminés en partie par prédicats et bounding boxes. Cela ne suffit pas à garantir que la sélection représente le montage physique voulu. Le modèle matériau et l'analyse observés ne prouvent pas fatigue, fluage, fracture, corrosion, vieillissement ou durée de vie.

Conséquence : bloquer les conditions manquantes/ambiguës; ajouter matériaux versionnés, modèles constitutifs, qualité et indépendance de maillage, convergence, vérification, validation expérimentale et quantification des incertitudes.

### 2.5 CFD et multiphysique
Fichier : src/mono/providers/cfd_openfoam.py.
Un fournisseur génère des cas OpenFOAM. La présence du code ne prouve ni le binaire, ni les cas de référence, ni la convergence, ni les bilans de conservation, ni la validation. Les contrats de couplage multiphysique ne sont pas visibles.

### 2.6 Molécules et matériaux
Fichiers : biomolecule_gemmi.py et biomolecule_openmm.py.
Gemmi traite des structures; OpenMM exécute des simulations. Un chemin de repli ENM existe après une erreur de préparation all-atom. ENM et Amber all-atom sont des modèles différents : le repli doit être explicite et autorisé, jamais présenté comme le même calcul. La découverte de molécules complète, le docking, la chimie quantique et la validation expérimentale ne sont pas démontrés.

### 2.7 Fabrication et robotique
Les fournisseurs CAM et additive existent, mais cela ne prouve pas une simulation machine complète, la détection exhaustive de collision/gouge, ni un post-processeur qualifié sur un contrôleur donné. Aucun système complet de robotique industrielle et de programmation hors ligne n'est démontré.

### 2.8 Sécurité d'exécution
Fichiers : ast_validator.py et ecip_executor.py.
Le validateur AST est un filtre syntaxique, pas un bac à sable. ecip_executor.py appelle exec dans le processus Python. Aucun cloisonnement OS de ce code n'est démontré.

Conséquence : code utilisateur et plugins doivent s'exécuter dans des workers isolés, permissions minimales, réseau désactivé par défaut, CPU/RAM/durée bornés, répertoire temporaire dédié, annulation et logs. AST ne sert qu'à prévalider.

### 2.9 Archives
Fichier : archive_mono.py.
Le format .mono est un tar compressé avec manifeste et SHA-256. La restauration utilise des chemins temporaires construits à partir de entity_id; les chemins doivent être strictement validés. Le lecteur doit limiter taille, nombre d'entrées, ratio de décompression, taille de manifeste et types de membres. Plusieurs providers sont restaurés avec des objets par défaut plutôt qu'avec tout leur état : l'affirmation « sans perte » n'est pas prouvée. SHA-256 vérifie l'intégrité, pas l'authenticité.

### 2.10 Persistance et jobs
Le document et Undo/Redo sont principalement en mémoire. Aucun stockage transactionnel durable complet des projets, révisions, événements, jobs et résultats n'est visible. Les calculs lourds ne doivent pas bloquer l'API ou l'interface.

### 2.11 API, Tauri et frontend
- Le serveur instancie MockLLMProvider : copilote LLM de production non démontré.
- Les messages WebSocket doivent être strictement schématisés, authentifiés, limités en taille et soumis à des quotas/origines.
- tauri.conf.json contient csp: null.
- Le démarrage backend depuis Tauri doit vérifier identité du processus, port, PID, token de session et arrêt propre.
- Le frontend repose principalement sur un grand module TypeScript impératif; React présent dans package.json ne signifie pas qu'il structure réellement l'interface.

### 2.12 Dépendances
Confronter tous les imports directs aux dépendances déclarées, verrouiller les versions, produire SBOM et inventaire de licences, tester les builds par OS. Les outils externes ne sont pas considérés installés parce que leur nom figure dans le code.

## 3. Audit du dépôt de compétences
Le dépôt public jbrandonp/antigravity-skills possède 5 320 entrées dans son arbre Git et 508 fichiers SKILL.md sur main au moment de l'inspection. Ce chiffre comprend suites, profils, sous-skills et thèmes; il ne signifie pas 508 skills activés dans Antigravity.

Les douze fichiers ciblés ont été retrouvés sur GitHub : build123d-ecip-cad-engineer, brep-kernel-architect, planegcs-sketch-solver, gmsh-calculix-fea, taichi-xpbd-cloth-simulation, molecular-dynamics-simulation, industrial-cam-engineer, bim-ifc-cad-architect, additive-manufacturing-slicer-craftsman, advanced-software-architecture-security-suite, postgresql-production-dba et humanoid-robotics-architect.

La présence sur GitHub est vérifiée. L'installation locale, l'activation Antigravity, la copie dans le workspace, les priorités effectives et les outils référencés ne peuvent pas être vérifiés depuis le dépôt distant. Le rapport local historique transmis dans la conversation affirme une installation, mais n'est pas une preuve actuelle.

### Couverture manquante pour l'ambition élargie
La recherche de chemins SKILL.md par domaine n'a pas trouvé de skill dédié identifiable pour :
- électromagnétisme;
- dynamique multicorps;
- fatigue, fracture, fluage, corrosion et prédiction de durée de vie;
- optimisation de forme/topologie;
- PLM et configuration EBOM/MBOM/change/release;
- CFD/OpenFOAM;
- science des matériaux et matériaux computationnels;
- modélisation naturelle (climat, hydrologie, géosciences, écologie).

Des skills génériques de data engineering, optimisation et robotique existent, mais ils ne remplacent pas des compétences disciplinaires validées. Le chiffre historique de 170 compétences ne prouve donc pas la couverture des nouveaux domaines demandés.

Les skills contiennent parfois des affirmations marketing ou excessives. Ils doivent être utilisés comme guides, puis confrontés à la documentation upstream, aux licences, aux tests et aux benchmarks.

## 4. Décision de reconstruction
Ne pas jeter les providers existants. Créer un domaine canonique V2 indépendant des moteurs, garder V1 importable, migrer chaque provider derrière un adaptateur et supprimer progressivement les contrats universels inadéquats.

## 5. Invariants
1. Pièce, assemblage, matériau, étude, exécution, résultat et preuve sont des objets distincts.
2. Toute quantité a une unité et une dimension explicites.
3. Toute donnée spatiale déclare son repère et sa convention.
4. Les références topologiques doivent être persistantes et résolues.
5. Aucune condition aux limites arbitraire ou fallback scientifique silencieux.
6. Code retour nul n'est pas convergence; convergence n'est pas validation.
7. Résultats bruts immuables; post-traitements versionnés.
8. Calculs/plugins isolés du processus UI/API.
9. Toute mutation durable est transactionnelle, versionnée et auditée.
10. Les révisions libérées sont immuables.
11. Les archives sont intégralement validées avant restauration.
12. L'IA propose; les moteurs déterministes calculent; les opérations critiques sont approuvées.
13. Une capacité n'est annoncée implémentée qu'avec tests, benchmark et preuve reproductible.
14. Le local-first doit évoluer vers calcul distant/HPC sans changer les contrats métier.

## 6. Crédibilité scientifique
Adopter une gouvernance VVUQ inspirée de NASA-STD-7009B / NASA-HDBK-7009B et des référentiels ASME pertinents. Vérifier éditions et champs d'application avant toute déclaration de conformité.
- https://standards.nasa.gov/standard/nasa/nasa-std-7009
- https://standards.nasa.gov/standard/nasa/nasa-hdbk-7009
- https://www.asme.org/codes-standards/publications-information/verification-validation-uncertainty

## 7. Limites
Audit statique. Aucun test/build n'a été exécuté, aucun solveur lancé, aucune machine locale inspectée, aucun calcul certifié. Les prochaines étapes doivent établir une CI et des preuves reproductibles.
