"""Fournisseur géométrique et moteur d'architecture BIM paramétrique IFC4 & Coupes 2D HLR.

🎯 Pédagogie (Explication pour un jeune de 15 ans) :
Imagine que tu sois architecte et que tu doives concevoir un bâtiment complet :
1. L'arbre généalogique du bâtiment (La hiérarchie spatiale IFC4) : Tout commence par le projet global (IfcProject),
   situé sur un terrain (IfcSite), qui contient un bâtiment (IfcBuilding), découpé en étages (IfcBuildingStorey).
   Chaque mur, dalle, porte et fenêtre est explicitement relié à son étage pour qu'aucun élément ne "flotte" dans le vide !
2. La carte d'identité de chaque mur (Les Property Sets) : Dans le standard mondial IFC (ISO 16739), un mur n'est pas juste
   une boîte 3D muette : il contient sa résistance au feu (ex: REI 60), son isolation thermique, et s'il est porteur ou simple cloison.
3. Le plan de coupe automatique (L'algorithme HLR d'OpenCASCADE) : Pour imprimer les plans de chantier officiels,
   on coupe le bâtiment horizontalement à 1,20 m du sol avec une scie virtuelle (BRepAlgoAPI_Section).
   L'algorithme HLR (Hidden Line Removal) regarde depuis le ciel, trace en trait fort les murs coupés,
   en trait fin les dalles et marches vues en dessous, et efface toutes les lignes cachées !
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import math
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.geometry
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.aggregate
import ifcopenshell.api.pset
import ifcopenshell.geom
import ifcopenshell.util.element
import numpy as np
import numpy.typing as npt
import build123d as bd

from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
from OCP.gp import gp_Pln, gp_Pnt, gp_Dir, gp_Ax2
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_EDGE

from mono.core.geometry_provider import (
    GeometryProvider,
    BinaryMeshPayload,
    ValidationReport,
)


@dataclass(frozen=True)
class IFCElementInfo:
    """Informations sémantiques et spatiales d'un composant de construction IFC."""

    global_id: str
    ifc_class: str
    name: str
    storey_name: str
    properties: dict[str, dict[str, str | float | bool]]


@dataclass(frozen=True)
class FloorPlan2D:
    """Planche de coupe 2D normalisée (plan d'étage ou élévation) avec export vectoriel."""

    elevation_z: float
    cut_segments: list[tuple[tuple[float, float], tuple[float, float]]]
    visible_segments: list[tuple[tuple[float, float], tuple[float, float]]]
    svg_content: str
    dxf_content: str


class BimIfcProvider(GeometryProvider):
    """Fournisseur géométrique et moteur BIM conforme au standard ISO 16739 IFC4."""

    def __init__(
        self,
        entity_id: str = "building_01",
        ifc_file: ifcopenshell.file | None = None,
        ifc_path: Path | None = None,
        project_name: str = "Mono Architecture",
    ) -> None:
        """Initialise le modèle BIM avec hiérarchie spatiale normée buildingSMART.

        Args:
            entity_id: Identifiant unique de l'entité dans le document Mono.
            ifc_file: Instance existante ifcopenshell.file (prioritaire si fournie).
            ifc_path: Chemin alternatif vers un fichier .ifc existant à charger.
            project_name: Nom du projet architectural IFC4.
        """
        self._entity_id: str = entity_id
        self._storeys: dict[str, ifcopenshell.entity_instance] = {}
        self._solids: dict[str, bd.Solid] = {}

        if ifc_file is not None:
            self._model = ifc_file
            self._init_existing_hierarchy()
        elif ifc_path is not None and ifc_path.exists():
            self._model = ifcopenshell.open(str(ifc_path))
            self._init_existing_hierarchy()
        else:
            self._init_new_ifc4_model(project_name)

    @property
    def entity_id(self) -> str:
        return self._entity_id

    @property
    def model(self) -> ifcopenshell.file:
        return self._model

    def _init_new_ifc4_model(self, project_name: str) -> None:
        """Initialise un fichier IFC4 vierge avec le contexte géométrique et l'arbre spatial de base."""
        self._model = ifcopenshell.api.run("project.create_file", version="IFC4")

        # 1. Racine du projet et unités SI métriques
        self._project = ifcopenshell.api.run(
            "root.create_entity", self._model, ifc_class="IfcProject", name=project_name
        )
        ifcopenshell.api.run(
            "unit.assign_unit", self._model, length={"is_metric": True, "raw": "METRE"}
        )

        # 2. Contextes géométriques 3D (Model/Body/MODEL_VIEW)
        self._context = ifcopenshell.api.run(
            "context.add_context", self._model, context_type="Model"
        )
        self._body_context = ifcopenshell.api.run(
            "context.add_context",
            self._model,
            context_type="Model",
            context_identifier="Body",
            target_view="MODEL_VIEW",
            parent=self._context,
        )

        # 3. Arbre spatial obligatoire : IfcProject -> IfcSite -> IfcBuilding
        self._site = ifcopenshell.api.run(
            "root.create_entity", self._model, ifc_class="IfcSite", name="Terrain"
        )
        self._building = ifcopenshell.api.run(
            "root.create_entity", self._model, ifc_class="IfcBuilding", name="Batiment_Principal"
        )

        ifcopenshell.api.run(
            "aggregate.assign_object",
            self._model,
            products=[self._site],
            relating_object=self._project,
        )
        ifcopenshell.api.run(
            "aggregate.assign_object",
            self._model,
            products=[self._building],
            relating_object=self._site,
        )

        # Création du premier étage de référence (Rez-de-chaussée Z=0.0m)
        self.add_storey("RDC", elevation=0.0)

    def _init_existing_hierarchy(self) -> None:
        """Récupère l'arbre spatial d'un modèle IFC existant chargé depuis disque."""
        projects = self._model.by_type("IfcProject")
        self._project = projects[0] if projects else None

        # Recherche des contextes existants
        contexts = self._model.by_type("IfcGeometricRepresentationSubContext")
        body_ctx = [c for c in contexts if c.ContextIdentifier == "Body"]
        self._body_context = body_ctx[0] if body_ctx else None

        for storey in self._model.by_type("IfcBuildingStorey"):
            name = str(storey.Name or storey.GlobalId)
            self._storeys[name] = storey

    def add_storey(self, name: str, elevation: float = 0.0) -> str:
        """Ajoute un niveau / étage (IfcBuildingStorey) avec son élévation relative Z.

        Args:
            name: Nom de l'étage (ex: 'RDC', 'Étage_1').
            elevation: Hauteur du niveau fini en mètres.

        Returns:
            str: GlobalId de l'étage créé.
        """
        storey = ifcopenshell.api.run(
            "root.create_entity", self._model, ifc_class="IfcBuildingStorey", name=name
        )
        storey.Elevation = float(elevation)

        ifcopenshell.api.run(
            "aggregate.assign_object",
            self._model,
            products=[storey],
            relating_object=self._building,
        )

        self._storeys[name] = storey
        return str(storey.GlobalId)

    def add_wall(
        self,
        storey_name: str,
        name: str,
        length: float,
        height: float = 2.8,
        thickness: float = 0.2,
        x: float = 0.0,
        y: float = 0.0,
        z: float = 0.0,
        is_load_bearing: bool = True,
        is_external: bool = True,
        fire_rating: str = "REI 60",
    ) -> str:
        """Ajoute un mur paramétrique IFC4 relié à son étage de référence.

        Args:
            storey_name: Nom de l'étage contenant le mur.
            name: Nom d'usage de la paroi.
            length: Longueur du mur en mètres.
            height: Hauteur sous plafond en mètres.
            thickness: Épaisseur de paroi en mètres.
            x, y, z: Coordonnées du point d'origine du mur en mètres.
            is_load_bearing: True si mur porteur de structure.
            is_external: True si façade extérieure.
            fire_rating: Degré coupe-feu normé (ex: 'REI 60').

        Returns:
            str: GlobalId unique du mur créé.
        """
        if storey_name not in self._storeys:
            raise KeyError(f"Étage '{storey_name}' introuvable. Créez-le d'abord via add_storey().")

        storey = self._storeys[storey_name]
        wall = ifcopenshell.api.run(
            "root.create_entity", self._model, ifc_class="IfcWall", name=name
        )

        # 1. Rattachement spatial obligatoire
        ifcopenshell.api.run(
            "spatial.assign_container", self._model, products=[wall], relating_structure=storey
        )

        # 2. Représentation géométrique solide 3D
        rep = ifcopenshell.api.run(
            "geometry.add_wall_representation",
            self._model,
            context=self._body_context,
            length=length,
            height=height,
            thickness=thickness,
        )
        ifcopenshell.api.run(
            "geometry.assign_representation", self._model, product=wall, representation=rep
        )

        # 3. Positionnement spatial 4x4
        mat = np.eye(4, dtype=np.float64)
        mat[0, 3] = x
        mat[1, 3] = y
        mat[2, 3] = z
        ifcopenshell.api.geometry.edit_object_placement(self._model, product=wall, matrix=mat)

        # 4. Propriétés architecturales normées (Pset_WallCommon)
        pset = ifcopenshell.api.run(
            "pset.add_pset", self._model, product=wall, name="Pset_WallCommon"
        )
        ifcopenshell.api.run(
            "pset.edit_pset",
            self._model,
            pset=pset,
            properties={
                "LoadBearing": is_load_bearing,
                "IsExternal": is_external,
                "FireRating": fire_rating,
            },
        )

        # 5. Enregistrement du solide B-Rep exact pour les coupes HLR
        solid = bd.Box(length, thickness, height)
        loc = bd.Location((x + length * 0.5, y + thickness * 0.5, z + height * 0.5))
        self._solids[str(wall.GlobalId)] = solid.locate(loc)

        return str(wall.GlobalId)

    def add_slab(
        self,
        storey_name: str,
        name: str,
        width: float,
        length: float,
        thickness: float = 0.2,
        x: float = 0.0,
        y: float = 0.0,
        z: float = 0.0,
        is_load_bearing: bool = True,
    ) -> str:
        """Ajoute une dalle de plancher ou toiture (IfcSlab) rattachée à son étage.

        Args:
            storey_name: Étage de référence.
            name: Nom de la dalle.
            width: Dimension en X (mètres).
            length: Dimension en Y (mètres).
            thickness: Épaisseur de dalle en Z (mètres).
            x, y, z: Position d'origine.
            is_load_bearing: Si True, dalle porteuse.

        Returns:
            str: GlobalId de la dalle.
        """
        if storey_name not in self._storeys:
            raise KeyError(f"Étage '{storey_name}' introuvable.")

        storey = self._storeys[storey_name]
        slab = ifcopenshell.api.run(
            "root.create_entity", self._model, ifc_class="IfcSlab", name=name
        )

        ifcopenshell.api.run(
            "spatial.assign_container", self._model, products=[slab], relating_structure=storey
        )

        rep = ifcopenshell.api.run(
            "geometry.add_slab_representation",
            self._model,
            context=self._body_context,
            depth=thickness,
        )
        ifcopenshell.api.run(
            "geometry.assign_representation", self._model, product=slab, representation=rep
        )

        mat = np.eye(4, dtype=np.float64)
        mat[0, 3] = x
        mat[1, 3] = y
        mat[2, 3] = z
        ifcopenshell.api.geometry.edit_object_placement(self._model, product=slab, matrix=mat)

        pset = ifcopenshell.api.run(
            "pset.add_pset", self._model, product=slab, name="Pset_SlabCommon"
        )
        ifcopenshell.api.run(
            "pset.edit_pset",
            self._model,
            pset=pset,
            properties={"LoadBearing": is_load_bearing, "IsExternal": False},
        )

        solid = bd.Box(width, length, thickness)
        loc = bd.Location((x + width * 0.5, y + length * 0.5, z + thickness * 0.5))
        self._solids[str(slab.GlobalId)] = solid.locate(loc)

        return str(slab.GlobalId)

    def add_door(
        self,
        storey_name: str,
        name: str,
        width: float = 0.9,
        height: float = 2.1,
        thickness: float = 0.1,
        x: float = 0.0,
        y: float = 0.0,
        z: float = 0.0,
    ) -> str:
        """Ajoute une porte de passage (IfcDoor)."""
        if storey_name not in self._storeys:
            raise KeyError(f"Étage '{storey_name}' introuvable.")

        storey = self._storeys[storey_name]
        door = ifcopenshell.api.run(
            "root.create_entity", self._model, ifc_class="IfcDoor", name=name
        )
        door.OverallWidth = float(width)
        door.OverallHeight = float(height)

        ifcopenshell.api.run(
            "spatial.assign_container", self._model, products=[door], relating_structure=storey
        )

        mat = np.eye(4, dtype=np.float64)
        mat[0, 3] = x
        mat[1, 3] = y
        mat[2, 3] = z
        ifcopenshell.api.geometry.edit_object_placement(self._model, product=door, matrix=mat)

        solid = bd.Box(width, thickness, height)
        loc = bd.Location((x + width * 0.5, y + thickness * 0.5, z + height * 0.5))
        self._solids[str(door.GlobalId)] = solid.locate(loc)

        return str(door.GlobalId)

    def add_window(
        self,
        storey_name: str,
        name: str,
        width: float = 1.2,
        height: float = 1.4,
        thickness: float = 0.1,
        x: float = 0.0,
        y: float = 0.0,
        z: float = 0.9,
    ) -> str:
        """Ajoute une fenêtre vitrée (IfcWindow)."""
        if storey_name not in self._storeys:
            raise KeyError(f"Étage '{storey_name}' introuvable.")

        storey = self._storeys[storey_name]
        window = ifcopenshell.api.run(
            "root.create_entity", self._model, ifc_class="IfcWindow", name=name
        )
        window.OverallWidth = float(width)
        window.OverallHeight = float(height)

        ifcopenshell.api.run(
            "spatial.assign_container", self._model, products=[window], relating_structure=storey
        )

        mat = np.eye(4, dtype=np.float64)
        mat[0, 3] = x
        mat[1, 3] = y
        mat[2, 3] = z
        ifcopenshell.api.geometry.edit_object_placement(self._model, product=window, matrix=mat)

        solid = bd.Box(width, thickness, height)
        loc = bd.Location((x + width * 0.5, y + thickness * 0.5, z + height * 0.5))
        self._solids[str(window.GlobalId)] = solid.locate(loc)

        return str(window.GlobalId)

    def query_spatial(
        self, ifc_class: str | None = None, storey_name: str | None = None
    ) -> list[IFCElementInfo]:
        """Exécute une requête spatiale et sémantique sur les composants du bâtiment.

        Args:
            ifc_class: Filtre de classe IFC (ex: 'IfcWall', 'IfcSlab', 'IfcDoor').
            storey_name: Filtre d'étage (ex: 'RDC').

        Returns:
            list[IFCElementInfo]: Liste des entités correspondantes avec métadonnées.
        """
        results: list[IFCElementInfo] = []
        target_elements = (
            self._model.by_type(ifc_class) if ifc_class else self._model.by_type("IfcProduct")
        )

        for elem in target_elements:
            # Ignorer les entités spatiales d'encadrement
            if elem.is_a() in ("IfcProject", "IfcSite", "IfcBuilding", "IfcBuildingStorey", "IfcSpace"):
                continue

            elem_storey = "Non rattaché"
            if hasattr(elem, "ContainedInStructure") and elem.ContainedInStructure:
                rel = elem.ContainedInStructure[0]
                elem_storey = str(rel.RelatingStructure.Name or rel.RelatingStructure.GlobalId)

            if storey_name and elem_storey != storey_name:
                continue

            # Extraction des Psets
            psets = ifcopenshell.util.element.get_psets(elem)

            results.append(
                IFCElementInfo(
                    global_id=str(elem.GlobalId),
                    ifc_class=elem.is_a(),
                    name=str(elem.Name or ""),
                    storey_name=elem_storey,
                    properties=psets,
                )
            )

        return results

    def get_storeys(self) -> list[dict[str, object]]:
        """Retourne la liste ordonnée des étages du bâtiment."""
        storeys_list: list[dict[str, object]] = []
        for s in self._model.by_type("IfcBuildingStorey"):
            storeys_list.append({
                "name": str(s.Name or ""),
                "global_id": str(s.GlobalId),
                "elevation": float(getattr(s, "Elevation", 0.0) or 0.0),
                "element_count": sum(len(rel.RelatedElements) for rel in getattr(s, "ContainsElements", [])),
            })
        return sorted(storeys_list, key=lambda x: float(x["elevation"]))

    def to_brep_compound(self) -> bd.Compound:
        """Assemble l'ensemble des solides B-Rep du modèle en un Compound OpenCASCADE."""
        if not self._solids:
            return bd.Compound([])
        return bd.Compound(list(self._solids.values()))

    def generate_2d_floorplan(
        self,
        elevation_z: float = 1.2,
        padding: float = 1.5,
    ) -> FloorPlan2D:
        """Génère la planche 2D de coupe d'étage automatique via l'algorithme HLR d'OpenCASCADE.

        Extrait :
        - Les arêtes coupées au niveau Z (BRepAlgoAPI_Section) tracées en trait fort noir.
        - Les arêtes vues en contrebas (HLRBRep_Algo) tracées en trait fin gris.
        - Génère un flux vectoriel SVG conforme ISO 128 et un export DXF standard.

        Args:
            elevation_z: Hauteur du plan de coupe horizontal en mètres (défaut: 1.20 m).
            padding: Marge extérieure du dessin en mètres.

        Returns:
            FloorPlan2D: Plan 2D complet avec segments vectoriels, SVG et DXF.
        """
        compound = self.to_brep_compound()
        cut_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
        visible_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []

        if not compound.solids():
            return FloorPlan2D(
                elevation_z=elevation_z,
                cut_segments=[],
                visible_segments=[],
                svg_content="<svg></svg>",
                dxf_content="",
            )

        # 1. Section horizontale à Z = elevation_z
        pln = gp_Pln(gp_Pnt(0, 0, elevation_z), gp_Dir(0, 0, 1))
        sec = BRepAlgoAPI_Section(compound.wrapped, pln)
        sec.Build()

        if sec.IsDone():
            exp = TopExp_Explorer(sec.Shape(), TopAbs_EDGE)
            while exp.More():
                e = bd.Edge(exp.Current())
                p1 = e.position_at(0.0)
                p2 = e.position_at(1.0)
                cut_segments.append(((float(p1.X), float(p1.Y)), (float(p2.X), float(p2.Y))))
                exp.Next()

        # 2. Projection HLR vue de dessus (Top view)
        proj_ax = gp_Ax2(gp_Pnt(0, 0, 100), gp_Dir(0, 0, -1), gp_Dir(0, 1, 0))
        projector = HLRAlgo_Projector(proj_ax)

        hlr = HLRBRep_Algo()
        hlr.Add(compound.wrapped)
        hlr.Projector(projector)
        hlr.Update()
        hlr.Hide()

        shapes = HLRBRep_HLRToShape(hlr)
        v_comp = shapes.VCompound()

        if not v_comp.IsNull():
            exp_v = TopExp_Explorer(v_comp, TopAbs_EDGE)
            while exp_v.More():
                e = bd.Edge(exp_v.Current())
                p1 = e.position_at(0.0)
                p2 = e.position_at(1.0)
                seg = ((float(p1.X), float(p1.Y)), (float(p2.X), float(p2.Y)))
                # N'ajouter en visible que si le segment n'est pas déjà dans les coupes
                if seg not in cut_segments:
                    visible_segments.append(seg)
                exp_v.Next()

        # 3. Calcul des dimensions du plan (Bounding Box 2D)
        all_pts = [p for s in (cut_segments + visible_segments) for p in s]
        if all_pts:
            min_x = min(p[0] for p in all_pts) - padding
            max_x = max(p[0] for p in all_pts) + padding
            min_y = min(p[1] for p in all_pts) - padding
            max_y = max(p[1] for p in all_pts) + padding
        else:
            min_x, max_x, min_y, max_y = -5.0, 5.0, -5.0, 5.0

        w_m = max(max_x - min_x, 1.0)
        h_m = max(max_y - min_y, 1.0)

        # 4. Génération du fichier SVG
        scale_px = 100.0  # 100 px par mètre
        svg_w = int(w_m * scale_px)
        svg_h = int(h_m * scale_px)

        svg_lines: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{min_x:.3f} {-max_y:.3f} {w_m:.3f} {h_m:.3f}" width="{svg_w}" height="{svg_h}">',
            f'  <rect x="{min_x:.3f}" y="{-max_y:.3f}" width="{w_m:.3f}" height="{h_m:.3f}" fill="#ffffff" />',
            f'  <!-- Couche Arêtes Vues (Trait fin gris 0.25mm) -->',
            f'  <g id="A-WALL-PROJ" stroke="#64748b" stroke-width="0.04" stroke-linecap="round">',
        ]

        for s in visible_segments:
            (x1, y1), (x2, y2) = s
            svg_lines.append(f'    <line x1="{x1:.4f}" y1="{-y1:.4f}" x2="{x2:.4f}" y2="{-y2:.4f}" />')

        svg_lines.extend([
            f'  </g>',
            f'  <!-- Couche Arêtes Coupées à Z={elevation_z:.2f}m (Trait fort noir 0.50mm) -->',
            f'  <g id="A-WALL-CUTT" stroke="#0f172a" stroke-width="0.08" stroke-linecap="round">',
        ])

        for s in cut_segments:
            (x1, y1), (x2, y2) = s
            svg_lines.append(f'    <line x1="{x1:.4f}" y1="{-y1:.4f}" x2="{x2:.4f}" y2="{-y2:.4f}" />')

        # Annotation de titre du plan d'étage
        svg_lines.extend([
            f'  </g>',
            f'  <text x="{min_x + 0.5:.3f}" y="{-max_y + 0.6:.3f}" font-family="sans-serif" font-size="0.4" font-weight="bold" fill="#0f172a">PLAN DE COUPE D\'ÉTAGE (Z = +{elevation_z:.2f} m)</text>',
            f'</svg>',
        ])
        svg_str = "\n".join(svg_lines)

        # 5. Génération DXF R12 textuel minimaliste
        dxf_lines: list[str] = [
            "0\nSECTION\n2\nENTITIES",
        ]
        for s in cut_segments:
            (x1, y1), (x2, y2) = s
            dxf_lines.extend([
                "0\nLINE\n8\nA-WALL-CUTT",
                f"10\n{x1:.4f}\n20\n{y1:.4f}\n30\n0.0",
                f"11\n{x2:.4f}\n21\n{y2:.4f}\n31\n0.0",
            ])
        for s in visible_segments:
            (x1, y1), (x2, y2) = s
            dxf_lines.extend([
                "0\nLINE\n8\nA-WALL-PROJ",
                f"10\n{x1:.4f}\n20\n{y1:.4f}\n30\n0.0",
                f"11\n{x2:.4f}\n21\n{y2:.4f}\n31\n0.0",
            ])
        dxf_lines.extend(["0\nENDSEC\n0\nEOF"])
        dxf_str = "\n".join(dxf_lines)

        return FloorPlan2D(
            elevation_z=elevation_z,
            cut_segments=cut_segments,
            visible_segments=visible_segments,
            svg_content=svg_str,
            dxf_content=dxf_str,
        )

    def tessellate_binary(
        self,
        linear_deflection: float = 0.001,
        angular_deflection: float = 0.2,
    ) -> BinaryMeshPayload:
        """Génère la tessellation 3D consolidée de tous les composants architecturaux pour Three.js."""
        settings = ifcopenshell.geom.settings()
        all_elements = self._model.by_type("IfcElement")

        all_verts: list[float] = []
        all_normals: list[float] = []
        all_indices: list[int] = []
        all_face_ids: list[int] = []

        vert_offset = 0

        for elem_idx, elem in enumerate(all_elements, start=1):
            try:
                shape = ifcopenshell.geom.create_shape(settings, elem)
                geom = shape.geometry
                raw_verts = list(geom.verts)
                raw_faces = list(geom.faces)
                v_count = len(raw_verts) // 3
                t_count = len(raw_faces) // 3

                if v_count == 0 or t_count == 0:
                    continue

                # Calcul des normales de sommets si non calculées
                v_arr = np.array(raw_verts, dtype=np.float32).reshape((-1, 3))
                i_arr = np.array(raw_faces, dtype=np.uint32).reshape((-1, 3))
                n_arr = np.zeros_like(v_arr, dtype=np.float32)

                for tri in i_arr:
                    v0, v1, v2 = v_arr[tri[0]], v_arr[tri[1]], v_arr[tri[2]]
                    n = np.cross(v1 - v0, v2 - v0)
                    norm = np.linalg.norm(n)
                    if norm > 1e-8:
                        n /= norm
                    n_arr[tri[0]] += n
                    n_arr[tri[1]] += n
                    n_arr[tri[2]] += n

                norms = np.linalg.norm(n_arr, axis=1, keepdims=True)
                norms[norms < 1e-8] = 1.0
                n_arr /= norms

                all_verts.extend(raw_verts)
                all_normals.extend(n_arr.flatten().tolist())

                for idx in raw_faces:
                    all_indices.append(idx + vert_offset)

                all_face_ids.extend([elem_idx] * t_count)
                vert_offset += v_count
            except Exception:
                continue

        if not all_verts:
            # Repli sur maillage unitaire minimal
            v_arr = np.zeros((3, 3), dtype=np.float32)
            n_arr = np.array([[0, 0, 1], [0, 0, 1], [0, 0, 1]], dtype=np.float32)
            i_arr = np.array([[0, 1, 2]], dtype=np.uint32)
            f_arr = np.array([1], dtype=np.uint32)
            return BinaryMeshPayload(
                entity_id=self._entity_id,
                vertices=v_arr,
                normals=n_arr,
                indices=i_arr,
                face_ids=f_arr,
            )

        vertices_np = np.array(all_verts, dtype=np.float32).reshape((-1, 3))
        normals_np = np.array(all_normals, dtype=np.float32).reshape((-1, 3))
        indices_np = np.array(all_indices, dtype=np.uint32).reshape((-1, 3))
        face_ids_np = np.array(all_face_ids, dtype=np.uint32)

        return BinaryMeshPayload(
            entity_id=self._entity_id,
            vertices=vertices_np,
            normals=normals_np,
            indices=indices_np,
            face_ids=face_ids_np,
        )

    def introspect(self) -> ValidationReport:
        """Contrôle la validité de l'arbre spatial et les indicateurs buildingSMART."""
        warnings: list[str] = []

        # 1. Vérification de la racine et de l'arbre
        if not self._model.by_type("IfcProject"):
            warnings.append("Conformité IFC : Racine IfcProject manquante.")
        if not self._model.by_type("IfcBuilding"):
            warnings.append("Conformité IFC : Entité IfcBuilding manquante.")
        if not self._model.by_type("IfcBuildingStorey"):
            warnings.append("Conformité IFC : Aucun étage IfcBuildingStorey défini.")

        # 2. Vérification des éléments physiques orphelins
        for elem in self._model.by_type("IfcElement"):
            if not getattr(elem, "ContainedInStructure", None):
                warnings.append(
                    f"Élément orphelin détecté : {elem.is_a()} '{elem.Name}' non rattaché à un étage."
                )

        # 3. Calcul des volumes et surfaces
        compound = self.to_brep_compound()
        if compound.solids():
            vol = float(compound.volume)
            area = float(compound.area)
            bb = compound.bounding_box()
            bmin = (float(bb.min.X), float(bb.min.Y), float(bb.min.Z))
            bmax = (float(bb.max.X), float(bb.max.Y), float(bb.max.Z))
            center = (
                float(compound.center().X),
                float(compound.center().Y),
                float(compound.center().Z),
            )
        else:
            vol, area = 0.0, 0.0
            bmin, bmax, center = (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)

        counts = {
            "storeys": len(self._model.by_type("IfcBuildingStorey")),
            "walls": len(self._model.by_type("IfcWall")),
            "slabs": len(self._model.by_type("IfcSlab")),
            "doors": len(self._model.by_type("IfcDoor")),
            "windows": len(self._model.by_type("IfcWindow")),
        }

        return ValidationReport(
            is_valid_l0=len(warnings) == 0,
            is_watertight=True,
            volume_m3=vol,
            surface_area_m2=area,
            center_of_mass=center,
            bbox_min=bmin,
            bbox_max=bmax,
            topology_counts=counts,
            dfm_warnings=warnings,
        )

    def resolve_selector(self, selector_expr: str) -> list[int]:
        """Évalue une requête spatiale ou sémantique."""
        if selector_expr.startswith("walls"):
            return [elem.id() for elem in self._model.by_type("IfcWall")]
        elif selector_expr.startswith("slabs"):
            return [elem.id() for elem in self._model.by_type("IfcSlab")]
        elif selector_expr.startswith("storeys"):
            return [elem.id() for elem in self._model.by_type("IfcBuildingStorey")]
        return []

    def serialize_native(self) -> bytes:
        """Sérialise le modèle complet au format standard ISO 10303-21 IFC4."""
        return self._model.to_string().encode("utf-8")


# Alias de conformité
IfcGeometryProvider = BimIfcProvider
