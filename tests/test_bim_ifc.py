"""Tests unitaires complets pour le moteur d'architecture BIM paramétrique IFC4 & Coupes 2D HLR."""

from __future__ import annotations

import ifcopenshell
from starlette.testclient import TestClient

from mono.providers.bim_ifc import (
    BimIfcProvider,
    IfcGeometryProvider,
    IFCElementInfo,
    FloorPlan2D,
)
from mono.ipc.server import app, state


def test_ifc4_hierarchy_creation_and_validation() -> None:
    """Vérifie la construction d'un bâtiment IFC4 normé ISO 16739 et la décomposition spatiale."""
    provider = BimIfcProvider(entity_id="house_01", project_name="Villa Mono")

    # 1. Vérification de la création de base
    assert provider.model.schema == "IFC4"
    assert len(provider.model.by_type("IfcProject")) == 1
    assert len(provider.model.by_type("IfcSite")) == 1
    assert len(provider.model.by_type("IfcBuilding")) == 1

    # 2. Ajout d'étages
    provider.add_storey("Etage_1", elevation=3.0)
    storeys = provider.get_storeys()
    assert len(storeys) == 2
    assert storeys[0]["name"] == "RDC"
    assert storeys[1]["name"] == "Etage_1"
    assert storeys[1]["elevation"] == 3.0

    # 3. Ajout d'éléments constructifs
    w_nord = provider.add_wall("RDC", "Mur_Nord", length=8.0, height=2.8, thickness=0.2, x=0.0, y=0.0, z=0.0)
    w_sud = provider.add_wall("RDC", "Mur_Sud", length=8.0, height=2.8, thickness=0.2, x=0.0, y=5.0, z=0.0)
    w_est = provider.add_wall("RDC", "Mur_Est", length=5.0, height=2.8, thickness=0.2, x=8.0, y=0.0, z=0.0)
    w_ouest = provider.add_wall("RDC", "Mur_Ouest", length=5.0, height=2.8, thickness=0.2, x=0.0, y=0.0, z=0.0)

    slab_id = provider.add_slab("RDC", "Dalle_Basse", width=8.2, length=5.2, thickness=0.2, x=-0.1, y=-0.1, z=-0.2)
    door_id = provider.add_door("RDC", "Porte_Entree", width=0.9, height=2.1, x=2.0, y=0.0, z=0.0)
    win_id = provider.add_window("RDC", "Fenetre_Salon", width=1.4, height=1.2, x=4.0, y=0.0, z=0.9)

    # 4. Requêtes spatiales
    walls_info = provider.query_spatial(ifc_class="IfcWall")
    assert len(walls_info) == 4
    for w in walls_info:
        assert w.storey_name == "RDC"
        assert "Pset_WallCommon" in w.properties
        assert w.properties["Pset_WallCommon"]["LoadBearing"] is True

    rdc_all = provider.query_spatial(storey_name="RDC")
    assert len(rdc_all) == 7  # 4 murs + 1 dalle + 1 porte + 1 fenêtre

    # 5. Audit de conformité buildingSMART
    report = provider.introspect()
    assert report.is_valid_l0 is True
    assert len(report.dfm_warnings) == 0
    assert report.topology_counts["storeys"] == 2
    assert report.topology_counts["walls"] == 4
    assert report.topology_counts["slabs"] == 1
    assert report.topology_counts["doors"] == 1
    assert report.topology_counts["windows"] == 1
    assert report.volume_m3 > 5.0


def test_ifc_2d_floorplan_hlr_cut() -> None:
    """Vérifie la coupe horizontale 2D d'architecture à Z=1.20m via BRepAlgoAPI_Section et HLRBRep_Algo."""
    provider = BimIfcProvider(entity_id="pavillon", project_name="Pavillon Test")
    provider.add_wall("RDC", "Mur_1", length=6.0, height=2.8, thickness=0.2, x=0.0, y=0.0, z=0.0)
    provider.add_wall("RDC", "Mur_2", length=4.0, height=2.8, thickness=0.2, x=0.0, y=3.0, z=0.0)

    floorplan: FloorPlan2D = provider.generate_2d_floorplan(elevation_z=1.20)

    # 1. Vérification des segments de coupe
    assert len(floorplan.cut_segments) > 0
    assert len(floorplan.visible_segments) > 0

    # 2. Vérification du format vectoriel SVG
    svg = floorplan.svg_content
    assert "<svg" in svg
    assert "A-WALL-CUTT" in svg
    assert "A-WALL-PROJ" in svg
    assert "PLAN DE COUPE D'ÉTAGE" in svg

    # 3. Vérification du format DXF
    dxf = floorplan.dxf_content
    assert "SECTION" in dxf
    assert "ENTITIES" in dxf
    assert "A-WALL-CUTT" in dxf
    assert "EOF" in dxf


def test_ifc_binary_tessellation_for_threejs() -> None:
    """Vérifie l'export binaire zero-copy des entités architecturales pour Three.js."""
    provider = BimIfcProvider(entity_id="immeuble_01")
    provider.add_wall("RDC", "Fascia", length=4.0, height=2.8, thickness=0.2)

    payload = provider.tessellate_binary()
    assert payload.entity_id == "immeuble_01"
    assert payload.vertices.shape[0] >= 8
    assert payload.vertices.shape[1] == 3
    assert payload.normals.shape[0] == payload.vertices.shape[0]
    assert payload.indices.shape[0] >= 12
    assert payload.face_ids.shape[0] == payload.indices.shape[0]

    # Vérification de l'alignement binaire
    raw = payload.to_bytes()
    assert len(raw) > 0


def test_ifc_native_serialization_and_reopening() -> None:
    """Vérifie la sérialisation au standard ISO 10303-21 et la réouverture fidèle."""
    provider = BimIfcProvider(entity_id="chalet", project_name="Chalet Montagne")
    provider.add_wall("RDC", "Mur_Bois", length=7.0, height=2.5, thickness=0.3)

    raw_ifc_bytes = provider.serialize_native()
    assert len(raw_ifc_bytes) > 500
    assert b"ISO-10303-21;" in raw_ifc_bytes
    assert b"IFCWALL" in raw_ifc_bytes

    # Réouverture avec IfcOpenShell depuis la chaîne de caractères
    reopened = ifcopenshell.file.from_string(raw_ifc_bytes.decode("utf-8"))
    assert len(reopened.by_type("IfcWall")) == 1
    assert reopened.by_type("IfcWall")[0].Name == "Mur_Bois"


def test_ifc_geometry_provider_alias_and_rest_endpoint() -> None:
    """Vérifie l'alias de conformité IfcGeometryProvider et l'endpoint REST /api/bim/floorplan."""
    assert IfcGeometryProvider is BimIfcProvider

    state.doc.entities.clear()
    provider = BimIfcProvider(entity_id="bim_appart")
    provider.add_wall("RDC", "Mur_Appart", length=5.0, height=2.8, thickness=0.2)
    state.doc.add_entity("bim_appart", provider)

    client = TestClient(app)
    res = client.get("/api/bim/floorplan?entity_id=bim_appart&elevation_z=1.2")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in res.text
    assert "A-WALL-CUTT" in res.text
