"""Tests complets de validation du moteur d'analyse par éléments finis (FEA) Gmsh & CalculiX."""

from __future__ import annotations

import json
import build123d as bd
import numpy as np
from starlette.testclient import TestClient

from mono.providers.fea_gmsh import (
    FeaGmshProvider,
    FEAMaterial,
    FEABoundaryCondition,
)
from mono.providers.brep_build123d import BRepBuild123dProvider
from mono.ipc.server import app, state


def test_fea_tension_bar_analytical() -> None:
    """Valide la précision du solveur CalculiX et du maillage C3D10 par rapport à la solution analytique F/A."""
    # Barre prismatique de section 10x10 mm et longueur 50 mm
    bar = bd.Box(10.0, 10.0, 50.0)

    # Matériau Aluminium 6061-T6
    mat = FEAMaterial(
        name="ALUMINUM_6061",
        young_modulus_gpa=70.0,
        poisson_ratio=0.33,
        yield_strength_mpa=275.0,
    )

    # Traction pure de 4 kN appliquée sur la face supérieure (Z=50)
    bc = FEABoundaryCondition(
        fixed_predicate="min_z",
        load_predicate="max_z",
        applied_force_n=(0.0, 0.0, 4000.0),
    )

    provider = FeaGmshProvider(
        entity_id="bar_01",
        part=bar,
        material=mat,
        boundary=bc,
        element_size_max=4.0,
        target_safety_factor=1.5,
    )

    res = provider.solve()

    # 1. Vérification du maillage C3D10
    assert res.num_nodes > 100
    assert res.num_elements > 50

    # 2. Validation analytique de la contrainte (sigma = F / A = 4 000 N / 100 mm² = 40 MPa)
    # L'écart entre la contrainte de Von Mises moyenne et la théorie doit être <= 5%
    theoretical_stress_mpa = 40.0
    relative_error = abs(res.mean_von_mises_mpa - theoretical_stress_mpa) / theoretical_stress_mpa
    assert relative_error <= 0.05, f"Écart analytique trop élevé : {relative_error * 100:.2f}%"

    # 3. Validation du coefficient de sécurité
    # Sf = 275 / max_sigma >= 1.5
    assert res.is_structurally_safe is True
    assert res.safety_factor > 1.5

    # 4. Validation de la charge utile binaire pour Three.js
    mesh_payload = provider.tessellate_binary()
    assert mesh_payload.entity_id == "bar_01"
    assert mesh_payload.vertices.shape[1] == 3
    assert mesh_payload.normals.shape[1] == 3
    assert mesh_payload.indices.shape[1] == 3
    assert mesh_payload.scalar_field is not None
    assert mesh_payload.scalar_field.shape[0] == mesh_payload.vertices.shape[0]

    # Vérification que le champ scalaire est bien normalisé [0.0, 1.0]
    assert np.min(mesh_payload.scalar_field) >= 0.0
    assert np.max(mesh_payload.scalar_field) <= 1.0001

    # 5. Validation de la sérialisation native .frd
    frd_bytes = provider.serialize_native()
    assert len(frd_bytes) > 1000
    assert b"CalculiX" in frd_bytes or b"100C" in frd_bytes

    # 6. Introspection et rapport
    report = provider.introspect()
    assert report.is_valid_l0 is True
    assert report.topology_counts["nodes"] == res.num_nodes


def test_fea_overload_plastic_warning() -> None:
    """Vérifie la détection d'une surcharge structurelle avec facteur de sécurité < 1.5."""
    bar = bd.Box(10.0, 10.0, 30.0)

    # Force excessive de 40 kN (contrainte nominale 400 MPa > 275 MPa de limite élastique)
    mat = FEAMaterial(yield_strength_mpa=275.0)
    bc = FEABoundaryCondition(
        fixed_predicate="min_z",
        load_predicate="max_z",
        applied_force_n=(0.0, 0.0, 40000.0),
    )

    provider = FeaGmshProvider(
        entity_id="overloaded_bar",
        part=bar,
        material=mat,
        boundary=bc,
        element_size_max=5.0,
        target_safety_factor=1.5,
    )

    res = provider.solve()
    assert res.is_structurally_safe is False
    assert res.safety_factor < 1.0

    report = provider.introspect()
    assert len(report.dfm_warnings) > 0
    assert "Facteur de sécurité insuffisant" in report.dfm_warnings[0]


def test_fea_defeaturing_clean() -> None:
    """Vérifie l'application de l'algorithme OpenCASCADE BRepAlgoAPI_Defeaturing sur des micro-congés réels."""
    from mono.providers.fea_gmsh import DefeaturingReport

    # 1. Création d'une boîte avec 4 micro-congés de rayon 1.0 mm
    base = bd.Box(30.0, 20.0, 15.0)
    filleted = bd.fillet(base.edges().filter_by(bd.Axis.Z), radius=1.0)
    initial_faces = len(list(filleted.faces()))
    assert initial_faces == 10  # 6 faces principales + 4 congés cylindriques

    provider = FeaGmshProvider(
        entity_id="box_filleted_fea",
        part=filleted,
        element_size_max=5.0,
        auto_defeaturing=True,
    )

    # 2. Appel du defeaturing avec suppression des congés R <= 1.5 mm
    report = provider.apply_defeaturing(max_fillet_radius_mm=1.5)
    assert isinstance(report, DefeaturingReport)
    assert report.is_success is True
    assert report.fillets_removed_count == 4
    assert report.final_face_count == 6  # Restauration exacte des 6 faces planes sans congés

    # 3. Résolution FEA sous CalculiX sur la pièce défeaturée
    res = provider.solve()
    assert res.num_nodes > 50
    assert res.num_elements > 30
    assert res.max_von_mises_mpa > 0.0
    assert res.is_structurally_safe is True


def test_fea_semantic_groups_and_deck_assembly() -> None:
    """Vérifie la définition de groupes physiques sémantiques personnalisés et l'assemblage du deck CalculiX."""
    box = bd.Box(25.0, 25.0, 25.0)
    bc = FEABoundaryCondition(
        fixed_predicate="min_z",
        load_predicate="max_z",
        applied_force_n=(0.0, 0.0, -3000.0),
        semantic_groups={
            "BASE_SUPPORT": "min_z",
            "TOP_LOAD_PAD": "max_z",
            "SIDE_PANEL_X": "max_x",
        },
    )

    provider = FeaGmshProvider(
        entity_id="box_semantic_fea",
        part=box,
        boundary=bc,
        element_size_max=6.0,
    )

    res = provider.solve()
    assert res.num_nodes > 40
    assert res.max_von_mises_mpa > 0.0
    assert res.max_displacement_mm > 0.0
    assert res.safety_factor > 0.0


def test_fea_rest_api_and_websocket() -> None:
    """Vérifie le déclenchement de la simulation FEA via l'API REST et le WebSocket."""
    state.doc.entities.clear()
    state.doc.parameters.clear()
    state.doc.history.clear()

    # Ajout d'une entité dans le document
    part = bd.Box(10.0, 10.0, 25.0)
    base_provider = BRepBuild123dProvider("beam_fea", part)
    state.doc.add_entity("beam_fea", base_provider)

    client = TestClient(app)

    # Appel REST POST /api/fea/simulate
    res_api = client.post(
        "/api/fea/simulate",
        json={
            "entity_id": "beam_fea",
            "material_name": "ALU_6061",
            "young_modulus_gpa": 70.0,
            "poisson_ratio": 0.33,
            "yield_strength_mpa": 275.0,
            "force_x_n": 0.0,
            "force_y_n": 0.0,
            "force_z_n": 5000.0,
            "fixed_predicate": "min_z",
            "load_predicate": "max_z",
            "element_size_max": 5.0,
            "target_safety_factor": 1.5,
        },
    )

    assert res_api.status_code == 200
    data = res_api.json()
    assert data["entity_id"] == "beam_fea"
    assert data["mean_von_mises_mpa"] > 10.0
    assert data["is_structurally_safe"] is True
    assert data["num_nodes"] > 50

    # Test via WebSocket
    with client.websocket_connect("/ws") as ws:
        _ = ws.receive_json()  # welcome

        ws.send_text(
            json.dumps({
                "type": "run_fea",
                "entity_id": "beam_fea",
                "young_modulus_gpa": 70.0,
                "yield_strength_mpa": 275.0,
                "force_z": 2000.0,
                "element_size_max": 6.0,
            })
        )

        header_resp = ws.receive_json()
        assert header_resp["type"] == "binary_mesh_header"
        assert header_resp["entity_id"] == "beam_fea"

        mesh_bytes = ws.receive_bytes()
        assert len(mesh_bytes) == header_resp["byte_size"]

        fea_done = ws.receive_json()
        assert fea_done["type"] == "fea_completed"
        assert fea_done["entity_id"] == "beam_fea"
        assert fea_done["is_structurally_safe"] is True
