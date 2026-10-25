"""Tests de validation pour la sérialisation BinaryCIF, Mol* et la dynamique OpenMM."""

from __future__ import annotations

import msgpack
import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from mono.ipc.server import app
from mono.providers.biomolecule_gemmi import BiomoleculeProvider
from mono.providers.biomolecule_openmm import OpenMMSimulator, OpenMMSimulationResult, TrajectoryFrame

# PDB d'un tétrapeptide (Ala-Cys-Cys-Gly)
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

# Dipeptide avec oxygène terminal OXT pour le champ Amber14 tout-atome
PDB_AMBER_PEPTIDE = """ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00 20.00           N
ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00 20.00           C
ATOM      3  C   ALA A   1       2.009   1.420   0.000  1.00 20.00           C
ATOM      4  O   ALA A   1       1.306   2.428   0.000  1.00 20.00           O
ATOM      5  CB  ALA A   1       1.989  -0.771  -1.205  1.00 20.00           C
ATOM      6  N   GLY A   2       3.242   1.420   0.500  1.00 25.00           N
ATOM      7  CA  GLY A   2       3.800   2.700   0.500  1.00 25.00           C
ATOM      8  C   GLY A   2       5.200   2.700   0.000  1.00 25.00           C
ATOM      9  O   GLY A   2       5.800   3.700   0.000  1.00 25.00           O
ATOM     10  OXT GLY A   2       5.800   1.600   0.000  1.00 25.00           O
TER      11      GLY A   2
END
"""


def test_binary_cif_serialization_and_structure() -> None:
    """Vérifie la sérialisation MessagePack conforme à la spécification BinaryCIF v0.3.0."""
    provider = BiomoleculeProvider(pdb_content=PDB_TEST_PEPTIDE)
    bcif_bytes = provider.serialize_binary_cif()

    assert len(bcif_bytes) > 0
    # Déballage MessagePack
    unpacked = msgpack.unpackb(bcif_bytes, raw=False)
    assert unpacked["version"] == "0.3.0"
    assert "Mono-Gemmi" in unpacked["encoder"]
    assert len(unpacked["dataBlocks"]) == 1

    block = unpacked["dataBlocks"][0]
    cat_names = [c["name"] for c in block["categories"]]
    assert "_entry" in cat_names
    assert "_atom_site" in cat_names

    atom_site = next(c for c in block["categories"] if c["name"] == "_atom_site")
    assert atom_site["rowCount"] == 21

    col_names = [col["name"] for col in atom_site["columns"]]
    for expected in ("id", "type_symbol", "label_atom_id", "label_comp_id", "Cartn_x", "Cartn_y", "Cartn_z"):
        assert expected in col_names


def test_mmcif_text_serialization() -> None:
    """Vérifie la sérialisation mmCIF textuelle standard."""
    provider = BiomoleculeProvider(pdb_content=PDB_TEST_PEPTIDE)
    cif_text = provider.serialize_mmcif()

    assert len(cif_text) > 0
    assert "_atom_site.Cartn_x" in cif_text
    assert "ALA" in cif_text
    assert "CYS" in cif_text
    assert "GLY" in cif_text


def test_update_positions_and_tessellation() -> None:
    """Vérifie que la mise à jour des coordonnées atomiques modifie immédiatement le maillage 3D."""
    provider = BiomoleculeProvider(pdb_content=PDB_TEST_PEPTIDE, mode="ball_and_stick")
    pos_init = provider.get_atom_positions()
    assert pos_init.shape == (21, 3)

    mesh_init = provider.tessellate_binary()
    init_center_x = float(np.mean(mesh_init.vertices[:, 0]))

    # Déplacement de 10 Angströms le long de l'axe X
    new_pos = pos_init.copy()
    new_pos[:, 0] += 10.0
    provider.update_positions(new_pos)

    updated_pos = provider.get_atom_positions()
    np.testing.assert_allclose(updated_pos[:, 0], pos_init[:, 0] + 10.0, atol=1e-4)

    mesh_updated = provider.tessellate_binary()
    updated_center_x = float(np.mean(mesh_updated.vertices[:, 0]))
    assert updated_center_x > init_center_x + 8.0


def test_openmm_simulation_enm_fallback() -> None:
    """Vérifie la dynamique moléculaire sous modèle élastique harmonique (ENM)."""
    sim = OpenMMSimulator(pdb_content=PDB_TEST_PEPTIDE, temperature_k=300.0, time_step_fs=2.0)
    assert sim.is_enm_fallback is True

    init_e = sim.minimize_energy(max_iterations=10)
    assert isinstance(init_e, float)

    result = sim.simulate(total_steps=40, report_interval=10)
    assert isinstance(result, OpenMMSimulationResult)
    assert result.success is True
    assert result.method == "ENM_Harmonic"
    assert result.total_steps == 40
    assert len(result.frames) == 5  # frame 0 + 4 steps
    assert result.mean_rmsd_angstrom >= 0.0

    frame0 = result.frames[0]
    assert isinstance(frame0, TrajectoryFrame)
    assert frame0.frame_index == 0
    assert frame0.rmsd_angstrom == 0.0
    assert frame0.positions.shape == (21, 3)


def test_openmm_simulation_amber_all_atom() -> None:
    """Vérifie la dynamique moléculaire tout-atome sous champ de force Amber14."""
    sim = OpenMMSimulator(pdb_content=PDB_AMBER_PEPTIDE, temperature_k=300.0, time_step_fs=2.0)
    assert sim.is_enm_fallback is False

    min_e = sim.minimize_energy(max_iterations=15)
    assert min_e < 0.0  # L'énergie potentielle équilibrée doit être négative

    result = sim.simulate(total_steps=20, report_interval=10)
    assert result.success is True
    assert result.method == "Amber14_AllAtom"
    assert len(result.frames) == 3
    assert result.max_rmsd_angstrom > 0.0


@pytest.mark.asyncio
async def test_biomolecule_rest_api_lifecycle() -> None:
    """Vérifie l'ensemble des endpoints REST biomoléculaires et la dynamique OpenMM via AsyncClient."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Chargement de la macromolécule
        load_resp = await client.post(
            "/api/biomolecule/load",
            json={
                "entity_id": "test_protein_001",
                "pdb_content": PDB_TEST_PEPTIDE,
                "mode": "ball_and_stick",
            },
        )
        assert load_resp.status_code == 200
        load_data = load_resp.json()
        assert load_data["status"] == "loaded"
        assert load_data["chains"] == ["A"]
        assert load_data["sequence"] == "ACCG"

        # 2. Export BinaryCIF (.bcif)
        bcif_resp = await client.get("/api/biomolecule/bcif?entity_id=test_protein_001")
        assert bcif_resp.status_code == 200
        assert bcif_resp.headers["content-type"] == "application/octet-stream"
        assert len(bcif_resp.content) > 0

        # 3. Export mmCIF textuel (.cif)
        cif_resp = await client.get("/api/biomolecule/cif?entity_id=test_protein_001")
        assert cif_resp.status_code == 200
        assert cif_resp.headers["content-type"].startswith("text/plain")
        assert "_atom_site" in cif_resp.text

        # 4. Conformation de Ramachandran
        rama_resp = await client.get("/api/biomolecule/ramachandran?entity_id=test_protein_001")
        assert rama_resp.status_code == 200
        rama_data = rama_resp.json()
        assert len(rama_data["ramachandran_points"]) == 2

        # 5. Simulation OpenMM
        sim_resp = await client.post(
            "/api/biomolecule/simulate",
            json={
                "entity_id": "test_protein_001",
                "total_steps": 30,
                "report_interval": 10,
                "temperature_k": 300.0,
            },
        )
        assert sim_resp.status_code == 200
        sim_data = sim_resp.json()
        assert sim_data["success"] is True
        assert sim_data["frame_count"] == 4

        # 6. Récupération d'une frame de trajectoire
        frame_resp = await client.get("/api/biomolecule/trajectory/frame?entity_id=test_protein_001&frame_index=1")
        assert frame_resp.status_code == 200
        frame_data = frame_resp.json()
        assert frame_data["frame_index"] == 1
        assert "potential_energy_kj_mol" in frame_data
        assert "rmsd_angstrom" in frame_data
        assert len(frame_data["positions"]) == 21
