# 🛠️ Mono Worker Binaries (`bin/workers`)

Ce répertoire héberge les moteurs autonomes externes sous licence copyleft, strictement isolés par processus afin de préserver la licence permissive libre (**Apache-2.0 / MIT**) du cœur de Mono.

---

## 1. Solveur Éléments Finis CalculiX (`ccx.exe`) — Requis en Phase 4
- **Version :** CalculiX 2.23 (Windows x64) avec solveur direct multithread Spooles.
- **Licence :** GPLv2.
- **Rôle :** Résolution mécanique linéaire-statique ($\mathbf{K} \mathbf{U} = \mathbf{F}$) et calcul du tenseur de contraintes de Cauchy et Von Mises.
- **Emplacement :** `bin/workers/ccx.exe` et `bin/workers/ccx_MT.exe`.
- **Mode d'Appel :** Sous-processus Python isolé via `subprocess.run(["bin/workers/ccx.exe", "-i", job_name])`.

---

## 2. Moteur de Tranchage 3D OrcaSlicer CLI (`orca-slicer.exe`) — Requis en Phase 8
- **Version :** OrcaSlicer v2.4.2 (Windows x64 Portable).
- **Licence :** AGPLv3.
- **Rôle :** Tranchage de modèles 3D pour fabrication additive FDM, calcul de hauteurs de couches adaptatives et génération de G-code machine.
- **Emplacement :** `bin/workers/orcaslicer/orca-slicer.exe`.
- **Mode d'Appel :** Invocation CLI sans tête en sous-processus isolé.
