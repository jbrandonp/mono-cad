import numpy as np
import build123d as bd

from mono.providers.brep_build123d import BRepBuild123dProvider
from mono.core.geometry_provider import BinaryMeshPayload, ValidationReport


def test_brep_provider_tessellation_and_binary_roundtrip() -> None:
    """Vérifie la tessellation binaire, le typage des tableaux et la désérialisation zero-copy."""
    # Création d'une boîte 100 x 50 x 20 mm
    box = bd.Box(100.0, 50.0, 20.0)
    provider = BRepBuild123dProvider("box_001", box, unit_scale_to_meters=0.001)

    payload = provider.tessellate_binary(linear_deflection=0.1)

    assert isinstance(payload, BinaryMeshPayload)
    assert payload.entity_id == "box_001"
    assert payload.vertices.dtype == np.float32
    assert payload.normals.dtype == np.float32
    assert payload.indices.dtype == np.uint32
    assert payload.face_ids.dtype == np.uint32

    # Une boîte possède au moins 8 sommets et 12 triangles
    vertex_count = payload.vertices.shape[0]
    triangle_count = payload.indices.shape[0]
    assert vertex_count >= 8
    assert triangle_count >= 12
    assert payload.face_ids.shape[0] == triangle_count

    # Test sérialisation / désérialisation binaire contiguë
    raw_bytes = payload.to_bytes()
    expected_byte_size = (vertex_count * 3 * 4) + (vertex_count * 3 * 4) + (triangle_count * 3 * 4) + (triangle_count * 4)
    assert len(raw_bytes) == expected_byte_size

    restored = BinaryMeshPayload.from_bytes("box_001", vertex_count, triangle_count, raw_bytes)
    np.testing.assert_array_equal(payload.vertices, restored.vertices)
    np.testing.assert_array_equal(payload.normals, restored.normals)
    np.testing.assert_array_equal(payload.indices, restored.indices)
    np.testing.assert_array_equal(payload.face_ids, restored.face_ids)


def test_brep_provider_introspect_l0_l1() -> None:
    """Vérifie l'audit déterministe L0/L1 et les calculs physiques en unités SI (mètres)."""
    # Boîte 100 mm x 50 mm x 20 mm = 0.1 m x 0.05 m x 0.02 m
    # Volume théorique = 0.0001 m³ = 1e-4 m³
    box = bd.Box(100.0, 50.0, 20.0)
    provider = BRepBuild123dProvider("box_002", box, unit_scale_to_meters=0.001)

    report = provider.introspect()
    assert isinstance(report, ValidationReport)
    assert report.is_valid_l0 is True
    assert report.is_watertight is True
    assert abs(report.volume_m3 - 1e-4) < 1e-7

    # Vérification topologique : une boîte a 6 faces, 12 arêtes, 8 sommets
    assert report.topology_counts["faces"] == 6
    assert report.topology_counts["edges"] == 12
    assert report.topology_counts["vertices"] == 8

    # Pas d'alerte DFM car dimensions > 1 mm
    assert len(report.dfm_warnings) == 0


def test_brep_provider_resolve_selector() -> None:
    """Vérifie l'évaluation des sélecteurs spatio-sémantiques."""
    box = bd.Box(10.0, 20.0, 30.0)
    provider = BRepBuild123dProvider("box_003", box)

    # Sélection de la face la plus haute selon l'axe Z
    selected_faces = provider.resolve_selector("faces().sort_by(Axis.Z)[-1]")
    assert len(selected_faces) == 1
    face_idx = selected_faces[0]
    assert 0 <= face_idx < 6


def test_brep_provider_serialize_native_step() -> None:
    """Vérifie l'exportation du solide au format STEP AP242."""
    box = bd.Box(10.0, 20.0, 30.0)
    provider = BRepBuild123dProvider("box_004", box)

    step_bytes = provider.serialize_native()
    assert len(step_bytes) > 0
    # Un fichier STEP valide commence par ISO-10303-21
    assert b"ISO-10303-21" in step_bytes
