# MONO — Contrat de calcul, solveurs et crédibilité scientifique
Version 2.0 — 25 novembre 2026
Statut : proposition normative interne, non ratifiée

## 1. Principe non négociable
Mono ne doit jamais confondre fichier préparé, exécutable lancé, code retour nul, solveur terminé, convergence numérique, vérification, validation et qualification pour un usage. L'interface et les API doivent afficher ces états séparément.

## 2. Solver Registry
Chaque solveur est décrit par un manifest versionné :
- solver_id, vendor/project, version exacte, build, OS/architecture ;
- licence, provenance, SHA-256/signature, SBOM et dépendances ;
- physique supportée, formulation, dimensions, éléments, modèles ;
- unités et conventions ;
- capacités, limitations et cas d'usage exclus ;
- paramètres, critères d'arrêt, tolérances et critères de convergence ;
- ressources CPU/RAM/GPU, parallélisme, durée maximale ;
- formats d'entrée/sortie et parseurs compatibles ;
- jeux de référence, statut de vérification et de validation ;
- contraintes d'export, d'installation et de distribution.

## 3. Cycle de vie d'une étude
1. Draft : définition du problème, objectif, domaine, hypothèses.
2. Input validation : unités, géométrie, matériau, conditions, domaine d'application.
3. Preparation : maillage, fichiers d'entrée, précontrôles.
4. Queued : admission, quota, ressources réservées.
5. Running : exécution isolée, logs, métriques.
6. Solver finished / failed / cancelled / timeout.
7. Extraction : parsing robuste et validation de schéma des résultats.
8. Numerical verification : convergence, bilans, résidus, erreurs estimées.
9. Code verification : benchmark, cas analytique, patch test, tests de régression.
10. Validation : comparaison à un référent expérimental ou de haute fidélité.
11. Uncertainty quantification : paramètres incertains, sensibilité, propagation.
12. Context qualification : domaine d'usage et niveau de risque autorisés.
13. Release : publication immuable du rapport et de ses artefacts.

## 4. Contrat SolverAdapter
Méthodes conceptuelles :
- validate_inputs(study_revision) -> InputValidationReport
- prepare(study_revision, workspace) -> PreparedCase
- execute(prepared_case, resource_profile, cancellation_token) -> ExecutionRecord
- collect_outputs(execution_record) -> ArtifactSet
- parse_results(artifact_set) -> TypedResultSet
- evaluate_convergence(result_set, criteria) -> ConvergenceReport
- verify(result_set, benchmark_suite) -> VerificationReport
- validate(result_set, reference_dataset) -> ValidationReport
- quantify_uncertainty(result_set, uncertainty_model) -> UQReport

Les appels sont idempotents quand possible, traçables et isolés. Les exceptions, timeouts et signaux système deviennent des états structurés.

## 5. Qualité du maillage
Chaque maillage enregistre :
- méthode, ordre, éléments, unités, taille caractéristique ;
- métriques qualité (aspect ratio, skewness, Jacobian, orthogonalité selon domaine) ;
- éléments invalides et éléments dégénérés ;
- sensibilité au raffinement ;
- groupes physiques, surfaces, frontières et identifiants stables ;
- origine géométrique, version et tolérances.
Aucune étude ne passe à l'exécution si les groupes de conditions aux limites sont vides, ambigus ou incohérents.

## 6. Conditions aux limites
- Surfaces/volumes sélectionnés par identifiants sémantiques stables ou références topologiques résolues et vérifiées.
- Les sélections automatiques par bounding box sont des suggestions visibles à l'utilisateur, jamais des substitutions silencieuses.
- Contrôle dimensionnel, signe, repère, type de charge, compatibilité cinématique et équilibre.
- L'étude est bloquée si les supports, charges, interfaces ou contacts sont manquants ou contradictoires.

## 7. VVUQ par discipline
### Structures
- tests analytiques, patch tests, comparaison de solution de référence ;
- convergence au maillage, convergence du solveur, conservation énergie ;
- linéaire/non linéaire, contacts, grandes déformations, plasticité ;
- fatigue, fracture, fluage et durée de vie uniquement après modèles matériaux et données d'essai adaptés.

### CFD / thermique
- qualité et indépendance du maillage ;
- résidus et critères physiques, bilan de masse/énergie/momentum ;
- sensibilité au modèle de turbulence et aux conditions aux limites ;
- validation avec données expérimentales ou benchmark reconnu.

### Electromagnétisme
- vérification des équations, conditions de jauge et conservation ;
- cas analytiques, maillage et pas temporel, bilan d'énergie ;
- validation selon domaine fréquentiel et matériaux.

### Multibody / robotique
- cohérence cinématique, conservation énergie/momentum ;
- détection de singularités, contacts, collisions, tolérances et contraintes ;
- validation trajectoires et post-processeur sur environnement hors ligne.

### Molécules / matériaux
- préparation des structures, protonation, états, champs de force et paramètres explicités ;
- reproductibilité, graines, température, ensemble statistique et convergence ;
- référence expérimentale ou calcul de référence, limites de validité ;
- aucun score de docking ou résultat de dynamique moléculaire ne constitue seul une preuve biologique.

## 8. Résultats et incertitudes
Chaque champ résultat expose : type scalaire/vectoriel/tensoriel, unités, repère, localisation, temps/fréquence, méthode d'interpolation, valeurs manquantes, précision, incertitude et provenance. Le post-traitement est versionné et ne modifie pas les données brutes.

## 9. Niveaux de crédibilité Mono
- M0 — préparé : aucun calcul exécuté.
- M1 — exécuté : processus terminé, pas encore convergence vérifiée.
- M2 — vérifié numériquement : critères et tests de vérification documentés.
- M3 — validé pour un domaine : comparaison à des références appropriées.
- M4 — qualifié pour un contexte d'usage : incertitude, risques et autorisation technique approuvés.

Ces niveaux sont une proposition de gouvernance produit, pas une norme externe ni une certification.

## 10. Références
- NASA-STD-7009B et NASA-HDBK-7009B : crédibilité, vérification, validation, incertitudes, cycle de vie des modèles et simulations.
- ASME VVUQ 1-2022 : terminologie.
- ASME V&V 10-2019 (R2025) : mécanique des solides computationnelle.
- ASME V&V 20 : CFD et transfert thermique.
Liens : https://standards.nasa.gov/standard/nasa/nasa-std-7009 ; https://standards.nasa.gov/standard/nasa/nasa-hdbk-7009 ; https://www.asme.org/codes-standards/publications-information/verification-validation-uncertainty.
