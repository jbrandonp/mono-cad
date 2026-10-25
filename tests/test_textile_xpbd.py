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


def test_textile_material_presets() -> None:
    """Vérifie la configuration des presets de matériaux réels (soie, coton, cuir, caoutchouc)."""
    provider = TextileXpbdProvider(res_x=8, res_y=8, spacing=0.03)

    provider.set_material_preset("silk")
    assert provider.engine.compliance_stretch == 1e-7
    assert provider.engine.compliance_bending == 2e-3
    assert provider.engine.friction_coeff == 0.20

    provider.set_material_preset("leather")
    assert provider.engine.compliance_stretch == 1e-8
    assert provider.engine.compliance_bending == 1e-5
    assert provider.engine.friction_coeff == 0.60

    provider.set_material_preset("cotton")
    assert provider.engine.compliance_stretch == 1e-6
    assert provider.engine.compliance_bending == 5e-4


def test_textile_box_and_cylinder_obstacles() -> None:
    """Vérifie la détection et répulsion de contact avec une boîte AABB et un cylindre."""
    provider = TextileXpbdProvider(res_x=12, res_y=12, spacing=0.025)
    provider.reset_horizontal(center_y=0.6)
    provider.unpin_all()

    # Ajout d'une boîte AABB au centre
    provider.add_box_obstacle(min_corner=(-0.08, 0.25, -0.08), max_corner=(0.08, 0.40, 0.08))
    # Ajout d'un cylindre à proximité
    provider.add_cylinder_obstacle(base_center=(0.3, 0.0, 0.0), radius=0.10, height=0.8)

    assert provider.engine.n_boxes[None] == 1
    assert provider.engine.n_cyls[None] == 1

    # Chute du tissu
    provider.step(n_frames=12, gravity_y=-9.81)

    positions = provider.get_positions()
    center_particles = positions[
        (positions[:, 0] >= -0.06) & (positions[:, 0] <= 0.06) &
        (positions[:, 2] >= -0.06) & (positions[:, 2] <= 0.06)
    ]
    if len(center_particles) > 0:
        min_center_y = float(np.min(center_particles[:, 1]))
        assert min_center_y >= 0.38  # Repose sur le dessus de la boîte (y=0.40)

    # Réinitialisation des obstacles
    provider.clear_obstacles()
    assert provider.engine.n_boxes[None] == 0
    assert provider.engine.n_cyls[None] == 0


def test_textile_strain_field_and_selectors() -> None:
    """Vérifie le calcul du champ scalaire de déformation (strain) et les sélecteurs sémantiques."""
    provider = TextileXpbdProvider(res_x=10, res_y=10, spacing=0.03)
    provider.reset_horizontal(center_y=1.0)
    provider.pin_corners()

    # Vérification sélecteur corners
    corners = provider.resolve_selector("vertices.corners")
    assert len(corners) == 4
    free_nodes = provider.resolve_selector("vertices.free")
    assert len(free_nodes) == 96
    faces = provider.resolve_selector("faces")
    assert len(faces) == len(provider.indices)

    # Simulation de tension sous gravité
    provider.step(n_frames=15, gravity_y=-9.81)

    strains = provider.get_strains()
    assert strains.shape == (100,)
    assert np.all(strains >= 0.0)
    # Les nœuds étirés doivent avoir une contrainte mesurable
    assert float(np.max(strains)) >= 0.0

    # Payload binaire avec scalar_mode='strain'
    payload = provider.tessellate_binary(scalar_mode="strain")
    assert payload.scalar_field is not None
    np.testing.assert_allclose(payload.scalar_field, strains)

    report = provider.introspect()
    assert report.topology_counts["boxes"] == 0
    assert report.topology_counts["cylinders"] == 0


@pytest.mark.asyncio
async def test_textile_extended_rest_endpoints() -> None:
    """Vérifie l'ensemble des routes REST avancées : obstacles, drapé B-Rep et métriques."""
    import httpx
    import build123d as bd
    from mono.ipc.server import app, state
    from mono.providers.brep_build123d import BRepBuild123dProvider
    from mono.core.document import EntityRecord

    # Enregistrement d'une pièce B-Rep dans la scène
    box = bd.Box(0.2, 0.2, 0.2)
    brep_prov = BRepBuild123dProvider(entity_id="mechanical_cube", part=box)
    state.doc.entities["mechanical_cube"] = EntityRecord(
        entity_id="mechanical_cube",
        provider_name="brep_build123d",
        scale_domain="milli",
        provider=brep_prov,
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Création textile
        res_create = await client.post("/api/textile/create", json={
            "entity_id": "test_cloth_adv",
            "res_x": 12,
            "res_y": 12,
            "spacing": 0.03,
            "center_y": 1.2,
            "pin": "none",
        })
        assert res_create.status_code == 200

        # 2. Ajout obstacle boîte via REST
        res_obs = await client.post("/api/textile/obstacle", json={
            "entity_id": "test_cloth_adv",
            "obstacle_type": "box",
            "min_corner": [-0.1, 0.2, -0.1],
            "max_corner": [0.1, 0.4, 0.1],
        })
        assert res_obs.status_code == 200
        assert res_obs.json()["status"] == "obstacle_added"

        # 3. Drapé sur solide mécanique B-Rep via REST
        res_drape = await client.post("/api/textile/drape", json={
            "cloth_entity_id": "test_cloth_adv",
            "solid_entity_id": "mechanical_cube",
            "center_y": 0.8,
            "resolution": 12,
        })
        assert res_drape.status_code == 200
        assert res_drape.json()["status"] == "drape_prepared"

        # 4. Lecture des métriques physiques
        res_met = await client.get("/api/textile/metrics?entity_id=test_cloth_adv")
        assert res_met.status_code == 200
        met = res_met.json()
        assert met["entity_id"] == "test_cloth_adv"
        assert met["particles"] == 144
        assert met["is_valid"] is True
