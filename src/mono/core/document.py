from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from mono.core.geometry_provider import GeometryProvider, BinaryMeshPayload, ValidationReport
from mono.core.command import CommandHistory


@dataclass
class ParameterDefinition:
    """Définition d'une variable dimensionnelle liée à des curseurs interactifs."""

    value: float
    min_value: float
    max_value: float
    unit: str
    label: str

    def to_dict(self) -> dict[str, float | str]:
        """Convertit le paramètre en dictionnaire pour la sérialisation JSON."""
        return {
            "value": self.value,
            "min": self.min_value,
            "max": self.max_value,
            "unit": self.unit,
            "label": self.label,
        }


@dataclass
class EntityRecord:
    """Enregistrement d'une entité physique au sein du graphe de scène."""

    entity_id: str
    provider_name: str
    scale_domain: str  # "nano", "micro", "milli", "meter"
    provider: GeometryProvider
    tags: list[str] = field(default_factory=list)
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_quat: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    is_dirty: bool = True
    script_code: str | None = None
    bound_parameters: list[str] = field(default_factory=list)
    scale_to_meters: float = 0.001


class MonoDocument:
    """Document Unifié servant de source unique de vérité physique et géométrique.

    Maintient en mémoire le graphe de scène multi-échelle, les paramètres interactifs,
    l'historique transactionnel (Undo/Redo) et l'état des entités.
    """

    def __init__(self) -> None:
        """Initialise un document vierge avec unités SI standard."""
        self.version: str = "2.0"
        self.units: dict[str, str] = {
            "length": "m",
            "mass": "kg",
            "time": "s",
            "temperature": "K",
        }
        self.parameters: dict[str, ParameterDefinition] = {}
        self.entities: dict[str, EntityRecord] = {}
        self.history: CommandHistory = CommandHistory()

    def register_parameter(
        self,
        name: str,
        value: float,
        min_value: float,
        max_value: float,
        unit: str = "m",
        label: str = "",
    ) -> None:
        """Enregistre un nouveau paramètre modifiable via les curseurs UI.

        Args:
            name: Nom technique unique (ex: 'bracket_width').
            value: Valeur actuelle initiale.
            min_value: Borne inférieure autorisée.
            max_value: Borne supérieure autorisée.
            unit: Symbole de l'unité (ex: 'm', 'mm', 'deg').
            label: Libellé d'affichage convivial pour l'utilisateur.
        """
        self.parameters[name] = ParameterDefinition(
            value=value,
            min_value=min_value,
            max_value=max_value,
            unit=unit,
            label=label or name,
        )

    def update_parameter(self, name: str, new_value: float) -> None:
        """Met à jour la valeur d'un paramètre existant en respectant ses bornes.

        Déclenche immédiatement la re-tessellation B-Rep des entités paramétriques liées (< 50 ms).

        Args:
            name: Nom du paramètre à mettre à jour.
            new_value: Nouvelle valeur numérique.

        Raises:
            KeyError: Si le paramètre n'existe pas.
        """
        if name not in self.parameters:
            raise KeyError(f"Paramètre inconnu : '{name}'")

        param = self.parameters[name]
        clamped = max(param.min_value, min(new_value, param.max_value))
        param.value = clamped

        # Re-tessellation paramétrique à la volée (< 50 ms) pour toutes les entités liées
        all_param_values = {k: v.value for k, v in self.parameters.items()}
        from mono.execution.ecip_executor import execute_ecip_cad_block
        from mono.providers.brep_build123d import BRepBuild123dProvider

        for rec in self.entities.values():
            if rec.script_code and name in rec.bound_parameters:
                try:
                    res = execute_ecip_cad_block(
                        rec.script_code,
                        param_overrides=all_param_values,
                        unit_scale_to_meters=rec.scale_to_meters,
                    )
                    rec.provider = BRepBuild123dProvider(
                        entity_id=rec.entity_id,
                        part=res.part,
                        unit_scale_to_meters=rec.scale_to_meters,
                    )
                    rec.is_dirty = True
                except Exception:
                    # En cas de valeur temporaire non reconstructible, préserver le solide antérieur
                    pass

    def attach_parametric_source(
        self,
        entity_id: str,
        script_code: str,
        bound_parameters: list[str],
        scale_to_meters: float = 0.001,
    ) -> None:
        """Associe un script déclaratif ECIP et ses variables à une entité de la scène.

        Args:
            entity_id: Identifiant unique de l'entité.
            script_code: Code source complet build123d / ECIP.
            bound_parameters: Noms des variables extraites pouvant modifier la géométrie.
            scale_to_meters: Facteur d'échelle vers les mètres SI.
        """
        if entity_id in self.entities:
            rec = self.entities[entity_id]
            rec.script_code = script_code
            rec.bound_parameters = list(bound_parameters)
            rec.scale_to_meters = scale_to_meters

    def add_entity(
        self,
        entity_id: str,
        provider: GeometryProvider,
        provider_name: str = "brep_build123d",
        scale_domain: str = "milli",
        tags: list[str] | None = None,
        position: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> None:
        """Ajoute ou remplace une entité géométrique dans le graphe de scène.

        Args:
            entity_id: Identifiant unique de l'entité.
            provider: Instance concrète implémentant GeometryProvider.
            provider_name: Nom du moteur technique (ex: 'brep_build123d').
            scale_domain: Domaine d'échelle natif ('nano', 'milli', 'meter').
            tags: Liste d'étiquettes sémantiques.
            position: Position mondiale (x, y, z) en mètres float64.
        """
        self.entities[entity_id] = EntityRecord(
            entity_id=entity_id,
            provider_name=provider_name,
            scale_domain=scale_domain,
            provider=provider,
            tags=list(tags or []),
            position=position,
            is_dirty=True,
        )

    def get_provider(self, entity_id: str) -> GeometryProvider | None:
        """Récupère le fournisseur géométrique d'une entité.

        Args:
            entity_id: Identifiant de l'entité recherchée.

        Returns:
            GeometryProvider | None: Le fournisseur ou None si inexistant.
        """
        record = self.entities.get(entity_id)
        return record.provider if record else None

    def remove_entity(self, entity_id: str) -> bool:
        """Supprime une entité de la scène.

        Args:
            entity_id: Identifiant de l'entité à supprimer.

        Returns:
            bool: True si supprimée, False si inexistante.
        """
        if entity_id in self.entities:
            del self.entities[entity_id]
            return True
        return False

    def collect_dirty_binary_payloads(
        self,
        linear_deflection: float = 0.001,
        angular_deflection: float = 0.2,
    ) -> list[BinaryMeshPayload]:
        """Tesselle et collecte les charges utiles binaires de toutes les entités modifiées.

        Marque ensuite les entités comme propres (is_dirty = False).

        Returns:
            list[BinaryMeshPayload]: Liste des charges prêtes pour le transfert WebSocket binaire.
        """
        payloads: list[BinaryMeshPayload] = []
        for record in self.entities.values():
            if record.is_dirty:
                payload = record.provider.tessellate_binary(linear_deflection, angular_deflection)
                payloads.append(payload)
                record.is_dirty = False
        return payloads

    def run_fea_simulation(
        self,
        entity_id: str,
        material: object | None = None,
        boundary: object | None = None,
        element_size_max: float = 6.0,
        target_safety_factor: float = 1.5,
    ) -> object:
        """Exécute une analyse éléments finis (FEA) sur une entité existante.

        Remplace le provider de l'entité par un FeaGmshProvider enrichi du champ
        scalaire de Von Mises et marque l'entité comme modifiée (is_dirty = True).

        Args:
            entity_id: Identifiant de l'entité à soumettre à l'analyse FEA.
            material: Propriétés élastiques du matériau (FEAMaterial).
            boundary: Conditions aux limites d'encastrement et d'effort (FEABoundaryCondition).
            element_size_max: Taille maximale des tétraèdres C3D10 en mm.
            target_safety_factor: Coefficient de sécurité minimal requis.

        Returns:
            FEAResult: Données complètes de la simulation (contraintes, déplacements, Sf).
        """
        if entity_id not in self.entities:
            raise KeyError(f"Entité géométrique '{entity_id}' introuvable dans le document.")

        rec = self.entities[entity_id]
        from mono.providers.brep_build123d import BRepBuild123dProvider
        from mono.providers.fea_gmsh import FeaGmshProvider, FEAMaterial, FEABoundaryCondition

        part = None
        if isinstance(rec.provider, BRepBuild123dProvider):
            part = rec.provider.part
        elif isinstance(rec.provider, FeaGmshProvider):
            part = rec.provider._part

        mat = material if isinstance(material, FEAMaterial) else FEAMaterial()
        bc = boundary if isinstance(boundary, FEABoundaryCondition) else FEABoundaryCondition()

        fea_provider = FeaGmshProvider(
            entity_id=entity_id,
            part=part,
            material=mat,
            boundary=bc,
            element_size_max=element_size_max,
            target_safety_factor=target_safety_factor,
        )

        result = fea_provider.solve()

        rec.provider = fea_provider
        rec.provider_name = "fea_gmsh"
        rec.is_dirty = True

        return result

    def to_semantic_summary(self) -> dict[str, object]:
        """Génère un résumé textuel compact pour l'injection dans le contexte des LLM/VLM.

        Returns:
            dict[str, object]: Arbre sémantique léger sans les maillages lourds.
        """
        entities_summary: dict[str, dict[str, object]] = {}
        for eid, rec in self.entities.items():
            report: ValidationReport = rec.provider.introspect()
            entities_summary[eid] = {
                "provider": rec.provider_name,
                "scale_domain": rec.scale_domain,
                "position": rec.position,
                "tags": rec.tags,
                "is_valid_l0": report.is_valid_l0,
                "is_watertight": report.is_watertight,
                "volume_m3": report.volume_m3,
                "bbox_min": report.bbox_min,
                "bbox_max": report.bbox_max,
                "topology": report.topology_counts,
                "dfm_warnings": report.dfm_warnings,
            }

        params_summary: dict[str, dict[str, float | str]] = {
            k: v.to_dict() for k, v in self.parameters.items()
        }

        return {
            "version": self.version,
            "units": self.units,
            "parameters": params_summary,
            "entities_count": len(self.entities),
            "entities": entities_summary,
        }
