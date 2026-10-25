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
from pathlib import Path
from typing import List, Literal, Tuple
import urllib.request
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


def _encode_string_array(strings: list[str]) -> dict[str, object]:
    """Encode une colonne de chaînes de caractères au format BinaryCIF StringArray."""
    unique: list[str] = list(dict.fromkeys(strings))
    str_indices: npt.NDArray[np.int32] = np.array([unique.index(s) for s in strings], dtype=np.int32)
    str_data: str = "".join(unique)
    offsets: list[int] = [0]
    for s in unique:
        offsets.append(offsets[-1] + len(s))
    offsets_arr: npt.NDArray[np.int32] = np.array(offsets, dtype=np.int32)
    return {
        "data": str_indices.tobytes(),
        "encoding": [
            {
                "kind": "StringArray",
                "stringData": str_data,
                "offsets": offsets_arr.tobytes(),
                "offsetEncoding": [{"kind": "ByteArray", "type": 3}],
                "dataEncoding": [{"kind": "ByteArray", "type": 3}],
            },
            {"kind": "ByteArray", "type": 3},
        ],
    }


def _encode_numeric_array(arr: npt.NDArray[np.number], type_code: int) -> dict[str, object]:
    """Encode un tableau numérique au format BinaryCIF ByteArray."""
    return {
        "data": arr.tobytes(),
        "encoding": [{"kind": "ByteArray", "type": type_code}],
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


def _generate_ribbon_mesh(
    points: list[npt.NDArray[np.float32]],
    b_factors: list[float],
    scale_to_mm: float = 1.0,
    n_sub: int = 4,
    rx_angstrom: float = 0.60,
    ry_angstrom: float = 0.20,
) -> Tuple[npt.NDArray[np.float32], npt.NDArray[np.float32], npt.NDArray[np.uint32], npt.NDArray[np.float32]]:
    """Génère un maillage de ruban continu le long des atomes C-alpha par interpolation spline Catmull-Rom."""
    pts = np.array(points, dtype=np.float32) * scale_to_mm
    n_pts = len(pts)
    if n_pts < 2:
        return (
            np.zeros((0, 3), dtype=np.float32),
            np.zeros((0, 3), dtype=np.float32),
            np.zeros((0, 3), dtype=np.uint32),
            np.zeros(0, dtype=np.float32),
        )

    rx = rx_angstrom * scale_to_mm
    ry = ry_angstrom * scale_to_mm

    spline_pts: list[npt.NDArray[np.float32]] = []
    spline_bs: list[float] = []

    for i in range(n_pts - 1):
        p0 = pts[max(0, i - 1)]
        p1 = pts[i]
        p2 = pts[i + 1]
        p3 = pts[min(n_pts - 1, i + 2)]
        b1 = b_factors[i]
        b2 = b_factors[i + 1]

        num_steps = n_sub if i < n_pts - 2 else n_sub + 1
        for step in range(num_steps):
            t = float(step) / float(n_sub)
            t2 = t * t
            t3 = t2 * t
            interp_pt = 0.5 * (
                (2.0 * p1)
                + (-p0 + p2) * t
                + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
                + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3
            )
            spline_pts.append(interp_pt.astype(np.float32))
            spline_bs.append(b1 + (b2 - b1) * t)

    m_spline = len(spline_pts)
    num_sides = 6
    angles = np.linspace(0, 2 * math.pi, num_sides, endpoint=False)
    cos_a = np.cos(angles).astype(np.float32)
    sin_a = np.sin(angles).astype(np.float32)

    # Initialisation du repère de Frenet / Bishop
    initial_tan = spline_pts[1] - spline_pts[0]
    initial_tan /= float(np.linalg.norm(initial_tan)) + 1e-8
    up_ref = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    if abs(float(initial_tan[1])) > 0.9:
        up_ref = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    curr_norm = np.cross(up_ref, initial_tan)
    curr_norm /= float(np.linalg.norm(curr_norm)) + 1e-8

    rings: list[list[npt.NDArray[np.float32]]] = []
    ring_normals: list[list[npt.NDArray[np.float32]]] = []
    ring_scalars: list[float] = []

    for i in range(m_spline):
        if i < m_spline - 1:
            tangent = spline_pts[i + 1] - spline_pts[i]
        else:
            tangent = spline_pts[i] - spline_pts[i - 1]
        t_len = float(np.linalg.norm(tangent))
        if t_len > 1e-6:
            tangent /= t_len

        # Transport parallèle du vecteur normal
        curr_norm = curr_norm - float(np.dot(curr_norm, tangent)) * tangent
        n_len = float(np.linalg.norm(curr_norm))
        if n_len > 1e-6:
            curr_norm /= n_len
        else:
            up_ref = np.array([0.0, 1.0, 0.0], dtype=np.float32)
            if abs(float(tangent[1])) > 0.9:
                up_ref = np.array([1.0, 0.0, 0.0], dtype=np.float32)
            curr_norm = np.cross(up_ref, tangent)
            curr_norm /= float(np.linalg.norm(curr_norm)) + 1e-8

        curr_binorm = np.cross(tangent, curr_norm)

        # Génération des sommets et normales de la section elliptique
        ring_v: list[npt.NDArray[np.float32]] = []
        ring_n: list[npt.NDArray[np.float32]] = []
        for k in range(num_sides):
            offset = curr_norm * (cos_a[k] * rx) + curr_binorm * (sin_a[k] * ry)
            ring_v.append(spline_pts[i] + offset)
            n_vec = curr_norm * (cos_a[k] / rx) + curr_binorm * (sin_a[k] / ry)
            n_vec /= float(np.linalg.norm(n_vec)) + 1e-8
            ring_n.append(n_vec)

        rings.append(ring_v)
        ring_normals.append(ring_n)
        ring_scalars.extend([spline_bs[i]] * num_sides)

    all_verts = np.array(rings, dtype=np.float32).reshape(-1, 3)
    all_normals = np.array(ring_normals, dtype=np.float32).reshape(-1, 3)
    all_scalars = np.array(ring_scalars, dtype=np.float32)

    triangles: list[list[int]] = []
    for i in range(m_spline - 1):
        r1 = i * num_sides
        r2 = (i + 1) * num_sides
        for k in range(num_sides):
            k_next = (k + 1) % num_sides
            v1 = r1 + k
            v2 = r1 + k_next
            v3 = r2 + k
            v4 = r2 + k_next
            triangles.append([v1, v2, v3])
            triangles.append([v2, v4, v3])

    all_tris = np.array(triangles, dtype=np.uint32)
    return all_verts, all_normals, all_tris, all_scalars


class BiomoleculeProvider(GeometryProvider):
    """Fournisseur géométrique et moteur biophysique macromoléculaire 3D.

    Charge les structures PDB ou mmCIF, génère la tessellation 3D pour Three.js,
    calcule la conformation stérique de Ramachandran et résout les sélecteurs biologiques.
    """

    def __init__(
        self,
        pdb_content: str | None = None,
        pdb_path: str | None = None,
        pdb_code: str | None = None,
        mode: Literal["ball_and_stick", "ribbon", "spheres", "sticks"] = "ball_and_stick",
        scale_to_mm: float = 1.0,
        entity_id: str | None = None,
    ) -> None:
        """Initialise le fournisseur biomoléculaire avec une chaîne, un fichier ou un code PDB."""
        self.entity_id: str = entity_id or "biomolecule_model"
        self.mode: Literal["ball_and_stick", "ribbon", "spheres", "sticks"] = mode
        self.scale_to_mm: float = scale_to_mm
        self.raw_pdb_text: str = ""
        self.structure: gemmi.Structure = gemmi.Structure()

        if pdb_content:
            self.load_pdb(pdb_content)
        elif pdb_path:
            self.load_file(pdb_path)
        elif pdb_code:
            self.fetch_pdb(pdb_code)
        else:
            # Structure de référence par défaut : Crambin (1CRN) si disponible, sinon dipeptide
            asset_1crn = Path(__file__).parent.parent / "assets" / "1CRN.cif"
            if asset_1crn.exists():
                self.load_file(str(asset_1crn))
            else:
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
        """Charge une structure protéique à partir d'une chaîne au format PDB ou mmCIF."""
        self.raw_pdb_text = content
        trimmed = content.lstrip()
        if trimmed.startswith("data_") or "loop_" in trimmed:
            self._load_cif_text(content)
        else:
            self.structure = gemmi.read_pdb_string(content)

    def _load_cif_text(self, text: str) -> None:
        """Charge un document CIF / mmCIF depuis une chaîne de caractères."""
        self.raw_pdb_text = text
        doc = gemmi.cif.read_string(text)
        self.structure = gemmi.make_structure_from_block(doc[0])

    def load_file(self, path: str) -> None:
        """Charge une structure depuis un fichier disque PDB ou mmCIF."""
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            self.raw_pdb_text = f.read()

        if path.lower().endswith((".cif", ".mmcif")):
            self.structure = gemmi.make_structure_from_block(gemmi.cif.read(path)[0])
        else:
            self.structure = gemmi.read_structure(path)

    def fetch_pdb(self, pdb_code: str) -> None:
        """Télécharge ou charge une structure macromoléculaire par son code PDB (ex: '1CRN')."""
        code = pdb_code.strip().upper()
        asset_dir = Path(__file__).parent.parent / "assets"

        # 1. Priorité au cache local si le fichier CIF ou PDB existe
        cif_file = asset_dir / f"{code}.cif"
        pdb_file = asset_dir / f"{code}.pdb"
        if cif_file.exists():
            self.load_file(str(cif_file))
            return
        if pdb_file.exists():
            self.load_file(str(pdb_file))
            return

        # 2. Téléchargement depuis le RCSB PDB
        try:
            url = f"https://files.rcsb.org/download/{code}.cif"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mono-CAD-Engine/2.0 (Biomolecular; Contact: support@mono-cad.org)"},
            )
            with urllib.request.urlopen(req, timeout=4.0) as response:
                content_bytes = response.read()
                cif_text = content_bytes.decode("utf-8", errors="ignore")
                self._load_cif_text(cif_text)
                return
        except Exception:
            pass

        # 3. Fallback d'urgence pour 1CRN
        if code == "1CRN" and cif_file.exists():
            self.load_file(str(cif_file))
            return

        raise RuntimeError(f"Impossible de récupérer la structure PDB '{code}' en ligne ou dans les assets locaux.")

    def get_crystallographic_info(self) -> dict[str, object]:
        """Extrait les paramètres de maille élémentaire, groupe spatial et résolution cristallographique."""
        cell = self.structure.cell
        helices_cnt = len(self.structure.helices) if hasattr(self.structure, "helices") else 0
        sheets_cnt = len(self.structure.sheets) if hasattr(self.structure, "sheets") else 0
        bridges = self.find_disulfide_bridges()
        res_val = float(self.structure.resolution) if (hasattr(self.structure, "resolution") and self.structure.resolution) else 0.0
        sp_name = self.structure.spacegroup_hm if self.structure.spacegroup_hm else "Unknown"

        return {
            "pdb_code": self.structure.name or "1CRN",
            "spacegroup": sp_name,
            "resolution_angstrom": res_val,
            "unit_cell": {
                "a": float(cell.a) if cell else 0.0,
                "b": float(cell.b) if cell else 0.0,
                "c": float(cell.c) if cell else 0.0,
                "alpha": float(cell.alpha) if cell else 0.0,
                "beta": float(cell.beta) if cell else 0.0,
                "gamma": float(cell.gamma) if cell else 0.0,
                "volume": float(cell.volume) if cell else 0.0,
            },
            "secondary_structure": {
                "helices_count": helices_cnt,
                "sheets_count": sheets_cnt,
            },
            "disulfide_bridges_count": len(bridges),
        }

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

        # 3. Génération des rubans (Cartoon / C-alpha)
        if self.mode == "ribbon":
            has_ribbon = False
            for chain in model:
                ca_pts: list[npt.NDArray[np.float32]] = []
                ca_bs: list[float] = []
                for res in chain:
                    ca = res.find_atom("CA", "*")
                    if ca:
                        ca_pts.append(np.array([ca.pos.x, ca.pos.y, ca.pos.z], dtype=np.float32))
                        ca_bs.append(float(ca.b_iso))

                if len(ca_pts) >= 2:
                    has_ribbon = True
                    r_verts, r_normals, r_tris, r_scalars = _generate_ribbon_mesh(
                        ca_pts, ca_bs, scale_to_mm=self.scale_to_mm
                    )
                    norm_scalars = (r_scalars - min_b) / b_range
                    all_verts.append(r_verts)
                    all_normals.append(r_normals)
                    all_indices.append(r_tris + vertex_offset)
                    all_scalars.append(norm_scalars)
                    vertex_offset += len(r_verts)

            # Fallback sur sphères si aucun atome C-alpha (ex. petites molécules sans squelette peptidique)
            if not has_ribbon:
                for pos, rad, b_factor, _ in atoms_data:
                    scaled_verts = (_SPHERE_VERTS * rad) + np.array(pos, dtype=np.float32)
                    norm_b = (b_factor - min_b) / b_range
                    scalars = np.full(len(scaled_verts), norm_b, dtype=np.float32)
                    all_verts.append(scaled_verts)
                    all_normals.append(_SPHERE_VERTS)
                    all_indices.append(_SPHERE_INDICES + vertex_offset)
                    all_scalars.append(scalars)
                    vertex_offset += len(scaled_verts)

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

    def serialize_mmcif(self) -> str:
        """Sérialise la structure au format standard mmCIF (CIF macromoléculaire)."""
        if not self.structure or len(self.structure) == 0:
            return ""
        doc = self.structure.make_mmcif_document()
        return doc.as_string()

    def to_cif_string(self) -> str:
        """Alias pour serialize_mmcif(), exportant la structure au format CIF."""
        return self.serialize_mmcif()

    def serialize_binary_cif(self) -> bytes:
        """Sérialise la macromolécule au format binaire BinaryCIF (.bcif) pour Mol* et RCSB PDB."""
        if not self.structure or len(self.structure) == 0:
            return b""

        import msgpack

        model = self.structure[0]
        group_pdb: list[str] = []
        atom_ids: list[int] = []
        type_symbols: list[str] = []
        atom_names: list[str] = []
        comp_ids: list[str] = []
        asym_ids: list[str] = []
        seq_ids: list[int] = []
        xs: list[float] = []
        ys: list[float] = []
        zs: list[float] = []
        occupancies: list[float] = []
        b_factors: list[float] = []

        atom_counter = 1
        for chain in model:
            for res in chain:
                r_num = int(res.seqid.num)
                for atom in res:
                    group_pdb.append("HETATM" if res.is_water() else "ATOM")
                    atom_ids.append(atom_counter)
                    type_symbols.append(atom.element.name.upper())
                    atom_names.append(atom.name)
                    comp_ids.append(res.name)
                    asym_ids.append(chain.name)
                    seq_ids.append(r_num)
                    xs.append(float(atom.pos.x))
                    ys.append(float(atom.pos.y))
                    zs.append(float(atom.pos.z))
                    occupancies.append(float(atom.occ))
                    b_factors.append(float(atom.b_iso))
                    atom_counter += 1

        row_count = len(atom_ids)
        if row_count == 0:
            return b""

        ids_arr = np.array(atom_ids, dtype=np.int32)
        seq_arr = np.array(seq_ids, dtype=np.int32)
        xs_arr = np.array(xs, dtype=np.float32)
        ys_arr = np.array(ys, dtype=np.float32)
        zs_arr = np.array(zs, dtype=np.float32)
        occ_arr = np.array(occupancies, dtype=np.float32)
        b_arr = np.array(b_factors, dtype=np.float32)

        columns: list[dict[str, object]] = [
            {"name": "group_PDB", "data": _encode_string_array(group_pdb)},
            {"name": "id", "data": _encode_numeric_array(ids_arr, 3)},
            {"name": "type_symbol", "data": _encode_string_array(type_symbols)},
            {"name": "label_atom_id", "data": _encode_string_array(atom_names)},
            {"name": "label_alt_id", "data": _encode_string_array(["."] * row_count)},
            {"name": "label_comp_id", "data": _encode_string_array(comp_ids)},
            {"name": "label_asym_id", "data": _encode_string_array(asym_ids)},
            {"name": "label_entity_id", "data": _encode_string_array(["1"] * row_count)},
            {"name": "label_seq_id", "data": _encode_numeric_array(seq_arr, 3)},
            {"name": "Cartn_x", "data": _encode_numeric_array(xs_arr, 32)},
            {"name": "Cartn_y", "data": _encode_numeric_array(ys_arr, 32)},
            {"name": "Cartn_z", "data": _encode_numeric_array(zs_arr, 32)},
            {"name": "occupancy", "data": _encode_numeric_array(occ_arr, 32)},
            {"name": "B_iso_or_equiv", "data": _encode_numeric_array(b_arr, 32)},
            {"name": "auth_seq_id", "data": _encode_numeric_array(seq_arr, 3)},
            {"name": "auth_comp_id", "data": _encode_string_array(comp_ids)},
            {"name": "auth_asym_id", "data": _encode_string_array(asym_ids)},
            {"name": "auth_atom_id", "data": _encode_string_array(atom_names)},
        ]

        header = self.structure.name if self.structure.name else "MONO_STRUCTURE"
        categories: list[dict[str, object]] = [
            {
                "name": "_entry",
                "rowCount": 1,
                "columns": [{"name": "id", "data": _encode_string_array([header])}],
            },
        ]

        if hasattr(self.structure, "cell") and self.structure.cell and self.structure.cell.is_crystal():
            cell = self.structure.cell
            categories.append({
                "name": "_cell",
                "rowCount": 1,
                "columns": [
                    {"name": "length_a", "data": _encode_numeric_array(np.array([float(cell.a)], dtype=np.float32), 32)},
                    {"name": "length_b", "data": _encode_numeric_array(np.array([float(cell.b)], dtype=np.float32), 32)},
                    {"name": "length_c", "data": _encode_numeric_array(np.array([float(cell.c)], dtype=np.float32), 32)},
                    {"name": "angle_alpha", "data": _encode_numeric_array(np.array([float(cell.alpha)], dtype=np.float32), 32)},
                    {"name": "angle_beta", "data": _encode_numeric_array(np.array([float(cell.beta)], dtype=np.float32), 32)},
                    {"name": "angle_gamma", "data": _encode_numeric_array(np.array([float(cell.gamma)], dtype=np.float32), 32)},
                ],
            })

        if hasattr(self.structure, "spacegroup_hm") and self.structure.spacegroup_hm:
            categories.append({
                "name": "_symmetry",
                "rowCount": 1,
                "columns": [
                    {"name": "space_group_name_H_M", "data": _encode_string_array([self.structure.spacegroup_hm])},
                ],
            })

        categories.append({
            "name": "_atom_site",
            "rowCount": row_count,
            "columns": columns,
        })

        bcif_payload: dict[str, object] = {
            "encoder": "Mono-Gemmi v2.0",
            "version": "0.3.0",
            "dataBlocks": [
                {
                    "header": header,
                    "categories": categories,
                }
            ],
        }

        return msgpack.packb(bcif_payload, use_bin_type=True)

    def get_atom_positions(self) -> npt.NDArray[np.float32]:
        """Extrait les coordonnées spatiales 3D de tous les atomes en Angströms."""
        if not self.structure or len(self.structure) == 0:
            return np.zeros((0, 3), dtype=np.float32)

        coords: list[tuple[float, float, float]] = []
        model = self.structure[0]
        for chain in model:
            for res in chain:
                for atom in res:
                    coords.append((float(atom.pos.x), float(atom.pos.y), float(atom.pos.z)))

        return np.array(coords, dtype=np.float32)

    def update_positions(self, positions: npt.NDArray[np.float32]) -> None:
        """Met à jour les coordonnées atomiques 3D en place (ex. depuis une trajectoire OpenMM)."""
        if not self.structure or len(self.structure) == 0:
            return

        model = self.structure[0]
        idx = 0
        n_pos = len(positions)

        for chain in model:
            for res in chain:
                for atom in res:
                    if idx < n_pos:
                        atom.pos = gemmi.Position(
                            float(positions[idx, 0]),
                            float(positions[idx, 1]),
                            float(positions[idx, 2]),
                        )
                        idx += 1
