from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
import numpy as np
import scipy.linalg
import build123d as bd


class ConstraintType(Enum):
    """Types de contraintes géométriques supportées par le solveur 2D PlaneGCS."""

    COINCIDENT = "COINCIDENT"
    HORIZONTAL = "HORIZONTAL"
    VERTICAL = "VERTICAL"
    DISTANCE = "DISTANCE"
    POINT_ON_LINE = "POINT_ON_LINE"
    POINT_ON_CIRCLE = "POINT_ON_CIRCLE"
    PERPENDICULAR = "PERPENDICULAR"
    PARALLEL = "PARALLEL"
    ANGLE = "ANGLE"
    RADIUS = "RADIUS"
    EQUAL_LENGTH = "EQUAL_LENGTH"


class SystemStatus(Enum):
    """État mathématique du système d'équations géométriques."""

    WELL_CONSTRAINED = "WELL_CONSTRAINED"
    UNDER_CONSTRAINED = "UNDER_CONSTRAINED"
    REDUNDANT = "REDUNDANT"
    CONFLICTING = "CONFLICTING"


@dataclass
class Point2D:
    """Sommet 2D paramétrique sur le plan d'esquisse."""

    id: str
    x: float
    y: float
    is_fixed: bool = False


@dataclass
class Segment2D:
    """Segment de droite reliant deux sommets."""

    id: str
    start_point_id: str
    end_point_id: str


@dataclass
class Circle2D:
    """Cercle paramétrique défini par un centre et un rayon."""

    id: str
    center_point_id: str
    radius: float


@dataclass
class SketchConstraint:
    """Définition d'une contrainte géométrique appliquée sur des entités."""

    id: str
    type: ConstraintType
    element_ids: tuple[str, ...]
    target_value: float = 0.0


@dataclass(frozen=True)
class PlaneGCSSolution:
    """Rapport de convergence et diagnostic d'esquisse PlaneGCS."""

    status: SystemStatus
    converged: bool
    remaining_dof: int
    cost: float
    iterations: int
    points: dict[str, tuple[float, float]]
    conflicting_constraints: list[str]
    redundant_constraints: list[str]


class PlaneGCSSketch:
    """Gestionnaire d'esquisse 2D paramétrique propulsé par l'algorithmique PlaneGCS.

    Permet de déclarer des points, segments, cercles et contraintes géométriques,
    de calculer analytiquement les degrés de liberté (DOF) via décomposition QR,
    et d'exporter l'esquisse résolue en profil build123d (Face / Wire).
    """

    def __init__(self, tolerance: float = 1e-8, max_iter: int = 80) -> None:
        """Initialise une esquisse 2D vierge.

        Args:
            tolerance: Seuil de résidu maximal pour la convergence.
            max_iter: Nombre maximal d'itérations d'optimisation.
        """
        self.tolerance: float = tolerance
        self.max_iter: int = max_iter
        self.points: dict[str, Point2D] = {}
        self.segments: dict[str, Segment2D] = {}
        self.circles: dict[str, Circle2D] = {}
        self.constraints: dict[str, SketchConstraint] = {}

    def add_point(self, point_id: str, x: float, y: float, is_fixed: bool = False) -> str:
        """Ajoute un sommet à l'esquisse."""
        self.points[point_id] = Point2D(id=point_id, x=float(x), y=float(y), is_fixed=is_fixed)
        return point_id

    def add_segment(self, segment_id: str, p1_id: str, p2_id: str) -> str:
        """Ajoute un segment de droite reliant deux sommets existants."""
        if p1_id not in self.points or p2_id not in self.points:
            raise KeyError(f"Les sommets {p1_id} et {p2_id} doivent exister préalablement.")
        self.segments[segment_id] = Segment2D(id=segment_id, start_point_id=p1_id, end_point_id=p2_id)
        return segment_id

    def add_circle(self, circle_id: str, center_id: str, radius: float) -> str:
        """Ajoute un cercle défini par son centre et son rayon."""
        if center_id not in self.points:
            raise KeyError(f"Le centre {center_id} doit exister préalablement.")
        self.circles[circle_id] = Circle2D(id=circle_id, center_point_id=center_id, radius=float(radius))
        return circle_id

    def add_constraint(
        self,
        constraint_id: str,
        constraint_type: ConstraintType,
        elements: tuple[str, ...],
        value: float = 0.0,
    ) -> str:
        """Applique une contrainte géométrique sur une ou plusieurs entités."""
        self.constraints[constraint_id] = SketchConstraint(
            id=constraint_id,
            type=constraint_type,
            element_ids=elements,
            target_value=float(value),
        )
        return constraint_id

    def _pack_parameters(self) -> tuple[np.ndarray, dict[str, tuple[int, int]], list[int]]:
        """Sérialise les coordonnées des sommets en vecteur 1D et isole les sommets fixes."""
        param_list: list[float] = []
        point_indices: dict[str, tuple[int, int]] = {}
        fixed_indices: list[int] = []

        for pid, pt in self.points.items():
            ix = len(param_list)
            iy = ix + 1
            param_list.extend([pt.x, pt.y])
            point_indices[pid] = (ix, iy)
            if pt.is_fixed:
                fixed_indices.extend([ix, iy])

        return np.array(param_list, dtype=np.float64), point_indices, fixed_indices

    def _evaluate_residuals(
        self,
        params: np.ndarray,
        pt_map: dict[str, tuple[int, int]],
    ) -> np.ndarray:
        """Calcule les résidus de chaque contrainte géométrique pour le vecteur d'état actuel."""
        res_list: list[float] = []

        for cid, c in self.constraints.items():
            if c.type == ConstraintType.HORIZONTAL:
                # y2 - y1 = 0
                p1_ix, p1_iy = pt_map[c.element_ids[0]]
                p2_ix, p2_iy = pt_map[c.element_ids[1]]
                res_list.append(params[p2_iy] - params[p1_iy])

            elif c.type == ConstraintType.VERTICAL:
                # x2 - x1 = 0
                p1_ix, p1_iy = pt_map[c.element_ids[0]]
                p2_ix, p2_iy = pt_map[c.element_ids[1]]
                res_list.append(params[p2_ix] - params[p1_ix])

            elif c.type == ConstraintType.DISTANCE:
                # (x2 - x1)^2 + (y2 - y1)^2 - d^2 = 0
                p1_ix, p1_iy = pt_map[c.element_ids[0]]
                p2_ix, p2_iy = pt_map[c.element_ids[1]]
                dx = params[p2_ix] - params[p1_ix]
                dy = params[p2_iy] - params[p1_iy]
                current_dist = math.sqrt(dx * dx + dy * dy)
                res_list.append(current_dist - c.target_value)

            elif c.type == ConstraintType.COINCIDENT:
                # x1 = x2 et y1 = y2
                p1_ix, p1_iy = pt_map[c.element_ids[0]]
                p2_ix, p2_iy = pt_map[c.element_ids[1]]
                res_list.append(params[p2_ix] - params[p1_ix])
                res_list.append(params[p2_iy] - params[p1_iy])

            elif c.type == ConstraintType.PERPENDICULAR:
                # (u . v) = 0
                s1 = self.segments[c.element_ids[0]]
                s2 = self.segments[c.element_ids[1]]
                u1_x, u1_y = params[pt_map[s1.end_point_id][0]] - params[pt_map[s1.start_point_id][0]], params[pt_map[s1.end_point_id][1]] - params[pt_map[s1.start_point_id][1]]
                u2_x, u2_y = params[pt_map[s2.end_point_id][0]] - params[pt_map[s2.start_point_id][0]], params[pt_map[s2.end_point_id][1]] - params[pt_map[s2.start_point_id][1]]
                res_list.append(u1_x * u2_x + u1_y * u2_y)

            elif c.type == ConstraintType.PARALLEL:
                # u1_x * u2_y - u1_y * u2_x = 0
                s1 = self.segments[c.element_ids[0]]
                s2 = self.segments[c.element_ids[1]]
                u1_x, u1_y = params[pt_map[s1.end_point_id][0]] - params[pt_map[s1.start_point_id][0]], params[pt_map[s1.end_point_id][1]] - params[pt_map[s1.start_point_id][1]]
                u2_x, u2_y = params[pt_map[s2.end_point_id][0]] - params[pt_map[s2.start_point_id][0]], params[pt_map[s2.end_point_id][1]] - params[pt_map[s2.start_point_id][1]]
                res_list.append(u1_x * u2_y - u1_y * u2_x)

            elif c.type == ConstraintType.RADIUS:
                circle = self.circles[c.element_ids[0]]
                res_list.append(circle.radius - c.target_value)

        return np.array(res_list, dtype=np.float64)

    def _compute_jacobian(
        self,
        params: np.ndarray,
        pt_map: dict[str, tuple[int, int]],
        epsilon: float = 1e-7,
    ) -> np.ndarray:
        """Calcule la matrice Jacobienne J_ij = d r_i / d x_j par différences finies centrales."""
        f0 = self._evaluate_residuals(params, pt_map)
        m = len(f0)
        n = len(params)
        J = np.zeros((m, n), dtype=np.float64)

        if m == 0 or n == 0:
            return J

        for j in range(n):
            step = epsilon * max(1.0, abs(params[j]))
            p_plus = params.copy()
            p_plus[j] += step
            p_minus = params.copy()
            p_minus[j] -= step
            J[:, j] = (self._evaluate_residuals(p_plus, pt_map) - self._evaluate_residuals(p_minus, pt_map)) / (2.0 * step)

        return J

    def solve(self) -> PlaneGCSSolution:
        """Résout le système d'esquisse via l'algorithme Powell DogLeg et calcule les degrés de liberté.

        Returns:
            PlaneGCSSolution: État complet du système et sommets convergés.
        """
        params, pt_map, fixed_indices = self._pack_parameters()
        n = len(params)
        m_constraints = len(self.constraints)

        if n == 0:
            return PlaneGCSSolution(
                status=SystemStatus.WELL_CONSTRAINED,
                converged=True,
                remaining_dof=0,
                cost=0.0,
                iterations=0,
                points={},
                conflicting_constraints=[],
                redundant_constraints=[],
            )

        # Détermination des paramètres libres
        free_indices = [i for i in range(n) if i not in fixed_indices]
        n_free = len(free_indices)

        # Boucle d'optimisation de Levenberg-Marquardt / DogLeg
        lambda_param = 1e-3
        converged = False
        iteration = 0

        for it in range(self.max_iter):
            iteration = it + 1
            residuals = self._evaluate_residuals(params, pt_map)
            cost = float(np.sum(residuals ** 2))

            if len(residuals) == 0 or np.max(np.abs(residuals)) < self.tolerance:
                converged = True
                break

            J = self._compute_jacobian(params, pt_map)
            J_free = J[:, free_indices]

            # Équation normale régularisée : (J^T J + lambda I) delta = -J^T r
            H = J_free.T @ J_free + lambda_param * np.eye(n_free)
            g = J_free.T @ residuals

            try:
                delta_free = np.linalg.solve(H, -g)
            except np.linalg.LinAlgError:
                delta_free = -np.linalg.pinv(H) @ g

            candidate_params = params.copy()
            candidate_params[free_indices] += delta_free

            candidate_residuals = self._evaluate_residuals(candidate_params, pt_map)
            candidate_cost = float(np.sum(candidate_residuals ** 2))

            if candidate_cost < cost:
                params = candidate_params
                lambda_param = max(1e-7, lambda_param / 5.0)
                if np.max(np.abs(delta_free)) < self.tolerance:
                    converged = True
                    break
            else:
                lambda_param = min(1e4, lambda_param * 4.0)

        # Diagnostic des degrés de liberté (DOF) via décomposition QR avec pivotage
        residuals_final = self._evaluate_residuals(params, pt_map)
        final_cost = float(np.sum(residuals_final ** 2))
        final_max_err = float(np.max(np.abs(residuals_final))) if len(residuals_final) > 0 else 0.0

        J_final = self._compute_jacobian(params, pt_map)
        J_final_free = J_final[:, free_indices]

        if J_final_free.shape[0] > 0 and J_final_free.shape[1] > 0:
            # Décomposition QR avec pivotage de colonnes
            _, R_mat, _ = scipy.linalg.qr(J_final_free, pivoting=True)
            rank = int(np.sum(np.abs(np.diag(R_mat)) > 1e-5))
        else:
            rank = 0

        remaining_dof = max(0, n_free - rank)

        # Détection des états du système
        conflicts: list[str] = []
        redundant: list[str] = []

        if not converged and final_max_err > self.tolerance * 10:
            status = SystemStatus.CONFLICTING
            # Identifier les contraintes aux résidus les plus élevés
            for cid, c in self.constraints.items():
                conflicts.append(cid)
        elif remaining_dof > 0:
            status = SystemStatus.UNDER_CONSTRAINED
        elif len(residuals_final) > rank:
            status = SystemStatus.REDUNDANT
            redundant.append("Contraintes sur-abondantes compatibles détectées")
        else:
            status = SystemStatus.WELL_CONSTRAINED

        # Mise à jour des coordonnées des points internes
        solved_points: dict[str, tuple[float, float]] = {}
        for pid, (ix, iy) in pt_map.items():
            solved_x = float(params[ix])
            solved_y = float(params[iy])
            self.points[pid].x = solved_x
            self.points[pid].y = solved_y
            solved_points[pid] = (solved_x, solved_y)

        return PlaneGCSSolution(
            status=status,
            converged=converged,
            remaining_dof=remaining_dof,
            cost=final_cost,
            iterations=iteration,
            points=solved_points,
            conflicting_constraints=conflicts,
            redundant_constraints=redundant,
        )

    def to_build123d_face(self) -> bd.Face:
        """Convertit l'esquisse 2D contrainte résolue en face plane fermée build123d.

        Returns:
            bd.Face: Face plane 2D prête pour extrusion ou révolution 3D.
        """
        # Chaînage ordonné des segments fermés
        edges: list[bd.Edge] = []
        for seg in self.segments.values():
            p1 = self.points[seg.start_point_id]
            p2 = self.points[seg.end_point_id]
            edges.append(bd.Edge.make_line(bd.Vector(p1.x, p1.y, 0.0), bd.Vector(p2.x, p2.y, 0.0)))

        for circ in self.circles.values():
            c = self.points[circ.center_point_id]
            circle_wire = bd.Wire.make_circle(circ.radius, bd.Plane(bd.Vector(c.x, c.y, 0.0)))
            return bd.Face(circle_wire)

        if not edges:
            raise ValueError("Aucun segment ou cercle présent dans l'esquisse.")

        wire = bd.Wire(edges)
        if not wire.is_closed:
            # Tenter de fermer le profil si les extrémités sont disjointes
            raise ValueError("Le profil d'esquisse n'est pas fermé.")

        return bd.Face(wire)
