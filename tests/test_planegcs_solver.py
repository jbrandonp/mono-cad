import math
import pytest
from mono.sketch.planegcs_solver import (
    PlaneGCSSketch,
    ConstraintType,
    SystemStatus,
)


def test_planegcs_rectangle_solve() -> None:
    """Vérifie la résolution d'un rectangle sous contraintes PlaneGCS."""
    sketch = PlaneGCSSketch()

    # Déclaration de 4 sommets avec P0 fixé à l'origine (0, 0)
    sketch.add_point("P0", 0.0, 0.0, is_fixed=True)
    sketch.add_point("P1", 45.0, 2.0)
    sketch.add_point("P2", 48.0, 22.0)
    sketch.add_point("P3", 2.0, 18.0)

    # Segments
    sketch.add_segment("S0", "P0", "P1")
    sketch.add_segment("S1", "P1", "P2")
    sketch.add_segment("S2", "P2", "P3")
    sketch.add_segment("S3", "P3", "P0")

    # Contraintes : Horizontalité S0 et S2, Verticalité S1 et S3
    sketch.add_constraint("C_H0", ConstraintType.HORIZONTAL, ("P0", "P1"))
    sketch.add_constraint("C_H2", ConstraintType.HORIZONTAL, ("P3", "P2"))
    sketch.add_constraint("C_V1", ConstraintType.VERTICAL, ("P1", "P2"))
    sketch.add_constraint("C_V3", ConstraintType.VERTICAL, ("P0", "P3"))

    # Cotes dimensionnelles : Largeur = 50 mm, Hauteur = 20 mm
    sketch.add_constraint("C_W", ConstraintType.DISTANCE, ("P0", "P1"), value=50.0)
    sketch.add_constraint("C_H", ConstraintType.DISTANCE, ("P0", "P3"), value=20.0)

    solution = sketch.solve()

    assert solution.converged is True
    assert solution.status == SystemStatus.WELL_CONSTRAINED
    assert solution.remaining_dof == 0

    # Vérification des coordonnées résolues
    p0 = solution.points["P0"]
    p1 = solution.points["P1"]
    p2 = solution.points["P2"]
    p3 = solution.points["P3"]

    assert abs(p0[0] - 0.0) < 1e-4 and abs(p0[1] - 0.0) < 1e-4
    assert abs(p1[0] - 50.0) < 1e-4 and abs(p1[1] - 0.0) < 1e-4
    assert abs(p2[0] - 50.0) < 1e-4 and abs(p2[1] - 20.0) < 1e-4
    assert abs(p3[0] - 0.0) < 1e-4 and abs(p3[1] - 20.0) < 1e-4

    # Conversion en face build123d
    face = sketch.to_build123d_face()
    assert abs(float(face.area) - 1000.0) < 1.0  # 50 x 20 = 1000 mm²


def test_planegcs_under_constrained_dof() -> None:
    """Vérifie la détection exacte des degrés de liberté restants (DOF)."""
    sketch = PlaneGCSSketch()
    sketch.add_point("P0", 0.0, 0.0, is_fixed=True)
    sketch.add_point("P1", 10.0, 10.0)

    # 1 seule contrainte de distance pour 2 coordonnées libres -> DOF = 1
    sketch.add_constraint("C_DIST", ConstraintType.DISTANCE, ("P0", "P1"), value=15.0)

    solution = sketch.solve()
    assert solution.converged is True
    assert solution.status == SystemStatus.UNDER_CONSTRAINED
    assert solution.remaining_dof == 1
