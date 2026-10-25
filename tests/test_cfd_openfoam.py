"""Tests unitaires complets pour le générateur de cas et solveur CFD OpenFOAM."""

from __future__ import annotations

import tempfile
from pathlib import Path
import build123d as bd

from mono.providers.cfd_openfoam import (
    CfdOpenFoamProvider,
    CFDFluidProperties,
    CFDBoundaryConfig,
    CFDSolverConfig,
)


def test_cfd_openfoam_case_generation_end_to_end() -> None:
    """Vérifie la génération complète de l'arborescence standard OpenFOAM v20+ pour simpleFoam."""
    # Création d'un profil aérodynamique simplifié (bloc effilé)
    part = bd.Box(100.0, 20.0, 15.0)

    fluid = CFDFluidProperties(
        name="AIR_20C",
        kinematic_viscosity_m2_s=1.5e-5,
        density_kg_m3=1.225,
    )

    boundary = CFDBoundaryConfig(
        inlet_velocity_m_s=(25.0, 0.0, 0.0),
        inlet_direction="min_x",
        outlet_direction="max_x",
        turbulence_intensity=0.04,
    )

    solver = CFDSolverConfig(
        solver_name="simpleFoam",
        turbulence_model="kOmegaSST",
        num_iterations=400,
        write_interval=50,
    )

    provider = CfdOpenFoamProvider(
        entity_id="wing_cfd",
        part=part,
        fluid=fluid,
        boundary=boundary,
        solver=solver,
    )

    with tempfile.TemporaryDirectory() as tmp_str:
        case_dir = Path(tmp_str) / "openfoam_case"
        provider.generate_case_directory(case_dir)

        # 1. Vérification des dossiers fondamentaux
        assert (case_dir / "0").is_dir()
        assert (case_dir / "constant").is_dir()
        assert (case_dir / "system").is_dir()
        assert (case_dir / "constant" / "triSurface").is_dir()

        # 2. Vérification des champs initiaux dans 0/
        u_file = case_dir / "0" / "U"
        assert u_file.exists()
        u_content = u_file.read_text(encoding="utf-8")
        assert "volVectorField" in u_content
        assert "(25.0 0.0 0.0)" in u_content
        assert "noSlip" in u_content

        p_file = case_dir / "0" / "p"
        assert p_file.exists()
        assert "volScalarField" in p_file.read_text(encoding="utf-8")

        assert (case_dir / "0" / "k").exists()
        assert (case_dir / "0" / "omega").exists()
        assert (case_dir / "0" / "nut").exists()

        # 3. Vérification des dictionnaires dans constant/
        tp_file = case_dir / "constant" / "transportProperties"
        assert tp_file.exists()
        assert "1.500000e-05" in tp_file.read_text(encoding="utf-8")

        turb_file = case_dir / "constant" / "turbulenceProperties"
        assert turb_file.exists()
        assert "kOmegaSST" in turb_file.read_text(encoding="utf-8")

        stl_file = case_dir / "constant" / "triSurface" / "part_geometry.stl"
        assert stl_file.exists()
        assert stl_file.stat().st_size > 500

        # 4. Vérification des fichiers de maillage et de contrôle dans system/
        cd_file = case_dir / "system" / "controlDict"
        assert cd_file.exists()
        cd_content = cd_file.read_text(encoding="utf-8")
        assert "simpleFoam" in cd_content
        assert "forces" in cd_content

        assert (case_dir / "system" / "fvSchemes").exists()
        assert (case_dir / "system" / "fvSolution").exists()

        bm_file = case_dir / "system" / "blockMeshDict"
        assert bm_file.exists()
        assert "hex" in bm_file.read_text(encoding="utf-8")

        snappy_file = case_dir / "system" / "snappyHexMeshDict"
        assert snappy_file.exists()
        assert "castellatedMesh" in snappy_file.read_text(encoding="utf-8")


def test_cfd_simulation_analytical_report() -> None:
    """Vérifie le calcul des grandeurs aérodynamiques (traînée, portance, Cd)."""
    part = bd.Box(50.0, 20.0, 10.0)
    provider = CfdOpenFoamProvider(
        entity_id="car_spoiler",
        part=part,
        boundary=CFDBoundaryConfig(inlet_velocity_m_s=(30.0, 0.0, 0.0)),
    )

    with tempfile.TemporaryDirectory() as tmp_str:
        run_dir = Path(tmp_str) / "run_spoiler"
        report = provider.simulate(run_dir)

        assert report.drag_force_n > 0.0
        assert report.drag_coefficient_cd > 0.05
        assert report.num_iterations == 300
        assert Path(report.case_path).exists()


def test_cfd_geometry_provider_contract() -> None:
    """Vérifie la conformité de CfdOpenFoamProvider au contrat d'interface GeometryProvider."""
    part = bd.Box(30.0, 15.0, 10.0)
    provider = CfdOpenFoamProvider(entity_id="test_body", part=part)

    # 1. Tessellation binaire
    payload = provider.tessellate_binary()
    assert payload.entity_id == "test_body"
    assert payload.vertices.shape[0] > 0
    assert payload.indices.shape[0] > 0

    # 2. Introspection
    val = provider.introspect()
    assert val.is_valid_l0 is True
    assert val.is_watertight is True

    # 3. Sérialisation native
    native = provider.serialize_native()
    assert len(native) > 100
