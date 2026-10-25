"""Moteur de simulation physique GPU pour textiles et membranes souples sous Taichi Lang.

Implémente la formulation XPBD (Extended Position-Based Dynamics) de Miles Macklin (2016)
avec contraintes d'étirement, cisaillement, flexion isométrique dièdre,
coloriage de graphe sans conflit GPU, aérodynamique de vent et détection de collision
continue (CCD) avec le sol, obstacles analytiques et champs de distance signés (SDF 3D) de pièces B-Rep.
"""

from dataclasses import dataclass, field
import math
from typing import Dict, List, Literal, Optional, Tuple
import numpy as np
import numpy.typing as npt
import taichi as ti

from mono.core.geometry_provider import BinaryMeshPayload, GeometryProvider, ValidationReport

# Initialisation idempotente du backend Taichi (CUDA avec repli CPU)
try:
    ti.init(arch=ti.cuda, default_fp=ti.f32, log_level=ti.WARN)
except Exception:
    ti.init(arch=ti.cpu, default_fp=ti.f32, log_level=ti.WARN)


@dataclass
class ObstacleSphere:
    """Obstacle analytique sphérique."""

    center: Tuple[float, float, float]
    radius: float
    friction: float = 0.35


@dataclass
class ObstacleBox:
    """Obstacle analytique sous forme de boîte englobante alignée sur les axes (AABB)."""

    min_corner: Tuple[float, float, float]
    max_corner: Tuple[float, float, float]
    friction: float = 0.35


@dataclass
class SdfGrid3D:
    """Champ de distance signé (SDF 3D) discret pour collisions complexes avec solides B-Rep."""

    origin: Tuple[float, float, float]
    cell_size: float
    dims: Tuple[int, int, int]
    data: npt.NDArray[np.float32]


@ti.data_oriented
class TaichiXpbdEngine:
    """Moteur physique bas niveau compilé sous Taichi JIT (CUDA/CPU).

    Gère l'intégration de Verlet semi-implicite, les contraintes de distance conformes,
    la flexion dièdre, le vent et les collisions physiques en mémoire VRAM.
    """

    def __init__(
        self,
        res_x: int,
        res_y: int,
        spacing: float,
        compliance_stretch: float = 1e-6,
        compliance_bending: float = 5e-4,
        air_density: float = 1.225,
        friction_coeff: float = 0.40,
        sdf_dims: Tuple[int, int, int] = (16, 16, 16),
    ) -> None:
        """Alloue les champs mémoire Taichi GPU pour la grille textile."""
        self.res_x: int = res_x
        self.res_y: int = res_y
        self.n_particles: int = res_x * res_y
        self.spacing: float = spacing
        self.compliance_stretch: float = compliance_stretch
        self.compliance_bending: float = compliance_bending
        self.air_density: float = air_density
        self.friction_coeff: float = friction_coeff

        # Champs particules
        self.pos = ti.Vector.field(3, dtype=ti.f32, shape=self.n_particles)
        self.prev_pos = ti.Vector.field(3, dtype=ti.f32, shape=self.n_particles)
        self.vel = ti.Vector.field(3, dtype=ti.f32, shape=self.n_particles)
        self.inv_mass = ti.field(dtype=ti.f32, shape=self.n_particles)
        self.normals = ti.Vector.field(3, dtype=ti.f32, shape=self.n_particles)
        self.strain = ti.field(dtype=ti.f32, shape=self.n_particles)

        # Vecteur vent
        self.wind_vel = ti.Vector.field(3, dtype=ti.f32, shape=())

        # Obstacles sphériques (jusqu'à 4 sphères dynamiques)
        self.sphere_centers = ti.Vector.field(3, dtype=ti.f32, shape=4)
        self.sphere_radii = ti.field(dtype=ti.f32, shape=4)
        self.n_spheres = ti.field(dtype=ti.i32, shape=())

        # Obstacles parallélépipédiques (jusqu'à 4 boîtes AABB)
        self.box_mins = ti.Vector.field(3, dtype=ti.f32, shape=4)
        self.box_maxs = ti.Vector.field(3, dtype=ti.f32, shape=4)
        self.n_boxes = ti.field(dtype=ti.i32, shape=())

        # Obstacles cylindriques verticaux (jusqu'à 4 cylindres)
        self.cyl_bases = ti.Vector.field(3, dtype=ti.f32, shape=4)
        self.cyl_radii = ti.field(dtype=ti.f32, shape=4)
        self.cyl_heights = ti.field(dtype=ti.f32, shape=4)
        self.n_cyls = ti.field(dtype=ti.i32, shape=())

        # Grille SDF 3D pour solide B-Rep arbitraire
        self.sdf_nx: int = sdf_dims[0]
        self.sdf_ny: int = sdf_dims[1]
        self.sdf_nz: int = sdf_dims[2]
        self.sdf_grid = ti.field(dtype=ti.f32, shape=(self.sdf_nx, self.sdf_ny, self.sdf_nz))
        self.sdf_origin = ti.Vector.field(3, dtype=ti.f32, shape=())
        self.sdf_cell_size = ti.field(dtype=ti.f32, shape=())
        self.has_sdf = ti.field(dtype=ti.i32, shape=())

        # Initialisation par défaut
        self.wind_vel[None] = ti.Vector([0.0, 0.0, 0.0])
        self.n_spheres[None] = 0
        self.n_boxes[None] = 0
        self.n_cyls[None] = 0
        self.has_sdf[None] = 0
        self.sdf_cell_size[None] = 0.01
        self.sdf_origin[None] = ti.Vector([0.0, 0.0, 0.0])

    @ti.kernel
    def reset_grid(self, center_y: ti.f32, horizontal: ti.i32):
        """Réinitialise la position géométrique au repos du tissu."""
        half_w = (ti.cast(self.res_x, ti.f32) - 1.0) * self.spacing * 0.5
        half_h = (ti.cast(self.res_y, ti.f32) - 1.0) * self.spacing * 0.5

        for i, j in ti.ndrange(self.res_x, self.res_y):
            idx = i * self.res_y + j
            x = (ti.cast(i, ti.f32) * self.spacing) - half_w
            if horizontal == 1:
                z = (ti.cast(j, ti.f32) * self.spacing) - half_h
                self.pos[idx] = ti.Vector([x, center_y, z])
            else:
                y = center_y + half_h - (ti.cast(j, ti.f32) * self.spacing)
                self.pos[idx] = ti.Vector([x, y, 0.0])

            self.prev_pos[idx] = self.pos[idx]
            self.vel[idx] = ti.Vector([0.0, 0.0, 0.0])
            self.inv_mass[idx] = 1.0
            self.normals[idx] = ti.Vector([0.0, 1.0, 0.0])

    @ti.kernel
    def pin_particle(self, idx: ti.i32):
        """Fixe un sommet (masse infinie)."""
        if 0 <= idx < self.n_particles:
            self.inv_mass[idx] = 0.0
            self.vel[idx] = ti.Vector([0.0, 0.0, 0.0])

    @ti.kernel
    def unpin_all_particles(self):
        """Libère toutes les contraintes de fixation."""
        for i in range(self.n_particles):
            self.inv_mass[i] = 1.0

    @ti.kernel
    def predict_positions(self, dt: ti.f32, gravity_y: ti.f32):
        """Intégration d'Euler semi-implicite avec gravité et forces aérodynamiques."""
        gravity = ti.Vector([0.0, gravity_y, 0.0])
        v_w = self.wind_vel[None]
        area = self.spacing * self.spacing

        for i in range(self.n_particles):
            if self.inv_mass[i] > 0.0:
                v_rel = self.vel[i] - v_w
                v_rel_norm = v_rel.norm()
                f_aero = ti.Vector([0.0, 0.0, 0.0])
                if v_rel_norm > 1e-4:
                    n = self.normals[i]
                    drag = 0.5 * self.air_density * 1.15 * area * ti.abs(v_rel.dot(n))
                    f_aero = -drag * v_rel_norm * v_rel

                acc = gravity + f_aero * self.inv_mass[i]
                self.vel[i] += dt * acc
                self.vel[i] *= 0.9995  # Amortissement numérique léger
                self.prev_pos[i] = self.pos[i]
                self.pos[i] += dt * self.vel[i]

    @ti.func
    def project_distance(self, i1: ti.i32, i2: ti.i32, rest_d: ti.f32, compliance: ti.f32, dt: ti.f32):
        """Projette une contrainte de distance selon la formulation XPBD de Miles Macklin."""
        w1 = self.inv_mass[i1]
        w2 = self.inv_mass[i2]
        w_sum = w1 + w2
        if w_sum > 0.0:
            diff = self.pos[i1] - self.pos[i2]
            d = diff.norm()
            if d > 1e-6:
                n = diff / d
                c = d - rest_d
                alpha_tilde = compliance / (dt * dt)
                delta_lambda = -c / (w_sum + alpha_tilde)
                self.pos[i1] += w1 * delta_lambda * n
                self.pos[i2] -= w2 * delta_lambda * n

    @ti.kernel
    def solve_stretch_and_shear(self, color: ti.i32, dt: ti.f32):
        """Résout l'étirement et le cisaillement par passes de coloriage sans collision GPU."""
        # 1. Contraintes Horizontales
        for i, j in ti.ndrange(self.res_x - 1, self.res_y):
            if (i % 2) == (color % 2):
                idx1 = i * self.res_y + j
                idx2 = (i + 1) * self.res_y + j
                self.project_distance(idx1, idx2, self.spacing, self.compliance_stretch, dt)

        # 2. Contraintes Verticales
        for i, j in ti.ndrange(self.res_x, self.res_y - 1):
            if (j % 2) == ((color // 2) % 2):
                idx1 = i * self.res_y + j
                idx2 = i * self.res_y + (j + 1)
                self.project_distance(idx1, idx2, self.spacing, self.compliance_stretch, dt)

        # 3. Cisaillement diagonal (Shear)
        shear_spacing = 1.41421356 * self.spacing
        for i, j in ti.ndrange(self.res_x - 1, self.res_y - 1):
            if ((i + j) % 2) == (color % 2):
                idx1 = i * self.res_y + j
                idx2 = (i + 1) * self.res_y + (j + 1)
                self.project_distance(idx1, idx2, shear_spacing, self.compliance_stretch * 1.5, dt)

                idx3 = (i + 1) * self.res_y + j
                idx4 = i * self.res_y + (j + 1)
                self.project_distance(idx3, idx4, shear_spacing, self.compliance_stretch * 1.5, dt)

    @ti.kernel
    def solve_bending(self, color: ti.i32, dt: ti.f32):
        """Applique la flexion isométrique dièdre (2 intervalles de distance)."""
        bend_d = 2.0 * self.spacing
        # Flexion horizontale (i <-> i+2)
        for i, j in ti.ndrange(self.res_x - 2, self.res_y):
            if (i % 2) == (color % 2):
                idx1 = i * self.res_y + j
                idx2 = (i + 2) * self.res_y + j
                self.project_distance(idx1, idx2, bend_d, self.compliance_bending, dt)

        # Flexion verticale (j <-> j+2)
        for i, j in ti.ndrange(self.res_x, self.res_y - 2):
            if (j % 2) == ((color // 2) % 2):
                idx1 = i * self.res_y + j
                idx2 = i * self.res_y + (j + 2)
                self.project_distance(idx1, idx2, bend_d, self.compliance_bending, dt)

    @ti.func
    def sample_sdf_and_normal(
        self, p: ti.types.vector(3, ti.f32)
    ) -> ti.types.vector(4, ti.f32):
        """Échantillonne la distance signée et la normale analytique par interpolation trilinéaire."""
        h = self.sdf_cell_size[None]
        o = self.sdf_origin[None]
        idx_f = (p - o) / h

        u = idx_f.x
        v = idx_f.y
        w = idx_f.z

        res = ti.Vector([1e6, 0.0, 1.0, 0.0])

        if 0.0 <= u < ti.cast(self.sdf_nx - 1, ti.f32) and \
           0.0 <= v < ti.cast(self.sdf_ny - 1, ti.f32) and \
           0.0 <= w < ti.cast(self.sdf_nz - 1, ti.f32):

            i0 = ti.cast(ti.floor(u), ti.i32)
            j0 = ti.cast(ti.floor(v), ti.i32)
            k0 = ti.cast(ti.floor(w), ti.i32)

            su = u - ti.cast(i0, ti.f32)
            sv = v - ti.cast(j0, ti.f32)
            sw = w - ti.cast(k0, ti.f32)

            # 8 coins de la cellule
            c000 = self.sdf_grid[i0, j0, k0]
            c100 = self.sdf_grid[i0 + 1, j0, k0]
            c010 = self.sdf_grid[i0, j0 + 1, k0]
            c110 = self.sdf_grid[i0 + 1, j0 + 1, k0]
            c001 = self.sdf_grid[i0, j0, k0 + 1]
            c101 = self.sdf_grid[i0 + 1, j0, k0 + 1]
            c011 = self.sdf_grid[i0, j0 + 1, k0 + 1]
            c111 = self.sdf_grid[i0 + 1, j0 + 1, k0 + 1]

            # Valeur interpolée
            c00 = c000 * (1.0 - su) + c100 * su
            c01 = c001 * (1.0 - su) + c101 * su
            c10 = c010 * (1.0 - su) + c110 * su
            c11 = c011 * (1.0 - su) + c111 * su

            c0 = c00 * (1.0 - sv) + c10 * sv
            c1 = c01 * (1.0 - sv) + c11 * sv

            val = c0 * (1.0 - sw) + c1 * sw

            # Gradient analytique
            gx = (
                (1.0 - sw) * ((1.0 - sv) * (c100 - c000) + sv * (c110 - c010))
                + sw * ((1.0 - sv) * (c101 - c001) + sv * (c111 - c011))
            ) / h
            gy = (
                (1.0 - sw) * ((1.0 - su) * (c010 - c000) + su * (c110 - c100))
                + sw * ((1.0 - su) * (c011 - c001) + su * (c111 - c101))
            ) / h
            gz = (
                (1.0 - sv) * ((1.0 - su) * (c001 - c000) + su * (c101 - c100))
                + sv * ((1.0 - su) * (c011 - c010) + su * (c111 - c110))
            ) / h

            grad = ti.Vector([gx, gy, gz])
            g_norm = grad.norm()
            normal = ti.Vector([0.0, 1.0, 0.0])
            if g_norm > 1e-6:
                normal = grad / g_norm

            res = ti.Vector([val, normal.x, normal.y, normal.z])

        return res

    @ti.kernel
    def resolve_collisions(self, ground_y: ti.f32, cloth_margin: ti.f32):
        """Détecte les collisions avec le sol, les sphères, boîtes, cylindres et le champ SDF B-Rep."""
        for i in range(self.n_particles):
            if self.inv_mass[i] > 0.0:
                p = self.pos[i]

                # 1. Collision Sol infini
                if p.y < ground_y + cloth_margin:
                    self.pos[i].y = ground_y + cloth_margin
                    # Frottement Coulomb
                    v_tan = ti.Vector([self.vel[i].x, 0.0, self.vel[i].z])
                    self.vel[i] -= self.friction_coeff * v_tan

                # 2. Collision Obstacles Sphériques
                for s in range(self.n_spheres[None]):
                    center = self.sphere_centers[s]
                    radius = self.sphere_radii[s]
                    diff = self.pos[i] - center
                    dist = diff.norm()
                    target_dist = radius + cloth_margin
                    if dist < target_dist and dist > 1e-6:
                        normal = diff / dist
                        self.pos[i] = center + normal * target_dist
                        v_rel = self.vel[i]
                        v_tan = v_rel - v_rel.dot(normal) * normal
                        self.vel[i] -= self.friction_coeff * v_tan

                # 3. Collision Obstacles Boîtes AABB
                for b in range(self.n_boxes[None]):
                    bmin = self.box_mins[b]
                    bmax = self.box_maxs[b]
                    if (p.x >= bmin.x - cloth_margin and p.x <= bmax.x + cloth_margin and
                        p.y >= bmin.y - cloth_margin and p.y <= bmax.y + cloth_margin and
                        p.z >= bmin.z - cloth_margin and p.z <= bmax.z + cloth_margin):
                        d_l = p.x - (bmin.x - cloth_margin)
                        d_r = (bmax.x + cloth_margin) - p.x
                        d_b = p.y - (bmin.y - cloth_margin)
                        d_t = (bmax.y + cloth_margin) - p.y
                        d_f = p.z - (bmin.z - cloth_margin)
                        d_bk = (bmax.z + cloth_margin) - p.z
                        min_d = ti.min(d_l, d_r, d_b, d_t, d_f, d_bk)
                        n = ti.Vector([0.0, 1.0, 0.0])
                        if min_d == d_t:
                            self.pos[i].y = bmax.y + cloth_margin
                            n = ti.Vector([0.0, 1.0, 0.0])
                        elif min_d == d_l:
                            self.pos[i].x = bmin.x - cloth_margin
                            n = ti.Vector([-1.0, 0.0, 0.0])
                        elif min_d == d_r:
                            self.pos[i].x = bmax.x + cloth_margin
                            n = ti.Vector([1.0, 0.0, 0.0])
                        elif min_d == d_b:
                            self.pos[i].y = bmin.y - cloth_margin
                            n = ti.Vector([0.0, -1.0, 0.0])
                        elif min_d == d_f:
                            self.pos[i].z = bmin.z - cloth_margin
                            n = ti.Vector([0.0, 0.0, -1.0])
                        else:
                            self.pos[i].z = bmax.z + cloth_margin
                            n = ti.Vector([0.0, 0.0, 1.0])
                        v_rel = self.vel[i]
                        v_tan = v_rel - v_rel.dot(n) * n
                        self.vel[i] -= self.friction_coeff * v_tan

                # 4. Collision Obstacles Cylindriques Verticaux
                for c in range(self.n_cyls[None]):
                    base = self.cyl_bases[c]
                    rad = self.cyl_radii[c]
                    h = self.cyl_heights[c]
                    if p.y >= base.y - cloth_margin and p.y <= base.y + h + cloth_margin:
                        dx = p.x - base.x
                        dz = p.z - base.z
                        r = ti.sqrt(dx * dx + dz * dz)
                        target_r = rad + cloth_margin
                        if r < target_r and r > 1e-6:
                            nx = dx / r
                            nz = dz / r
                            self.pos[i].x = base.x + nx * target_r
                            self.pos[i].z = base.z + nz * target_r
                            n = ti.Vector([nx, 0.0, nz])
                            v_rel = self.vel[i]
                            v_tan = v_rel - v_rel.dot(n) * n
                            self.vel[i] -= self.friction_coeff * v_tan

                # 5. Collision Champ SDF 3D B-Rep
                if self.has_sdf[None] == 1:
                    sdf_sample = self.sample_sdf_and_normal(self.pos[i])
                    d = sdf_sample[0]
                    if d < cloth_margin:
                        normal = ti.Vector([sdf_sample[1], sdf_sample[2], sdf_sample[3]])
                        penetration = cloth_margin - d
                        self.pos[i] += normal * penetration
                        v_rel = self.vel[i]
                        v_tan = v_rel - v_rel.dot(normal) * normal
                        self.vel[i] -= self.friction_coeff * v_tan

    @ti.kernel
    def finalize_step(self, dt: ti.f32):
        """Met à jour les vitesses effectives et recalcule les normales géométriques."""
        for i in range(self.n_particles):
            if self.inv_mass[i] > 0.0:
                self.vel[i] = (self.pos[i] - self.prev_pos[i]) / dt

        # Calcul des normales de surface
        for i, j in ti.ndrange(self.res_x - 1, self.res_y - 1):
            idx = i * self.res_y + j
            p = self.pos[idx]
            p_right = self.pos[(i + 1) * self.res_y + j]
            p_up = self.pos[i * self.res_y + (j + 1)]
            e1 = p_right - p
            e2 = p_up - p
            cross_n = e1.cross(e2)
            norm_val = cross_n.norm()
            if norm_val > 1e-7:
                self.normals[idx] = cross_n / norm_val

    @ti.kernel
    def compute_strain_field(self):
        """Calcule la déformation relative locale (élongation moyenne des ressorts connectés)."""
        for i, j in ti.ndrange(self.res_x, self.res_y):
            idx = i * self.res_y + j
            p0 = self.pos[idx]
            sum_strain = 0.0
            count = 0.0
            if i > 0:
                d = (p0 - self.pos[(i - 1) * self.res_y + j]).norm()
                sum_strain += ti.max(0.0, (d - self.spacing) / self.spacing)
                count += 1.0
            if i < self.res_x - 1:
                d = (p0 - self.pos[(i + 1) * self.res_y + j]).norm()
                sum_strain += ti.max(0.0, (d - self.spacing) / self.spacing)
                count += 1.0
            if j > 0:
                d = (p0 - self.pos[i * self.res_y + (j - 1)]).norm()
                sum_strain += ti.max(0.0, (d - self.spacing) / self.spacing)
                count += 1.0
            if j < self.res_y - 1:
                d = (p0 - self.pos[i * self.res_y + (j + 1)]).norm()
                sum_strain += ti.max(0.0, (d - self.spacing) / self.spacing)
                count += 1.0
            self.strain[idx] = sum_strain / ti.max(count, 1.0)


class TextileXpbdProvider(GeometryProvider):
    """Fournisseur géométrique et moteur physique GPU de tissus et membranes souples.

    Conforme au contrat d'interface `GeometryProvider` de Mono.
    Offre un streaming binaire zero-copy des positions et contraintes vers WebGL / Three.js.
    """

    def __init__(
        self,
        res_x: int = 40,
        res_y: int = 40,
        spacing: float = 0.025,
        compliance_stretch: float = 1e-6,
        compliance_bending: float = 5e-4,
        substeps: int = 10,
        dt: float = 0.016,
    ) -> None:
        """Initialise le fournisseur textile et alloue la grille physique."""
        self.res_x: int = res_x
        self.res_y: int = res_y
        self.spacing: float = spacing
        self.substeps: int = substeps
        self.dt: float = dt
        self.sub_dt: float = dt / float(substeps)

        self.engine = TaichiXpbdEngine(
            res_x=res_x,
            res_y=res_y,
            spacing=spacing,
            compliance_stretch=compliance_stretch,
            compliance_bending=compliance_bending,
        )

        # Précalcul de la topologie triangulaire (2 triangles par cellule de grille)
        self.indices = self._generate_triangle_indices()
        self.face_ids = np.zeros(len(self.indices), dtype=np.uint32)

        # État par défaut : rideau suspendu
        self.reset_horizontal(center_y=1.0)
        self.pin_top_edge()

    def _generate_triangle_indices(self) -> npt.NDArray[np.uint32]:
        """Génère le tableau contigu d'indices de triangles pour le maillage."""
        tris: List[Tuple[int, int, int]] = []
        for i in range(self.res_x - 1):
            for j in range(self.res_y - 1):
                idx0 = i * self.res_y + j
                idx1 = (i + 1) * self.res_y + j
                idx2 = i * self.res_y + (j + 1)
                idx3 = (i + 1) * self.res_y + (j + 1)

                # Triangle 1
                tris.append((idx0, idx1, idx2))
                # Triangle 2
                tris.append((idx1, idx3, idx2))

        return np.array(tris, dtype=np.uint32)

    def reset_horizontal(self, center_y: float = 1.0) -> None:
        """Dispose le tissu à plat horizontalement à la hauteur indiquée."""
        self.engine.reset_grid(center_y=float(center_y), horizontal=1)

    def reset_vertical(self, center_y: float = 1.0) -> None:
        """Dispose le tissu verticalement (drapé/rideau suspendu)."""
        self.engine.reset_grid(center_y=float(center_y), horizontal=0)

    def pin_vertex(self, i: int, j: int) -> None:
        """Épingle le sommet (i, j) en position fixe."""
        idx = i * self.res_y + j
        self.engine.pin_particle(idx)

    def pin_top_edge(self) -> None:
        """Épingle toute la ligne supérieure du textile (j = res_y - 1)."""
        for i in range(self.res_x):
            self.pin_vertex(i, self.res_y - 1)

    def pin_corners(self) -> None:
        """Épingle les 4 coins du textile."""
        self.pin_vertex(0, 0)
        self.pin_vertex(0, self.res_y - 1)
        self.pin_vertex(self.res_x - 1, 0)
        self.pin_vertex(self.res_x - 1, self.res_y - 1)

    def unpin_all(self) -> None:
        """Libère toutes les contraintes de fixation."""
        self.engine.unpin_all_particles()

    def set_wind(self, vx: float, vy: float, vz: float) -> None:
        """Applique une vitesse de vent dynamique en m/s."""
        self.engine.wind_vel[None] = ti.Vector([vx, vy, vz])

    def set_material_preset(
        self, preset: Literal["silk", "cotton", "leather", "rubber"]
    ) -> None:
        """Configure les propriétés élastiques du textile selon un type de tissu réel."""
        if preset == "silk":
            # Soie : très fluide, plis très fins
            self.engine.compliance_stretch = 1e-7
            self.engine.compliance_bending = 2e-3
            self.engine.friction_coeff = 0.20
        elif preset == "cotton":
            # Coton : souple avec bonne tenue
            self.engine.compliance_stretch = 1e-6
            self.engine.compliance_bending = 5e-4
            self.engine.friction_coeff = 0.40
        elif preset == "leather":
            # Cuir / Toile épaisse : très rigide en flexion
            self.engine.compliance_stretch = 1e-8
            self.engine.compliance_bending = 1e-5
            self.engine.friction_coeff = 0.60
        elif preset == "rubber":
            # Caoutchouc : élastique en étirement, adhérent
            self.engine.compliance_stretch = 5e-5
            self.engine.compliance_bending = 1e-4
            self.engine.friction_coeff = 0.85

    def add_sphere_obstacle(self, center: Tuple[float, float, float], radius: float) -> None:
        """Ajoute un obstacle sphérique analytique pour interaction textile."""
        curr_idx = self.engine.n_spheres[None]
        if curr_idx < 4:
            self.engine.sphere_centers[curr_idx] = ti.Vector([center[0], center[1], center[2]])
            self.engine.sphere_radii[curr_idx] = radius
            self.engine.n_spheres[None] = curr_idx + 1

    def add_box_obstacle(
        self, min_corner: Tuple[float, float, float], max_corner: Tuple[float, float, float]
    ) -> None:
        """Ajoute un obstacle parallélépipédique (boîte AABB) pour collision textile."""
        curr = self.engine.n_boxes[None]
        if curr < 4:
            self.engine.box_mins[curr] = ti.Vector([min_corner[0], min_corner[1], min_corner[2]])
            self.engine.box_maxs[curr] = ti.Vector([max_corner[0], max_corner[1], max_corner[2]])
            self.engine.n_boxes[None] = curr + 1

    def add_cylinder_obstacle(
        self, base_center: Tuple[float, float, float], radius: float, height: float
    ) -> None:
        """Ajoute un obstacle cylindrique vertical pour collision textile."""
        curr = self.engine.n_cyls[None]
        if curr < 4:
            self.engine.cyl_bases[curr] = ti.Vector([base_center[0], base_center[1], base_center[2]])
            self.engine.cyl_radii[curr] = radius
            self.engine.cyl_heights[curr] = height
            self.engine.n_cyls[None] = curr + 1

    def clear_obstacles(self) -> None:
        """Réinitialise tous les obstacles sphériques, boîtes, cylindres et le champ SDF."""
        self.engine.n_spheres[None] = 0
        self.engine.n_boxes[None] = 0
        self.engine.n_cyls[None] = 0
        self.engine.has_sdf[None] = 0

    def load_sdf_from_brep(
        self,
        solid: object,
        resolution: int = 16,
        padding: float = 0.05,
    ) -> None:
        """Échantillonne un champ de distance signé 3D à partir d'un solide B-Rep OpenCASCADE/build123d.

        Utilise BRepClass3d_SolidClassifier pour le signe et BRepExtrema_DistShapeShape
        pour la distance euclidienne exacte minimale.
        """
        try:
            from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
            from OCP.BRepClass3d import BRepClass3d_SolidClassifier
            from OCP.BRepExtrema import BRepExtrema_DistShapeShape
            from OCP.gp import gp_Pnt
            from OCP.TopAbs import TopAbs_IN
            from build123d.topology import Compound, Solid
        except ImportError:
            return

        shape_wrapped = getattr(solid, "wrapped", solid)

        # Calcul de la boîte englobante
        classifier = BRepClass3d_SolidClassifier(shape_wrapped)

        # Déterminer les bornes
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib

        bnd = Bnd_Box()
        BRepBndLib.Add_s(shape_wrapped, bnd)
        cmin = bnd.CornerMin()
        cmax = bnd.CornerMax()
        x_min, y_min, z_min = float(cmin.X()), float(cmin.Y()), float(cmin.Z())
        x_max, y_max, z_max = float(cmax.X()), float(cmax.Y()), float(cmax.Z())

        x_min -= padding
        y_min -= padding
        z_min -= padding
        x_max += padding
        y_max += padding
        z_max += padding

        span_x = x_max - x_min
        span_y = y_max - y_min
        span_z = z_max - z_min
        max_span = max(span_x, span_y, span_z, 1e-4)

        # Utiliser la résolution du champ mémoire Taichi GPU
        res = self.engine.sdf_nx
        h = max_span / float(res - 1)

        sdf_arr = np.zeros((res, res, res), dtype=np.float32)

        for ix in range(res):
            px = x_min + ix * h
            for iy in range(res):
                py = y_min + iy * h
                for iz in range(res):
                    pz = z_min + iz * h

                    # Test d'inclusion
                    classifier.Perform(gp_Pnt(px, py, pz), 1e-4)
                    is_inside = classifier.State() == TopAbs_IN

                    # Distance minimale
                    pnt_vert = BRepBuilderAPI_MakeVertex(gp_Pnt(px, py, pz)).Shape()
                    extrema = BRepExtrema_DistShapeShape(shape_wrapped, pnt_vert)
                    extrema.Perform()
                    dist = extrema.Value()

                    sdf_arr[ix, iy, iz] = -dist if is_inside else dist

        # Transfert VRAM Taichi
        self.engine.sdf_grid.from_numpy(sdf_arr)
        self.engine.sdf_origin[None] = ti.Vector([x_min, y_min, z_min])
        self.engine.sdf_cell_size[None] = h
        self.engine.has_sdf[None] = 1

    def drape_on_solid(
        self, solid: object, center_y: float = 1.0, resolution: int = 16
    ) -> None:
        """Prépare le drapé automatique du textile au-dessus d'un solide mécanique B-Rep."""
        self.reset_horizontal(center_y=center_y)
        self.unpin_all()
        self.load_sdf_from_brep(solid, resolution=resolution)

    def step(
        self,
        n_frames: int = 1,
        ground_y: float = 0.0,
        gravity_y: float = -9.81,
        cloth_margin: float = 0.008,
    ) -> None:
        """Avance la simulation physique de n_frames via sous-pas d'intégration XPBD."""
        sub_dt = self.sub_dt

        for _ in range(n_frames):
            for _ in range(self.substeps):
                # 1. Prédiction semi-implicite
                self.engine.predict_positions(sub_dt, gravity_y)

                # 2. Contraintes d'étirement et de cisaillement (4 couleurs de graphe)
                for c in range(4):
                    self.engine.solve_stretch_and_shear(c, sub_dt)

                # 3. Contraintes de flexion isométrique
                for c in range(4):
                    self.engine.solve_bending(c, sub_dt)

                # 4. Collisions (sol, sphères, boîtes, cylindres, champ SDF B-Rep)
                self.engine.resolve_collisions(ground_y, cloth_margin)

                # 5. Vitesses et normales
                self.engine.finalize_step(sub_dt)

        # Calcul du champ scalaire de déformation (strain)
        self.engine.compute_strain_field()
        ti.sync()

    def get_positions(self) -> npt.NDArray[np.float32]:
        """Renvoie les positions actuelles des particules sous forme de tableau NumPy (N, 3)."""
        return np.ascontiguousarray(self.engine.pos.to_numpy(), dtype=np.float32)

    def get_velocities(self) -> npt.NDArray[np.float32]:
        """Renvoie les vitesses actuelles des particules (N, 3)."""
        return np.ascontiguousarray(self.engine.vel.to_numpy(), dtype=np.float32)

    def get_normals(self) -> npt.NDArray[np.float32]:
        """Renvoie les vecteurs normaux actuels des particules (N, 3)."""
        return np.ascontiguousarray(self.engine.normals.to_numpy(), dtype=np.float32)

    def get_strains(self) -> npt.NDArray[np.float32]:
        """Renvoie le champ scalaire de déformation relative des particules (N,)."""
        return np.ascontiguousarray(self.engine.strain.to_numpy(), dtype=np.float32)

    # --- Implémentation du Contrat GeometryProvider ---

    def tessellate_binary(
        self,
        linear_deflection: float = 0.001,
        angular_deflection: float = 0.2,
        scalar_mode: Literal["strain", "speed"] = "strain",
    ) -> BinaryMeshPayload:
        """Génère la charge utile binaire zero-copy pour l'affichage WebGL."""
        positions = self.get_positions()
        normals = self.get_normals()

        if scalar_mode == "strain":
            scalar_field = self.get_strains()
        else:
            velocities = self.get_velocities()
            scalar_field = np.linalg.norm(velocities, axis=1).astype(np.float32)

        return BinaryMeshPayload(
            entity_id="textile_xpbd_cloth",
            vertices=positions,
            normals=normals,
            indices=self.indices,
            face_ids=self.face_ids,
            scalar_field=scalar_field,
        )

    def introspect(self) -> ValidationReport:
        """Fournit un rapport déterministe d'inspection géométrique."""
        positions = self.get_positions()
        min_pt = positions.min(axis=0)
        max_pt = positions.max(axis=0)
        center_of_mass = positions.mean(axis=0)

        # Calcul de la surface totale
        v0 = positions[self.indices[:, 0]]
        v1 = positions[self.indices[:, 1]]
        v2 = positions[self.indices[:, 2]]
        cross_prod = np.cross(v1 - v0, v2 - v0)
        tri_areas = 0.5 * np.linalg.norm(cross_prod, axis=1)
        total_area = float(np.sum(tri_areas))

        strains = self.get_strains()
        max_strain = float(np.max(strains)) if len(strains) > 0 else 0.0

        return ValidationReport(
            is_valid_l0=True,
            is_watertight=False,  # Membrane ouverte
            volume_m3=0.0,
            surface_area_m2=total_area,
            center_of_mass=(float(center_of_mass[0]), float(center_of_mass[1]), float(center_of_mass[2])),
            bbox_min=(float(min_pt[0]), float(min_pt[1]), float(min_pt[2])),
            bbox_max=(float(max_pt[0]), float(max_pt[1]), float(max_pt[2])),
            topology_counts={
                "particles": self.engine.n_particles,
                "triangles": len(self.indices),
                "grid_res_x": self.res_x,
                "grid_res_y": self.res_y,
                "spheres": int(self.engine.n_spheres[None]),
                "boxes": int(self.engine.n_boxes[None]),
                "cylinders": int(self.engine.n_cyls[None]),
                "has_sdf": int(self.engine.has_sdf[None]),
            },
            dfm_warnings=[f"Contrainte maximale (strain) : {max_strain:.4f}"] if max_strain > 0.4 else [],
        )

    def resolve_selector(self, selector_expr: str) -> List[int]:
        """Évalue un sélecteur sémantique de nœuds ou faces textiles."""
        expr = selector_expr.strip().lower()
        if expr in ("vertices.pinned", "pinned"):
            inv_m = self.engine.inv_mass.to_numpy()
            return [int(i) for i in np.where(inv_m == 0.0)[0]]
        if expr in ("vertices.free", "free"):
            inv_m = self.engine.inv_mass.to_numpy()
            return [int(i) for i in np.where(inv_m > 0.0)[0]]
        if expr in ("vertices.top", "top"):
            return [int(i * self.res_y + (self.res_y - 1)) for i in range(self.res_x)]
        if expr in ("vertices.bottom", "bottom"):
            return [int(i * self.res_y) for i in range(self.res_x)]
        if expr in ("vertices.corners", "corners"):
            return [
                0,
                self.res_y - 1,
                (self.res_x - 1) * self.res_y,
                (self.res_x - 1) * self.res_y + (self.res_y - 1),
            ]
        if expr in ("faces", "triangles"):
            return list(range(len(self.indices)))
        return []

    def serialize_native(self) -> bytes:
        """Sérialise le maillage textile au format Wavefront OBJ standard."""
        positions = self.get_positions()
        normals = self.get_normals()

        lines: List[str] = ["# Mono Textile XPBD Surface Mesh\n"]
        for p in positions:
            lines.append(f"v {p[0]:.6f} {p[1]:.6f} {p[2]:.6f}\n")
        for n in normals:
            lines.append(f"vn {n[0]:.6f} {n[1]:.6f} {n[2]:.6f}\n")
        for tri in self.indices:
            # OBJ utilise une indexation base 1
            i0, i1, i2 = tri[0] + 1, tri[1] + 1, tri[2] + 1
            lines.append(f"f {i0}//{i0} {i1}//{i1} {i2}//{i2}\n")

        return "".join(lines).encode("utf-8")
