# Mono — Plateforme multidisciplinaire d'ingénierie et de sciences computationnelles

**État au 25 novembre 2026 : prototype logiciel en reconstruction architecturale.**

Mono ambitionne de relier dans un environnement cohérent la conception CAO, la simulation multiphysique, l'optimisation, la fabrication, la robotique, le PLM, les sciences moléculaires, la science des matériaux et les données scientifiques.

Le dépôt actuel contient des prototypes spécialisés. Il ne constitue pas encore une plateforme industrielle complète et aucune certification ou qualification générale n'est revendiquée.

## Ce qui existe dans le dépôt

| Domaine | État vérifiable dans le code |
|---|---|
| CAO B-Rep | Prototype build123d/OCP |
| Esquisses et dessins 2D | Solveur interne et génération de dessins à qualifier |
| Visualisation | Three.js, viewport et Mol* |
| FEA | Fournisseur Gmsh/CalculiX; exécution réelle dépend du binaire externe |
| CFD | Préparation de cas OpenFOAM; validation de bout en bout non démontrée |
| BIM/IFC | Prototype IfcOpenShell |
| Textile | Prototype XPBD/Taichi |
| Molécules | Ingestion Gemmi et prototype OpenMM |
| CAM et fabrication additive | Fournisseurs prototypes; post-processeurs et programmes machine non qualifiés |
| PLM, robotique avancée, électromagnétisme, multibody, optimisation topologique, Big Data et systèmes naturels | Non démontrés comme capacités complètes |

Consulter la [matrice des capacités](docs/architecture/CAPABILITY_MATRIX.md) pour les limites par domaine.

## Reconstruction des fondations

La branche `foundation-reset-2026-11` contient la première tranche de reconstruction :

- Modèle de domaine V2 indépendant des moteurs.
- Références d'objets, révisions, artefacts, études et exécutions.
- Quantités physiques avec unités/dimensions et conversions vérifiées.
- Ports spécialisés pour solveurs, géométrie, maillage, stockage, événements, jobs et extensions.
- Transitions de calcul séparant fin du solveur, convergence, vérification, validation et qualification.
- Exécution ECIP dans le processus backend désactivée par défaut : le filtre AST n'est pas un bac à sable.
- Mise à jour paramétrique atomique, sans état géométrique partiellement modifié.
- Première protection du format .mono contre chemins dangereux, liens, manifestes invalides et archives surdimensionnées.
- CI Windows pour Python et CI frontend Bun avec lockfiles.

Ces changements constituent une première tranche, pas la fin de la refonte.

## Architecture et gouvernance

- [Audit des fondations](docs/audit/FOUNDATION_AUDIT.md)
- [Architecture de référence V2](docs/architecture/FOUNDATION_V2.md)
- [Décisions d'architecture](docs/architecture/DECISIONS.md)
- [Matrice des capacités](docs/architecture/CAPABILITY_MATRIX.md)
- [Feuille de reconstruction](docs/architecture/RECONSTRUCTION_ROADMAP.md)

La stratégie retenue est un monolithe modulaire local-first avec workers de calcul isolés. PostgreSQL est la cible pour la source de vérité transactionnelle; l'installation desktop locale doit encore être prototypée. Les artefacts volumineux sont séparés des métadonnées.

## Validation actuelle

Un run Windows CI terminé sur le commit `1ef9a793` a rapporté :

- **111 tests réussis**
- **12 tests ignorés**, car ils nécessitent CalculiX/OrcaSlicer ou une archive de démonstration absente du runner
- **3 avertissements**

Le frontend a également réussi son installation Bun verrouillée et son build. Un run plus récent doit encore confirmer le dernier état de la branche.

Un test ignoré n'est pas une validation du moteur correspondant. Les tests FEA/CFD doivent être exécutés avec les vrais binaires et jeux de référence.

## Environnement

Le projet cible Python 3.12, uv, TypeScript/Bun, Three.js et Tauri 2/Rust. Les moteurs scientifiques externes sont optionnels et doivent être inventoriés, versionnés, licenciés et testés individuellement.

Le rapport matériel fourni précédemment par l'utilisateur est historique; cette session n'a pas inspecté son PC, son GPU, CUDA, ni ses installations locales. Voir l'[audit de l'environnement](docs/audit/FOUNDATION_AUDIT.md).

## Démarrage développeur

Les commandes suivantes supposent que les prérequis du système et les dépendances natives du projet sont disponibles :

```bash
uv sync --locked
uv run pytest -q
cd frontend
bun install --frozen-lockfile
bun run build
```

Le build Tauri et les moteurs externes doivent être testés séparément sur chaque système d'exploitation supporté.

## Sécurité et limites

- Ne pas exécuter de code ECIP non fiable. Le mode historique in-process est désactivé par défaut et reste non isolé même lorsqu'il est activé explicitement.
- Ne pas utiliser les résultats de simulation comme preuve de conception qualifiée sans vérification, validation, incertitudes et domaine d'applicabilité.
- Ne pas libérer de G-code ou trajectoire robot sur une machine réelle sans simulation, validation du post-processeur et contrôle humain.
- Les révisions PLM, la persistance PostgreSQL, la queue durable et le sandbox OS sont encore à implémenter.

## Traçabilité

L'ancien README est conservé dans [docs/legacy/README_HISTORICAL_SPEC.md](docs/legacy/README_HISTORICAL_SPEC.md). Il est archivé comme document historique et ses affirmations ne sont plus la source de vérité du projet.
