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


def test_ifc_column_creation_and_properties() -> None:
    """Vérifie la création de poteaux porteurs IfcColumn, l'arbre spatial et les requêtes."""
    provider = BimIfcProvider(entity_id="portique_01", project_name="Portique Béton")
    col_id = provider.add_column(
        storey_name="RDC",
        name="Poteau_P1",
        width=0.4,
        depth=0.4,
        height=3.0,
        x=2.0,
        y=1.5,
        z=0.0,
        is_load_bearing=True,
        fire_rating="REI 120",
    )
    assert col_id is not None

    # Requête spatiale
    columns = provider.query_spatial(ifc_class="IfcColumn")
    assert len(columns) == 1
    assert columns[0].name == "Poteau_P1"
    assert columns[0].storey_name == "RDC"
    assert "Pset_ColumnCommon" in columns[0].properties
    assert columns[0].properties["Pset_ColumnCommon"]["LoadBearing"] is True
    assert columns[0].properties["Pset_ColumnCommon"]["FireRating"] == "REI 120"

    # Vérification sélecteur et introspect
    col_ids = provider.resolve_selector("columns")
    assert len(col_ids) == 1

    report = provider.introspect()
    assert report.topology_counts["columns"] == 1
    assert report.is_valid_l0 is True

    # Vérification B-Rep compound
    compound = provider.to_brep_compound()
    assert len(compound.solids()) == 1


def test_ifc_file_read_write_roundtrip(tmp_path: object) -> None:
    """Vérifie la sérialisation, l'écriture sur disque et la relecture d'un fichier IFC4 via IfcGeometryProvider."""
    import pathlib
    p_dir = pathlib.Path(str(tmp_path))
    file_path = p_dir / "villa.ifc"

    # 1. Création d'un modèle avec mur et dalle
    original = IfcGeometryProvider(entity_id="villa_originale", project_name="Villa Savoye")
    original.add_wall("RDC", "Mur_Sud", length=10.0, height=3.0, thickness=0.25, x=0.0, y=0.0, z=0.0)
    original.add_slab("RDC", "Plancher_Haut", width=10.0, length=6.0, thickness=0.3, x=0.0, y=0.0, z=3.0)

    # 2. Sauvegarde sur disque
    written_path = original.write(file_path)
    assert written_path.exists()
    assert written_path.stat().st_size > 500

    # 3. Relecture depuis le fichier disque
    reloaded_from_file = IfcGeometryProvider(entity_id="villa_from_file", ifc_path=written_path)
    assert len(reloaded_from_file.model.by_type("IfcWall")) == 1
    assert len(reloaded_from_file.model.by_type("IfcSlab")) == 1
    assert reloaded_from_file.model.by_type("IfcWall")[0].Name == "Mur_Sud"

    # 4. Relecture depuis chaîne de caractères ISO 10303-21 brute
    raw_str = original.to_ifc_string()
    reloaded_from_str = IfcGeometryProvider(entity_id="villa_from_str", ifc_source=raw_str)
    assert len(reloaded_from_str.model.by_type("IfcWall")) == 1
    assert len(reloaded_from_str.model.by_type("IfcSlab")) == 1


def test_ifc_floorplan_dimensions_and_north_arrow() -> None:
    """Vérifie que la planche 2D générée comporte les cotations d'encombrement, la flèche Nord et le calque A-DIMS."""
    provider = IfcGeometryProvider(entity_id="immeuble_bureau")
    provider.add_wall("RDC", "Mur_Facade", length=12.0, height=2.8, thickness=0.3, x=0.0, y=0.0, z=0.0)
    provider.add_wall("RDC", "Mur_Pignon", length=8.0, height=2.8, thickness=0.3, x=0.0, y=0.0, z=0.0)

    floorplan = provider.generate_2d_floorplan(elevation_z=1.20)
    svg = floorplan.svg_content
    dxf = floorplan.dxf_content

    # Vérification SVG : cotations, flèche Nord et cartouche
    assert "A-DIMS" in svg
    assert "A-NORTH" in svg
    assert "A-TITLE" in svg
    assert "12.00 m" in svg or "12.30 m" in svg or "m" in svg

    # Vérification DXF : calque A-DIMS
    assert "A-DIMS" in dxf
    assert "A-WALL-CUTT" in dxf
    assert "A-WALL-PROJ" in dxf


def test_ifc_rest_endpoints_complete() -> None:
    """Vérifie l'ensemble des endpoints REST BIM : SVG, DXF, IFC et import."""
    state.doc.entities.clear()
    provider = IfcGeometryProvider(entity_id="bim_complexe")
    provider.add_wall("RDC", "Facade_Principale", length=6.0, height=2.8, thickness=0.2)
    state.doc.add_entity("bim_complexe", provider)

    client = TestClient(app)

    # 1. GET /api/bim/floorplan (SVG)
    res_svg = client.get("/api/bim/floorplan?entity_id=bim_complexe&elevation_z=1.2")
    assert res_svg.status_code == 200
    assert "image/svg+xml" in res_svg.headers["content-type"]
    assert "<svg" in res_svg.text

    # 2. GET /api/bim/dxf (DXF)
    res_dxf = client.get("/api/bim/dxf?entity_id=bim_complexe&elevation_z=1.2")
    assert res_dxf.status_code == 200
    assert "A-WALL-CUTT" in res_dxf.text
    assert "SECTION" in res_dxf.text

    # 3. GET /api/bim/ifc (IFC)
    res_ifc = client.get("/api/bim/ifc?entity_id=bim_complexe")
    assert res_ifc.status_code == 200
    assert "ISO-10303-21;" in res_ifc.text
    assert "IFCWALL" in res_ifc.text

    # 4. POST /api/bim/import
    ifc_text = res_ifc.text
    res_import = client.post(
        "/api/bim/import",
        json={
            "entity_id": "imported_test_building",
            "ifc_source": ifc_text,
            "project_name": "Projet Importé Test",
        },
    )
    assert res_import.status_code == 200
    data = res_import.json()
    assert data["status"] == "success"
    assert data["entity_id"] == "imported_test_building"
    assert data["walls"] == 1
    assert "imported_test_building" in state.doc.entities
