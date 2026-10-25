from __future__ import annotations

import build123d as bd
from mono.core.document import MonoDocument
from mono.providers.brep_build123d import BRepBuild123dProvider
from mono.agent.cadengbench_gates import (
    CADEngBenchGatekeeper,
    GateEvaluationResult,
    L0Result,
    L1Result,
    L2ZResult,
    L2EResult,
)


def test_gate_l0_valid_box() -> None:
    """Vérifie que la porte L0 valide un solide manifold régulier à volume positif."""
    box = bd.Box(100.0, 50.0, 20.0)
    res = CADEngBenchGatekeeper.evaluate_l0(box)

    assert isinstance(res, L0Result)
    assert res.is_valid is True
    assert res.is_watertight is True
    assert res.volume_m3 > 0
    assert res.solids_count == 1
    assert res.error is None


def test_gate_l1_dfm_wall_thickness() -> None:
    """Vérifie que la porte L1 détecte avec précision une paroi mince sous le seuil DFM (1.0 mm)."""
    # 1. Pièce conforme (épaisseur 20 mm >= 1.0 mm)
    thick_box = bd.Box(100.0, 50.0, 20.0)
    res_thick = CADEngBenchGatekeeper.evaluate_l1_dfm(thick_box)
    assert res_thick.is_valid is True
    assert res_thick.min_wall_thickness_mm >= 1.0
    assert len(res_thick.errors) == 0

    # 2. Pièce non conforme (épaisseur 0.5 mm < 1.0 mm)
    thin_box = bd.Box(100.0, 50.0, 0.5)
    res_thin = CADEngBenchGatekeeper.evaluate_l1_dfm(thin_box)
    assert res_thin.is_valid is False
    assert res_thin.min_wall_thickness_mm < 1.0
    assert any("Épaisseur minimale de paroi" in err for err in res_thin.errors)


def test_gate_l1_dfm_holes_and_ratio() -> None:
    """Vérifie la détection des diamètres de perçage et du ratio L/D."""
    # Bloc 50x50x20 mm percé au centre avec un trou Ø 8 mm (rayon 4 mm) -> L/D = 20 / 8 = 2.5 <= 8.0
    with bd.BuildPart() as p1:
        bd.Box(50.0, 50.0, 20.0)
        with bd.Locations((0, 0, 0)):
            bd.Hole(radius=4.0, depth=20.0)
    res_p1 = CADEngBenchGatekeeper.evaluate_l1_dfm(p1.part)
    assert res_p1.is_valid is True
    assert 8.0 in res_p1.hole_diameters_mm
    assert len(res_p1.errors) == 0

    # Bloc avec perçage trop petit Ø 1.0 mm (rayon 0.5 mm < seuil 2.0 mm)
    with bd.BuildPart() as p2:
        bd.Box(50.0, 50.0, 20.0)
        with bd.Locations((0, 0, 0)):
            bd.Hole(radius=0.5, depth=20.0)
    res_p2 = CADEngBenchGatekeeper.evaluate_l1_dfm(p2.part)
    assert res_p2.is_valid is False
    assert any("trop petit" in err for err in res_p2.errors)


def test_gate_l1_target_dimensions_match() -> None:
    """Vérifie le contrôle d'alignement avec les cotes cibles spécifiées."""
    box = bd.Box(100.0, 50.0, 20.0)

    # Cibles conformes
    res_ok = CADEngBenchGatekeeper.evaluate_l1_dfm(
        box,
        target_dimensions={"length": 100.0, "width": 50.0, "height": 20.0},
    )
    assert res_ok.target_dims_matched is True
    assert res_ok.is_valid is True

    # Cibles divergentes (> 5%)
    res_bad = CADEngBenchGatekeeper.evaluate_l1_dfm(
        box,
        target_dimensions={"length": 150.0, "width": 50.0, "height": 20.0},
    )
    assert res_bad.target_dims_matched is False
    assert res_bad.is_valid is False


def test_gate_l2_z_parametric_robustness() -> None:
    """Vérifie que la porte L2-Z teste les variations ±10% sur les paramètres."""
    script = """import build123d as bd
length = PARAMS.get("length", 100.0)
width = PARAMS.get("width", 50.0)
height = PARAMS.get("height", 20.0)
with bd.BuildPart() as p:
    bd.Box(length, width, height)
RESULT_PART = p.part
"""
    params = {"length": 100.0, "width": 50.0, "height": 20.0}
    res = CADEngBenchGatekeeper.evaluate_l2_z_parametric(script, params)

    assert isinstance(res, L2ZResult)
    assert res.is_valid is True
    assert res.variations_tested == 6  # 3 params x 2 variations
    assert len(res.failed_variations) == 0


def test_gate_l2_e_isolation_and_clash() -> None:
    """Vérifie la détection de dommages collatéraux et d'interpénétration (clash)."""
    doc = MonoDocument()
    b1 = bd.Box(50.0, 50.0, 20.0)
    p1 = BRepBuild123dProvider("box_a", b1)
    doc.add_entity("box_a", p1)

    # 1. Deuxième solide disjoint (sans collision)
    b2 = bd.Pos(100.0, 0, 0) * bd.Box(30.0, 30.0, 20.0)
    res_ok = CADEngBenchGatekeeper.evaluate_l2_e_isolation(
        target_entity_id="box_b",
        current_part=b2,
        doc=doc,
    )
    assert res_ok.is_valid is True
    assert res_ok.clash_detected is False

    # 2. Deuxième solide chevauchant (collision physique avec box_a)
    b_clash = bd.Pos(20.0, 0, 0) * bd.Box(50.0, 50.0, 20.0)
    res_clash = CADEngBenchGatekeeper.evaluate_l2_e_isolation(
        target_entity_id="box_clash",
        current_part=b_clash,
        doc=doc,
    )
    assert res_clash.is_valid is False
    assert res_clash.clash_detected is True
    assert any("box_a" in ent for ent in res_clash.clashing_entities)


def test_full_cadengbench_evaluation_and_dict() -> None:
    """Vérifie l'évaluation consolidée des 4 portes et la conversion en dictionnaire."""
    box = bd.Box(80.0, 40.0, 15.0)
    res = CADEngBenchGatekeeper.evaluate(
        entity_id="test_part",
        part=box,
        target_dimensions={"l": 80.0, "w": 40.0, "h": 15.0},
    )

    assert isinstance(res, GateEvaluationResult)
    assert res.overall_passed is True
    assert res.score == 1.0
    data = res.to_dict()
    assert data["overall_passed"] is True
    assert data["score"] == 1.0
    assert "l0" in data and "l1" in data and "l2_z" in data and "l2_e" in data
