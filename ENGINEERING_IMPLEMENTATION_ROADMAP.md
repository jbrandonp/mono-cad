# Mono CAD Studio — plan d'implémentation par incréments
Date : 25 novembre 2026
Branche : rebaseline/engineering-platform-2026-11
Statut : plan de travail, non exécuté sauf changements explicitement committés

## Incrément 0 — Assainissement de la vérité produit
- Corriger CFD : aucun état convergé si aucun solveur n'a tourné; séparer estimation et solution CFD.
- Ajouter un état explicite pour solveur absent, échec, résultat inconnu, non-convergence et convergence démontrée.
- Ne jamais remplacer silencieusement une sortie de solveur par une approximation présentée comme résultat exact.
- Corriger le générateur G-code fallback : soit l'identifier strictement comme démonstration non fabricable, soit interdire son export machine.
- Exposer la méthode OpenMM utilisée (Amber tout-atome vs ENM fallback).
- Implémenter ou retirer les types de contraintes 2D non pris en charge.
- Déclarer les dépendances directes réellement importées.
- Ajouter CI et rapprocher le nombre de tests annoncé du nombre exécuté.

## Incrément 1 — Fondations CAO/CAE
- Modèle d'assemblage, références, contraintes, interfaces de pièces, contacts et cinématique.
- Matériaux versionnés, unités et provenance.
- Contrats de solveur, registre, gestion de jobs, logs et résultats.
- Validation de maillage et étude de convergence.

## Incrément 2 — Durabilité et optimisation
- Analyse modale, flambement, non-linéaire et thermo-mécanique.
- Fatigue (courbes S-N/e-N, règle de Miner, cycles, spectres), fracture et durée de vie.
- Optimisation de forme et topologie avec contraintes de fabrication.
- DOE, optimisation multiobjectif et robustesse.

## Incrément 3 — Physique multiphysique
- CFD de production (maillage, qualité, solveur, résidus, conservation, post-traitement).
- Électromagnétisme (électrostatique, magnétostatique, harmonique/transitoire, couplage).
- Dynamique multicorps (cinématique, dynamique, contacts, articulations, actionneurs).
- Acoustique et couplages thermiques/structurels selon priorité.

## Incrément 4 — Fabrication et robotique
- Process planning, opérations, gammes et ressources.
- CAM avec modèle stock, outils, bridages, enlèvement de matière, collisions et cinématique machine.
- Slicing fiable via moteur réel; aucun G-code géométriquement fictif ne peut être qualifié.
- Programmation robotique hors ligne, cinématique, planification de trajectoire, singularités, collisions et post-processeurs.

## Incrément 5 — PLM
- Exigences, variantes, EBOM/MBOM, configuration produit, révisions, ECO/ECN, approbations, qualité et traçabilité.

## Incrément 6 — Science et données
- Découverte moléculaire, docking, scoring, simulation et chimie computationnelle.
- Science des matériaux et prédiction de propriétés avec provenance.
- Veille scientifique/industrielle.
- Plateforme Big Data.
- Modèles de systèmes naturels par discipline.

## Règle de livraison
Chaque incrément doit comporter : spécification, code, tests automatisés, jeux de référence, preuve d'exécution, documentation utilisateur, limites connues et revue technique. Ne pas annoncer une fonctionnalité comme « implémentée » avant que ces éléments minimaux soient disponibles.
