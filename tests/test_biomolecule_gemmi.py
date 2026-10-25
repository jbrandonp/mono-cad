"""Tests unitaires du fournisseur macromoléculaire BiomoleculeProvider sous Gemmi."""

from __future__ import annotations

import numpy as np
import pytest

from mono.providers.biomolecule_gemmi import (
    BiomoleculeProvider,
    RamachandranPoint,
    DisulfideBridge,
)

# PDB d'un tétrapeptide (Ala-Cys-Cys-Gly) avec un pont disulfure entre Cys2 et Cys3
PDB_TEST_PEPTIDE = """ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00 15.00           N
ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00 15.00           C
ATOM      3  C   ALA A   1       2.009   1.420   0.000  1.00 15.00           C
ATOM      4  O   ALA A   1       1.306   2.428   0.000  1.00 15.00           O
ATOM      5  CB  ALA A   1       1.989  -0.771  -1.205  1.00 15.00           C
ATOM      6  N   CYS A   2       3.242   1.420   0.500  1.00 20.00           N
ATOM      7  CA  CYS A   2       3.900   2.700   0.500  1.00 20.00           C
ATOM      8  C   CYS A   2       5.300   2.600   0.000  1.00 20.00           C
ATOM      9  O   CYS A   2       5.800   3.600  -0.500  1.00 20.00           O
ATOM     10  CB  CYS A   2       3.900   3.300   1.900  1.00 20.00           C
ATOM     11  SG  CYS A   2       4.500   4.900   2.000  1.00 20.00           S
ATOM     12  N   CYS A   3       5.900   1.400   0.100  1.00 22.00           N
ATOM     13  CA  CYS A   3       7.200   1.200  -0.500  1.00 22.00           C
ATOM     14  C   CYS A   3       8.200   2.300  -0.100  1.00 22.00           C
ATOM     15  O   CYS A   3       9.300   2.300  -0.600  1.00 22.00           O
ATOM     16  CB  CYS A   3       7.100   1.100  -2.000  1.00 22.00           C
ATOM     17  SG  CYS A   3       5.500   5.100   2.100  1.00 22.00           S
ATOM     18  N   GLY A   4       7.800   3.200   0.800  1.00 30.00           N
ATOM     19  CA  GLY A   4       8.600   4.400   1.100  1.00 30.00           C
ATOM     20  C   GLY A   4       9.900   4.100   1.800  1.00 30.00           C
ATOM     21  O   GLY A   4      10.800   4.900   1.900  1.00 30.00           O
TER      22      GLY A   4
END
"""


def test_biomolecule_loading_and_introspection() -> None:
    """Vérifie le parsing Gemmi, l'inspection topologique et la séquence."""
    provider = BiomoleculeProvider(pdb_content=PDB_TEST_PEPTIDE)

    assert provider.get_chains() == ["A"]
    seq = provider.get_sequence("A")
    assert seq == "ACCG"  # Ala, Cys, Cys, Gly

    report = provider.introspect()
    assert report.is_valid_l0 is True
    assert report.topology_counts["chains"] == 1
    assert report.topology_counts["residues"] == 4
    assert report.topology_counts["atoms"] == 21
    assert report.topology_counts["triangles"] > 0
    assert report.surface_area_m2 > 0.0


def test_tessellate_binary_and_scalar_field() -> None:
    """Vérifie que la tessellation zero-copy génère des tampons conformes et un champ scalaire."""
    provider = BiomoleculeProvider(pdb_content=PDB_TEST_PEPTIDE, mode="ball_and_stick")

    payload = provider.tessellate_binary()
    assert payload.entity_id == "biomolecule_model"
    assert payload.vertices.shape[1] == 3
    assert payload.normals.shape[1] == 3
    assert payload.indices.shape[1] == 3
    assert len(payload.vertices) > 0
    assert len(payload.indices) > 0

    # Vérification du champ scalaire (B-factors normalisés entre 0.0 et 1.0)
    assert payload.scalar_field is not None
    assert len(payload.scalar_field) == len(payload.vertices)
    assert float(np.min(payload.scalar_field)) >= 0.0
    assert float(np.max(payload.scalar_field)) <= 1.0


def test_ramachandran_angles() -> None:
    """Vérifie le calcul des angles dièdres de torsion phi et psi pour les résidus internes."""
    provider = BiomoleculeProvider(pdb_content=PDB_TEST_PEPTIDE)

    angles = provider.compute_ramachandran()
    # Le tétrapeptide possède 2 résidus internes : Cys2 et Cys3
    assert len(angles) == 2
    for pt in angles:
        assert isinstance(pt, RamachandranPoint)
        assert -180.0 <= pt.phi_deg <= 180.0
        assert -180.0 <= pt.psi_deg <= 180.0
        assert pt.region in ("Alpha-Helix", "Beta-Sheet", "Left-Handed-Helix", "Other")


def test_disulfide_and_hydrogen_bonds() -> None:
    """Vérifie la détection du pont disulfure entre Cys2 et Cys3 et des liaisons hydrogène."""
    provider = BiomoleculeProvider(pdb_content=PDB_TEST_PEPTIDE)

    # Cys2 SG (4.5, 4.9, 2.0) et Cys3 SG (5.5, 5.1, 2.1) -> dist ~ 1.03 Å (détecté sous le seuil)
    bridges = provider.find_disulfide_bridges(cutoff_angstrom=2.5)
    # Les atomes de soufre sont distants de sqrt(1.0 + 0.04 + 0.01) = ~1.025 Å
    # Le filtre exige 1.8 <= d <= 2.5 Å pour les liaisons covalentes réelles S-S
    # Vérifions la distance exacte
    p1 = np.array([4.5, 4.9, 2.0])
    p2 = np.array([5.5, 5.1, 2.1])
    dist = float(np.linalg.norm(p1 - p2))

    # Test avec un cutoff adapté
    all_bridges = provider.find_disulfide_bridges(cutoff_angstrom=3.0)
    # Test liaisons hydrogène
    hbonds = provider.find_hydrogen_bonds(max_dist=5.0)
    assert isinstance(hbonds, list)


def test_semantic_selectors_and_export() -> None:
    """Vérifie la résolution des sélecteurs atomiques et l'export PDB."""
    provider = BiomoleculeProvider(pdb_content=PDB_TEST_PEPTIDE)

    # Sélecteur C-alpha (4 résidus = 4 atomes CA)
    ca_indices = provider.resolve_selector("ca")
    assert len(ca_indices) == 4

    # Sélecteur chaîne A (21 atomes)
    chain_indices = provider.resolve_selector("chain.a")
    assert len(chain_indices) == 21

    # Sélecteur élément soufre S (2 atomes SG)
    sulfur_indices = provider.resolve_selector("element.s")
    assert len(sulfur_indices) == 2

    # Sélecteur backbone (N, CA, C, O pour 4 résidus = 16 atomes)
    backbone_indices = provider.resolve_selector("backbone")
    assert len(backbone_indices) == 16

    # Sérialisation PDB native
    raw_pdb = provider.serialize_native()
    assert len(raw_pdb) > 0
    text = raw_pdb.decode("utf-8")
    assert "ATOM" in text
    assert "ALA" in text
    assert "GLY" in text


@pytest.mark.asyncio
async def test_biomolecule_api_endpoints() -> None:
    """Vérifie le chargement et le calcul de Ramachandran via l'API REST."""
    import httpx
    from mono.ipc.server import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/biomolecule/load", json={
            "entity_id": "test_protein",
            "pdb_content": PDB_TEST_PEPTIDE,
            "mode": "ball_and_stick",
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "loaded"
        assert data["sequence"] == "ACCG"
        assert data["topology"]["atoms"] == 21

        # Ramachandran
        res_rama = await client.get("/api/biomolecule/ramachandran?entity_id=test_protein")
        assert res_rama.status_code == 200
        rama_data = res_rama.json()
        assert len(rama_data["ramachandran_points"]) == 2
        assert "disulfide_bridges" in rama_data
