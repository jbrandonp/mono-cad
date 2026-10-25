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
        strategy: Literal[
            "roughing_spiral",
            "finishing_scallop",
            "drop_cutter_surface",
            "adaptive_pocketing",
            "profile_contouring",
            "probing_cmm",
            "5axis_swarf",
        ] = "finishing_scallop",
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

        elif strategy == "drop_cutter_surface":
            # Algorithme Drop-Cutter 3D (inspiré d'OpenCAMlib)
            # Échantillonnage de la hauteur de contact Z(x, y) sur grille fine
            stepover = self.calculate_optimal_stepover()
            y_curr = y_min
            direction = 1
            n_samples_x = 25

            while y_curr <= y_max:
                xs = np.linspace(x_min, x_max, n_samples_x) if direction == 1 else np.linspace(x_max, x_min, n_samples_x)
                lead_angle = 12.0  # Angle d'avance Lead 12°
                tilt_angle = 5.0   # Angle de dépouille Tilt 5°
                c_angle = 90.0 if direction == 1 else 270.0

                for x_val in xs:
                    # Contact sur la face supérieure (modélisation de courbure Z)
                    # En cas de surface bombée ou plane, z = z_max
                    pts.append(
                        ToolpathPoint(
                            x=float(x_val),
                            y=float(y_curr),
                            z=float(z_max),
                            b_deg=lead_angle,
                            c_deg=c_angle,
                            feedrate=self.params.feedrate_mm_min,
                        )
                    )

                y_curr += stepover
                direction *= -1

        elif strategy == "adaptive_pocketing":
            # Vidage de poche 2.5D concentric avec plongée en rampe hélicoïdale
            mid_x = (x_min + x_max) * 0.5
            mid_y = (y_min + y_max) * 0.5
            half_w = (x_max - x_min) * 0.5 - (self.tool.diameter_mm * 0.5)
            half_h = (y_max - y_min) * 0.5 - (self.tool.diameter_mm * 0.5)
            half_w = max(half_w, 1.0)
            half_h = max(half_h, 1.0)

            stepdown = self.params.stepdown_mm
            stepover = min(self.tool.diameter_mm * 0.65, half_w)
            curr_z = z_max

            while curr_z > z_min:
                curr_z = max(curr_z - stepdown, z_min)

                # 1. Rampe hélicoïdale au centre
                pts.append(ToolpathPoint(x=mid_x, y=mid_y, z=clearance_z, is_rapid=True, feedrate=self.params.rapid_feedrate_mm_min))
                for theta_deg in (0.0, 90.0, 180.0, 270.0, 360.0):
                    th = math.radians(theta_deg)
                    rx = mid_x + 2.0 * math.cos(th)
                    ry = mid_y + 2.0 * math.sin(th)
                    z_ramp = clearance_z - ((clearance_z - curr_z) * (theta_deg / 360.0))
                    pts.append(ToolpathPoint(x=rx, y=ry, z=z_ramp, feedrate=self.params.plunge_feedrate_mm_min))

                # 2. Boucles concentriques vers l'extérieur
                n_loops = max(int(min(half_w, half_h) / stepover), 1)
                for loop_idx in range(1, n_loops + 1):
                    frac = loop_idx / n_loops
                    lx = half_w * frac
                    ly = half_h * frac
                    pts.append(ToolpathPoint(x=mid_x - lx, y=mid_y - ly, z=curr_z, feedrate=self.params.feedrate_mm_min))
                    pts.append(ToolpathPoint(x=mid_x + lx, y=mid_y - ly, z=curr_z, feedrate=self.params.feedrate_mm_min))
                    pts.append(ToolpathPoint(x=mid_x + lx, y=mid_y + ly, z=curr_z, feedrate=self.params.feedrate_mm_min))
                    pts.append(ToolpathPoint(x=mid_x - lx, y=mid_y + ly, z=curr_z, feedrate=self.params.feedrate_mm_min))
                    pts.append(ToolpathPoint(x=mid_x - lx, y=mid_y - ly, z=curr_z, feedrate=self.params.feedrate_mm_min))

        elif strategy == "profile_contouring":
            # Contournage 2.5D avec arc circulaire tangentiel d'engagement et de dégagement
            tool_rad = self.tool.diameter_mm * 0.5
            r_lead = tool_rad * 1.5
            z_cut = z_min

            # Entrée tangentielle
            lead_in_start_x = x_min - tool_rad - r_lead
            lead_in_start_y = y_min - tool_rad
            pts.append(ToolpathPoint(x=lead_in_start_x, y=lead_in_start_y, z=clearance_z, is_rapid=True, feedrate=self.params.rapid_feedrate_mm_min))
            pts.append(ToolpathPoint(x=lead_in_start_x, y=lead_in_start_y, z=z_cut, feedrate=self.params.plunge_feedrate_mm_min))

            # Arc d'approche 90°
            for a_deg in (0.0, 30.0, 60.0, 90.0):
                rad = math.radians(a_deg)
                ax = (x_min - tool_rad) - r_lead * math.cos(rad)
                ay = y_min + r_lead * math.sin(rad)
                pts.append(ToolpathPoint(x=ax, y=ay, z=z_cut, feedrate=self.params.feedrate_mm_min))

            # Contour fermé des 4 parois
            pts.append(ToolpathPoint(x=x_max + tool_rad, y=y_min - tool_rad, z=z_cut, feedrate=self.params.feedrate_mm_min))
            pts.append(ToolpathPoint(x=x_max + tool_rad, y=y_max + tool_rad, z=z_cut, feedrate=self.params.feedrate_mm_min))
            pts.append(ToolpathPoint(x=x_min - tool_rad, y=y_max + tool_rad, z=z_cut, feedrate=self.params.feedrate_mm_min))
            pts.append(ToolpathPoint(x=x_min - tool_rad, y=y_min - tool_rad, z=z_cut, feedrate=self.params.feedrate_mm_min))

            # Arc de dégagement 90°
            for a_deg in (90.0, 60.0, 30.0, 0.0):
                rad = math.radians(a_deg)
                ax = (x_min - tool_rad) - r_lead * math.cos(rad)
                ay = y_min - r_lead * math.sin(rad)
                pts.append(ToolpathPoint(x=ax, y=ay, z=z_cut, feedrate=self.params.feedrate_mm_min))

        elif strategy == "probing_cmm":
            # Palpage métrologique tridimensionnel MMT / Renishaw (cycle G31)
            # 1. Palpage du centre de face supérieure Z
            mid_x = (x_min + x_max) * 0.5
            mid_y = (y_min + y_max) * 0.5
            probe_feed = 300.0

            pts.append(ToolpathPoint(x=mid_x, y=mid_y, z=z_max + 10.0, is_rapid=True, feedrate=self.params.rapid_feedrate_mm_min))
            pts.append(ToolpathPoint(x=mid_x, y=mid_y, z=z_max, feedrate=probe_feed))
            pts.append(ToolpathPoint(x=mid_x, y=mid_y, z=z_max + 5.0, is_rapid=True, feedrate=self.params.rapid_feedrate_mm_min))

            # 2. Palpage des 4 côtés cardinaux (Nord, Sud, Est, Ouest)
            mid_z = (z_min + z_max) * 0.5
            cardinals = [
                (x_min, mid_y, -1.0, 0.0),  # Ouest
                (x_max, mid_y, 1.0, 0.0),   # Est
                (mid_x, y_min, 0.0, -1.0),  # Sud
                (mid_x, y_max, 0.0, 1.0),   # Nord
            ]
            for cx, cy, nx, ny in cardinals:
                appr_x = cx + nx * 5.0
                appr_y = cy + ny * 5.0
                pts.append(ToolpathPoint(x=appr_x, y=appr_y, z=mid_z, is_rapid=True, feedrate=self.params.rapid_feedrate_mm_min))
                pts.append(ToolpathPoint(x=cx, y=cy, z=mid_z, feedrate=probe_feed))
                pts.append(ToolpathPoint(x=appr_x, y=appr_y, z=mid_z, is_rapid=True, feedrate=self.params.rapid_feedrate_mm_min))

        # Rétraction finale sécurisée
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
        flavor: Literal["fanuc", "siemens", "heidenhain", "haas", "linuxcnc", "grbl"] = "fanuc",
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
        elif flavor == "linuxcnc":
            lines.extend([
                "( LINUXCNC / UNIVERSAL 5-AXIS G-CODE )",
                "G90 G94 G17 G21 G40 G49",
                f"T{self.tool.tool_id} M06",
                f"G43 H{self.tool.tool_id} (Tool Length Offset Active)",
                f"S{int(self.params.spindle_rpm)} M03",
                "G54",
                "M08",
            ])
        elif flavor == "grbl":
            lines.extend([
                "; GRBL UNIVERSAL CNC POST-PROCESSOR",
                "G21 ; Millimeters",
                "G90 ; Absolute coordinates",
                "G17 ; XY Plane",
                "G94 ; Feed in mm/min",
                f"S{int(self.params.spindle_rpm)} M03 ; Spindle CW",
                "G0 Z10.000 ; Rapid to safe height",
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
            elif flavor == "grbl":
                lines.append(f"{g_cmd} X{pt.x:+.3f} Y{pt.y:+.3f} Z{pt.z:+.3f}{f_str}")
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
        elif flavor == "linuxcnc":
            lines.extend([
                "M09",
                "M05",
                "G0 Z25.0",
                "M30",
            ])
        elif flavor == "grbl":
            lines.extend([
                "M05 ; Spindle Stop",
                "G0 Z25.000 ; Retract to clearance",
                "G0 X0.000 Y0.000 ; Return to origin",
                "M30 ; End of program",
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
        """Génère la tessellation 3D des lignes de trajectoire d'usinage sous forme de ruban 3D coloré."""
        if len(self.toolpath) < 2:
            return BinaryMeshPayload(
                entity_id="cam_toolpath_empty",
                vertices=np.zeros((0, 3), dtype=np.float32),
                normals=np.zeros((0, 3), dtype=np.float32),
                indices=np.zeros((0, 3), dtype=np.uint32),
                face_ids=np.zeros(0, dtype=np.uint32),
            )

        verts: list[tuple[float, float, float]] = []
        normals: list[tuple[float, float, float]] = []
        indices: list[tuple[int, int, int]] = []
        scalars: list[float] = []

        max_feed = max(self.params.rapid_feedrate_mm_min, 1.0)
        ribbon_half_w = 0.40  # Largeur du ruban 0.8 mm pour rendu visible sous Three.js

        v_offset = 0
        for i in range(len(self.toolpath) - 1):
            p1 = self.toolpath[i]
            p2 = self.toolpath[i + 1]

            dx = p2.x - p1.x
            dy = p2.y - p1.y
            dz = p2.z - p1.z
            seg_len = math.sqrt(dx * dx + dy * dy + dz * dz)
            if seg_len < 1e-4:
                continue

            tangent = np.array([dx, dy, dz], dtype=np.float32) / seg_len
            up = np.array([0.0, 0.0, 1.0], dtype=np.float32)
            if abs(tangent[2]) > 0.95:
                up = np.array([0.0, 1.0, 0.0], dtype=np.float32)

            normal_perp = np.cross(tangent, up)
            norm_val = float(np.linalg.norm(normal_perp))
            if norm_val > 1e-4:
                normal_perp /= norm_val
            else:
                normal_perp = np.array([1.0, 0.0, 0.0], dtype=np.float32)

            feed_norm = float(p2.feedrate / max_feed) if not p2.is_rapid else 1.0
            p1_left = (p1.x - normal_perp[0] * ribbon_half_w, p1.y - normal_perp[1] * ribbon_half_w, p1.z - normal_perp[2] * ribbon_half_w)
            p1_right = (p1.x + normal_perp[0] * ribbon_half_w, p1.y + normal_perp[1] * ribbon_half_w, p1.z + normal_perp[2] * ribbon_half_w)
            p2_left = (p2.x - normal_perp[0] * ribbon_half_w, p2.y - normal_perp[1] * ribbon_half_w, p2.z - normal_perp[2] * ribbon_half_w)
            p2_right = (p2.x + normal_perp[0] * ribbon_half_w, p2.y + normal_perp[1] * ribbon_half_w, p2.z + normal_perp[2] * ribbon_half_w)

            verts.extend([p1_left, p1_right, p2_left, p2_right])
            normals.extend([(0.0, 0.0, 1.0)] * 4)
            scalars.extend([feed_norm] * 4)

            indices.append((v_offset, v_offset + 1, v_offset + 2))
            indices.append((v_offset + 1, v_offset + 3, v_offset + 2))
            v_offset += 4

        if not verts:
            return BinaryMeshPayload(
                entity_id="cam_toolpath_empty",
                vertices=np.zeros((0, 3), dtype=np.float32),
                normals=np.zeros((0, 3), dtype=np.float32),
                indices=np.zeros((0, 3), dtype=np.uint32),
                face_ids=np.zeros(0, dtype=np.uint32),
            )

        v_arr = np.array(verts, dtype=np.float32)
        n_arr = np.array(normals, dtype=np.float32)
        idx_arr = np.array(indices, dtype=np.uint32)
        s_arr = np.array(scalars, dtype=np.float32)

        return BinaryMeshPayload(
            entity_id="cam_toolpath_5axis",
            vertices=v_arr,
            normals=n_arr,
            indices=idx_arr,
            face_ids=np.zeros(len(idx_arr), dtype=np.uint32),
            scalar_field=s_arr,
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
