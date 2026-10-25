from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import numpy as np
import numpy.typing as npt


@dataclass(frozen=True)
class BinaryMeshPayload:
    """Charge utile binaire d'un maillage géométrique 3D pour transfert zero-copy.

    Regroupe les coordonnées de sommets (float32), les normales (float32),
    les indices des triangles (uint32), les identifiants de faces topologiques
    (uint32) et optionnellement un champ scalaire continu (ex: contraintes de von Mises)
    dans des tableaux contigus alignés en mémoire.
    """

    entity_id: str
    vertices: npt.NDArray[np.float32]  # Coordonnées (N, 3) locales centrées
    normals: npt.NDArray[np.float32]  # Vecteurs normaux unitaires (N, 3)
    indices: npt.NDArray[np.uint32]  # Indices de triangulation (M, 3) ou (M*3,)
    face_ids: npt.NDArray[np.uint32]  # Identifiant topologique de face pour chaque triangle (M,)
    scalar_field: npt.NDArray[np.float32] | None = None  # Champ scalaire par sommet (N,) ex: von Mises normalisé

    def to_bytes(self) -> bytes:
        """Sérialise les tableaux contigus en un unique tampon binaire.

        Ordre d'alignement binaire (4 octets par élément) :
        1. vertices (float32)
        2. normals (float32)
        3. indices (uint32)
        4. face_ids (uint32)
        5. scalar_field (float32, optionnel)

        Returns:
            bytes: Flux binaire contigu directement projetable dans Three.js.
        """
        v_bytes = np.ascontiguousarray(self.vertices, dtype=np.float32).tobytes()
        n_bytes = np.ascontiguousarray(self.normals, dtype=np.float32).tobytes()
        i_bytes = np.ascontiguousarray(self.indices, dtype=np.uint32).tobytes()
        f_bytes = np.ascontiguousarray(self.face_ids, dtype=np.uint32).tobytes()
        s_bytes = (
            np.ascontiguousarray(self.scalar_field, dtype=np.float32).tobytes()
            if self.scalar_field is not None
            else b""
        )
        return v_bytes + n_bytes + i_bytes + f_bytes + s_bytes

    @classmethod
    def from_bytes(
        cls,
        entity_id: str,
        vertex_count: int,
        triangle_count: int,
        raw_bytes: bytes,
    ) -> BinaryMeshPayload:
        """Désérialise un flux binaire contigu en instance BinaryMeshPayload.

        Args:
            entity_id: Identifiant unique de l'entité géométrique.
            vertex_count: Nombre total de sommets (N).
            triangle_count: Nombre total de triangles (M).
            raw_bytes: Flux d'octets brut reçu depuis le transport réseau.

        Returns:
            BinaryMeshPayload: Données structurées sous forme de tableaux NumPy.
        """
        v_len = vertex_count * 3
        i_len = triangle_count * 3
        f_len = triangle_count

        offset = 0
        v_size = v_len * 4
        vertices = np.frombuffer(raw_bytes, dtype=np.float32, count=v_len, offset=offset).reshape(
            (vertex_count, 3)
        )
        offset += v_size

        n_size = v_len * 4
        normals = np.frombuffer(raw_bytes, dtype=np.float32, count=v_len, offset=offset).reshape(
            (vertex_count, 3)
        )
        offset += n_size

        i_size = i_len * 4
        indices = np.frombuffer(raw_bytes, dtype=np.uint32, count=i_len, offset=offset).reshape(
            (triangle_count, 3)
        )
        offset += i_size

        face_ids = np.frombuffer(raw_bytes, dtype=np.uint32, count=f_len, offset=offset)
        offset += f_len * 4

        scalar_field: npt.NDArray[np.float32] | None = None
        if len(raw_bytes) >= offset + vertex_count * 4:
            scalar_field = np.frombuffer(raw_bytes, dtype=np.float32, count=vertex_count, offset=offset)

        return cls(
            entity_id=entity_id,
            vertices=vertices,
            normals=normals,
            indices=indices,
            face_ids=face_ids,
            scalar_field=scalar_field,
        )


@dataclass(frozen=True)
class ValidationReport:
    """Rapport de conformité déterministe pour les portes de contrôle L0 et L1.

    Contient les indicateurs mathématiques exacts garantissant qu'un solide
    B-Rep est valide, fermé et respecte les contraintes élémentaires de fabricabilité.
    """

    is_valid_l0: bool
    is_watertight: bool
    volume_m3: float
    surface_area_m2: float
    center_of_mass: tuple[float, float, float]
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    topology_counts: dict[str, int]
    dfm_warnings: list[str]


class GeometryProvider(ABC):
    """Contrat d'interface abstrait pour tous les fournisseurs géométriques métier.

    Définit les opérations fondamentales que chaque moteur (B-Rep, Maillage,
    BIM, Molécule, Textile) doit obligatoirement implémenter.
    """

    @abstractmethod
    def tessellate_binary(
        self,
        linear_deflection: float = 0.001,
        angular_deflection: float = 0.2,
    ) -> BinaryMeshPayload:
        """Génère la tessellation binaire pour l'affichage temps réel dans le Viewport.

        Args:
            linear_deflection: Tolérance de flèche linéaire en mètres.
            angular_deflection: Tolérance de déviation angulaire en radians.

        Returns:
            BinaryMeshPayload: Données de géométrie triangulée prêtes pour le GPU.
        """
        ...

    @abstractmethod
    def introspect(self) -> ValidationReport:
        """Exécute l'inspection géométrique déterministe (Porte L0 / L1).

        Returns:
            ValidationReport: Rapport synthétique de validité et mesures physiques.
        """
        ...

    @abstractmethod
    def resolve_selector(self, selector_expr: str) -> list[int]:
        """Évalue un sélecteur spatio-sémantique et renvoie les identifiants topologiques.

        Args:
            selector_expr: Expression de sélection (ex: 'faces().sort_by(Axis.Z)[-1]').

        Returns:
            list[int]: Liste d'identifiants uniques des entités sous-jacentes.
        """
        ...

    @abstractmethod
    def serialize_native(self) -> bytes:
        """Exporte l'état physique exact au format natif (STEP, IFC, BinaryCIF).

        Returns:
            bytes: Flux binaire du modèle géométrique complet.
        """
        ...
