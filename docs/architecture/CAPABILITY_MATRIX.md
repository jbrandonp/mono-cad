# Mono — Matrice des capacités observées et des écarts
Base : master audité statiquement. Prototype présent ne signifie pas validé ni certifié.

| Domaine | Présence observée | Écart majeur |
|---|---|---|
| CAO 3D B-Rep | Prototype build123d/OCP | Références persistantes, assemblages, PMI, robustesse, validation |
| CAO 2D | Solveur interne et dessins | Couverture contraintes, DOF, conflits, cotation normative |
| Visualisation 3D | Three.js, viewport, Mol* | Champs vectoriels/tensoriels, volumes, séries temporelles, grandes scènes |
| FEA | Gmsh/CalculiX provider | BC sémantiques, non-linéaire, contacts, convergence, VVUQ |
| CFD | Fournisseur OpenFOAM | Binaire, maillage, conservation, benchmarks et validation |
| Electromagnétisme | Non démontré | Solveur FEM/BEM/FDTD et cas de référence |
| Thermique | Partiel/indirect | Modèles et validation dédiés |
| Multibody | Non démontré | Cinématique, contacts, dynamique, conservation |
| Durée de vie matériaux | Non démontré | Fatigue, fluage, fracture, corrosion, vieillissement, UQ |
| Optimisation topologique | Non démontré | Paramétrique, forme, topologie, contraintes de fabrication |
| Process planning | Partiel CAM/slicer | Gammes, ressources, temps, coûts, simulation de procédé |
| Simulation usinage | Non démontré comme simulation machine complète | Enlèvement matière, collisions, gouges, cinématique machine |
| Robotique | Non démontré | Cellule, URDF, singularités, collisions, OLP et post-processeurs |
| PLM | Non démontré | EBOM/MBOM, configurations, révisions, ECO/ECN, release |
| BIM/IFC | Prototype IfcOpenShell | IDS, coordination, conformité et échanges qualifiés |
| Textile | Prototype Taichi/XPBD | Tests GPU, validation physique et performance |
| Bio/molécules | Prototype Gemmi/OpenMM | Ligands, docking, reproductibilité, modèles explicités |
| Science matériaux | Partiel via matériaux FEA | Base versionnée, microstructure, modèles prédictifs |
| Veille/intelligence | Orchestration avec MockLLMProvider | LLM réel, sources, droits, provenance, évaluation |
| Big Data | Non démontré | Ingestion, catalogue, lineage, traitement distribué |
| Systèmes naturels | Non démontré | Modèles, CRS, calibration, observations et validation |
| Multi-agents | Orchestrateur orienté CAD | Concurrence, isolation, conflits et workflows multi-domaines |
| Format .mono | Tar/Zstandard et hashes | Limites, sécurité, fidélité restauration, signatures |
| Persistance PLM | Non démontré | Transactions durables, révisions, outbox, jobs |

Conclusion : Mono est un ensemble de prototypes spécialisés, pas encore une plateforme intégrée. Les capacités doivent être livrées par preuves, domaine par domaine.
