"""Tests unitaires du moteur de FAO et d'usinage CNC multi-axes CamMachiningProvider."""

from __future__ import annotations

import math
import build123d as bd
import pytest

from mono.providers.cam_machining import (
    CamMachiningProvider,
    CamAuditReport,
    MachiningParameters,
    ToolDefinition,
    ToolpathPoint,
)


def test_optimal_stepover_and_scallop_formulas() -> None:
    """Vérifie l'exactitude mathématique du pas optimal (Ra) et de la hauteur de crête."""
    tool = ToolDefinition(diameter_mm=10.0, corner_radius_mm=5.0)
    provider = CamMachiningProvider(tool=tool)

    # Pour Ra = 0.8 µm = 0.0008 mm, s = sqrt(32 * 5.0 * 0.0008) = sqrt(0.128) ≈ 0.35777 mm
    stepover = provider.calculate_optimal_stepover(target_ra_um=0.8)
    expected_s = math.sqrt(32.0 * 5.0 * 0.0008)
    assert pytest.approx(stepover, rel=1e-3) == expected_s

    # Hauteur de crête pour ce stepover : h = s^2 / (8 * R) = 0.128 / 40 = 0.0032 mm = 3.2 µm
    scallop = provider.calculate_scallop_height(stepover)
    assert pytest.approx(scallop, rel=1e-3) == 3.2


def test_rtcp_pivot_calculation() -> None:
    """Vérifie la cinématique inverse RTCP (position pivot broche depuis la pointe d'outil)."""
    tool = ToolDefinition(length_mm=100.0)
    provider = CamMachiningProvider(tool=tool)

    tip_pos = (10.0, 20.0, 30.0)

    # 1. Outil vertical (B=0°, C=0°) -> V = (0, 0, 1) -> Pivot = Tip + (0, 0, 100) = (10, 20, 130)
    pivot_vert = provider.compute_rtcp_pivot(tip_pos, b_deg=0.0, c_deg=0.0)
    assert pivot_vert == (10.0, 20.0, 130.0)

    # 2. Outil incliné à 90° selon X (B=90°, C=0°) -> V = (1, 0, 0) -> Pivot = (110, 20, 30)
    pivot_horiz = provider.compute_rtcp_pivot(tip_pos, b_deg=90.0, c_deg=0.0)
    assert pytest.approx(pivot_horiz[0], abs=1e-2) == 110.0
    assert pytest.approx(pivot_horiz[1], abs=1e-2) == 20.0
    assert pytest.approx(pivot_horiz[2], abs=1e-2) == 30.0


def test_toolpath_generation_and_audit() -> None:
    """Vérifie la génération de trajectoires 3D, l'estimation de temps et le rapport d'audit."""
    box = bd.Box(50, 50, 20)
    provider = CamMachiningProvider(part=box)

    pts = provider.generate_toolpath(strategy="finishing_scallop")
    assert len(pts) > 10

    audit = provider.audit_cam()
    assert isinstance(audit, CamAuditReport)
    assert audit.total_points == len(pts)
    assert audit.toolpath_length_mm > 0.0
    assert audit.estimated_machining_time_seconds > 0.0
    assert audit.is_collision_free is True


def test_post_processors_flavors() -> None:
    """Vérifie la syntaxe des post-processeurs Fanuc, Siemens, Heidenhain, Haas et LinuxCNC."""
    box = bd.Box(30, 30, 15)
    provider = CamMachiningProvider(part=box)

    # Fanuc : G43.4 H1 et G49
    fanuc_code = provider.post_process_gcode(flavor="fanuc")
    assert "G43.4 H1" in fanuc_code
    assert "G49" in fanuc_code
    assert "M30" in fanuc_code

    # Siemens : TRAORI et TRAFOOF
    siemens_code = provider.post_process_gcode(flavor="siemens")
    assert "TRAORI" in siemens_code
    assert "TRAFOOF" in siemens_code

    # Heidenhain : M128 et M129
    heidenhain_code = provider.post_process_gcode(flavor="heidenhain")
    assert "M128" in heidenhain_code
    assert "M129" in heidenhain_code

    # Haas NGC : G234 TCPC et G49
    haas_code = provider.post_process_gcode(flavor="haas")
    assert "G234" in haas_code
    assert "G49" in haas_code

    # LinuxCNC : M6 G43
    linuxcnc_code = provider.post_process_gcode(flavor="linuxcnc")
    assert "LINUXCNC" in linuxcnc_code or "LinuxCNC" in linuxcnc_code
    assert "G43 H1" in linuxcnc_code

    # GRBL Universel : G21 G90 G17 M03 M05 M30
    grbl_code = provider.post_process_gcode(flavor="grbl")
    assert "GRBL" in grbl_code
    assert "G21" in grbl_code
    assert "G90" in grbl_code
    assert "M03" in grbl_code
    assert "M05" in grbl_code
    assert "M30" in grbl_code


def test_cam_strategies_drop_cutter_pocket_profile_probe() -> None:
    """Vérifie l'exécution des stratégies avancées : Drop-Cutter, Évidement, Profilage et Palpage."""
    box = bd.Box(40, 40, 20)
    provider = CamMachiningProvider(part=box)

    # 1. Drop-Cutter 3D avec angles d'attaque (Lead) et d'inclinaison (Tilt)
    dc_pts = provider.generate_toolpath(strategy="drop_cutter_surface")
    assert len(dc_pts) > 10
    # Vérifie que les angles B et C ont été calculés pour éviter la coupe à vitesse nulle au centre
    assert any(pt.b_deg != 0.0 or pt.c_deg != 0.0 for pt in dc_pts)

    # 2. Évidement 2.5D (Adaptive Pocketing) avec rampe hélicoïdale
    pocket_pts = provider.generate_toolpath(strategy="adaptive_pocketing")
    assert len(pocket_pts) > 10
    # Vérifie la présence de points de plongée hélicoïdale
    z_coords = [pt.z for pt in pocket_pts]
    assert min(z_coords) < 0.0

    # 3. Profilage 2.5D avec engagement/dégagement tangentiel en arc de cercle
    profile_pts = provider.generate_toolpath(strategy="profile_contouring")
    assert len(profile_pts) >= 8

    # 4. Palpage CMM de métrologie
    probe_pts = provider.generate_toolpath(strategy="probing_cmm")
    assert len(probe_pts) >= 3


def test_geometry_provider_contract() -> None:
    """Vérifie la tessellation binaire zero-copy et les sélecteurs de points rapides/travail."""
    provider = CamMachiningProvider()

    payload = provider.tessellate_binary()
    assert payload.entity_id == "cam_toolpath_5axis"
    assert len(payload.vertices) > 0
    assert payload.scalar_field is not None
    assert len(payload.scalar_field) == len(payload.vertices)

    # Sélecteurs de mouvements G0 / G1
    rapid_indices = provider.resolve_selector("rapid")
    cutting_indices = provider.resolve_selector("cutting")
    assert len(rapid_indices) > 0
    assert len(cutting_indices) > 0
    assert len(rapid_indices) + len(cutting_indices) == len(provider.toolpath)

    # Introspection
    report = provider.introspect()
    assert report.is_valid_l0 is True

    # Sérialisation G-code native
    raw_gcode = provider.serialize_native()
    assert len(raw_gcode) > 0
    assert b"RTCP" in raw_gcode


@pytest.mark.asyncio
async def test_cam_api_endpoints() -> None:
    """Vérifie la génération de trajectoire FAO 5-axes et l'export G-code via l'API REST."""
    import httpx
    from mono.ipc.server import app, state
    from mono.core.document import EntityRecord
    from mono.providers.brep_build123d import BRepBuild123dProvider

    box = bd.Box(40, 40, 20)
    state.doc.entities["box_for_cam"] = EntityRecord(
        entity_id="box_for_cam",
        provider_name="brep_build123d",
        scale_domain="milli",
        provider=BRepBuild123dProvider("box_for_cam", box),
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Génération trajectoire Drop-Cutter
        res_cam = await client.post("/api/cam/generate", json={
            "entity_id": "box_for_cam",
            "strategy": "drop_cutter_surface",
            "target_roughness_ra_um": 0.8,
        })
        assert res_cam.status_code == 200
        data_cam = res_cam.json()
        assert data_cam["total_points"] > 0
        assert data_cam["is_collision_free"] is True
        toolpath_id = data_cam["toolpath_entity_id"]

        # 2. Export G-code Siemens
        res_gcode = await client.get(f"/api/cam/gcode?entity_id={toolpath_id}&flavor=siemens")
        assert res_gcode.status_code == 200
        gcode_txt = res_gcode.text
        assert "TRAORI" in gcode_txt
        assert "TRAFOOF" in gcode_txt

        # 3. Export G-code Haas NGC
        res_haas = await client.get(f"/api/cam/gcode?entity_id={toolpath_id}&flavor=haas")
        assert res_haas.status_code == 200
        assert "G234" in res_haas.text
