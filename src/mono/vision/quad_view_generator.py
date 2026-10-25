from __future__ import annotations

import io
import math
from pathlib import Path
from dataclasses import dataclass
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import build123d as bd


@dataclass(frozen=True)
class QuadViewResult:
    """Résultat de génération de la planche composite 4 vues orthographiques/isométrique."""

    image_bytes: bytes
    file_path: str | None
    bbox_dims: tuple[float, float, float]
    volume_mm3: float


class QuadViewGenerator:
    """Générateur de planches composites 4 vues orthographiques cotées (1024x1024).

    Conçu pour alimenter le Juge VLM (*Vision-Language Model*) selon le standard
    CADEngBench / CADArena. Génère un cadran 2x2 :
    - Cadran 1 (Haut-Gauche) : Vue de dessus (Top, plan XY)
    - Cadran 2 (Haut-Droite) : Vue isométrique (3D avec profondeur et surlignage)
    - Cadran 3 (Bas-Gauche) : Vue de face cotée (Front, plan XZ avec cotes automatiques)
    - Cadran 4 (Bas-Droite) : Vue de droite (Right, plan YZ)
    """

    def __init__(self, size: int = 1024) -> None:
        """Initialise le générateur de planches.

        Args:
            size: Dimension carrée de la planche finale en pixels (1024x1024 par défaut).
        """
        self.size: int = size
        self.half_size: int = size // 2

        # Palette graphique d'ingénierie sombre (High-Contrast CAD Dark Theme)
        self.bg_color = (18, 21, 28, 255)  # #12151c
        self.grid_color = (35, 42, 54, 255)
        self.border_color = (55, 65, 81, 255)
        self.edge_color = (240, 243, 246, 255)
        self.iso_edge_color = (0, 210, 255, 255)  # Cyan #00d2ff
        self.dim_color = (255, 179, 71, 255)  # Ambre pour les cotes
        self.text_color = (220, 226, 235, 255)
        self.axis_x_color = (255, 82, 82, 255)  # Rouge
        self.axis_y_color = (76, 175, 80, 255)  # Vert
        self.axis_z_color = (68, 138, 255, 255)  # Bleu

    def generate(
        self,
        part: bd.Part | bd.Solid | bd.Compound,
        output_path: str | None = None,
        title: str = "MONO CADEngBench Inspection",
    ) -> QuadViewResult:
        """Génère la planche composite 4 vues 1024x1024 au format PNG.

        Args:
            part: Solide géométrique exact build123d.
            output_path: Chemin d'enregistrement optionnel.
            title: Libellé principal affiché dans le cartouche d'en-tête.

        Returns:
            QuadViewResult: Flux binaire PNG, dimensions et métadonnées.
        """
        img = Image.new("RGBA", (self.size, self.size), self.bg_color)
        draw = ImageDraw.Draw(img)

        # Extraction des caractéristiques géométriques
        bbox = part.bounding_box()
        dx = float(bbox.size.X)
        dy = float(bbox.size.Y)
        dz = float(bbox.size.Z)
        max_dim = max(dx, dy, dz, 1e-3)
        vol_mm3 = float(part.volume)

        # Extraction de tous les segments d'arêtes du solide
        edges_segments = self._extract_edge_segments(part)

        # Rendu des 4 cadrans
        # Cadran 1 : Haut-Gauche -> Top View (XY)
        self._render_view(
            draw=draw,
            segments=edges_segments,
            origin_x=0,
            origin_y=0,
            view_type="TOP (Plan XY)",
            proj_fn=lambda p: (p[0], -p[1]),  # X vers droite, Y vers haut
            max_dim=max_dim,
            center=(float(part.center().X), float(part.center().Y)),
            dim_label=f"W={dx:.1f} mm, D={dy:.1f} mm",
        )

        # Cadran 2 : Haut-Droite -> Isometric View (3D)
        self._render_view(
            draw=draw,
            segments=edges_segments,
            origin_x=self.half_size,
            origin_y=0,
            view_type="ISOMETRIC (3D)",
            proj_fn=lambda p: (
                (p[0] - p[1]) * math.cos(math.radians(30)),
                -p[2] + (p[0] + p[1]) * math.sin(math.radians(30)) * 0.5,
            ),
            max_dim=max_dim * 1.3,
            center=(0.0, 0.0),
            is_isometric=True,
            dim_label="Axonométrique 30°",
        )

        # Cadran 3 : Bas-Gauche -> Front View (XZ)
        self._render_view(
            draw=draw,
            segments=edges_segments,
            origin_x=0,
            origin_y=self.half_size,
            view_type="FRONT (Plan XZ)",
            proj_fn=lambda p: (p[0], -p[2]),  # X vers droite, Z vers haut
            max_dim=max_dim,
            center=(float(part.center().X), float(part.center().Z)),
            dim_label=f"W={dx:.1f} mm, H={dz:.1f} mm",
            draw_dimensions=True,
            dim_val_x=dx,
            dim_val_y=dz,
        )

        # Cadran 4 : Bas-Droite -> Right View (YZ)
        self._render_view(
            draw=draw,
            segments=edges_segments,
            origin_x=self.half_size,
            origin_y=self.half_size,
            view_type="RIGHT (Plan YZ)",
            proj_fn=lambda p: (p[1], -p[2]),  # Y vers droite, Z vers haut
            max_dim=max_dim,
            center=(float(part.center().Y), float(part.center().Z)),
            dim_label=f"D={dy:.1f} mm, H={dz:.1f} mm",
        )

        # Lignes de séparation de cadrans et cartouche central
        draw.line([(self.half_size, 0), (self.half_size, self.size)], fill=self.border_color, width=2)
        draw.line([(0, self.half_size), (self.size, self.half_size)], fill=self.border_color, width=2)

        # Bandeau de titre supérieur
        banner_text = f"{title} | Dimensions: {dx:.1f}x{dy:.1f}x{dz:.1f} mm | Vol: {vol_mm3:.1f} mm³ | Faces: {len(part.faces())}"
        draw.rectangle([(0, 0), (self.size, 26)], fill=(12, 14, 18, 220))
        draw.text((12, 6), banner_text, fill=self.text_color)

        # Encodage en flux d'octets PNG
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        if output_path:
            out_file = Path(output_path)
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_bytes(png_bytes)

        return QuadViewResult(
            image_bytes=png_bytes,
            file_path=output_path,
            bbox_dims=(dx, dy, dz),
            volume_mm3=vol_mm3,
        )

    def _extract_edge_segments(
        self,
        part: bd.Part | bd.Solid | bd.Compound,
        samples_per_edge: int = 12,
    ) -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
        """Discrétise les arêtes 3D exactes en segments de droite pour projection rapide."""
        segments: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []

        for edge in part.edges():
            try:
                # Échantillonnage le long de l'arête
                curve = edge.wrapped
                # Utilisation de positions discrètes via build123d
                pts = [edge.position_at(t) for t in np.linspace(0.0, 1.0, samples_per_edge)]
                for i in range(len(pts) - 1):
                    p1 = (float(pts[i].X), float(pts[i].Y), float(pts[i].Z))
                    p2 = (float(pts[i + 1].X), float(pts[i + 1].Y), float(pts[i + 1].Z))
                    segments.append((p1, p2))
            except Exception:
                continue

        return segments

    def _render_view(
        self,
        draw: ImageDraw.ImageDraw,
        segments: list[tuple[tuple[float, float, float], tuple[float, float, float]]],
        origin_x: int,
        origin_y: int,
        view_type: str,
        proj_fn: callable,
        max_dim: float,
        center: tuple[float, float],
        is_isometric: bool = False,
        dim_label: str = "",
        draw_dimensions: bool = False,
        dim_val_x: float = 0.0,
        dim_val_y: float = 0.0,
    ) -> None:
        """Projette et dessine un cadran avec grille, axes et arêtes géométriques."""
        vw = self.half_size
        vh = self.half_size
        cx = origin_x + vw // 2
        cy = origin_y + vh // 2

        # 1. Grille millimétrique graduée
        grid_step = 40
        for gx in range(origin_x + grid_step, origin_x + vw, grid_step):
            draw.line([(gx, origin_y + 30), (gx, origin_y + vh)], fill=self.grid_color, width=1)
        for gy in range(origin_y + 30 + grid_step, origin_y + vh, grid_step):
            draw.line([(origin_x, gy), (origin_x + vw, gy)], fill=self.grid_color, width=1)

        # 2. Facteur d'échelle pour faire tenir la pièce à 70% du cadran
        margin = 75.0
        usable_size = (vw - 2.0 * margin)
        scale = usable_size / max(max_dim, 1e-4)

        # 3. Projection et tracé des arêtes
        line_color = self.iso_edge_color if is_isometric else self.edge_color
        line_width = 2 if is_isometric else 1

        for p1, p2 in segments:
            u1, v1 = proj_fn(p1)
            u2, v2 = proj_fn(p2)

            sx1 = cx + (u1 - center[0]) * scale
            sy1 = cy + (v1 - center[1]) * scale
            sx2 = cx + (u2 - center[0]) * scale
            sy2 = cy + (v2 - center[1]) * scale

            draw.line([(sx1, sy1), (sx2, sy2)], fill=line_color, width=line_width)

        # 4. Tracé des cotes dimensionnelles pour la vue de face
        if draw_dimensions and dim_val_x > 0 and dim_val_y > 0:
            # Cote horizontale (bas)
            y_dim = origin_y + vh - 25
            x1 = cx - (dim_val_x / 2.0) * scale
            x2 = cx + (dim_val_x / 2.0) * scale
            draw.line([(x1, y_dim), (x2, y_dim)], fill=self.dim_color, width=2)
            draw.line([(x1, y_dim - 5), (x1, y_dim + 5)], fill=self.dim_color, width=2)
            draw.line([(x2, y_dim - 5), (x2, y_dim + 5)], fill=self.dim_color, width=2)
            draw.text((cx - 20, y_dim - 18), f"{dim_val_x:.1f} mm", fill=self.dim_color)

            # Cote verticale (gauche)
            x_dim = origin_x + 25
            y1 = cy - (dim_val_y / 2.0) * scale
            y2 = cy + (dim_val_y / 2.0) * scale
            draw.line([(x_dim, y1), (x_dim, y2)], fill=self.dim_color, width=2)
            draw.line([(x_dim - 5, y1), (x_dim + 5, y1)], fill=self.dim_color, width=2)
            draw.line([(x_dim - 5, y2), (x_dim + 5, y2)], fill=self.dim_color, width=2)
            draw.text((x_dim + 6, cy - 8), f"{dim_val_y:.1f} mm", fill=self.dim_color)

        # 5. Titre du cadran et étiquette de cote
        draw.rectangle([(origin_x + 8, origin_y + 32), (origin_x + 200, origin_y + 54)], fill=(24, 28, 38, 200))
        draw.text((origin_x + 12, origin_y + 35), view_type, fill=self.text_color)
        if dim_label:
            draw.text((origin_x + 12, origin_y + 58), dim_label, fill=self.dim_color)
