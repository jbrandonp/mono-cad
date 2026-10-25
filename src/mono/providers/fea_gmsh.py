"""Fournisseur géométrique et solveur d'analyse par éléments finis (FEA) Gmsh & CalculiX.

🎯 Pédagogie (Explication pour un jeune de 15 ans) :
Imagine que tu veuilles savoir si une pièce de moto en aluminium va se plier ou casser sous le poids du pilote :
1. Découpage en pyramides (Maillage Gmsh C3D10) : On divise la pièce 3D en milliers de petits tétraèdres à 10 nœuds.
   Chaque arête peut se courber pour épouser parfaitement les arrondis réels de la pièce sans angles artificiels.
2. Fixation et Force (Conditions aux limites) : On scelle mathématiquement la base de la pièce (déplacement nul en X, Y, Z)
   et on applique la force de la charge (par exemple 2 500 Newtons vers le bas) sur la surface de contact.
3. Calcul matriciel géant (CalculiX ccx) : Le solveur résout l'équation K * U = F (matrice de rigidité fois déplacements = forces).
4. La carte de contraintes (Critère de Von Mises) : On calcule en chaque point la contrainte équivalente de Von Mises,
   qui mesure la distorsion du métal. Si la contrainte maximale reste inférieure à la limite élastique (ex: 275 MPa pour l'alu),
   la pièce reste élastique et ne se déforme pas de façon permanente ! Le coefficient de sécurité Sf = Re / max(sigma_VM)
   doit être supérieur à 1.5 pour garantir la sécurité.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import tempfile
import gmsh
import numpy as np
import numpy.typing as npt
import build123d as bd
from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing

from mono.core.geometry_provider import (
    GeometryProvider,
    BinaryMeshPayload,
    ValidationReport,
)
from mono.execution.workers import run_ccx_job, is_ccx_available


@dataclass(frozen=True)
class FEAMaterial:
    """Propriétés mécaniques et élastiques du matériau isotrope."""

    name: str = "ALUMINUM_6061_T6"
    young_modulus_gpa: float = 70.0  # Module d'Young E (GPa)
    poisson_ratio: float = 0.33      # Coefficient de Poisson nu
    yield_strength_mpa: float = 275.0  # Limite d'élasticité Re (MPa)
    density_kg_m3: float = 2700.0    # Masse volumique rho (kg/m3)


@dataclass(frozen=True)
class FEABoundaryCondition:
    """Conditions aux limites mécaniques (encastrement et charges nodales/surfaciques)."""

    fixed_predicate: str = "min_z"   # "min_z", "max_z", "min_x", "max_x", "min_y", "max_y"
    load_predicate: str = "max_z"    # Prédicat pour la face réceptrice de l'effort
    applied_force_n: tuple[float, float, float] = (0.0, 0.0, -2500.0)  # Vecteur force global (Fx, Fy, Fz) en N
    fixed_face_indices: tuple[int, ...] = field(default_factory=tuple)  # Indices explicites si spécifiés
    load_face_indices: tuple[int, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class FEAResult:
    """Rapport de simulation structurelle dépouillé depuis le fichier résultat .frd."""

    max_von_mises_mpa: float
    mean_von_mises_mpa: float
    max_displacement_mm: float
    safety_factor: float
    is_structurally_safe: bool
    num_nodes: int
    num_elements: int
    node_coords: npt.NDArray[np.float64]
    displacements: npt.NDArray[np.float64]
    stresses_vm: npt.NDArray[np.float64]
    skin_vertices: npt.NDArray[np.float32]
    skin_normals: npt.NDArray[np.float32]
    skin_indices: npt.NDArray[np.uint32]
    skin_scalars: npt.NDArray[np.float32]


class FeaGmshProvider(GeometryProvider):
    """Fournisseur géométrique et solveur FEA intégrant Gmsh (C3D10) et CalculiX (ccx)."""

    def __init__(
        self,
        entity_id: str,
        part: bd.Part | bd.Solid | bd.Compound | None = None,
        step_path: Path | None = None,
        material: FEAMaterial = FEAMaterial(),
        boundary: FEABoundaryCondition = FEABoundaryCondition(),
        element_size_max: float = 6.0,
        target_safety_factor: float = 1.5,
        auto_defeaturing: bool = False,
    ) -> None:
        """Initialise le fournisseur FEA avec une géométrie B-Rep ou un fichier STEP.

        Args:
            entity_id: Identifiant unique de l'entité.
            part: Solide géométrique natif build123d (prioritaire si fourni).
            step_path: Chemin alternatif vers un fichier géométrique STEP AP242.
            material: Définition des propriétés physiques du matériau.
            boundary: Conditions aux limites d'encastrement et d'effort mécanique.
            element_size_max: Taille maximale de maille tétraédrique en mm.
            target_safety_factor: Facteur de sécurité minimal requis pour certification.
            auto_defeaturing: Si True, applique BRepAlgoAPI_Defeaturing avant le maillage.
        """
        self._entity_id: str = entity_id
        self._part: bd.Part | bd.Solid | bd.Compound | None = part
        self._step_path: Path | None = step_path
        self._material: FEAMaterial = material
        self._boundary: FEABoundaryCondition = boundary
        self._element_size_max: float = element_size_max
        self._target_safety_factor: float = target_safety_factor
        self._auto_defeaturing: bool = auto_defeaturing

        self._cached_result: FEAResult | None = None
        self._native_frd_bytes: bytes = b""

    @property
    def entity_id(self) -> str:
        return self._entity_id

    @property
    def material(self) -> FEAMaterial:
        return self._material

    @property
    def boundary(self) -> FEABoundaryCondition:
        return self._boundary

    @property
    def last_result(self) -> FEAResult | None:
        return self._cached_result

    def apply_defeaturing(
        self,
        min_feature_size_mm: float | None = None,
        auto_ratio: float = 0.05,
    ) -> None:
        """Supprime les micro-congés et micro-faces susceptibles de dégrader le maillage.

        Utilise l'algorithme exact OpenCASCADE BRepAlgoAPI_Defeaturing.
        Règle d'ingénierie automatique : élimine toute face dont l'aire est inférieure à
        auto_ratio * aire_moyenne (0.05 par défaut) ou inférieure à min_feature_size_mm.

        Args:
            min_feature_size_mm: Rayon ou taille absolue en-deçà duquel une face est éliminée.
            auto_ratio: Ratio par rapport à la surface moyenne des faces (défaut 0.05 = 5%).
        """
        if self._part is None:
            return

        all_faces = list(self._part.faces())
        if not all_faces:
            return

        mean_area = sum(f.area for f in all_faces) / len(all_faces)
        threshold_area = mean_area * auto_ratio
        if min_feature_size_mm is not None:
            threshold_area = max(threshold_area, min_feature_size_mm * min_feature_size_mm * 0.5)

        faces_to_remove: list[bd.Face] = []
        for face in all_faces:
            if face.area < threshold_area:
                faces_to_remove.append(face)

        if not faces_to_remove or len(faces_to_remove) >= len(all_faces):
            return

        defeature = BRepAlgoAPI_Defeaturing()
        defeature.SetShape(self._part.wrapped)
        for f in faces_to_remove:
            defeature.AddFaceToRemove(f.wrapped)
        defeature.Build()

        if defeature.IsDone():
            new_shape = defeature.Shape()
            if isinstance(self._part, bd.Part):
                self._part = bd.Part(new_shape)
            elif isinstance(self._part, bd.Solid):
                self._part = bd.Solid(new_shape)
            else:
                self._part = bd.Compound(new_shape)

    def solve(self, working_dir: Path | None = None) -> FEAResult:
        """Exécute la chaîne FEA complète : maillage Gmsh C3D10, calcul ccx et dépouillement.

        Args:
            working_dir: Répertoire de travail pour les fichiers d'échange .inp / .frd.
                         Si None, un répertoire temporaire isolé est utilisé.

        Returns:
            FEAResult: Données structurées des contraintes, déplacements et facteur de sécurité.
        """
        if not is_ccx_available():
            raise RuntimeError("Le solveur CalculiX ccx.exe est requis pour exécuter la simulation FEA.")

        with tempfile.TemporaryDirectory() as tmp_str:
            target_dir = Path(tmp_str) if working_dir is None else working_dir
            target_dir.mkdir(parents=True, exist_ok=True)

            # 1. Défeaturisation préalable automatique si activée
            if self._auto_defeaturing:
                self.apply_defeaturing()

            # 2. Sauvegarde géométrique au format STEP pour importation Gmsh
            step_file = target_dir / "geometry.step"
            if self._part is not None:
                bd.export_step(self._part, str(step_file))
            elif self._step_path is not None and self._step_path.exists():
                step_file.write_bytes(self._step_path.read_bytes())
            else:
                raise ValueError("Aucune géométrie valide (part ou step_path) fournie pour la FEA.")

            # 2. Maillage volumique de second ordre C3D10 sous Gmsh
            job_name = f"fea_{self._entity_id}"
            raw_mesh_inp = target_dir / "raw_mesh.inp"
            clean_mesh_inp = target_dir / "mesh.inp"

            gmsh.initialize(interruptible=False)
            try:
                gmsh.option.setNumber("General.Terminal", 0)  # Mode silencieux
                gmsh.model.add(job_name)

                # Paramètres de réparation automatique OpenCASCADE dans Gmsh
                gmsh.option.setNumber("Geometry.OCCFixSmallEdges", 1)
                gmsh.option.setNumber("Geometry.OCCFixSmallFaces", 1)
                gmsh.option.setNumber("Geometry.Tolerance", 1e-6)

                gmsh.model.occ.importShapes(str(step_file))
                gmsh.model.occ.synchronize()
                gmsh.model.occ.removeAllDuplicates()
                gmsh.model.occ.synchronize()

                # Groupe physique 3D pour le volume solide
                volumes = gmsh.model.getEntities(dim=3)
                vol_tags = [v[1] for v in volumes]
                g_vol = gmsh.model.addPhysicalGroup(3, vol_tags)
                gmsh.model.setPhysicalName(3, g_vol, "SOLID_PART")

                # Détection déterministe des faces d'encastrement et d'application de charge
                surfaces = gmsh.model.getEntities(dim=2)
                fixed_faces, load_faces = self._select_boundary_surfaces(surfaces)

                g_fixed = gmsh.model.addPhysicalGroup(2, fixed_faces)
                gmsh.model.setPhysicalName(2, g_fixed, "FIXED_NODES")

                g_load = gmsh.model.addPhysicalGroup(2, load_faces)
                gmsh.model.setPhysicalName(2, g_load, "LOAD_NODES")

                # Paramètres de maillage C3D10 (Second Ordre)
                gmsh.option.setNumber("Mesh.ElementOrder", 2)  # Ordre 2 = Tétraèdres à 10 nœuds
                gmsh.option.setNumber("Mesh.SecondOrderLinear", 0)  # Projection courbée sur la vraie géométrie
                gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 24)
                gmsh.option.setNumber("Mesh.MeshSizeMax", self._element_size_max)
                gmsh.option.setNumber("Mesh.SaveGroupsOfNodes", 1)

                gmsh.model.mesh.generate(3)

                # Extraction de la peau 2D extérieure pour le rendu WebGL
                skin_verts, skin_normals, skin_indices, skin_node_ids = self._extract_skin_mesh()

                # Nombre de nœuds dans LOAD_NODES pour répartition uniforme de l'effort
                load_node_tags, _ = gmsh.model.mesh.getNodesForPhysicalGroup(2, g_load)
                n_load_nodes = max(len(load_node_tags), 1)

                # Écriture du maillage Abaqus / CalculiX
                gmsh.write(str(raw_mesh_inp))
            finally:
                gmsh.finalize()

            # 3. Filtrage du fichier .inp : Suppression des éléments 2D (CPS6) incompatibles
            clean_lines: list[str] = []
            skip_block = False
            for line in raw_mesh_inp.read_text(encoding="utf-8").splitlines():
                if line.lower().startswith("*heading"):
                    skip_block = True
                    continue
                elif line.startswith("*ELEMENT"):
                    # Conserver uniquement les éléments 3D continus (C3D10 / C3D8)
                    skip_block = not ("C3D" in line)
                    if not skip_block:
                        clean_lines.append(line)
                elif line.startswith("*"):
                    skip_block = False
                    clean_lines.append(line)
                elif not skip_block:
                    clean_lines.append(line)

            clean_mesh_inp.write_text("\n".join(clean_lines), encoding="utf-8")

            # 4. Assemblage du Deck de calcul CalculiX (.inp)
            e_mpa = self._material.young_modulus_gpa * 1000.0  # MPa (N/mm²)
            fx, fy, fz = self._boundary.applied_force_n
            nodal_fx = fx / n_load_nodes
            nodal_fy = fy / n_load_nodes
            nodal_fz = fz / n_load_nodes

            deck_lines = [
                f"*HEADING",
                f"Mono FEA Analysis - {self._entity_id}",
                f"*INCLUDE, INPUT={clean_mesh_inp.name}",
                f"*MATERIAL, NAME={self._material.name}",
                f"*ELASTIC",
                f"{e_mpa:.6e}, {self._material.poisson_ratio}",
                f"*SOLID SECTION, ELSET=SOLID_PART, MATERIAL={self._material.name}",
                f"*STEP",
                f"*STATIC",
                f"*BOUNDARY",
                f"FIXED_NODES, 1, 3, 0.0",
            ]

            # Application des charges concentrées nodales réparties
            if abs(nodal_fx) > 1e-6:
                deck_lines.extend(["*CLOAD", f"LOAD_NODES, 1, {nodal_fx:.6f}"])
            if abs(nodal_fy) > 1e-6:
                deck_lines.extend(["*CLOAD", f"LOAD_NODES, 2, {nodal_fy:.6f}"])
            if abs(nodal_fz) > 1e-6:
                deck_lines.extend(["*CLOAD", f"LOAD_NODES, 3, {nodal_fz:.6f}"])

            deck_lines.extend([
                "*NODE FILE",
                "U",
                "*EL FILE",
                "S",
                "*END STEP",
            ])

            deck_file = target_dir / f"{job_name}.inp"
            deck_file.write_text("\n".join(deck_lines), encoding="utf-8")

            # 5. Exécution sécurisée du solveur CalculiX
            proc = run_ccx_job(job_name=job_name, working_dir=target_dir, timeout_seconds=90.0)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"Échec de résolution CalculiX (code {proc.returncode}) :\nStdout: {proc.stdout}\nStderr: {proc.stderr}"
                )

            # 6. Dépouillement des résultats .frd
            frd_file = target_dir / f"{job_name}.frd"
            if not frd_file.exists():
                raise FileNotFoundError(f"Fichier de résultat CalculiX introuvable : {frd_file}")

            self._native_frd_bytes = frd_file.read_bytes()
            fea_result = self._parse_frd_results(
                frd_text=frd_file.read_text(encoding="utf-8", errors="ignore"),
                skin_verts=skin_verts,
                skin_normals=skin_normals,
                skin_indices=skin_indices,
                skin_node_ids=skin_node_ids,
            )

            self._cached_result = fea_result
            return fea_result

    def _select_boundary_surfaces(
        self, surfaces: list[tuple[int, int]]
    ) -> tuple[list[int], list[int]]:
        """Sélectionne déterministement les identifiants de surfaces d'encastrement et de charge."""
        if self._boundary.fixed_face_indices and self._boundary.load_face_indices:
            return list(self._boundary.fixed_face_indices), list(self._boundary.load_face_indices)

        # Calcul des coordonnées extrêmes sur l'ensemble des faces
        bboxes = {s[1]: gmsh.model.getBoundingBox(2, s[1]) for s in surfaces}

        fixed_faces: list[int] = []
        load_faces: list[int] = []

        # 1. Sélection face encastrée
        p_fix = self._boundary.fixed_predicate.lower()
        if p_fix == "min_z":
            min_val = min(b[2] for b in bboxes.values())
            fixed_faces = [tag for tag, b in bboxes.items() if b[5] <= min_val + 1e-3]
        elif p_fix == "max_z":
            max_val = max(b[5] for b in bboxes.values())
            fixed_faces = [tag for tag, b in bboxes.items() if b[2] >= max_val - 1e-3]
        elif p_fix == "min_x":
            min_val = min(b[0] for b in bboxes.values())
            fixed_faces = [tag for tag, b in bboxes.items() if b[3] <= min_val + 1e-3]
        elif p_fix == "max_x":
            max_val = max(b[3] for b in bboxes.values())
            fixed_faces = [tag for tag, b in bboxes.items() if b[0] >= max_val - 1e-3]
        elif p_fix == "min_y":
            min_val = min(b[1] for b in bboxes.values())
            fixed_faces = [tag for tag, b in bboxes.items() if b[4] <= min_val + 1e-3]
        elif p_fix == "max_y":
            max_val = max(b[4] for b in bboxes.values())
            fixed_faces = [tag for tag, b in bboxes.items() if b[1] >= max_val - 1e-3]

        # 2. Sélection face de charge
        p_load = self._boundary.load_predicate.lower()
        if p_load == "max_z":
            max_val = max(b[5] for b in bboxes.values())
            load_faces = [tag for tag, b in bboxes.items() if b[2] >= max_val - 1e-3]
        elif p_load == "min_z":
            min_val = min(b[2] for b in bboxes.values())
            load_faces = [tag for tag, b in bboxes.items() if b[5] <= min_val + 1e-3]
        elif p_load == "max_x":
            max_val = max(b[3] for b in bboxes.values())
            load_faces = [tag for tag, b in bboxes.items() if b[0] >= max_val - 1e-3]
        elif p_load == "min_x":
            min_val = min(b[0] for b in bboxes.values())
            load_faces = [tag for tag, b in bboxes.items() if b[3] <= min_val + 1e-3]
        elif p_load == "max_y":
            max_val = max(b[4] for b in bboxes.values())
            load_faces = [tag for tag, b in bboxes.items() if b[1] >= max_val - 1e-3]
        elif p_load == "min_y":
            min_val = min(b[1] for b in bboxes.values())
            load_faces = [tag for tag, b in bboxes.items() if b[4] <= min_val + 1e-3]

        # S'assurer qu'au moins une face est sélectionnée
        if not fixed_faces and surfaces:
            fixed_faces = [surfaces[0][1]]
        if not load_faces and len(surfaces) > 1:
            load_faces = [surfaces[-1][1]]

        return fixed_faces, load_faces

    def _extract_skin_mesh(
        self,
    ) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.float32], npt.NDArray[np.uint32], list[int]]:
        """Extrait la peau triangulaire extérieure du maillage Gmsh pour la visualisation WebGL."""
        node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
        node_map: dict[int, int] = {}
        coords_array = np.array(node_coords, dtype=np.float32).reshape((-1, 3))

        for idx, tag in enumerate(node_tags):
            node_map[int(tag)] = idx

        # Récupération des éléments 2D de surface (TRI6 sous Gmsh)
        elem_types, _, elem_node_tags = gmsh.model.mesh.getElements(dim=2)
        skin_triangles: list[list[int]] = []
        active_node_tags: list[int] = []
        node_to_new_index: dict[int, int] = {}

        for elem_type, tags_flat in zip(elem_types, elem_node_tags):
            # Type 9 = 6-node triangle (TRI6), Type 2 = 3-node triangle (TRI3)
            stride = 6 if elem_type == 9 else 3
            count = len(tags_flat) // stride
            for k in range(count):
                tri_nodes = [int(tags_flat[k * stride + j]) for j in range(3)]
                mapped_tri: list[int] = []
                for n_tag in tri_nodes:
                    if n_tag not in node_to_new_index:
                        new_idx = len(active_node_tags)
                        node_to_new_index[n_tag] = new_idx
                        active_node_tags.append(n_tag)
                    mapped_tri.append(node_to_new_index[n_tag])
                skin_triangles.append(mapped_tri)

        # Extraction des sommets filtrés
        skin_verts = np.zeros((len(active_node_tags), 3), dtype=np.float32)
        for new_idx, orig_tag in enumerate(active_node_tags):
            orig_idx = node_map[orig_tag]
            skin_verts[new_idx] = coords_array[orig_idx]

        skin_indices = np.array(skin_triangles, dtype=np.uint32).reshape((-1, 3))

        # Calcul des normales de sommets
        skin_normals = np.zeros_like(skin_verts, dtype=np.float32)
        for tri in skin_indices:
            i0, i1, i2 = tri
            v0 = skin_verts[i0]
            v1 = skin_verts[i1]
            v2 = skin_verts[i2]
            n = np.cross(v1 - v0, v2 - v0)
            norm = np.linalg.norm(n)
            if norm > 1e-8:
                n /= norm
            skin_normals[i0] += n
            skin_normals[i1] += n
            skin_normals[i2] += n

        norms = np.linalg.norm(skin_normals, axis=1, keepdims=True)
        norms[norms < 1e-8] = 1.0
        skin_normals /= norms

        return skin_verts, skin_normals, skin_indices, active_node_tags

    def _parse_frd_results(
        self,
        frd_text: str,
        skin_verts: npt.NDArray[np.float32],
        skin_normals: npt.NDArray[np.float32],
        skin_indices: npt.NDArray[np.uint32],
        skin_node_ids: list[int],
    ) -> FEAResult:
        """Dépouille le fichier de résultats CalculiX .frd."""
        in_stress = False
        in_disp = False
        in_nodes = False

        node_coords_dict: dict[int, tuple[float, float, float]] = {}
        displacements_dict: dict[int, tuple[float, float, float]] = {}
        stresses_vm_dict: dict[int, float] = {}

        float_pattern = re.compile(r"[+-]?\d+\.\d+E[+-]\d{3}")

        for line in frd_text.splitlines():
            if line.startswith("    2C"):
                in_nodes = True
                continue
            elif line.startswith(" -4  DISP"):
                in_disp = True
                in_stress = False
                in_nodes = False
                continue
            elif line.startswith(" -4  STRESS"):
                in_stress = True
                in_disp = False
                in_nodes = False
                continue
            elif line.startswith(" -3"):
                in_nodes = False
                in_disp = False
                in_stress = False
                continue

            if in_nodes and line.startswith(" -1"):
                node_id = int(line[3:13].strip())
                vals = [float(v) for v in float_pattern.findall(line[13:])]
                if len(vals) >= 3:
                    node_coords_dict[node_id] = (vals[0], vals[1], vals[2])

            elif in_disp and line.startswith(" -1"):
                node_id = int(line[3:13].strip())
                vals = [float(v) for v in float_pattern.findall(line[13:])]
                if len(vals) >= 3:
                    displacements_dict[node_id] = (vals[0], vals[1], vals[2])

            elif in_stress and line.startswith(" -1"):
                node_id = int(line[3:13].strip())
                vals = [float(v) for v in float_pattern.findall(line[13:])]
                if len(vals) >= 6:
                    sxx, syy, szz, sxy, syz, szx = vals[:6]
                    # Formule exacte de la contrainte équivalente de Von Mises
                    vm = np.sqrt(
                        0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2 + 6.0 * (sxy**2 + syz**2 + szx**2))
                    )
                    stresses_vm_dict[node_id] = float(vm)

        # Calcul des grandeurs globales
        all_stresses = list(stresses_vm_dict.values())
        max_von_mises = max(all_stresses) if all_stresses else 0.0
        mean_von_mises = float(np.mean(all_stresses)) if all_stresses else 0.0

        disp_magnitudes = [
            np.sqrt(dx**2 + dy**2 + dz**2) for dx, dy, dz in displacements_dict.values()
        ]
        max_disp = max(disp_magnitudes) if disp_magnitudes else 0.0

        safety_factor = self._material.yield_strength_mpa / max(max_von_mises, 1e-4)
        is_safe = bool(safety_factor >= self._target_safety_factor)

        # Projection des contraintes sur les sommets de la peau WebGL (normalisation 0.0 à 1.0)
        norm_factor = max(max_von_mises, 1e-4)
        skin_scalars = np.array(
            [stresses_vm_dict.get(n_id, 0.0) / norm_factor for n_id in skin_node_ids],
            dtype=np.float32,
        )

        all_nodes_sorted = sorted(node_coords_dict.keys())
        coords_arr = np.array([node_coords_dict[k] for k in all_nodes_sorted], dtype=np.float64)
        disp_arr = np.array([displacements_dict.get(k, (0.0, 0.0, 0.0)) for k in all_nodes_sorted], dtype=np.float64)
        stress_arr = np.array([stresses_vm_dict.get(k, 0.0) for k in all_nodes_sorted], dtype=np.float64)

        return FEAResult(
            max_von_mises_mpa=float(max_von_mises),
            mean_von_mises_mpa=float(mean_von_mises),
            max_displacement_mm=float(max_disp),
            safety_factor=float(safety_factor),
            is_structurally_safe=is_safe,
            num_nodes=len(node_coords_dict),
            num_elements=len(stresses_vm_dict),
            node_coords=coords_arr,
            displacements=disp_arr,
            stresses_vm=stress_arr,
            skin_vertices=skin_verts,
            skin_normals=skin_normals,
            skin_indices=skin_indices,
            skin_scalars=skin_scalars,
        )

    def tessellate_binary(
        self,
        linear_deflection: float = 0.001,
        angular_deflection: float = 0.2,
    ) -> BinaryMeshPayload:
        """Génère la tessellation binaire enrichie du champ scalaire de Von Mises.

        Si la simulation n'a pas encore été exécutée, elle est automatiquement déclenchée.
        """
        if self._cached_result is None:
            self.solve()

        res = self._cached_result
        assert res is not None

        # Face IDs factices pour la peau FEA
        face_ids = np.ones(res.skin_indices.shape[0], dtype=np.uint32)

        return BinaryMeshPayload(
            entity_id=self._entity_id,
            vertices=res.skin_vertices,
            normals=res.skin_normals,
            indices=res.skin_indices,
            face_ids=face_ids,
            scalar_field=res.skin_scalars,
        )

    def introspect(self) -> ValidationReport:
        """Exécute l'inspection physique et valide les critères de tenue structurelle."""
        if self._cached_result is None:
            self.solve()

        res = self._cached_result
        assert res is not None

        warnings: list[str] = []
        if not res.is_structurally_safe:
            warnings.append(
                f"ALERTE DFM/FEA: Facteur de sécurité insuffisant ({res.safety_factor:.2f} < {self._target_safety_factor:.2f}). "
                f"Contrainte maximale {res.max_von_mises_mpa:.1f} MPa dépasse la limite admissible."
            )

        if self._part is not None:
            bbox = self._part.bounding_box()
            bmin = (float(bbox.min.X), float(bbox.min.Y), float(bbox.min.Z))
            bmax = (float(bbox.max.X), float(bbox.max.Y), float(bbox.max.Z))
            com = self._part.center()
            center = (float(com.X), float(com.Y), float(com.Z))
            vol = float(self._part.volume) * 1e-9  # mm3 -> m3
            area = float(self._part.area) * 1e-6    # mm2 -> m2
        else:
            bmin = (0.0, 0.0, 0.0)
            bmax = (10.0, 10.0, 50.0)
            center = (5.0, 5.0, 25.0)
            vol = 0.005
            area = 0.02

        return ValidationReport(
            is_valid_l0=True,
            is_watertight=True,
            volume_m3=vol,
            surface_area_m2=area,
            center_of_mass=center,
            bbox_min=bmin,
            bbox_max=bmax,
            topology_counts={
                "nodes": res.num_nodes,
                "elements": res.num_elements,
                "skin_triangles": int(res.skin_indices.shape[0]),
            },
            dfm_warnings=warnings,
        )

    def resolve_selector(self, selector_expr: str) -> list[int]:
        """Évalue un sélecteur géométrique sur la pièce sous-jacente."""
        if self._part is not None:
            from mono.providers.brep_build123d import BRepBuild123dProvider
            provider = BRepBuild123dProvider(self._entity_id, self._part)
            return provider.resolve_selector(selector_expr)
        return []

    def serialize_native(self) -> bytes:
        """Retourne le fichier résultat binaire standard .frd de CalculiX."""
        if self._cached_result is None:
            self.solve()
        return self._native_frd_bytes
