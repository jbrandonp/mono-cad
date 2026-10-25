"""Tests unitaires et d'intégration du moteur de mise en plan industrielle TechDraw 2D et GD&T."""

from __future__ import annotations

import build123d as bd
import pytest

from mono.providers.techdraw_drafting import (
    DimensionAnnotation,
    GdtFeatureControlFrame,
    TechDrawDraftingProvider,
    TechDrawReport,
    TitleBlock,
)


def test_techdraw_box_hlr_projections() -> None:
    """Vérifie la projection HLR sub-segmentaire 4 vues (Face, Dessus, Droite, Isométrique)."""
    box = bd.Box(60, 40, 20)
    tb = TitleBlock(title="SUPPORT MOTEUR", drawing_number="DWG-042", author="Brandon")
    provider = TechDrawDraftingProvider(part=box, sheet_format="A3", title_block=tb)

    report = provider.generate_drawing(views=["front", "top", "right", "iso"], auto_dimension=True)
    assert isinstance(report, TechDrawReport)
    assert report.views_count == 4
    assert report.total_visible_segments > 0
    assert report.total_hidden_segments > 0
    assert report.dimensions_count >= 2
    assert report.gdt_count >= 1
    assert report.is_valid_iso128 is True
    assert report.sheet_width_mm == 420.0
    assert report.sheet_height_mm == 297.0


def test_gdt_feature_control_frames_and_symbols() -> None:
    """Vérifie la génération des cadres de tolérance géométrique GD&T ISO 1101 / ASME Y14.5."""
    box = bd.Box(50, 50, 10)
    provider = TechDrawDraftingProvider(part=box)
    provider.generate_drawing()

    frame = GdtFeatureControlFrame(
        symbol="perpendicularity",
        tolerance_mm=0.02,
        diameter_symbol=False,
        material_condition="mmc",
        datum_primary="A",
        datum_secondary="B",
        view_id="front",
    )
    assert frame.get_symbol_unicode() == "⟂"

    provider.add_gdt_frame(frame)
    svg = provider.to_svg()
    assert "⟂" in svg
    assert "0.02" in svg
    assert "A" in svg
    assert "B" in svg


def test_associative_dimensioning() -> None:
    """Vérifie l'ajout de cotes dimensionnelles associatives avec flèches et lignes de rappel."""
    box = bd.Box(80, 50, 30)
    provider = TechDrawDraftingProvider(part=box)
    provider.generate_drawing(auto_dimension=False)

    dim = DimensionAnnotation(
        p1=(-40.0, -15.0),
        p2=(40.0, -15.0),
        value_mm=80.0,
        text_override="80.0 ±0.05",
        dim_type="horizontal",
        view_id="front",
    )
    provider.add_dimension(dim)

    svg = provider.to_svg()
    assert "80.0 ±0.05" in svg
    assert 'marker-start="url(#arrow)"' in svg
    assert 'marker-end="url(#arrow)"' in svg


def test_svg_iso7200_titleblock_and_projection_symbol() -> None:
    """Vérifie la conformité du cartouche d'ingénierie ISO 7200 et du symbole premier dièdre."""
    box = bd.Box(30, 20, 10)
    tb = TitleBlock(
        title="BRIDE DE SERRAGE",
        drawing_number="DWG-900",
        material="Titane Ti-6Al-4V",
        company="AEROSPACE MONO",
    )
    provider = TechDrawDraftingProvider(part=box, sheet_format="A4", orientation="landscape", title_block=tb)
    svg = provider.to_svg()

    assert "<svg" in svg
    assert "</svg>" in svg
    assert "BRIDE DE SERRAGE" in svg
    assert "DWG-900" in svg
    assert "Titane Ti-6Al-4V" in svg
    assert "AEROSPACE MONO" in svg
    # Symbole de projection premier dièdre (cône tronqué)
    assert "polygon points=" in svg


def test_dxf_autocad_export() -> None:
    """Vérifie l'export binaire DXF AutoCAD R2010 avec calques normalisés."""
    box = bd.Box(40, 20, 15)
    provider = TechDrawDraftingProvider(part=box)
    provider.generate_drawing()

    dxf_bytes = provider.to_dxf()
    assert len(dxf_bytes) > 1000
    dxf_str = dxf_bytes.decode("utf-8", errors="ignore")
    assert "0_BORDER" in dxf_str
    assert "0_TITLEBLOCK" in dxf_str
    assert "0_VISIBLE" in dxf_str
    assert "0_HIDDEN" in dxf_str


def test_geometry_provider_contract() -> None:
    """Vérifie la conformité de TechDrawDraftingProvider avec le contrat GeometryProvider."""
    provider = TechDrawDraftingProvider()
    payload = provider.tessellate_binary()

    assert payload.entity_id == "techdraw_001"
    assert len(payload.vertices) == 4
    assert len(payload.indices) == 2
    assert payload.scalar_field is not None

    rep = provider.introspect()
    assert rep.is_valid_l0 is True

    raw_native = provider.serialize_native()
    assert b"<svg" in raw_native


@pytest.mark.asyncio
async def test_techdraw_api_endpoints() -> None:
    """Vérifie la génération de plan 2D et les exports SVG/DXF via l'API REST."""
    import httpx
    from mono.core.document import EntityRecord
    from mono.ipc.server import app, state
    from mono.providers.brep_build123d import BRepBuild123dProvider

    box = bd.Box(50, 35, 15)
    state.doc.entities["box_for_techdraw"] = EntityRecord(
        entity_id="box_for_techdraw",
        provider_name="brep_build123d",
        scale_domain="milli",
        provider=BRepBuild123dProvider("box_for_techdraw", box),
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Génération du plan
        res_gen = await client.post("/api/techdraw/generate", json={
            "entity_id": "box_for_techdraw",
            "sheet_format": "A3",
            "title": "SUPPORT CHASSIS",
            "drawing_number": "DWG-101",
        })
        assert res_gen.status_code == 200
        data = res_gen.json()
        assert data["views_count"] == 4
        assert data["total_visible_segments"] > 0
        td_id = data["techdraw_entity_id"]

        # 2. Récupération SVG
        res_svg = await client.get(f"/api/techdraw/svg?entity_id={td_id}")
        assert res_svg.status_code == 200
        assert "image/svg+xml" in res_svg.headers["content-type"]
        assert "<svg" in res_svg.text
        assert "SUPPORT CHASSIS" in res_svg.text

        # 3. Récupération DXF
        res_dxf = await client.get(f"/api/techdraw/dxf?entity_id={td_id}")
        assert res_dxf.status_code == 200
        assert "application/dxf" in res_dxf.headers["content-type"]
        assert len(res_dxf.content) > 1000
