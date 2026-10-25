"""Fournisseur géométrique et moteur biophysique macromoléculaire 3D sous Gemmi.

Gère le chargement, l'analyse topologique et la tessellation 3D zero-copy de structures
protéiques et acides nucléiques (PDB, mmCIF) conformément au contrat `GeometryProvider`.
Génère les représentations graphiques (bâtonnets, rubans C-alpha, sphères van der Waals),
calcule les angles de torsion de Ramachandran (phi, psi), identifie les ponts disulfures
et projette les facteurs thermiques B-factor / score de confiance pLDDT en champ scalaire WebGL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import List, Literal, Tuple
import gemmi
import numpy as np
import numpy.typing as npt

from mono.core.geometry_provider import BinaryMeshPayload, GeometryProvider, ValidationReport


@dataclass(frozen=True)
class RamachandranPoint:
    """Point d'angle dièdre de torsion pour un résidu protéique."""

    chain: str
    res_name: str
    res_num: int
    phi_deg: float
    psi_deg: float
    region: str  # "Alpha-Helix", "Beta-Sheet", "Left-Handed-Helix", "Other"


@dataclass(frozen=True)
class DisulfideBridge:
    """Liaison covalente de pont disulfure entre deux résidus Cystéine."""

    chain1: str
    res1_num: int
    chain2: str
    res2_num: int
    distance_angstrom: float


@dataclass(frozen=True)
class HydrogenBond:
    """Liaison hydrogène détectée entre donneur et accepteur."""

    donor_chain: str
    donor_res: int
    donor_atom: str
    acceptor_chain: str
    acceptor_res: int
    acceptor_atom: str
    distance_angstrom: float


# Rayons de van der Waals approximatifs (en Angströms)
VDW_RADII: dict[str, float] = {
    "H": 1.20,
    "C": 1.70,
    "N": 1.55,
    "O": 1.52,
    "P": 1.80,
    "S": 1.80,
    "FE": 1.80,
    "ZN": 1.39,
    "MG": 1.73,
    "CA": 2.00,
}


def _create_unit_sphere(stacks: int = 6, slices: int = 8) -> Tuple[npt.NDArray[np.float32], npt.NDArray[np.uint32]]:
    """Génère un maillage de sphère unitaire centrée en (0, 0, 0)."""
    vertices: list[tuple[float, float, float]] = []
    indices: list[tuple[int, int, int]] = []

    for i in range(stacks + 1):
        lat = (math.pi * i) / stacks - (math.pi / 2.0)
        sin_lat = math.sin(lat)
        cos_lat = math.cos(lat)

        for j in range(slices):
            lon = (2.0 * math.pi * j) / slices
            sin_lon = math.sin(lon)
            cos_lon = math.cos(lon)

            x = cos_lat * cos_lon
            y = sin_lat
            z = cos_lat * sin_lon
            vertices.append((x, y, z))

    for i in range(stacks):
        for j in range(slices):
            first = i * slices + j
            second = first + slices
            next_j = (j + 1) % slices
            first_next = i * slices + next_j
            second_next = first_next + slices

            indices.append((first, second, first_next))
            indices.append((second, second_next, first_next))

    return np.array(vertices, dtype=np.float32), np.array(indices, dtype=np.uint32)


def _create_unit_cylinder(segments: int = 6) -> Tuple[npt.NDArray[np.float32], npt.NDArray[np.uint32]]:
    """Génère un cylindre unitaire de rayon 1.0 et de longueur 1.0 orienté le long de l'axe Z (de Z=0 à Z=1)."""
    vertices: list[tuple[float, float, float]] = []
    indices: list[tuple[int, int, int]] = []

    for i in range(segments):
        angle = (2.0 * math.pi * i) / segments
        x = math.cos(angle)
        y = math.sin(angle)
        vertices.append((x, y, 0.0))  # Base inférieure (0 à segments-1)
        vertices.append((x, y, 1.0))  # Base supérieure (segments à 2*segments-1)

    for i in range(segments):
        bot1 = i * 2
        top1 = bot1 + 1
        bot2 = ((i + 1) % segments) * 2
        top2 = bot2 + 1

        indices.append((bot1, bot2, top1))
        indices.append((bot2, top2, top1))

    return np.array(vertices, dtype=np.float32), np.array(indices, dtype=np.uint32)


# Précalcul des gabarits géométriques primitifs
_SPHERE_VERTS, _SPHERE_INDICES = _create_unit_sphere(stacks=6, slices=8)
_CYL_VERTS, _CYL_INDICES = _create_unit_cylinder(segments=6)


class BiomoleculeProvider(GeometryProvider):
    """Fournisseur géométrique et moteur biophysique macromoléculaire 3D.

    Charge les structures PDB ou mmCIF, génère la tessellation 3D pour Three.js,
    calcule la conformation stérique de Ramachandran et résout les sélecteurs biologiques.
    """

    def __init__(
        self,
        pdb_content: str | None = None,
        pdb_path: str | None = None,
        mode: Literal["ball_and_stick", "ribbon", "spheres", "sticks"] = "ball_and_stick",
        scale_to_mm: float = 1.0,
    ) -> None:
        """Initialise le fournisseur biomoléculaire avec une chaîne ou un fichier PDB."""
        self.mode: Literal["ball_and_stick", "ribbon", "spheres", "sticks"] = mode
        self.scale_to_mm: float = scale_to_mm
        self.raw_pdb_text: str = ""
        self.structure: gemmi.Structure = gemmi.Structure()

        if pdb_content:
            self.load_pdb(pdb_content)
        elif pdb_path:
            self.load_file(pdb_path)
        else:
            # Structure minimale par défaut : dipeptide Ala-Gly
            default_pdb = (
                "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00 20.00           N\n"
                "ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00 20.00           C\n"
                "ATOM      3  C   ALA A   1       2.009   1.420   0.000  1.00 20.00           C\n"
                "ATOM      4  O   ALA A   1       1.306   2.428   0.000  1.00 20.00           O\n"
                "ATOM      5  CB  ALA A   1       1.989  -0.771  -1.205  1.00 20.00           C\n"
                "ATOM      6  N   GLY A   2       3.242   1.420   0.500  1.00 25.00           N\n"
                "ATOM      7  CA  GLY A   2       3.800   2.700   0.500  1.00 25.00           C\n"
                "ATOM      8  C   GLY A   2       5.200   2.700   0.000  1.00 25.00           C\n"
                "ATOM      9  O   GLY A   2       5.800   3.700   0.000  1.00 25.00           O\n"
                "TER      10      GLY A   2\n"
                "END\n"
            )
            self.load_pdb(default_pdb)

    def load_pdb(self, content: str) -> None:
        """Charge une structure protéique à partir d'une chaîne au format PDB."""
        self.raw_pdb_text = content
        self.structure = gemmi.read_pdb_string(content)

    def load_file(self, path: str) -> None:
        """Charge une structure depuis un fichier disque PDB ou mmCIF."""
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            self.raw_pdb_text = f.read()

        if path.lower().endswith((".cif", ".mmcif")):
            self.structure = gemmi.make_structure_from_block(gemmi.cif.read(path)[0])
        else:
            self.structure = gemmi.read_structure(path)

    def get_chains(self) -> List[str]:
        """Renvoie la liste des identifiants de chaînes macromoléculaires."""
        if not self.structure or len(self.structure) == 0:
            return []
        return [chain.name for chain in self.structure[0]]

    def get_sequence(self, chain_id: str = "A") -> str:
        """Extrait la séquence en acides aminés en code 1 lettre pour une chaîne donnée."""
        if not self.structure or len(self.structure) == 0:
            return ""

        model = self.structure[0]
        chain = model.find_chain(chain_id)
        if chain is None:
            return ""

        seq_chars: list[str] = []
        for res in chain:
            tab_res = gemmi.find_tabulated_residue(res.name)
            if tab_res and tab_res.one_letter_code:
                seq_chars.append(tab_res.one_letter_code)
            else:
                seq_chars.append("X")

        return "".join(seq_chars)

    def compute_ramachandran(self) -> List[RamachandranPoint]:
        """Calcule les angles dièdres de torsion phi et psi pour chaque résidu protéique."""
        points: list[RamachandranPoint] = []
        if not self.structure or len(self.structure) == 0:
            return points

        model = self.structure[0]
        for chain in model:
            polymer = chain.get_polymer()
            if not polymer or len(polymer) < 3:
                continue

            for idx in range(1, len(polymer) - 1):
                prev_res = polymer[idx - 1]
                curr_res = polymer[idx]
                next_res = polymer[idx + 1]

                prev_c = prev_res.find_atom("C", "*")
                curr_n = curr_res.find_atom("N", "*")
                curr_ca = curr_res.find_atom("CA", "*")
                curr_c = curr_res.find_atom("C", "*")
                next_n = next_res.find_atom("N", "*")

                if prev_c and curr_n and curr_ca and curr_c:
                    phi_rad = gemmi.calculate_dihedral(
                        prev_c.pos, curr_n.pos, curr_ca.pos, curr_c.pos
                    )
                    phi_deg = math.degrees(phi_rad)
                else:
                    phi_deg = 0.0

                if curr_n and curr_ca and curr_c and next_n:
                    psi_rad = gemmi.calculate_dihedral(
                        curr_n.pos, curr_ca.pos, curr_c.pos, next_n.pos
                    )
                    psi_deg = math.degrees(psi_rad)
                else:
                    psi_deg = 0.0

                # Classification géométrique de la conformation
                region = "Other"
                if -160.0 <= phi_deg <= -30.0 and -80.0 <= psi_deg <= -10.0:
                    region = "Alpha-Helix"
                elif -180.0 <= phi_deg <= -60.0 and (90.0 <= psi_deg <= 180.0 or -180.0 <= psi_deg <= -150.0):
                    region = "Beta-Sheet"
                elif 30.0 <= phi_deg <= 90.0 and 10.0 <= psi_deg <= 80.0:
                    region = "Left-Handed-Helix"

                points.append(
                    RamachandranPoint(
                        chain=chain.name,
                        res_name=curr_res.name,
                        res_num=int(curr_res.seqid.num),
                        phi_deg=round(phi_deg, 2),
                        psi_deg=round(psi_deg, 2),
                        region=region,
                    )
                )

        return points

    def find_disulfide_bridges(self, cutoff_angstrom: float = 2.5) -> List[DisulfideBridge]:
        """Détecte les ponts disulfures (liaisons S-S entre Cystéines à distance < 2.5 Å)."""
        bridges: list[DisulfideBridge] = []
        if not self.structure or len(self.structure) == 0:
            return bridges

        cys_atoms: list[tuple[str, int, gemmi.Atom]] = []
        model = self.structure[0]
        for chain in model:
            for res in chain:
                if res.name in ("CYS", "CYX"):
                    sg = res.find_atom("SG", "*")
                    if sg:
                        cys_atoms.append((chain.name, int(res.seqid.num), sg))

        n_cys = len(cys_atoms)
        for i in range(n_cys):
            ch1, num1, a1 = cys_atoms[i]
            for j in range(i + 1, n_cys):
                ch2, num2, a2 = cys_atoms[j]
                d = a1.pos.dist(a2.pos)
                if 1.8 <= d <= cutoff_angstrom:
                    bridges.append(
                        DisulfideBridge(
                            chain1=ch1,
                            res1_num=num1,
                            chain2=ch2,
                            res2_num=num2,
                            distance_angstrom=round(d, 3),
                        )
                    )

        return bridges

    def find_hydrogen_bonds(self, max_dist: float = 3.5) -> List[HydrogenBond]:
        """Détecte les liaisons hydrogène potentielles entre atomes donneurs (N) et accepteurs (O)."""
        hbonds: list[HydrogenBond] = []
        if not self.structure or len(self.structure) == 0:
            return hbonds

        nitrogens: list[tuple[str, int, str, gemmi.Position]] = []
        oxygens: list[tuple[str, int, str, gemmi.Position]] = []

        model = self.structure[0]
        for chain in model:
            for res in chain:
                r_num = int(res.seqid.num)
                for atom in res:
                    if atom.element.name == "N":
                        nitrogens.append((chain.name, r_num, atom.name, atom.pos))
                    elif atom.element.name == "O":
                        oxygens.append((chain.name, r_num, atom.name, atom.pos))

        for d_ch, d_num, d_name, d_pos in nitrogens:
            for a_ch, a_num, a_name, a_pos in oxygens:
                # Éviter les atomes consécutifs du même résidu
                if d_ch == a_ch and abs(d_num - a_num) <= 1:
                    continue
                dist = d_pos.dist(a_pos)
                if 2.4 <= dist <= max_dist:
                    hbonds.append(
                        HydrogenBond(
                            donor_chain=d_ch,
                            donor_res=d_num,
                            donor_atom=d_name,
                            acceptor_chain=a_ch,
                            acceptor_res=a_num,
                            acceptor_atom=a_name,
                            distance_angstrom=round(dist, 3),
                        )
                    )

        return hbonds

    # --- Implémentation du Contrat GeometryProvider ---

    def tessellate_binary(
        self,
        linear_deflection: float = 0.001,
        angular_deflection: float = 0.2,
    ) -> BinaryMeshPayload:
        """Génère la tessellation 3D zero-copy sous forme de sphères atomiques et liaisons cylindriques."""
        if not self.structure or len(self.structure) == 0:
            return BinaryMeshPayload(
                entity_id="biomolecule_empty",
                vertices=np.zeros((0, 3), dtype=np.float32),
                normals=np.zeros((0, 3), dtype=np.float32),
                indices=np.zeros((0, 3), dtype=np.uint32),
                face_ids=np.zeros(0, dtype=np.uint32),
            )

        model = self.structure[0]
        atoms_data: list[tuple[tuple[float, float, float], float, float, str]] = []

        # Extraction de tous les atomes
        for chain in model:
            for res in chain:
                for atom in res:
                    pos = (atom.pos.x * self.scale_to_mm, atom.pos.y * self.scale_to_mm, atom.pos.z * self.scale_to_mm)
                    b_factor = float(atom.b_iso)
                    elem = atom.element.name.upper()
                    radius = VDW_RADII.get(elem, 1.50) * 0.25 * self.scale_to_mm
                    atoms_data.append((pos, radius, b_factor, elem))

        n_atoms = len(atoms_data)
        if n_atoms == 0:
            return BinaryMeshPayload(
                entity_id="biomolecule_empty",
                vertices=np.zeros((0, 3), dtype=np.float32),
                normals=np.zeros((0, 3), dtype=np.float32),
                indices=np.zeros((0, 3), dtype=np.uint32),
                face_ids=np.zeros(0, dtype=np.uint32),
            )

        # Calcul des liaisons covalentes (distance interatomique < 1.9 Angström)
        bonds: list[tuple[int, int]] = []
        if self.mode in ("ball_and_stick", "sticks"):
            cutoff = 1.90 * self.scale_to_mm
            for i in range(n_atoms):
                p1 = atoms_data[i][0]
                for j in range(i + 1, min(i + 25, n_atoms)):
                    p2 = atoms_data[j][0]
                    dx = p1[0] - p2[0]
                    dy = p1[1] - p2[1]
                    dz = p1[2] - p2[2]
                    d_sq = dx * dx + dy * dy + dz * dz
                    if 0.1 <= d_sq <= (cutoff * cutoff):
                        bonds.append((i, j))

        # Assemblage des sommets et triangles
        all_verts: list[npt.NDArray[np.float32]] = []
        all_normals: list[npt.NDArray[np.float32]] = []
        all_indices: list[npt.NDArray[np.uint32]] = []
        all_scalars: list[npt.NDArray[np.float32]] = []

        vertex_offset = 0

        # Normalisation des facteurs B-factor (0.0 à 1.0)
        b_vals = [a[2] for a in atoms_data]
        min_b = min(b_vals)
        max_b = max(b_vals)
        b_range = max(max_b - min_b, 1e-4)

        # 1. Génération des sphères atomiques
        if self.mode in ("ball_and_stick", "spheres"):
            for pos, rad, b_factor, _ in atoms_data:
                scaled_verts = (_SPHERE_VERTS * rad) + np.array(pos, dtype=np.float32)
                norm_b = (b_factor - min_b) / b_range
                scalars = np.full(len(scaled_verts), norm_b, dtype=np.float32)

                all_verts.append(scaled_verts)
                all_normals.append(_SPHERE_VERTS)  # La normale d'une sphère unitaire est le sommet lui-même
                all_indices.append(_SPHERE_INDICES + vertex_offset)
                all_scalars.append(scalars)
                vertex_offset += len(scaled_verts)

        # 2. Génération des cylindres de liaison
        if self.mode in ("ball_and_stick", "sticks"):
            cyl_radius = 0.08 * self.scale_to_mm
            for i, j in bonds:
                p1 = np.array(atoms_data[i][0], dtype=np.float32)
                p2 = np.array(atoms_data[j][0], dtype=np.float32)
                diff = p2 - p1
                length = float(np.linalg.norm(diff))
                if length < 1e-5:
                    continue

                axis_z = diff / length
                # Vecteur perpendiculaire pour la base orthonormée
                up = np.array([0.0, 1.0, 0.0], dtype=np.float32)
                if abs(axis_z[1]) > 0.95:
                    up = np.array([1.0, 0.0, 0.0], dtype=np.float32)
                axis_x = np.cross(up, axis_z)
                axis_x /= np.linalg.norm(axis_x)
                axis_y = np.cross(axis_z, axis_x)

                rot_matrix = np.column_stack([axis_x, axis_y, axis_z])

                # Transformation des sommets du gabarit cylindre
                cyl_local = _CYL_VERTS.copy()
                cyl_local[:, 0] *= cyl_radius
                cyl_local[:, 1] *= cyl_radius
                cyl_local[:, 2] *= length

                transformed_verts = (cyl_local @ rot_matrix.T) + p1
                cyl_normals = (_CYL_VERTS * np.array([1.0, 1.0, 0.0], dtype=np.float32)) @ rot_matrix.T

                # Facteur B moyen pour la liaison
                b_avg = ((atoms_data[i][2] + atoms_data[j][2]) * 0.5 - min_b) / b_range
                scalars = np.full(len(transformed_verts), b_avg, dtype=np.float32)

                all_verts.append(transformed_verts.astype(np.float32))
                all_normals.append(cyl_normals.astype(np.float32))
                all_indices.append(_CYL_INDICES + vertex_offset)
                all_scalars.append(scalars)
                vertex_offset += len(transformed_verts)

        final_vertices = np.vstack(all_verts)
        final_normals = np.vstack(all_normals)
        final_indices = np.vstack(all_indices)
        final_scalars = np.concatenate(all_scalars)
        face_ids = np.zeros(len(final_indices), dtype=np.uint32)

        return BinaryMeshPayload(
            entity_id="biomolecule_model",
            vertices=final_vertices,
            normals=final_normals,
            indices=final_indices,
            face_ids=face_ids,
            scalar_field=final_scalars,
        )

    def introspect(self) -> ValidationReport:
        """Fournit un rapport déterministe d'inspection biologique et géométrique."""
        payload = self.tessellate_binary()
        if len(payload.vertices) == 0:
            return ValidationReport(
                is_valid_l0=False,
                is_watertight=False,
                volume_m3=0.0,
                surface_area_m2=0.0,
                center_of_mass=(0.0, 0.0, 0.0),
                bbox_min=(0.0, 0.0, 0.0),
                bbox_max=(0.0, 0.0, 0.0),
                topology_counts={},
                dfm_warnings=["Structure macromoléculaire vide"],
            )

        min_pt = payload.vertices.min(axis=0)
        max_pt = payload.vertices.max(axis=0)
        com = payload.vertices.mean(axis=0)

        model = self.structure[0]
        n_chains = len(model)
        n_residues = sum(len(chain) for chain in model)
        n_atoms = sum(sum(len(res) for res in chain) for chain in model)

        return ValidationReport(
            is_valid_l0=True,
            is_watertight=False,
            volume_m3=0.0,
            surface_area_m2=float(n_atoms * 4.0 * math.pi * (1.5e-3 ** 2)),
            center_of_mass=(float(com[0]), float(com[1]), float(com[2])),
            bbox_min=(float(min_pt[0]), float(min_pt[1]), float(min_pt[2])),
            bbox_max=(float(max_pt[0]), float(max_pt[1]), float(max_pt[2])),
            topology_counts={
                "chains": n_chains,
                "residues": n_residues,
                "atoms": n_atoms,
                "triangles": len(payload.indices),
            },
            dfm_warnings=[],
        )

    def resolve_selector(self, selector_expr: str) -> List[int]:
        """Évalue un sélecteur sémantique atomique (chaîne, élément, atomes CA)."""
        expr = selector_expr.strip().lower()
        if not self.structure or len(self.structure) == 0:
            return []

        matched_indices: list[int] = []
        atom_idx = 0
        model = self.structure[0]

        for chain in model:
            ch_name = chain.name.lower()
            for res in chain:
                res_name = res.name.lower()
                for atom in res:
                    elem = atom.element.name.lower()
                    a_name = atom.name.lower()

                    match = False
                    if expr == "ca" and a_name == "ca":
                        match = True
                    elif expr.startswith("chain.") and expr.split(".")[1] == ch_name:
                        match = True
                    elif expr.startswith("element.") and expr.split(".")[1] == elem:
                        match = True
                    elif expr == "backbone" and a_name in ("n", "ca", "c", "o"):
                        match = True
                    elif expr in ("atoms", "all"):
                        match = True

                    if match:
                        matched_indices.append(atom_idx)
                    atom_idx += 1

        return matched_indices

    def serialize_native(self) -> bytes:
        """Sérialise la structure au format PDB textuel standard."""
        if self.raw_pdb_text:
            return self.raw_pdb_text.encode("utf-8")
        return self.structure.make_pdb_string().encode("utf-8")
