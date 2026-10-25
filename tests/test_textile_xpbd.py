"""Tests unitaires du moteur de simulation textile GPU Taichi XPBD."""

from __future__ import annotations

import numpy as np
import pytest

from mono.providers.textile_xpbd import TextileXpbdProvider


def test_textile_initialization_and_topology() -> None:
    """Vérifie la topologie triangulaire, l'allocation et le rapport d'inspection."""
    provider = TextileXpbdProvider(res_x=10, res_y=10, spacing=0.05)

    assert provider.res_x == 10
    assert provider.res_y == 10
    assert provider.engine.n_particles == 100

    # 2 triangles par cellule : (10 - 1) * (10 - 1) * 2 = 9 * 9 * 2 = 162 triangles
    assert len(provider.indices) == 162

    report = provider.introspect()
    assert report.is_valid_l0 is True
    assert report.is_watertight is False
    assert report.topology_counts["particles"] == 100
    assert report.topology_counts["triangles"] == 162
    assert report.surface_area_m2 > 0.0


def test_textile_pinning_and_gravity() -> None:
    """Vérifie que les nœuds épinglés restent immobiles tandis que le reste chute sous gravité."""
    provider = TextileXpbdProvider(res_x=12, res_y=12, spacing=0.03)
    provider.reset_horizontal(center_y=1.5)
    provider.pin_top_edge()

    pos_init = provider.get_positions()
    top_indices = provider.resolve_selector("top")
    pinned_indices = provider.resolve_selector("pinned")

    assert set(top_indices) == set(pinned_indices)

    # 10 pas de simulation
    provider.step(n_frames=10, gravity_y=-9.81)

    pos_after = provider.get_positions()

    # Les nœuds épinglés ne doivent pas avoir bougé
    for idx in pinned_indices:
        np.testing.assert_allclose(pos_after[idx], pos_init[idx], atol=1e-4)

    # Le bas du tissu (nœuds libres) doit être descendu
    bottom_indices = provider.resolve_selector("bottom")
    for idx in bottom_indices:
        assert pos_after[idx, 1] < pos_init[idx, 1] - 0.02


def test_textile_sphere_collision() -> None:
    """Vérifie que le tissu se drape sur un obstacle sphérique sans interpénétration."""
    provider = TextileXpbdProvider(res_x=16, res_y=16, spacing=0.02)
    provider.reset_horizontal(center_y=0.6)
    provider.unpin_all()

    sphere_center = (0.0, 0.35, 0.0)
    sphere_radius = 0.15
    provider.add_sphere_obstacle(center=sphere_center, radius=sphere_radius)

    # Laisser tomber le tissu sur la sphère
    provider.step(n_frames=15, gravity_y=-9.81)

    positions = provider.get_positions()
    c = np.array(sphere_center, dtype=np.float32)

    # Calcul des distances au centre de la sphère
    dists = np.linalg.norm(positions - c, axis=1)

    # Aucun point ne doit pénétrer à l'intérieur de la sphère (rayon - tolérance marge)
    min_dist = float(np.min(dists))
    assert min_dist >= sphere_radius - 0.015


def test_textile_ground_collision() -> None:
    """Vérifie que les nœuds textiles s'arrêtent au niveau du sol sans le traverser."""
    provider = TextileXpbdProvider(res_x=10, res_y=10, spacing=0.04)
    provider.reset_horizontal(center_y=0.2)
    provider.unpin_all()

    ground_y = 0.0
    provider.step(n_frames=20, ground_y=ground_y, gravity_y=-9.81)

    positions = provider.get_positions()
    # Tous les Y doivent être au-dessus du sol
    min_y = float(np.min(positions[:, 1]))
    assert min_y >= ground_y - 1e-4


def test_textile_brep_sdf_collision() -> None:
    """Vérifie la génération du champ SDF à partir d'un solide B-Rep et la répulsion physique."""
    import build123d as bd

    # Création d'une boîte B-Rep solide de 10 cm
    box = bd.Box(0.12, 0.12, 0.12)
    box = box.move(bd.Location((0.0, 0.25, 0.0)))

    provider = TextileXpbdProvider(res_x=12, res_y=12, spacing=0.025)
    provider.reset_horizontal(center_y=0.5)
    provider.unpin_all()

    # Échantillonnage de la SDF B-Rep
    provider.load_sdf_from_brep(box, resolution=16)

    assert provider.engine.has_sdf[None] == 1

    # Chute sur le solide
    provider.step(n_frames=15, gravity_y=-9.81)

    positions = provider.get_positions()
    # Le tissu doit reposer au-dessus du sommet de la boîte (y = 0.25 + 0.06 = 0.31)
    center_particles = positions[
        (positions[:, 0] >= -0.05) & (positions[:, 0] <= 0.05) &
        (positions[:, 2] >= -0.05) & (positions[:, 2] <= 0.05)
    ]
    if len(center_particles) > 0:
        min_center_y = float(np.min(center_particles[:, 1]))
        assert min_center_y >= 0.28  # Posé sur ou juste au-dessus du cube


def test_geometry_provider_contract_and_export() -> None:
    """Vérifie la conformité au contrat GeometryProvider : tessellation binaire et OBJ."""
    provider = TextileXpbdProvider(res_x=8, res_y=8, spacing=0.03)

    payload = provider.tessellate_binary()
    assert payload.entity_id == "textile_xpbd_cloth"
    assert payload.vertices.shape == (64, 3)
    assert payload.normals.shape == (64, 3)
    assert payload.indices.shape == (98, 3)
    assert payload.scalar_field is not None
    assert payload.scalar_field.shape == (64,)

    # Sérialisation OBJ native
    raw_obj = provider.serialize_native()
    assert len(raw_obj) > 0
    obj_text = raw_obj.decode("utf-8")
    assert "v " in obj_text
    assert "vn " in obj_text
    assert "f " in obj_text


@pytest.mark.asyncio
async def test_textile_api_endpoints() -> None:
    """Vérifie le cycle d'instanciation et d'animation textile via l'API REST."""
    import httpx
    from mono.ipc.server import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Création
        res_create = await client.post("/api/textile/create", json={
            "entity_id": "cloth_curtain",
            "res_x": 10,
            "res_y": 10,
            "spacing": 0.04,
            "orientation": "vertical",
            "pin": "top",
        })
        assert res_create.status_code == 200
        data = res_create.json()
        assert data["status"] == "created"
        assert data["particles"] == 100

        # 2. Step
        res_step = await client.post("/api/textile/step", json={
            "entity_id": "cloth_curtain",
            "n_frames": 2,
            "wind_z": 2.5,
        })
        assert res_step.status_code == 200
        step_data = res_step.json()
        assert step_data["status"] == "stepped"
        assert step_data["n_frames"] == 2
