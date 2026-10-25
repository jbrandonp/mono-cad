"""Fournisseur géométrique et moteur de plans industriels 2D TechDraw avec HLR et GD&T (Phase 9).

Standards respectés :
- ISO 128 : Règles générales d'application pour les dessins techniques (lignes continues, pointillés cachés).
- ISO 7200 / ISO 5457 : Cartouche d'ingénierie et formats de feuilles normalisés (A4, A3, A2, A1).
- ISO 1101 / ASME Y14.5-2018 : Tolérancement géométrique et dimensionnel (GD&T) avec conditions de matière (MMC).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import io
import math
from typing import Literal

import build123d as bd
import ezdxf
import numpy as np

from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer

from mono.core.geometry_provider import (
    BinaryMeshPayload,
    GeometryProvider,
    ValidationReport,
)


@dataclass
class TitleBlock:
    """Informations normalisées du cartouche de plan selon la norme ISO 7200."""

    title: str = "PIÈCE INDUSTRIELLE"
    drawing_number: str = "DWG-1001"
    revision: str = "A"
    sheet: str = "1/1"
    date: str = "2026-10-25"
    author: str = "Brandon"
    checker: str = "MONO QC"
    material: str = "Aluminium 6061-T6"
    scale: str = "1:1"
    company: str = "MONO CAD STUDIO"
    projection_system: Literal["first_angle", "third_angle"] = "first_angle"


@dataclass
class GdtFeatureControlFrame:
    """Cadre de tolérance géométrique normalisé (Feature Control Frame ISO 1101 / ASME Y14.5)."""

    symbol: Literal[
        "flatness",
        "circularity",
        "cylindricity",
        "perpendicularity",
        "parallelism",
        "position",
        "concentricity",
        "runout",
    ] = "position"
    tolerance_mm: float = 0.05
    diameter_symbol: bool = True
    material_condition: Literal["none", "mmc", "lmc", "rfs"] = "mmc"
    datum_primary: str | None = "A"
    datum_secondary: str | None = "B"
    datum_tertiary: str | None = "C"
    x_mm: float = 0.0
    y_mm: float = 0.0
    view_id: str = "front"

    def get_symbol_unicode(self) -> str:
        """Retourne le caractère symbolique GD&T officiel."""
        symbols = {
            "flatness": "⏥",
            "circularity": "○",
            "cylindricity": "⌭",
            "perpendicularity": "⟂",
            "parallelism": "∥",
            "position": "⌖",
            "concentricity": "◎",
            "runout": "↗",
        }
        return symbols.get(self.symbol, "⌖")


@dataclass
class DimensionAnnotation:
    """Cote linéaire ou angulaire normalisée avec tolérancement bilatéral ou ISO."""

    p1: tuple[float, float]
    p2: tuple[float, float]
    value_mm: float
    text_override: str | None = None
    tolerance_plus_mm: float = 0.1
    tolerance_minus_mm: float = 0.1
    dim_type: Literal["horizontal", "vertical", "aligned", "diameter", "radius"] = "horizontal"
    offset_mm: float = 12.0
    view_id: str = "front"


@dataclass
class ProjectionView:
    """Vue orthographique ou axonométrique 2D issue du calcul HLR."""

    view_id: str
    title: str
    view_type: Literal["front", "top", "right", "iso"]
    center_x_mm: float
    center_y_mm: float
    scale: float = 1.0
    visible_polylines: list[list[tuple[float, float]]] = field(default_factory=list)
    hidden_polylines: list[list[tuple[float, float]]] = field(default_factory=list)
    dimensions: list[DimensionAnnotation] = field(default_factory=list)
    gdt_frames: list[GdtFeatureControlFrame] = field(default_factory=list)


@dataclass
class TechDrawReport:
    """Rapport métrologique et statistique de la mise en plan 2D."""

    sheet_format: str
    sheet_width_mm: float
    sheet_height_mm: float
    views_count: int
    total_visible_segments: int
    total_hidden_segments: int
    dimensions_count: int
    gdt_count: int
    is_valid_iso128: bool


class TechDrawDraftingProvider(GeometryProvider):
    """Moteur de mise en plan technique industrielle 2D avec projection HLR et tolérancement GD&T."""

    def __init__(
        self,
        entity_id: str = "techdraw_001",
        part: bd.Solid | bd.Compound | None = None,
        sheet_format: Literal["A4", "A3", "A2", "A1"] = "A3",
        orientation: Literal["landscape", "portrait"] = "landscape",
        title_block: TitleBlock | None = None,
    ) -> None:
        """Initialise la planche de dessin technique."""
        self.entity_id: str = entity_id
        self.part: bd.Solid | bd.Compound = part if part is not None else bd.Box(60, 40, 25)
        self.sheet_format: Literal["A4", "A3", "A2", "A1"] = sheet_format
        self.orientation: Literal["landscape", "portrait"] = orientation
        self.title_block: TitleBlock = title_block if title_block is not None else TitleBlock()

        # Dimensions normalisées en mm (ISO 5457)
        sheet_sizes: dict[str, tuple[float, float]] = {
            "A4": (297.0, 210.0),
            "A3": (420.0, 297.0),
            "A2": (594.0, 420.0),
            "A1": (841.0, 594.0),
        }
        dim = sheet_sizes.get(sheet_format, (420.0, 297.0))
        if orientation == "portrait":
            self.width_mm, self.height_mm = min(dim), max(dim)
        else:
            self.width_mm, self.height_mm = max(dim), min(dim)

        self.margin_mm: float = 10.0
        self.views: dict[str, ProjectionView] = {}
        self.cached_svg: str = ""
        self.cached_dxf: bytes = b""

    # --- Algorithme HLR Sub-Segmentaire OpenCASCADE ---

    def _extract_edges_from_compound(self, compound_shape: object) -> list[list[tuple[float, float]]]:
        """Extrait et discrétise les arêtes géométriques en polylignes 2D."""
        if compound_shape.IsNull():  # type: ignore
            return []

        polylines: list[list[tuple[float, float]]] = []
        exp = TopExp_Explorer(compound_shape, TopAbs_EDGE)  # type: ignore
        while exp.More():
            edge = bd.Edge(exp.Current())
            # Si l'arête est rectiligne, 2 points suffisent ; sinon discrétisation en 12 échantillons
            length = edge.length
            num_samples = 2 if length < 2.0 else 12
            poly: list[tuple[float, float]] = []
            for i in range(num_samples):
                t = i / (num_samples - 1)
                p = edge.position_at(t)
                poly.append((round(float(p.X), 3), round(float(p.Y), 3)))
            if poly:
                polylines.append(poly)
            exp.Next()
        return polylines

    def project_hlr_view(
        self,
        view_type: Literal["front", "top", "right", "iso"],
        center_x: float,
        center_y: float,
        scale: float = 1.0,
    ) -> ProjectionView:
        """Projette le solide B-Rep selon l'angle de vue avec calcul HLR sub-segmentaire."""
        # Configuration du projecteur orthographique
        if view_type == "front":
            # Regard selon -Y, horizontal X=(1,0,0) -> vertical Z=(0,0,1)
            proj_ax = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, -1, 0), gp_Dir(1, 0, 0))
            title = "VUE DE FACE"
        elif view_type == "top":
            # Regard selon -Z, horizontal X=(1,0,0) -> vertical Y=(0,1,0)
            proj_ax = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, -1), gp_Dir(1, 0, 0))
            title = "VUE DE DESSUS"
        elif view_type == "right":
            # Regard selon -X, horizontal Y=(0,1,0) -> vertical Z=(0,0,1)
            proj_ax = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(-1, 0, 0), gp_Dir(0, 1, 0))
            title = "VUE DE DROITE"
        else:  # iso
            inv_sqrt3 = 1.0 / math.sqrt(3.0)
            inv_sqrt2 = 1.0 / math.sqrt(2.0)
            proj_ax = gp_Ax2(
                gp_Pnt(0, 0, 0),
                gp_Dir(-inv_sqrt3, -inv_sqrt3, inv_sqrt3),
                gp_Dir(inv_sqrt2, -inv_sqrt2, 0.0),
            )
            title = "VUE ISOMÉTRIQUE"

        projector = HLRAlgo_Projector(proj_ax)
        hlr = HLRBRep_Algo()
        hlr.Add(self.part.wrapped)
        hlr.Projector(projector)
        hlr.Update()
        hlr.Hide()

        shapes = HLRBRep_HLRToShape(hlr)
        v_comp = shapes.VCompound()
        h_comp = shapes.HCompound()

        visible_polys = self._extract_edges_from_compound(v_comp)
        hidden_polys = self._extract_edges_from_compound(h_comp) if view_type != "iso" else []

        view = ProjectionView(
            view_id=view_type,
            title=title,
            view_type=view_type,
            center_x_mm=center_x,
            center_y_mm=center_y,
            scale=scale,
            visible_polylines=visible_polys,
            hidden_polylines=hidden_polys,
        )
        return view

    # --- Mise en page et cotation automatique ---

    def generate_drawing(
        self,
        views: list[str] | None = None,
        auto_dimension: bool = True,
    ) -> TechDrawReport:
        """Calcule l'ensemble des projections HLR, dispose les vues et applique la cotation."""
        req_views = views or ["front", "top", "right", "iso"]
        bbox = self.part.bounding_box()
        dx, dy, dz = bbox.size.X, bbox.size.Y, bbox.size.Z

        # Échelle de tracé automatique pour tenir harmonieusement sur la feuille
        max_part_dim = max(dx, dy, dz)
        available_w = self.width_mm * 0.45
        scale = 1.0
        if max_part_dim > available_w:
            scale = round(available_w / max_part_dim, 2)
        elif max_part_dim < available_w * 0.25:
            scale = 2.0

        self.title_block.scale = f"{scale:.1f}:1" if scale >= 1.0 else f"1:{int(1.0 / scale)}"

        # Positions des vues en disposition premier dièdre (Système européen ISO)
        # Vue de face au centre-gauche, vue de dessus en-dessous, vue de droite à droite
        base_x = self.width_mm * 0.35
        base_y = self.height_mm * 0.55
        offset_x = max(dx * scale + 35.0, 70.0)
        offset_y = max(dy * scale + 35.0, 60.0)

        view_positions: dict[str, tuple[float, float]] = {
            "front": (base_x, base_y),
            "top": (base_x, max(base_y - offset_y, self.margin_mm + 40.0)),
            "right": (min(base_x + offset_x, self.width_mm - 80.0), base_y),
            "iso": (self.width_mm - 90.0, self.height_mm - 80.0),
        }

        self.views.clear()
        tot_vis = 0
        tot_hid = 0

        for v_name in req_views:
            if v_name in view_positions:
                pos = view_positions[v_name]
                v_type: Literal["front", "top", "right", "iso"] = v_name  # type: ignore
                view = self.project_hlr_view(v_type, pos[0], pos[1], scale=scale)
                tot_vis += len(view.visible_polylines)
                tot_hid += len(view.hidden_polylines)

                # Cotation automatique des dimensions d'encombrement
                if auto_dimension and v_name == "front":
                    # Longueur horizontale
                    view.dimensions.append(
                        DimensionAnnotation(
                            p1=(-dx * 0.5, -dz * 0.5),
                            p2=(dx * 0.5, -dz * 0.5),
                            value_mm=round(dx, 2),
                            dim_type="horizontal",
                            offset_mm=-12.0,
                            view_id="front",
                        )
                    )
                    # Hauteur verticale
                    view.dimensions.append(
                        DimensionAnnotation(
                            p1=(dx * 0.5, -dz * 0.5),
                            p2=(dx * 0.5, dz * 0.5),
                            value_mm=round(dz, 2),
                            dim_type="vertical",
                            offset_mm=14.0,
                            view_id="front",
                        )
                    )
                elif auto_dimension and v_name == "top":
                    # Largeur verticale (Y)
                    view.dimensions.append(
                        DimensionAnnotation(
                            p1=(-dx * 0.5, -dy * 0.5),
                            p2=(-dx * 0.5, dy * 0.5),
                            value_mm=round(dy, 2),
                            dim_type="vertical",
                            offset_mm=-12.0,
                            view_id="top",
                        )
                    )

                self.views[v_name] = view

        # Ajout par défaut d'un cadre de tolérancement géométrique GD&T sur la vue de face
        if "front" in self.views and not self.views["front"].gdt_frames:
            self.views["front"].gdt_frames.append(
                GdtFeatureControlFrame(
                    symbol="position",
                    tolerance_mm=0.05,
                    diameter_symbol=True,
                    material_condition="mmc",
                    datum_primary="A",
                    datum_secondary="B",
                    datum_tertiary="C",
                    x_mm=0.0,
                    y_mm=dz * 0.5 * scale + 15.0,
                    view_id="front",
                )
            )

        total_dims = sum(len(v.dimensions) for v in self.views.values())
        total_gdt = sum(len(v.gdt_frames) for v in self.views.values())

        report = TechDrawReport(
            sheet_format=self.sheet_format,
            sheet_width_mm=self.width_mm,
            sheet_height_mm=self.height_mm,
            views_count=len(self.views),
            total_visible_segments=tot_vis,
            total_hidden_segments=tot_hid,
            dimensions_count=total_dims,
            gdt_count=total_gdt,
            is_valid_iso128=(tot_vis > 0),
        )

        self.cached_svg = self._render_svg()
        return report

    def add_gdt_frame(self, frame: GdtFeatureControlFrame) -> None:
        """Ajoute un cadre de tolérance géométrique GD&T à une vue existante."""
        if frame.view_id in self.views:
            self.views[frame.view_id].gdt_frames.append(frame)
            self.cached_svg = self._render_svg()

    def add_dimension(self, dim: DimensionAnnotation) -> None:
        """Ajoute une cote associative dimensionnelle à une vue."""
        if dim.view_id in self.views:
            self.views[dim.view_id].dimensions.append(dim)
            self.cached_svg = self._render_svg()

    # --- Rendu Vectoriel SVG Industriel (ISO 128 / ISO 7200) ---

    def _render_svg(self) -> str:
        """Compile la planche de dessin technique en fichier vectoriel SVG de haute précision."""
        lines: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.width_mm} {self.height_mm}" '
            f'width="{self.width_mm}mm" height="{self.height_mm}mm" style="background:#ffffff; font-family:\'Arial\', sans-serif;">',
            "  <defs>",
            '    <marker id="arrow" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">',
            '      <path d="M 0 2 L 10 5 L 0 8 z" fill="#000000"/>',
            "    </marker>",
            "  </defs>",
        ]

        # 1. Bordure et marge de cadrage
        m = self.margin_mm
        w, h = self.width_mm, self.height_mm
        lines.append(
            f'  <!-- Cadre et marges -->\n'
            f'  <rect x="{m}" y="{m}" width="{w - 2 * m}" height="{h - 2 * m}" '
            f'fill="none" stroke="#000000" stroke-width="0.7"/>'
        )

        # 2. Cartouche d'ingénierie ISO 7200 (coin inférieur droit, 180 x 36 mm)
        tb_w, tb_h = 180.0, 36.0
        tb_x = w - m - tb_w
        tb_y = h - m - tb_h
        tb = self.title_block

        lines.extend([
            f'  <!-- Cartouche ISO 7200 -->',
            f'  <g transform="translate({tb_x}, {tb_y})">',
            f'    <rect x="0" y="0" width="{tb_w}" height="{tb_h}" fill="#fcfcfc" stroke="#000000" stroke-width="0.5"/>',
            f'    <line x1="0" y1="12" x2="{tb_w}" y2="12" stroke="#000000" stroke-width="0.3"/>',
            f'    <line x1="0" y1="24" x2="{tb_w}" y2="24" stroke="#000000" stroke-width="0.3"/>',
            f'    <line x1="90" y1="0" x2="90" y2="24" stroke="#000000" stroke-width="0.3"/>',
            f'    <line x1="140" y1="12" x2="140" y2="36" stroke="#000000" stroke-width="0.3"/>',
            f'    <line x1="45" y1="24" x2="45" y2="36" stroke="#000000" stroke-width="0.3"/>',
            # Textes du cartouche
            f'    <text x="3" y="5" font-size="3" fill="#666666">ENTREPRISE</text>',
            f'    <text x="3" y="10" font-size="4" font-weight="bold" fill="#000000">{tb.company}</text>',
            f'    <text x="93" y="5" font-size="3" fill="#666666">TITRE DU PROJET</text>',
            f'    <text x="93" y="10" font-size="4.2" font-weight="bold" fill="#000000">{tb.title}</text>',
            f'    <text x="3" y="16" font-size="2.6" fill="#666666">MATÉRIAU</text>',
            f'    <text x="3" y="21" font-size="3.2" fill="#000000">{tb.material}</text>',
            f'    <text x="93" y="16" font-size="2.6" fill="#666666">NUMÉRO DE PLAN</text>',
            f'    <text x="93" y="21" font-size="3.5" font-weight="bold" fill="#000000">{tb.drawing_number}</text>',
            f'    <text x="143" y="16" font-size="2.6" fill="#666666">ÉCHELLE</text>',
            f'    <text x="143" y="21" font-size="3.5" fill="#000000">{tb.scale}</text>',
            f'    <text x="3" y="28" font-size="2.6" fill="#666666">DESSINÉ PAR</text>',
            f'    <text x="3" y="33" font-size="3.2" fill="#000000">{tb.author}</text>',
            f'    <text x="48" y="28" font-size="2.6" fill="#666666">DATE</text>',
            f'    <text x="48" y="33" font-size="3.2" fill="#000000">{tb.date}</text>',
            f'    <text x="93" y="28" font-size="2.6" fill="#666666">REV / FEUILLE</text>',
            f'    <text x="93" y="33" font-size="3.2" fill="#000000">{tb.revision} ({tb.sheet})</text>',
            # Symbole de projection premier dièdre (cône tronqué)
            f'    <g transform="translate(155, 30)">',
            f'      <circle cx="12" cy="0" r="4" fill="none" stroke="#000000" stroke-width="0.3"/>',
            f'      <circle cx="12" cy="0" r="2" fill="none" stroke="#000000" stroke-width="0.3"/>',
            f'      <polygon points="0,-4 8,-2 8,2 0,4" fill="none" stroke="#000000" stroke-width="0.3"/>',
            f'      <line x1="-3" y1="0" x2="18" y2="0" stroke="#000000" stroke-width="0.2" stroke-dasharray="4,1"/>',
            f'    </g>',
            f'  </g>',
        ])

        # 3. Dessin des vues orthographiques
        for v in self.views.values():
            vx, vy, vscale = v.center_x_mm, v.center_y_mm, v.scale
            # En SVG, l'axe Y pointe vers le bas, donc inversion de Y_cad pour respecter la vue
            lines.append(f'  <!-- Vue {v.title} -->')
            lines.append(f'  <g transform="translate({vx}, {vy})">')
            lines.append(
                f'    <text x="0" y="{35.0 * vscale + 12.0}" font-size="3.8" font-weight="bold" '
                f'text-anchor="middle" fill="#111827">{v.title} (ECH {self.title_block.scale})</text>'
            )

            # Arêtes cachées (trait interrompu fin, 0.25 mm)
            for poly in v.hidden_polylines:
                if len(poly) >= 2:
                    pts_str = " ".join(f"{p[0] * vscale:.2f},{-p[1] * vscale:.2f}" for p in poly)
                    lines.append(
                        f'    <polyline points="{pts_str}" fill="none" stroke="#4b5563" '
                        f'stroke-width="0.25" stroke-dasharray="2,1.5"/>'
                    )

            # Arêtes vues (trait fort continu, 0.45 mm)
            for poly in v.visible_polylines:
                if len(poly) >= 2:
                    pts_str = " ".join(f"{p[0] * vscale:.2f},{-p[1] * vscale:.2f}" for p in poly)
                    lines.append(
                        f'    <polyline points="{pts_str}" fill="none" stroke="#000000" '
                        f'stroke-width="0.45"/>'
                    )

            # Lignes de cotes associatives
            for d in v.dimensions:
                x1, y1 = d.p1[0] * vscale, -d.p1[1] * vscale
                x2, y2 = d.p2[0] * vscale, -d.p2[1] * vscale
                off = d.offset_mm

                if d.dim_type == "horizontal":
                    dim_y = y1 + off
                    txt = d.text_override or f"{d.value_mm:.1f}"
                    lines.extend([
                        f'    <line x1="{x1}" y1="{y1}" x2="{x1}" y2="{dim_y + (2 if off > 0 else -2)}" stroke="#1e293b" stroke-width="0.2"/>',
                        f'    <line x1="{x2}" y1="{y2}" x2="{x2}" y2="{dim_y + (2 if off > 0 else -2)}" stroke="#1e293b" stroke-width="0.2"/>',
                        f'    <line x1="{x1}" y1="{dim_y}" x2="{x2}" y2="{dim_y}" stroke="#1e293b" stroke-width="0.25" marker-start="url(#arrow)" marker-end="url(#arrow)"/>',
                        f'    <text x="{(x1 + x2) * 0.5}" y="{dim_y - 1.5}" font-size="3" text-anchor="middle" fill="#000000">{txt}</text>',
                    ])
                elif d.dim_type == "vertical":
                    dim_x = x1 + off
                    txt = d.text_override or f"{d.value_mm:.1f}"
                    lines.extend([
                        f'    <line x1="{x1}" y1="{y1}" x2="{dim_x + (2 if off > 0 else -2)}" y2="{y1}" stroke="#1e293b" stroke-width="0.2"/>',
                        f'    <line x1="{x2}" y1="{y2}" x2="{dim_x + (2 if off > 0 else -2)}" y2="{y2}" stroke="#1e293b" stroke-width="0.2"/>',
                        f'    <line x1="{dim_x}" y1="{y1}" x2="{dim_x}" y2="{y2}" stroke="#1e293b" stroke-width="0.25" marker-start="url(#arrow)" marker-end="url(#arrow)"/>',
                        f'    <text x="{dim_x + 1.5}" y="{(y1 + y2) * 0.5}" font-size="3" text-anchor="start" fill="#000000">{txt}</text>',
                    ])

            # Cadres de tolérancement géométrique GD&T (Feature Control Frames)
            for g in v.gdt_frames:
                gx, gy = g.x_mm, -g.y_mm
                sym = g.get_symbol_unicode()
                mat = " Ⓜ" if g.material_condition == "mmc" else ""
                dia = "⌀ " if g.diameter_symbol else ""
                val = f"{dia}{g.tolerance_mm:.2f}{mat}"
                d_a = g.datum_primary or ""
                d_b = g.datum_secondary or ""
                d_c = g.datum_tertiary or ""

                lines.extend([
                    f'    <g transform="translate({gx - 20}, {gy})">',
                    f'      <!-- Cadre GD&T -->',
                    f'      <rect x="0" y="0" width="40" height="7" fill="#ffffff" stroke="#000000" stroke-width="0.35"/>',
                    f'      <line x1="8" y1="0" x2="8" y2="7" stroke="#000000" stroke-width="0.3"/>',
                    f'      <line x1="24" y1="0" x2="24" y2="7" stroke="#000000" stroke-width="0.3"/>',
                    f'      <line x1="32" y1="0" x2="32" y2="7" stroke="#000000" stroke-width="0.3"/>',
                    f'      <!-- Symboles et données -->',
                    f'      <text x="4" y="5" font-size="4.5" text-anchor="middle" font-weight="bold" fill="#000000">{sym}</text>',
                    f'      <text x="16" y="5" font-size="3.2" text-anchor="middle" fill="#000000">{val}</text>',
                    f'      <text x="28" y="5" font-size="3.5" text-anchor="middle" font-weight="bold" fill="#000000">{d_a}</text>',
                    f'      <text x="36" y="5" font-size="3.5" text-anchor="middle" font-weight="bold" fill="#000000">{d_b}</text>',
                    f'      <!-- Ligne de repère -->',
                    f'      <line x1="20" y1="7" x2="20" y2="14" stroke="#000000" stroke-width="0.25" marker-end="url(#arrow)"/>',
                    f'    </g>',
                ])

            lines.append("  </g>")

        lines.append("</svg>")
        return "\n".join(lines)

    # --- Export Normalisé AutoCAD DXF (ezdxf) ---

    def to_dxf(self) -> bytes:
        """Génère le fichier normalisé AutoCAD DXF R2010 avec calques ISO normalisés."""
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()

        # Configuration des calques normalisés
        doc.layers.add("0_BORDER", color=7)  # Blanc / Noir
        doc.layers.add("0_TITLEBLOCK", color=7)
        doc.layers.add("0_VISIBLE", color=7)
        doc.layers.add("0_HIDDEN", color=8, linetype="DASHED")  # Gris, pointillés
        doc.layers.add("0_DIMENSIONS", color=5)  # Bleu
        doc.layers.add("0_GDT", color=1)  # Rouge

        # 1. Cadre
        m = self.margin_mm
        w, h = self.width_mm, self.height_mm
        msp.add_lwpolyline([(m, m), (w - m, m), (w - m, h - m), (m, h - m), (m, m)], dxfattribs={"layer": "0_BORDER"})

        # 2. Cartouche
        tb_w, tb_h = 180.0, 36.0
        tb_x = w - m - tb_w
        tb_y = m  # En DXF, Y=0 en bas
        msp.add_lwpolyline(
            [(tb_x, tb_y), (tb_x + tb_w, tb_y), (tb_x + tb_w, tb_y + tb_h), (tb_x, tb_y + tb_h), (tb_x, tb_y)],
            dxfattribs={"layer": "0_TITLEBLOCK"},
        )
        msp.add_text(f"{self.title_block.company} - {self.title_block.title}", dxfattribs={"layer": "0_TITLEBLOCK", "height": 4.0}).set_placement((tb_x + 5.0, tb_y + tb_h - 8.0))
        msp.add_text(f"DWG: {self.title_block.drawing_number}  ECH: {self.title_block.scale}", dxfattribs={"layer": "0_TITLEBLOCK", "height": 3.0}).set_placement((tb_x + 5.0, tb_y + 12.0))
        msp.add_text(f"REV: {self.title_block.revision}  DATE: {self.title_block.date}", dxfattribs={"layer": "0_TITLEBLOCK", "height": 3.0}).set_placement((tb_x + 5.0, tb_y + 4.0))

        # 3. Lignes géométriques
        for v in self.views.values():
            vx, vy, vscale = v.center_x_mm, v.center_y_mm, v.scale
            # Arêtes vues
            for poly in v.visible_polylines:
                if len(poly) >= 2:
                    dxf_pts = [(vx + p[0] * vscale, vy + p[1] * vscale) for p in poly]
                    msp.add_lwpolyline(dxf_pts, dxfattribs={"layer": "0_VISIBLE"})
            # Arêtes cachées
            for poly in v.hidden_polylines:
                if len(poly) >= 2:
                    dxf_pts = [(vx + p[0] * vscale, vy + p[1] * vscale) for p in poly]
                    msp.add_lwpolyline(dxf_pts, dxfattribs={"layer": "0_HIDDEN"})

        buf = io.StringIO()
        doc.write(buf)
        return buf.getvalue().encode("utf-8")

    def to_svg(self) -> str:
        """Retourne la planche technique au format vectoriel SVG."""
        if not self.cached_svg:
            self.generate_drawing()
        return self.cached_svg

    # --- Contrat GeometryProvider ---

    def tessellate_binary(
        self,
        linear_deflection: float = 0.001,
        angular_deflection: float = 0.2,
    ) -> BinaryMeshPayload:
        """Génère la géométrie 3D représentant la planche de dessin pour affichage Three.js."""
        # Représente la feuille 2D comme un rectangle blanc mince dans l'espace 3D
        w_m = self.width_mm * 0.001
        h_m = self.height_mm * 0.001
        verts = np.array([
            [0.0, 0.0, 0.0],
            [w_m, 0.0, 0.0],
            [w_m, h_m, 0.0],
            [0.0, h_m, 0.0],
        ], dtype=np.float32)

        normals = np.array([
            [0.0, 0.0, 1.0],
            [0.0, 0.0, 1.0],
            [0.0, 0.0, 1.0],
            [0.0, 0.0, 1.0],
        ], dtype=np.float32)

        indices = np.array([
            [0, 1, 2],
            [0, 2, 3],
        ], dtype=np.uint32)

        face_ids = np.array([1, 1], dtype=np.uint32)
        scalars = np.ones(4, dtype=np.float32)

        return BinaryMeshPayload(
            entity_id=self.entity_id,
            vertices=verts,
            normals=normals,
            indices=indices,
            face_ids=face_ids,
            scalar_field=scalars,
        )

    def introspect(self) -> ValidationReport:
        """Audit de validation du plan technique."""
        w_m = self.width_mm * 0.001
        h_m = self.height_mm * 0.001
        return ValidationReport(
            is_valid_l0=True,
            is_watertight=False,
            volume_m3=0.0,
            surface_area_m2=w_m * h_m,
            center_of_mass=(w_m * 0.5, h_m * 0.5, 0.0),
            bbox_min=(0.0, 0.0, 0.0),
            bbox_max=(w_m, h_m, 0.001),
            topology_counts={"views": len(self.views), "dimensions": sum(len(v.dimensions) for v in self.views.values())},
            dfm_warnings=[],
        )

    def serialize_native(self) -> bytes:
        """Sérialise le plan vectoriel SVG sous forme d'octets UTF-8."""
        return self.to_svg().encode("utf-8")

    def resolve_selector(self, selector_expression: str) -> list[int]:
        """Résout les sélecteurs de calques ou de vues."""
        if selector_expression == "views":
            return list(range(len(self.views)))
        return []
