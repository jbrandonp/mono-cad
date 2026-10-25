"""Tests unitaires du moteur de fabrication additive et d'audit DfAM sous OrcaSlicer CLI."""

from __future__ import annotations

import build123d as bd
import pytest

from mono.providers.additive_slicer import AdditiveSlicerProvider, DfAMReport, GCodeAnalysis


def test_dfam_audit_box() -> None:
    """Vérifie l'audit DfAM sur un cube simple sans surplomb."""
    box = bd.Box(40, 40, 20)
    provider = AdditiveSlicerProvider(part=box, build_volume=(250, 250, 250))

    report = provider.audit_dfam()
    assert isinstance(report, DfAMReport)
    assert report.fits_build_plate is True
    assert report.is_manufacturable is True
    # Un cube n'a aucun surplomb supérieur à 45°
    assert report.overhang_ratio < 0.05
    assert len(report.warnings) == 0


def test_dfam_audit_overhang_cantilever() -> None:
    """Vérifie la détection de surplombs critiques sur une géométrie en porte-à-faux."""
    # Création d'une pièce en T avec grands surplombs à 90° sous les bras du T
    base = bd.Box(20, 20, 40)
    top_bar = bd.Box(80, 20, 15).move(bd.Location((0, 0, 25)))
    t_shape = base + top_bar

    provider = AdditiveSlicerProvider(part=t_shape)
    report = provider.audit_dfam(max_overhang_angle_deg=45.0)

    # Doit détecter les faces horizontales sous les bras du T (surplomb à 90°)
    assert report.overhang_area_mm2 > 0.0
    assert report.max_overhang_angle_deg >= 45.0
    assert any("supports" in w.lower() for w in report.warnings)


def test_analytical_slicing_and_gcode_analysis(tmp_path) -> None:
    """Vérifie la génération et l'analyse thermodynamique/métrologique du G-code."""
    cyl = bd.Cylinder(radius=15, height=10)
    provider = AdditiveSlicerProvider(part=cyl)

    analysis = provider.slice_to_gcode(
        output_dir=str(tmp_path),
        layer_height_mm=0.20,
        infill_percent=20,
        nozzle_temp_c=210,
        bed_temp_c=60,
        use_orcaslicer=False,  # Test du tranchage analytique déterministe
    )

    assert isinstance(analysis, GCodeAnalysis)
    assert analysis.is_valid is True
    assert analysis.has_heating is True
    assert analysis.has_homing is True
    assert analysis.nozzle_temp_c == 210.0
    assert analysis.bed_temp_c == 60.0
    assert analysis.layer_count >= 10
    assert analysis.filament_length_mm > 0.0
    assert analysis.estimated_mass_grams > 0.0
    assert analysis.estimated_time_seconds > 0.0


def test_geometry_provider_contract_and_scalar_field() -> None:
    """Vérifie la conformité au contrat GeometryProvider et le champ scalaire de surplomb."""
    cone = bd.Cone(bottom_radius=20, top_radius=5, height=25)
    provider = AdditiveSlicerProvider(part=cone)

    payload = provider.tessellate_binary()
    assert payload.entity_id == "additive_slicer_part"
    assert len(payload.vertices) > 0
    assert len(payload.normals) == len(payload.vertices)
    assert payload.scalar_field is not None
    assert len(payload.scalar_field) == len(payload.vertices)

    # Introspection
    rep = provider.introspect()
    assert rep.is_valid_l0 is True

    # Export natif STEP
    step_bytes = provider.serialize_native()
    assert len(step_bytes) > 0
    assert b"ISO-10303-21" in step_bytes


@pytest.mark.asyncio
async def test_slicer_api_endpoints() -> None:
    """Vérifie l'audit DfAM et le tranchage via l'API REST."""
    import httpx
    from mono.ipc.server import app, state
    from mono.core.document import EntityRecord
    from mono.providers.brep_build123d import BRepBuild123dProvider

    box = bd.Box(30, 30, 15)
    state.doc.entities["box_for_slicing"] = EntityRecord(
        entity_id="box_for_slicing",
        provider_name="brep_build123d",
        scale_domain="milli",
        provider=BRepBuild123dProvider("box_for_slicing", box),
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Audit DfAM
        res_audit = await client.post("/api/slicer/audit", json={"entity_id": "box_for_slicing"})
        assert res_audit.status_code == 200
        data_audit = res_audit.json()
        assert data_audit["is_manufacturable"] is True
        assert data_audit["fits_build_plate"] is True

        # Tranchage
        res_slice = await client.post("/api/slicer/slice", json={
            "entity_id": "box_for_slicing",
            "layer_height_mm": 0.20,
        })
        assert res_slice.status_code == 200
        data_slice = res_slice.json()
        assert data_slice["is_valid"] is True
        assert data_slice["layer_count"] > 0
        slicer_entity_id = data_slice["slicer_entity_id"]

        # Téléchargement G-code fabrication additive
        res_gcode = await client.get(f"/api/slicer/gcode?entity_id={slicer_entity_id}")
        assert res_gcode.status_code == 200
        assert "G28" in res_gcode.text
        assert "M104" in res_gcode.text or "M109" in res_gcode.text
