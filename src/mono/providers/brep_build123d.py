from __future__ import annotations

import tempfile
from pathlib import Path
import numpy as np
import numpy.typing as npt
import build123d as bd
from OCP.BRepCheck import BRepCheck_Analyzer

from mono.core.geometry_provider import (
    GeometryProvider,
    BinaryMeshPayload,
    ValidationReport,
)


class BRepBuild123dProvider(GeometryProvider):
    """Fournisseur géométrique CAO B-Rep fondé sur build123d et OpenCASCADE (OCP).

    Génère des pièces mécaniques exactes, contrôle la validité topologique L0/L1,
    calcule la tessellation binaire pour WebGL et évalue les sélecteurs sémantiques.
    """

    def __init__(
        self,
        entity_id: str,
        part: bd.Part | bd.Solid | bd.Compound,
        unit_scale_to_meters: float = 0.001,
    ) -> None:
        """Initialise le fournisseur B-Rep avec un solide build123d.

        Args:
            entity_id: Identifiant unique de l'entité dans le document.
            part: Solide géométrique exact build123d.
            unit_scale_to_meters: Facteur de conversion des unités CAD vers le mètre SI (ex: 0.001 pour mm -> m).
        """
        self._entity_id: str = entity_id
        self._part: bd.Part | bd.Solid | bd.Compound = part
        self._scale: float = unit_scale_to_meters

    @property
    def entity_id(self) -> str:
        """Identifiant unique de l'entité."""
        return self._entity_id

    @property
    def part(self) -> bd.Part | bd.Solid | bd.Compound:
        """Objet géométrique natif build123d."""
        return self._part

    def tessellate_binary(
        self,
        linear_deflection: float = 0.001,
        angular_deflection: float = 0.2,
    ) -> BinaryMeshPayload:
        """Décompose le solide B-Rep en triangles et produit le flux binaire zero-copy.

        Chaque face du solide est maillée individuellement afin d'associer
        rigoureusement chaque triangle à l'identifiant topologique de sa face d'origine.

        Args:
            linear_deflection: Tolérance de flèche linéaire en unités du modèle.
            angular_deflection: Tolérance de flèche angulaire en radians.

        Returns:
            BinaryMeshPayload: Tableaux NumPy de sommets, normales, indices et face_ids.
        """
        all_vertices: list[list[float]] = []
        all_normals: list[list[float]] = []
        all_indices: list[list[int]] = []
        all_face_ids: list[int] = []

        faces = self._part.faces()
        vertex_offset = 0

        # Centre géométrique pour le rendu RTC (Relative-To-Center) local
        center = self._part.center()
        cx, cy, cz = float(center.X), float(center.Y), float(center.Z)

        for face_idx, face in enumerate(faces):
            try:
                face_verts, face_tris = face.tessellate(linear_deflection)
            except Exception:
                continue

            if not face_verts or not face_tris:
                continue

            # Calcul ou interpolation des normales
            face_norm_vec = face.normal_at(face.center())
            fnx, fny, fnz = float(face_norm_vec.X), float(face_norm_vec.Y), float(face_norm_vec.Z)

            for vert in face_verts:
                # Coordonnées relatives centrées en unités locales
                vx = float(vert.X) - cx
                vy = float(vert.Y) - cy
                vz = float(vert.Z) - cz
                all_vertices.append([vx, vy, vz])

                # Pour les surfaces courbes, on calcule la normale locale au sommet si possible
                try:
                    norm = face.normal_at(vert)
                    all_normals.append([float(norm.X), float(norm.Y), float(norm.Z)])
                except Exception:
                    all_normals.append([fnx, fny, fnz])

            for tri in face_tris:
                all_indices.append([
                    tri[0] + vertex_offset,
                    tri[1] + vertex_offset,
                    tri[2] + vertex_offset,
                ])
                all_face_ids.append(face_idx)

            vertex_offset += len(face_verts)

        if not all_vertices:
            # En cas de forme vide ou dégénérée, tableau vide valide
            np_verts = np.empty((0, 3), dtype=np.float32)
            np_norms = np.empty((0, 3), dtype=np.float32)
            np_indices = np.empty((0, 3), dtype=np.uint32)
            np_face_ids = np.empty((0,), dtype=np.uint32)
        else:
            np_verts = np.ascontiguousarray(all_vertices, dtype=np.float32)
            np_norms = np.ascontiguousarray(all_normals, dtype=np.float32)
            np_indices = np.ascontiguousarray(all_indices, dtype=np.uint32)
            np_face_ids = np.ascontiguousarray(all_face_ids, dtype=np.uint32)

        return BinaryMeshPayload(
            entity_id=self._entity_id,
            vertices=np_verts,
            normals=np_norms,
            indices=np_indices,
            face_ids=np_face_ids,
        )

    def introspect(self) -> ValidationReport:
        """Contrôle déterministe des portes L0 (intégrité B-Rep) et L1 (DFM).

        Returns:
            ValidationReport: Rapport synthétique complet.
        """
        shape = self._part.wrapped
        analyzer = BRepCheck_Analyzer(shape)
        is_valid_occt = bool(analyzer.IsValid())

        # Conversion vers le système international d'unités (mètres)
        scale = self._scale
        vol_m3 = float(self._part.volume) * (scale ** 3)
        area_m2 = float(self._part.area) * (scale ** 2)

        center = self._part.center()
        com = (
            float(center.X) * scale,
            float(center.Y) * scale,
            float(center.Z) * scale,
        )

        bbox = self._part.bounding_box()
        b_min = (
            float(bbox.min.X) * scale,
            float(bbox.min.Y) * scale,
            float(bbox.min.Z) * scale,
        )
        b_max = (
            float(bbox.max.X) * scale,
            float(bbox.max.Y) * scale,
            float(bbox.max.Z) * scale,
        )

        faces = self._part.faces()
        edges = self._part.edges()
        vertices = self._part.vertices()
        solids = self._part.solids()

        topo_counts: dict[str, int] = {
            "solids": len(solids),
            "faces": len(faces),
            "edges": len(edges),
            "vertices": len(vertices),
        }

        # Porte L0 : Forme valide OCCT, volume positif et solide fermé
        is_watertight = is_valid_occt and vol_m3 > 1e-15 and self._part.is_valid
        is_l0 = is_valid_occt and is_watertight

        # Porte L1 : Vérifications de fabricabilité (DFM)
        dfm_warnings: list[str] = []

        # 1. Vérification de l'épaisseur minimale de paroi
        min_dim = min(b_max[0] - b_min[0], b_max[1] - b_min[1], b_max[2] - b_min[2])
        if min_dim < 0.001:  # Seuil minimal DFM 1.0 mm
            dfm_warnings.append(
                f"Épaisseur minimale hors-tout inférieure au seuil DFM (1.0 mm) : {min_dim * 1000.0:.2f} mm"
            )

        # 2. Vérification des perçages circulaires
        for face in faces:
            if face.geom_type == bd.GeomType.CYLINDER:
                # Rayon en mètres
                radius_m = float(face.radius) * scale
                diameter_mm = radius_m * 2.0 * 1000.0
                if diameter_mm < 2.0:  # Perçage usinable minimal 2.0 mm
                    dfm_warnings.append(
                        f"Perçage cylindrique sous le seuil d'usinabilité DFM (< 2.0 mm) : Ø {diameter_mm:.2f} mm"
                    )

        return ValidationReport(
            is_valid_l0=is_l0,
            is_watertight=is_watertight,
            volume_m3=vol_m3,
            surface_area_m2=area_m2,
            center_of_mass=com,
            bbox_min=b_min,
            bbox_max=b_max,
            topology_counts=topo_counts,
            dfm_warnings=dfm_warnings,
        )

    def resolve_selector(self, selector_expr: str) -> list[int]:
        """Évalue une expression de sélection spatio-sémantique sur le solide.

        Args:
            selector_expr: Expression Python relative à `part` (ex: 'faces().sort_by(Axis.Z)[-1]').

        Returns:
            list[int]: Liste des indices des faces identifiées.
        """
        all_faces = self._part.faces()
        context = {
            "part": self._part,
            "Axis": bd.Axis,
            "GeomType": bd.GeomType,
            "bd": bd,
        }

        try:
            # Évaluation sous environnement contrôlé
            result = eval(f"part.{selector_expr}", {"__builtins__": {}}, context)
        except Exception as exc:
            raise ValueError(f"Impossible d'évaluer le sélecteur '{selector_expr}': {exc}") from exc

        selected_indices: list[int] = []

        if isinstance(result, bd.Face):
            for idx, face in enumerate(all_faces):
                if face.is_same(result):
                    selected_indices.append(idx)
        elif isinstance(result, (list, tuple, bd.ShapeList)):
            for item in result:
                if isinstance(item, bd.Face):
                    for idx, face in enumerate(all_faces):
                        if face.is_same(item) and idx not in selected_indices:
                            selected_indices.append(idx)

        return selected_indices

    def serialize_native(self) -> bytes:
        """Exporte le solide au format STEP AP242 standard de l'industrie.

        Returns:
            bytes: Contenu binaire du fichier STEP AP242.
        """
        with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)

        try:
            bd.export_step(self._part, str(tmp_path))
            return tmp_path.read_bytes()
        finally:
            if tmp_path.exists():
                tmp_path.unlink()
