"""Moteur de FAO et d'usinage multi-axes CNC (CAM 5-Axes RTCP & ISO 6983).

Fournit la cinématique RTCP (Rotation Tool Center Point / Tool Tip Tracking) continue,
le calcul optimal de la hauteur de crête (Scallop Height Ra), la détection de talonnement
(Toolholder Gouge Checking) et les post-processeurs industriels (Fanuc G43.4, Siemens TRAORI, Heidenhain M128).
Conforme au contrat d'interface `GeometryProvider` de Mono.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import List, Literal, Tuple
import numpy as np
import numpy.typing as npt

import build123d as bd
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib

from mono.core.geometry_provider import BinaryMeshPayload, GeometryProvider, ValidationReport


@dataclass(frozen=True)
class ToolDefinition:
    """Définition géométrique et dynamique de l'outil coupant."""

    tool_id: int = 1
    tool_type: Literal["ball_end", "flat_end", "bull_nose"] = "ball_end"
    diameter_mm: float = 10.0
    length_mm: float = 75.0
    corner_radius_mm: float = 5.0  # Pour fraise hémisphérique R = D/2
    flute_count: int = 4
    holder_diameter_mm: float = 45.0
    holder_length_mm: float = 50.0


@dataclass(frozen=True)
class MachiningParameters:
    """Paramètres d'usinage et conditions de coupe."""

    spindle_rpm: float = 12000.0
    feedrate_mm_min: float = 3600.0
    plunge_feedrate_mm_min: float = 800.0
    rapid_feedrate_mm_min: float = 15000.0
    stepdown_mm: float = 2.0
    target_roughness_ra_um: float = 0.8  # Finition Ra 0.8 µm


@dataclass(frozen=True)
class ToolpathPoint:
    """Point de trajectoire d'usinage 5-axes avec coordonnées et orientation angulaire."""

    x: float
    y: float
    z: float
    b_deg: float = 0.0  # Inclinaison tête/table (axe B)
    c_deg: float = 0.0  # Rotation table (axe C)
    feedrate: float = 3600.0
    is_rapid: bool = False


@dataclass(frozen=True)
class CamAuditReport:
    """Rapport de validation de trajectoire FAO et fabricabilité par usinage."""

    total_points: int
    toolpath_length_mm: float
    estimated_machining_time_seconds: float
    optimal_stepover_mm: float
    scallop_height_um: float
    is_collision_free: bool
    gouge_warnings: List[str]


class CamMachiningProvider(GeometryProvider):
    """Fournisseur géométrique de FAO 5-axes et de génération de trajectoires CNC.

    Conforme au contrat `GeometryProvider` de Mono.
    Projette les trajectoires d'usinage en 3D avec le vecteur d'avance (feedrate)
    en champ scalaire pour contrôle visuel dans le Viewport Three.js.
    """

    def __init__(
        self,
        part: bd.Solid | bd.Compound | None = None,
        tool: ToolDefinition | None = None,
        params: MachiningParameters | None = None,
    ) -> None:
        """Initialise le module d'usinage CAM avec la pièce et les outils coupants."""
        if part is None:
            self.part: bd.Solid | bd.Compound = bd.Box(40, 40, 20)
        else:
            self.part = part

        self.tool: ToolDefinition = tool if tool is not None else ToolDefinition()
        self.params: MachiningParameters = params if params is not None else MachiningParameters()
        self.toolpath: list[ToolpathPoint] = []
        self.cached_gcode: str = ""

        # Calcul automatique de la trajectoire nominale
        self.generate_toolpath(strategy="finishing_scallop")

    def calculate_optimal_stepover(self, target_ra_um: float | None = None) -> float:
        """Calcule le pas latéral maximal pour garantir l'état de surface Ra visé.

        Formule : s = sqrt(32 * R_tool * Ra_mm)
        """
        ra_um = target_ra_um if target_ra_um is not None else self.params.target_roughness_ra_um
        ra_mm = ra_um * 1e-3
        r_tool = self.tool.corner_radius_mm
        stepover = math.sqrt(max(32.0 * r_tool * ra_mm, 1e-6))
        return min(stepover, self.tool.diameter_mm * 0.75)

    def calculate_scallop_height(self, stepover_mm: float) -> float:
        """Calcule la hauteur de crête résiduelle (Scallop Height) en micromètres.

        Formule : h = (s^2) / (8 * R_tool)
        """
        r_tool = self.tool.corner_radius_mm
        h_mm = (stepover_mm * stepover_mm) / (8.0 * r_tool)
        return h_mm * 1000.0  # en micromètres

    def compute_rtcp_pivot(
        self,
        tip_pos: Tuple[float, float, float],
        b_deg: float,
        c_deg: float,
    ) -> Tuple[float, float, float]:
        """Calcule la position du pivot de broche machine par cinématique inverse RTCP 5-axes.

        P_pivot = P_tip + L_tool * V_tool
        V_tool = (sin(B)*cos(C), sin(B)*sin(C), cos(B))
        """
        b_rad = math.radians(b_deg)
        c_rad = math.radians(c_deg)

        vx = math.sin(b_rad) * math.cos(c_rad)
        vy = math.sin(b_rad) * math.sin(c_rad)
        vz = math.cos(b_rad)

        l_tool = self.tool.length_mm
        px = tip_pos[0] + l_tool * vx
        py = tip_pos[1] + l_tool * vy
        pz = tip_pos[2] + l_tool * vz

        return (round(px, 4), round(py, 4), round(pz, 4))

    def generate_toolpath(
        self,
        strategy: Literal["roughing_spiral", "finishing_scallop", "5axis_swarf"] = "finishing_scallop",
    ) -> List[ToolpathPoint]:
        """Génère la trajectoire d'usinage 3D continue selon la stratégie sélectionnée."""
        bnd = Bnd_Box()
        BRepBndLib.Add_s(self.part.wrapped, bnd)
        cmin = bnd.CornerMin()
        cmax = bnd.CornerMax()

        x_min, y_min, z_min = cmin.X(), cmin.Y(), cmin.Z()
        x_max, y_max, z_max = cmax.X(), cmax.Y(), cmax.Z()

        pts: list[ToolpathPoint] = []
        clearance_z = z_max + 10.0

        # Point de départ sécurisé (approche rapide)
        pts.append(ToolpathPoint(x=x_min, y=y_min, z=clearance_z, is_rapid=True, feedrate=self.params.rapid_feedrate_mm_min))

        if strategy == "roughing_spiral":
            # Ébauche par niveaux Z
            curr_z = z_max
            stepdown = self.params.stepdown_mm
            while curr_z > z_min:
                curr_z = max(curr_z - stepdown, z_min)
                pts.append(ToolpathPoint(x=x_min, y=y_min, z=curr_z, feedrate=self.params.plunge_feedrate_mm_min))
                pts.append(ToolpathPoint(x=x_max, y=y_min, z=curr_z, feedrate=self.params.feedrate_mm_min))
                pts.append(ToolpathPoint(x=x_max, y=y_max, z=curr_z, feedrate=self.params.feedrate_mm_min))
                pts.append(ToolpathPoint(x=x_min, y=y_max, z=curr_z, feedrate=self.params.feedrate_mm_min))
                pts.append(ToolpathPoint(x=x_min, y=y_min, z=curr_z, feedrate=self.params.feedrate_mm_min))

        elif strategy in ("finishing_scallop", "5axis_swarf"):
            # Finition à crête constante avec balayage et orientation 5-axes
            stepover = self.calculate_optimal_stepover()
            y_curr = y_min
            direction = 1

            while y_curr <= y_max:
                x_start = x_min if direction == 1 else x_max
                x_end = x_max if direction == 1 else x_min

                # Inclinaison 5-axes douce (Lead/Tilt angles) pour éviter la coupe à vitesse nulle au pôle
                b_angle = 15.0  # Inclinaison de 15°
                c_angle = 45.0 if direction == 1 else 225.0

                pts.append(ToolpathPoint(x=x_start, y=y_curr, z=z_max, b_deg=b_angle, c_deg=c_angle, feedrate=self.params.feedrate_mm_min))
                pts.append(ToolpathPoint(x=x_end, y=y_curr, z=z_max, b_deg=b_angle, c_deg=c_angle, feedrate=self.params.feedrate_mm_min))

                y_curr += stepover
                direction *= -1

        # Rétraction finale
        pts.append(ToolpathPoint(x=pts[-1].x, y=pts[-1].y, z=clearance_z, is_rapid=True, feedrate=self.params.rapid_feedrate_mm_min))

        self.toolpath = pts
        return pts

    def check_holder_gouge(
        self,
        holder_clearance_mm: float = 1.5,
    ) -> List[str]:
        """Vérifie l'absence de talonnement (collision entre porte-outil et pièce brute)."""
        warnings: list[str] = []
        bnd = Bnd_Box()
        BRepBndLib.Add_s(self.part.wrapped, bnd)
        cmax_z = bnd.CornerMax().Z()

        holder_radius = self.tool.holder_diameter_mm * 0.5
        tool_len = self.tool.length_mm

        for idx, pt in enumerate(self.toolpath):
            if pt.is_rapid:
                continue

            # Position du bas du cône porte-outil
            holder_bottom_z = pt.z + (tool_len * math.cos(math.radians(pt.b_deg)))
            if holder_bottom_z < (cmax_z + holder_clearance_mm):
                warnings.append(
                    f"Alerte talonnement bloc {idx + 1} : Porte-outil Z={holder_bottom_z:.2f} mm "
                    f"trop proche de la surface pièce Z_max={cmax_z:.2f} mm (Marge < {holder_clearance_mm} mm)."
                )
                break

        return warnings

    def post_process_gcode(
        self,
        flavor: Literal["fanuc", "siemens", "heidenhain", "haas"] = "fanuc",
    ) -> str:
        """Génère le programme ISO 6983 spécifique au directeur de commande numérique (DCN)."""
        lines: list[str] = []
        now_comment = f"; MONO 5-Axis RTCP CAM Post-Processor [{flavor.upper()}]"
        lines.append(now_comment)
        lines.append(f"; Tool D={self.tool.diameter_mm}mm, L={self.tool.length_mm}mm | Spindle={self.params.spindle_rpm} RPM")

        # Entête selon DCN
        if flavor == "fanuc":
            lines.extend([
                "%",
                "O1001 (PART_5AXIS_MACHINING)",
                "G90 G94 G17 G40 G49",
                "G21 (Metric)",
                f"T{self.tool.tool_id} M06",
                f"S{int(self.params.spindle_rpm)} M03",
                "G54",
                "G43.4 H1 (RTCP Mode ON - Tool Center Point Tracking)",
                "M08",
            ])
        elif flavor == "siemens":
            lines.extend([
                "; SIEMENS 840D / ONE",
                "G90 G94 G17",
                f"T=\"TOOL_{self.tool.tool_id}\" M06",
                f"S{int(self.params.spindle_rpm)} M03",
                "TRAORI (RTCP Transformation Activation)",
                "M08",
            ])
        elif flavor == "heidenhain":
            lines.extend([
                "BEGIN PGM 1001 MM",
                f"TOOL CALL {self.tool.tool_id} Z S{int(self.params.spindle_rpm)}",
                "M128 (TCPM Active)",
                "M08",
            ])
        else:  # haas
            lines.extend([
                "%",
                "O1001",
                "G90 G94 G17",
                f"T{self.tool.tool_id} M06",
                f"S{int(self.params.spindle_rpm)} M03",
                "G234 H1 (Tool Center Point Control)",
                "M08",
            ])

        # Blocs de mouvements
        for pt in self.toolpath:
            g_cmd = "G0" if pt.is_rapid else "G1"
            f_str = f" F{pt.feedrate:.0f}" if not pt.is_rapid else ""
            if flavor == "heidenhain":
                l_str = f"L X{pt.x:+.3f} Y{pt.y:+.3f} Z{pt.z:+.3f} B{pt.b_deg:+.3f} C{pt.c_deg:+.3f}"
                l_str += f" F{pt.feedrate:.0f}" if not pt.is_rapid else " FMAX"
                lines.append(l_str)
            else:
                lines.append(
                    f"{g_cmd} X{pt.x:+.3f} Y{pt.y:+.3f} Z{pt.z:+.3f} "
                    f"B{pt.b_deg:+.3f} C{pt.c_deg:+.3f}{f_str}"
                )

        # Pied de programme
        if flavor == "fanuc":
            lines.extend([
                "G49 (RTCP Mode Cancel)",
                "M09",
                "M05",
                "G91 G28 Z0.0",
                "G28 X0.0 Y0.0 B0.0 C0.0",
                "M30",
                "%",
            ])
        elif flavor == "siemens":
            lines.extend([
                "TRAFOOF (Deactivate Transformation)",
                "M09",
                "M05",
                "M30",
            ])
        elif flavor == "heidenhain":
            lines.extend([
                "M129 (TCPM Reset)",
                "M09",
                "M05",
                "END PGM 1001 MM",
            ])
        else:
            lines.extend([
                "G49",
                "M09",
                "M05",
                "G91 G28 Z0.",
                "M30",
                "%",
            ])

        self.cached_gcode = "\n".join(lines)
        return self.cached_gcode

    def audit_cam(self) -> CamAuditReport:
        """Produit un rapport d'audit exhaustif sur la trajectoire et le temps d'usinage."""
        total_len = 0.0
        total_time = 0.0

        for i in range(len(self.toolpath) - 1):
            p1 = self.toolpath[i]
            p2 = self.toolpath[i + 1]
            dx = p2.x - p1.x
            dy = p2.y - p1.y
            dz = p2.z - p1.z
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            total_len += dist
            feed = p2.feedrate if not p2.is_rapid else self.params.rapid_feedrate_mm_min
            feed_sec = max(feed / 60.0, 1.0)
            total_time += dist / feed_sec

        stepover = self.calculate_optimal_stepover()
        scallop_um = self.calculate_scallop_height(stepover)
        warnings = self.check_holder_gouge()

        return CamAuditReport(
            total_points=len(self.toolpath),
            toolpath_length_mm=round(total_len, 2),
            estimated_machining_time_seconds=round(total_time, 1),
            optimal_stepover_mm=round(stepover, 3),
            scallop_height_um=round(scallop_um, 2),
            is_collision_free=(len(warnings) == 0),
            gouge_warnings=warnings,
        )

    # --- Implémentation du Contrat GeometryProvider ---

    def tessellate_binary(
        self,
        linear_deflection: float = 0.001,
        angular_deflection: float = 0.2,
    ) -> BinaryMeshPayload:
        """Génère la tessellation 3D des lignes de trajectoire d'usinage (polylignes 3D)."""
        if len(self.toolpath) < 2:
            return BinaryMeshPayload(
                entity_id="cam_toolpath_empty",
                vertices=np.zeros((0, 3), dtype=np.float32),
                normals=np.zeros((0, 3), dtype=np.float32),
                indices=np.zeros((0, 3), dtype=np.uint32),
                face_ids=np.zeros(0, dtype=np.uint32),
            )

        # Génération d'une extrusion tubulaire le long des points de la trajectoire
        verts: list[tuple[float, float, float]] = []
        indices: list[tuple[int, int, int]] = []
        scalars: list[float] = []

        max_feed = max(self.params.rapid_feedrate_mm_min, 1.0)

        for pt in self.toolpath:
            verts.append((pt.x, pt.y, pt.z))
            scalars.append(float(pt.feedrate / max_feed))

        v_arr = np.array(verts, dtype=np.float32)
        n_pts = len(v_arr)

        # Création de segments d'indices de ligne (triangles dégénérés ou quad-strips simples)
        for i in range(n_pts - 1):
            indices.append((i, i + 1, i))

        return BinaryMeshPayload(
            entity_id="cam_toolpath_5axis",
            vertices=v_arr,
            normals=np.zeros_like(v_arr),
            indices=np.array(indices, dtype=np.uint32),
            face_ids=np.zeros(len(indices), dtype=np.uint32),
            scalar_field=np.array(scalars, dtype=np.float32),
        )

    def introspect(self) -> ValidationReport:
        """Fournit un rapport déterministe d'inspection FAO."""
        audit = self.audit_cam()
        bnd = Bnd_Box()
        BRepBndLib.Add_s(self.part.wrapped, bnd)
        cmin = bnd.CornerMin()
        cmax = bnd.CornerMax()

        return ValidationReport(
            is_valid_l0=True,
            is_watertight=True,
            volume_m3=0.0,
            surface_area_m2=0.0,
            center_of_mass=(0.0, 0.0, 0.0),
            bbox_min=(float(cmin.X()), float(cmin.Y()), float(cmin.Z())),
            bbox_max=(float(cmax.X()), float(cmax.Y()), float(cmax.Z())),
            topology_counts={
                "toolpath_points": audit.total_points,
                "tool_id": self.tool.tool_id,
                "estimated_time_s": int(audit.estimated_machining_time_seconds),
            },
            dfm_warnings=audit.gouge_warnings,
        )

    def resolve_selector(self, selector_expr: str) -> List[int]:
        """Résout les sélecteurs de points de trajectoire (ex: 'rapid', 'cutting')."""
        expr = selector_expr.strip().lower()
        if expr in ("rapid", "g0"):
            return [idx for idx, pt in enumerate(self.toolpath) if pt.is_rapid]
        if expr in ("cutting", "g1"):
            return [idx for idx, pt in enumerate(self.toolpath) if not pt.is_rapid]
        return list(range(len(self.toolpath)))

    def serialize_native(self) -> bytes:
        """Sérialise le programme d'usinage au format G-code ISO 6983 standard."""
        if not self.cached_gcode:
            self.post_process_gcode()
        return self.cached_gcode.encode("utf-8")
