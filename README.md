# Mono — Plateforme multidisciplinaire d'ingénierie et de sciences computationnelles

**État au 26 novembre 2026 : prototype logiciel en reconstruction architecturale.**

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

Consulter la [matrice des capacités](docs/architecture/CAPABILITY_MATRIX.md) et le référentiel V3.1 / E71 dans la Library pour l'état détaillé et les limites par domaine.

## Reconstruction des fondations

La branche `foundation-reset-2026-11` contient la première tranche de reconstruction :

- Contrats Domain Core initiaux, ports d'infrastructure et quantités physiques dimensionnées.
- Références d'objets, révisions, artefacts, études et exécutions.
- Transitions de calcul séparant fin du solveur, convergence, vérification, validation et qualification.
- Mise à jour paramétrique atomique : rollback si une reconstruction dépendante échoue.
- Exécution ECIP dans le processus backend désactivée par défaut : le filtre AST n'est pas un bac à sable.
- Première protection du format .mono contre chemins dangereux, liens, manifestes invalides et archives surdimensionnées.
- CI Python sur Windows, Linux et macOS; build frontend Bun verrouillé.

Ces changements constituent une première tranche, pas la fin de la refonte. Le modèle de persistance, l'outbox, la queue durable, le sandbox OS, l'installateur Tauri, le PLM complet et les solveurs avancés restent à construire.

## Architecture et gouvernance

- [Audit des fondations](docs/audit/FOUNDATION_AUDIT.md)
- [Architecture de référence V2 — historique](docs/architecture/FOUNDATION_V2.md)
- [Décisions d'architecture](docs/architecture/DECISIONS.md)
- [Matrice des capacités](docs/architecture/CAPABILITY_MATRIX.md)
- [Feuille de reconstruction](docs/architecture/RECONSTRUCTION_ROADMAP.md)

La cible proposée est un monolithe modulaire local-first avec des workers de calcul réellement isolés. PostgreSQL est recommandé dans E04 V1.1 et repris comme cible candidate, mais cette décision reste à ratifier par ADR et prototype d'installation/upgrade/backup/restore sur Windows desktop. Les artefacts volumineux doivent être séparés des métadonnées.

## Validation CI vérifiée

<badge>Run 45723972613 · commit 99624cc</badge>

| Environnement | Tests réussis | Ignorés | Avertissements |
|---|---:|---:|---:|
| Windows | 111 | 12 | 3 |
| Linux | 111 | 12 | 3 |
| macOS | 111 | 12 | 3 |

Le build frontend a également réussi avec Bun verrouillé. Le workflow installe libGLU/libGL sur Linux pour les dépendances Gmsh/OpenCASCADE.

Les 12 tests ignorés dans chaque environnement sont explicitement identifiés : neuf tests de validation FEA/CalculiX, un test de présence CalculiX/OrcaSlicer, un solveur CalculiX end-to-end et un test d'archive de démonstration absente. Ces tests ne sont pas simulés et leur absence ne vaut pas validation physique.

Les trois avertissements concernent Taichi (dépréciation Python et séquence d'échappement) et l'usage de httpx avec Starlette TestClient. Le build frontend signale encore l'externalisation Node de fs/crypto par h264-mp4-encoder et un chunk principal d'environ 6 Mo. Le build Tauri, l'exécution dans un navigateur réel, les solveurs externes et l'installation desktop ne sont pas validés par ce workflow.

[Consulter le run CI multiplateforme](https://github.com/jbrandonp/mono-cad/actions/runs/45723972613).

## Environnement développeur

Le projet cible Python 3.12, uv, TypeScript/Bun, Three.js et Tauri 2/Rust. Les moteurs scientifiques externes sont optionnels et doivent être inventoriés, versionnés, licenciés et testés individuellement.

Les commandes suivantes supposent que les prérequis du système et les dépendances natives du projet sont disponibles :

```bash
uv sync --locked
uv run pytest -q -rs
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
- La branche de reconstruction est une PR draft non fusionnée; ce README ne décrit pas master comme contenant ces modifications.

## Traçabilité documentaire

Le dossier Library actif est maintenant MONO_V2_INDEX_MAITRE_v2_41.md, avec le référentiel V3.1 et l'audit transversal E71. E52-E69 sont présents comme bundles de pièces jointes Project mais leur contenu n'a pas pu être lu dans la session; ils restent un blocage de revue documentaire.

L'ancien README et les anciens index sont conservés dans les archives historiques. Le README historique n'est plus la source de vérité du projet.
