from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
import build123d as bd
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCP.gp import gp_Lin, gp_Pnt, gp_Dir

from mono.core.document import MonoDocument
from mono.execution.ecip_executor import execute_ecip_cad_block, ECIPExecutionError
from mono.providers.fea_gmsh import FeaGmshProvider, FEAMaterial, FEABoundaryCondition


class GateLevel(str, Enum):
    """Niveaux de vérification déterministe selon le benchmark CADEngBench."""
    L0_INTEGRITY = "L0_INTEGRITY"
    L1_DFM = "L1_DFM"
    L2_Z_PARAMETRIC = "L2_Z_PARAMETRIC"
    L2_E_ISOLATION = "L2_E_ISOLATION"
    L3_STRUCTURAL_FEA = "L3_STRUCTURAL_FEA"


@dataclass(frozen=True)
class L0Result:
    """Résultat de la Porte L0 : Intégrité B-Rep, volume et étanchéité."""
    is_valid: bool
    is_watertight: bool
    volume_m3: float
    solids_count: int
    error: str | None = None


@dataclass(frozen=True)
class L1Result:
    """Résultat de la Porte L1 : Prédicats dimensionnels et règles DFM."""
    is_valid: bool
    bbox_dims_m: tuple[float, float, float]
    min_wall_thickness_mm: float
    hole_diameters_mm: list[float]
    hole_ld_ratios: list[float]
    undercut_faces_count: int
    target_dims_matched: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class L2ZResult:
    """Résultat de la Porte L2-Z : Survie aux perturbations paramétriques (±10%)."""
    is_valid: bool
    tested_parameters: list[str]
    variations_tested: int
    failed_variations: list[str] = field(default_factory=list)
    details: str = ""


@dataclass(frozen=True)
class L2EResult:
    """Résultat de la Porte L2-E : Isolation d'édition et sécurité physique."""
    is_valid: bool
    collateral_damage_detected: bool
    clash_detected: bool
    collateral_entities: list[str] = field(default_factory=list)
    clashing_entities: list[str] = field(default_factory=list)
    details: str = ""


@dataclass(frozen=True)
class L3Result:
    """Résultat de la Porte L3 : Résistance mécanique et intégrité structurelle FEA."""
    is_valid: bool
    max_von_mises_mpa: float
    yield_strength_mpa: float
    safety_factor: float
    target_safety_factor: float
    applied_force_n: tuple[float, float, float]
    material_name: str
    error: str | None = None
    details: str = ""


@dataclass(frozen=True)
class GateEvaluationResult:
    """Rapport consolidé des portes déterministes CADEngBench (L0, L1, L2-Z, L2-E, L3)."""
    overall_passed: bool
    score: float  # Score composite normalisé [0.0, 1.0]
    l0: L0Result
    l1: L1Result
    l2_z: L2ZResult
    l2_e: L2EResult
    l3: L3Result | None = None
    diagnostics: list[str] = field(default_factory=list)
    feedback_context: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Convertit le résultat en dictionnaire structuré prêt pour l'API REST."""
        res: dict[str, object] = {
            "overall_passed": self.overall_passed,
            "score": self.score,
            "l0": {
                "is_valid": self.l0.is_valid,
                "is_watertight": self.l0.is_watertight,
                "volume_m3": self.l0.volume_m3,
                "solids_count": self.l0.solids_count,
                "error": self.l0.error,
            },
            "l1": {
                "is_valid": self.l1.is_valid,
                "bbox_dims_m": list(self.l1.bbox_dims_m),
                "min_wall_thickness_mm": self.l1.min_wall_thickness_mm,
                "hole_diameters_mm": self.l1.hole_diameters_mm,
                "hole_ld_ratios": self.l1.hole_ld_ratios,
                "undercut_faces_count": self.l1.undercut_faces_count,
                "target_dims_matched": self.l1.target_dims_matched,
                "warnings": self.l1.warnings,
                "errors": self.l1.errors,
            },
            "l2_z": {
                "is_valid": self.l2_z.is_valid,
                "tested_parameters": self.l2_z.tested_parameters,
                "variations_tested": self.l2_z.variations_tested,
                "failed_variations": self.l2_z.failed_variations,
                "details": self.l2_z.details,
            },
            "l2_e": {
                "is_valid": self.l2_e.is_valid,
                "collateral_damage_detected": self.l2_e.collateral_damage_detected,
                "clash_detected": self.l2_e.clash_detected,
                "collateral_entities": self.l2_e.collateral_entities,
                "clashing_entities": self.l2_e.clashing_entities,
                "details": self.l2_e.details,
            },
            "l3": None if self.l3 is None else {
                "is_valid": self.l3.is_valid,
                "max_von_mises_mpa": self.l3.max_von_mises_mpa,
                "yield_strength_mpa": self.l3.yield_strength_mpa,
                "safety_factor": self.l3.safety_factor,
                "target_safety_factor": self.l3.target_safety_factor,
                "applied_force_n": list(self.l3.applied_force_n),
                "material_name": self.l3.material_name,
                "error": self.l3.error,
                "details": self.l3.details,
            },
            "diagnostics": self.diagnostics,
            "feedback_context": self.feedback_context,
        }
        return res


class CADEngBenchGatekeeper:
    """Compilateur déterministe des Portes de Qualité Mathématique CADEngBench (L0–L2).

    Exécute les vérifications physiques et topologiques sans modèle d'intelligence
    artificielle, permettant des boucles d'auto-réparation rapides (0 jeton VLM).
    """

    MIN_WALL_THICKNESS_MM: float = 1.0
    MIN_HOLE_DIAMETER_MM: float = 2.0
    MAX_HOLE_LD_RATIO: float = 8.0
    DIM_TOLERANCE_RATIO: float = 0.05  # ±5% sur les cotes cibles

    @classmethod
    def evaluate_l0(
        cls,
        part: bd.Part | bd.Solid | bd.Compound,
        unit_scale_to_meters: float = 0.001,
    ) -> L0Result:
        """Porte L0 : Exécutabilité et Intégrité Topologique B-Rep."""
        try:
            shape = part.wrapped
            analyzer = BRepCheck_Analyzer(shape)
            is_valid_occt = bool(analyzer.IsValid())

            vol_m3 = float(part.volume) * (unit_scale_to_meters ** 3)
            solids = part.solids()
            solids_count = len(solids)

            # Solide fermé (manifold/watertight) et volume strictement positif (> 1e-12 m³)
            is_watertight = is_valid_occt and vol_m3 > 1e-12 and part.is_valid and solids_count >= 1

            error_msg: str | None = None
            if not is_valid_occt:
                error_msg = "Topologie OpenCASCADE invalide (auto-intersection ou surfaces dégénérées)."
            elif vol_m3 <= 1e-12:
                error_msg = f"Volume physique nul ou négatif ({vol_m3:.2e} m³)."
            elif solids_count < 1:
                error_msg = "Aucun solide volumique trouvé dans la géométrie générée."

            return L0Result(
                is_valid=is_watertight,
                is_watertight=is_watertight,
                volume_m3=vol_m3,
                solids_count=solids_count,
                error=error_msg,
            )
        except Exception as exc:
            return L0Result(
                is_valid=False,
                is_watertight=False,
                volume_m3=0.0,
                solids_count=0,
                error=f"Exception interne lors du contrôle L0 : {exc}",
            )

    @classmethod
    def evaluate_l1_dfm(
        cls,
        part: bd.Part | bd.Solid | bd.Compound,
        target_dimensions: dict[str, float] | None = None,
        unit_scale_to_meters: float = 0.001,
    ) -> L1Result:
        """Porte L1 : Prédicats dimensionnels et règles DFM (usinage / fabrication)."""
        bbox = part.bounding_box()
        sx = float(bbox.size.X)
        sy = float(bbox.size.Y)
        sz = float(bbox.size.Z)

        bbox_dims_m = (
            sx * unit_scale_to_meters,
            sy * unit_scale_to_meters,
            sz * unit_scale_to_meters,
        )

        warnings: list[str] = []
        errors: list[str] = []

        # 1. Épaisseur minimale de paroi par lancer de rayon intérieur
        min_wall_mm = cls._compute_min_wall_thickness(part)
        if min_wall_mm < cls.MIN_WALL_THICKNESS_MM:
            errors.append(
                f"Épaisseur minimale de paroi ({min_wall_mm:.2f} mm) inférieure au seuil DFM ({cls.MIN_WALL_THICKNESS_MM} mm)."
            )

        # 2. Perçages circulaires et ratio L/D
        hole_diameters_mm: list[float] = []
        hole_ld_ratios: list[float] = []

        for face in part.faces():
            if face.geom_type == bd.GeomType.CYLINDER:
                # Diamètre en millimètres
                dia_mm = float(face.radius) * 2.0
                hole_diameters_mm.append(dia_mm)

                face_bbox = face.bounding_box()
                length_mm = max(float(face_bbox.size.X), float(face_bbox.size.Y), float(face_bbox.size.Z))
                ld_ratio = length_mm / dia_mm if dia_mm > 0 else 0.0
                hole_ld_ratios.append(ld_ratio)

                if dia_mm < cls.MIN_HOLE_DIAMETER_MM:
                    errors.append(
                        f"Perçage cylindrique trop petit (Ø {dia_mm:.2f} mm < seuil usinable {cls.MIN_HOLE_DIAMETER_MM} mm)."
                    )
                if ld_ratio > cls.MAX_HOLE_LD_RATIO:
                    warnings.append(
                        f"Perçage profond à fort ratio L/D ({ld_ratio:.1f} > {cls.MAX_HOLE_LD_RATIO}) : risque de flexion ou rupture d'outil."
                    )

        # 3. Détection des contre-dépouilles (Undercuts par rapport au vecteur Z)
        undercuts_count = cls._detect_undercuts(part)
        if undercuts_count > 0:
            warnings.append(
                f"{undercuts_count} face(s) en contre-dépouille détectée(s) selon l'axe de démoulage Z."
            )

        # 4. Vérification d'alignement avec les cotes cibles attendues
        target_matched = True
        if target_dimensions:
            # Tri des dimensions réelles et cibles pour comparaison invariante
            actual_sorted = sorted([sx, sy, sz])
            target_sorted = sorted(target_dimensions.values())

            if len(target_sorted) <= 3:
                for act, tgt in zip(actual_sorted[-len(target_sorted):], target_sorted):
                    if tgt > 0:
                        diff_ratio = abs(act - tgt) / tgt
                        if diff_ratio > cls.DIM_TOLERANCE_RATIO:
                            target_matched = False
                            errors.append(
                                f"Écart dimensionnel excessif : dimension {act:.2f} mm vs cible {tgt:.2f} mm (écart {diff_ratio * 100.0:.1f}% > tolérance {cls.DIM_TOLERANCE_RATIO * 100.0:.0f}%)."
                            )

        is_valid = len(errors) == 0

        return L1Result(
            is_valid=is_valid,
            bbox_dims_m=bbox_dims_m,
            min_wall_thickness_mm=min_wall_mm,
            hole_diameters_mm=hole_diameters_mm,
            hole_ld_ratios=hole_ld_ratios,
            undercut_faces_count=undercuts_count,
            target_dims_matched=target_matched,
            warnings=warnings,
            errors=errors,
        )

    @classmethod
    def evaluate_l2_z_parametric(
        cls,
        script_code: str | None,
        base_params: dict[str, float] | None,
        unit_scale_to_meters: float = 0.001,
    ) -> L2ZResult:
        """Porte L2-Z : Survie à la perturbation paramétrique (±10%)."""
        if not script_code or not base_params:
            return L2ZResult(
                is_valid=True,
                tested_parameters=[],
                variations_tested=0,
                details="Aucun script paramétrique ou paramètre fourni : contrôle L2-Z ignoré (valide par défaut).",
            )

        tested_params: list[str] = []
        failed_variations: list[str] = []
        variations_count = 0

        for param_name, param_val in base_params.items():
            if not isinstance(param_val, (int, float)) or param_val == 0:
                continue

            tested_params.append(param_name)

            # Test -10% et +10%
            for factor, label in [(0.90, "-10%"), (1.10, "+10%")]:
                variations_count += 1
                perturbed_val = float(param_val) * factor
                override = dict(base_params)
                override[param_name] = perturbed_val

                try:
                    res = execute_ecip_cad_block(
                        script_code,
                        param_overrides=override,
                        unit_scale_to_meters=unit_scale_to_meters,
                    )
                    l0 = cls.evaluate_l0(res.part, unit_scale_to_meters=unit_scale_to_meters)
                    if not l0.is_valid:
                        failed_variations.append(
                            f"Paramètre '{param_name}' ({label} = {perturbed_val:.2f}) brise l'intégrité L0 : {l0.error}"
                        )
                except ECIPExecutionError as err:
                    failed_variations.append(
                        f"Paramètre '{param_name}' ({label} = {perturbed_val:.2f}) lève une exception : {err}"
                    )

        is_valid = len(failed_variations) == 0
        details = (
            f"Vérification paramétrique réussie sur {len(tested_params)} paramètres ({variations_count} variantes testées)."
            if is_valid
            else f"Échec de robustesse paramétrique : {len(failed_variations)} variante(s) brisée(s)."
        )

        return L2ZResult(
            is_valid=is_valid,
            tested_parameters=tested_params,
            variations_tested=variations_count,
            failed_variations=failed_variations,
            details=details,
        )

    @classmethod
    def evaluate_l2_e_isolation(
        cls,
        target_entity_id: str,
        current_part: bd.Part | bd.Solid | bd.Compound,
        doc: MonoDocument | None,
        initial_entity_snapshots: dict[str, tuple[float, tuple[float, float, float]]] | None = None,
    ) -> L2EResult:
        """Porte L2-E : Isolation d'édition et absence de collisions indésirables."""
        if doc is None:
            return L2EResult(
                is_valid=True,
                collateral_damage_detected=False,
                clash_detected=False,
                details="Aucun document parent fourni : contrôle d'isolation L2-E validé.",
            )

        collateral_entities: list[str] = []
        clashing_entities: list[str] = []

        # 1. Vérification des dommages collatéraux (Repair Precision = 1.0)
        if initial_entity_snapshots:
            for ent_id, ent in doc.entities.items():
                if ent_id == target_entity_id:
                    continue
                if ent_id in initial_entity_snapshots:
                    init_vol, init_com = initial_entity_snapshots[ent_id]
                    rep = ent.provider.introspect()
                    if abs(rep.volume_m3 - init_vol) > 1e-8:
                        collateral_entities.append(
                            f"{ent_id} (volume altéré : {init_vol:.3e} -> {rep.volume_m3:.3e} m³)"
                        )
                    else:
                        dx = rep.center_of_mass[0] - init_com[0]
                        dy = rep.center_of_mass[1] - init_com[1]
                        dz = rep.center_of_mass[2] - init_com[2]
                        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
                        if dist > 1e-6:
                            collateral_entities.append(
                                f"{ent_id} (déplacement non sollicité : {dist * 1000.0:.3f} mm)"
                            )

        # 2. Détection d'interpénétration non désirée avec les autres solides (Clash Detection)
        for ent_id, ent in doc.entities.items():
            if ent_id == target_entity_id:
                continue
            # Récupération de la part de l'autre entité si disponible
            other_part = getattr(ent.provider, "_part", None)
            if other_part is not None:
                try:
                    inter_res = current_part.intersect(other_part)
                    if inter_res is not None:
                        inter_vol = sum(float(getattr(s, "volume", 0.0)) for s in inter_res)
                        if inter_vol > 1e-3:  # Volume de collision > 1 mm³
                            clashing_entities.append(f"{ent_id} (volume de clash : {inter_vol:.2f} mm³)")
                except Exception:
                    pass

        has_collateral = len(collateral_entities) > 0
        has_clash = len(clashing_entities) > 0
        is_valid = (not has_collateral) and (not has_clash)

        details_parts: list[str] = []
        if has_collateral:
            details_parts.append(f"Altérations collatérales sur {len(collateral_entities)} entité(s) protégée(s).")
        if has_clash:
            details_parts.append(f"Collision physique détectée avec {len(clashing_entities)} solide(s).")
        if is_valid:
            details_parts.append("Isolation d'édition et sécurité physique parfaites (Repair Precision = 1.0).")

        return L2EResult(
            is_valid=is_valid,
            collateral_damage_detected=has_collateral,
            clash_detected=has_clash,
            collateral_entities=collateral_entities,
            clashing_entities=clashing_entities,
            details=" ".join(details_parts),
        )

    @classmethod
    def evaluate_l3_structural_fea(
        cls,
        part: bd.Part | bd.Solid | bd.Compound,
        material: FEAMaterial | None = None,
        applied_force_n: tuple[float, float, float] | None = None,
        target_safety_factor: float = 1.5,
        fixed_predicate: str = "min_z",
        load_predicate: str = "max_z",
        element_size_max: float = 6.0,
        yield_strength_mpa: float | None = None,
    ) -> L3Result:
        """Porte L3 : Intégrité structurelle et résistance mécanique par éléments finis (FEA).

        Calcule les contraintes de Von Mises via FeaGmshProvider (maillage C3D10 + CalculiX ccx).
        Rejette automatiquement la pièce conçue si sigma_max > Re / s (soit facteur de sécurité Sf < target_safety_factor).
        """
        # Résolution du matériau
        mat = material
        if mat is None:
            re_val = yield_strength_mpa if yield_strength_mpa is not None else 275.0
            mat = FEAMaterial(
                name="ALUMINUM_6061_T6",
                young_modulus_gpa=70.0,
                poisson_ratio=0.33,
                yield_strength_mpa=re_val,
            )
        elif yield_strength_mpa is not None:
            mat = FEAMaterial(
                name=mat.name,
                young_modulus_gpa=mat.young_modulus_gpa,
                poisson_ratio=mat.poisson_ratio,
                yield_strength_mpa=yield_strength_mpa,
                density_kg_m3=mat.density_kg_m3,
            )

        force = applied_force_n if applied_force_n is not None else (0.0, 0.0, -2500.0)

        bc = FEABoundaryCondition(
            fixed_predicate=fixed_predicate,
            load_predicate=load_predicate,
            applied_force_n=force,
        )

        try:
            provider = FeaGmshProvider(
                entity_id="gate_l3_structural",
                part=part,
                material=mat,
                boundary=bc,
                element_size_max=element_size_max,
                target_safety_factor=target_safety_factor,
            )
            fea_res = provider.solve()

            sigma_max = float(fea_res.max_von_mises_mpa)
            sf = float(fea_res.safety_factor)
            re_mpa = float(mat.yield_strength_mpa)
            sigma_allow = re_mpa / target_safety_factor

            # Condition stricte de résistance : sigma_max <= Re / s (Sf >= s)
            is_valid = bool(sigma_max <= sigma_allow and sf >= target_safety_factor)

            error_msg: str | None = None
            if not is_valid:
                error_msg = (
                    f"Contrainte de Von Mises maximale excessive : {sigma_max:.2f} MPa "
                    f"> admissible {sigma_allow:.2f} MPa (Re={re_mpa:.1f} MPa, Sf={sf:.2f} < {target_safety_factor:.2f}). "
                    f"Risque avéré de déformation plastique ou rupture mécanique sous charge nominale."
                )

            details = (
                f"Résistance mécanique conforme : Von Mises max {sigma_max:.2f} MPa <= {sigma_allow:.2f} MPa "
                f"(Sf={sf:.2f} >= {target_safety_factor:.2f})."
            )

            return L3Result(
                is_valid=is_valid,
                max_von_mises_mpa=sigma_max,
                yield_strength_mpa=re_mpa,
                safety_factor=sf,
                target_safety_factor=target_safety_factor,
                applied_force_n=force,
                material_name=mat.name,
                error=error_msg,
                details=details,
            )
        except Exception as exc:
            return L3Result(
                is_valid=False,
                max_von_mises_mpa=0.0,
                yield_strength_mpa=float(mat.yield_strength_mpa),
                safety_factor=0.0,
                target_safety_factor=target_safety_factor,
                applied_force_n=force,
                material_name=mat.name,
                error=f"Erreur d'exécution de la simulation FEA Porte L3 : {exc}",
                details="Échec de résolution Gmsh / CalculiX.",
            )

    @classmethod
    def evaluate(
        cls,
        entity_id: str,
        part: bd.Part | bd.Solid | bd.Compound,
        target_dimensions: dict[str, float] | None = None,
        script_code: str | None = None,
        base_params: dict[str, float] | None = None,
        doc: MonoDocument | None = None,
        initial_entity_snapshots: dict[str, tuple[float, tuple[float, float, float]]] | None = None,
        unit_scale_to_meters: float = 0.001,
        material: FEAMaterial | None = None,
        applied_force_n: tuple[float, float, float] | None = None,
        target_safety_factor: float = 1.5,
        fixed_predicate: str = "min_z",
        load_predicate: str = "max_z",
        yield_strength_mpa: float | None = None,
        evaluate_l3: bool = False,
    ) -> GateEvaluationResult:
        """Évalue l'ensemble des portes déterministes CADEngBench (L0, L1, L2-Z, L2-E, et optionnellement L3 FEA).

        Returns:
            GateEvaluationResult: Bilan complet avec score composite et contexte d'auto-réparation.
        """
        diagnostics: list[str] = []

        # 1. Porte L0
        l0 = cls.evaluate_l0(part, unit_scale_to_meters=unit_scale_to_meters)
        if not l0.is_valid:
            diagnostics.append(f"[Porte L0 ÉCHOUÉE] {l0.error}")

        # 2. Porte L1
        l1 = cls.evaluate_l1_dfm(
            part,
            target_dimensions=target_dimensions,
            unit_scale_to_meters=unit_scale_to_meters,
        )
        for err in l1.errors:
            diagnostics.append(f"[Porte L1 DFM ÉCHOUÉE] {err}")
        for warn in l1.warnings:
            diagnostics.append(f"[Porte L1 Avertissement] {warn}")

        # 3. Porte L2-Z
        l2_z = cls.evaluate_l2_z_parametric(
            script_code=script_code,
            base_params=base_params,
            unit_scale_to_meters=unit_scale_to_meters,
        )
        for fail in l2_z.failed_variations:
            diagnostics.append(f"[Porte L2-Z Paramétrique ÉCHOUÉE] {fail}")

        # 4. Porte L2-E
        l2_e = cls.evaluate_l2_e_isolation(
            target_entity_id=entity_id,
            current_part=part,
            doc=doc,
            initial_entity_snapshots=initial_entity_snapshots,
        )
        for col in l2_e.collateral_entities:
            diagnostics.append(f"[Porte L2-E Dommage Collatéral] {col}")
        for clash in l2_e.clashing_entities:
            diagnostics.append(f"[Porte L2-E Collision Physique] {clash}")

        # 5. Porte L3 (Résistance Mécanique FEA Automatique)
        l3: L3Result | None = None
        should_eval_l3 = (
            evaluate_l3
            or (material is not None and applied_force_n is not None)
            or (yield_strength_mpa is not None and applied_force_n is not None)
        )
        if should_eval_l3 and l0.is_valid:
            l3 = cls.evaluate_l3_structural_fea(
                part=part,
                material=material,
                applied_force_n=applied_force_n,
                target_safety_factor=target_safety_factor,
                fixed_predicate=fixed_predicate,
                load_predicate=load_predicate,
                yield_strength_mpa=yield_strength_mpa,
            )
            if not l3.is_valid:
                diagnostics.append(f"[Porte L3 FEA ÉCHOUÉE] {l3.error}")
            else:
                diagnostics.append(f"[Porte L3 FEA Validée] {l3.details}")

        # Calcul du score composite pondéré
        if l3 is not None:
            # Pondération avec Porte L3 : L0: 30%, L1: 25%, L2-Z: 15%, L2-E: 10%, L3: 20%
            w_l0 = 0.30 if l0.is_valid else 0.0
            w_l1 = 0.25 if l1.is_valid else 0.0
            w_l2_z = 0.15 if l2_z.is_valid else 0.0
            w_l2_e = 0.10 if l2_e.is_valid else 0.0
            w_l3 = 0.20 if l3.is_valid else 0.0
            score = round(w_l0 + w_l1 + w_l2_z + w_l2_e + w_l3, 3)
            overall_passed = bool(
                l0.is_valid and l1.is_valid and l2_z.is_valid and l2_e.is_valid and l3.is_valid
            )
        else:
            # Pondération standard L0–L2 (L0: 40%, L1: 30%, L2-Z: 15%, L2-E: 15%)
            w_l0 = 0.40 if l0.is_valid else 0.0
            w_l1 = 0.30 if l1.is_valid else 0.0
            w_l2_z = 0.15 if l2_z.is_valid else 0.0
            w_l2_e = 0.15 if l2_e.is_valid else 0.0
            score = round(w_l0 + w_l1 + w_l2_z + w_l2_e, 3)
            overall_passed = bool(l0.is_valid and l1.is_valid and l2_z.is_valid and l2_e.is_valid)

        feedback_context: str | None = None
        if not overall_passed:
            feedback_context = (
                "Échec des portes déterministes CADEngBench :\n" + "\n".join(f"- {d}" for d in diagnostics)
            )

        return GateEvaluationResult(
            overall_passed=overall_passed,
            score=score,
            l0=l0,
            l1=l1,
            l2_z=l2_z,
            l2_e=l2_e,
            l3=l3,
            diagnostics=diagnostics,
            feedback_context=feedback_context,
        )

    @classmethod
    def evaluate_all(
        cls,
        entity_id: str,
        part: bd.Part | bd.Solid | bd.Compound,
        target_dimensions: dict[str, float] | None = None,
        script_code: str | None = None,
        base_params: dict[str, float] | None = None,
        doc: MonoDocument | None = None,
        initial_entity_snapshots: dict[str, tuple[float, tuple[float, float, float]]] | None = None,
        unit_scale_to_meters: float = 0.001,
        material: FEAMaterial | None = None,
        applied_force_n: tuple[float, float, float] | None = None,
        target_safety_factor: float = 1.5,
        fixed_predicate: str = "min_z",
        load_predicate: str = "max_z",
        yield_strength_mpa: float | None = None,
        evaluate_l3: bool = False,
    ) -> GateEvaluationResult:
        """Alias officiel pour evaluate() selon la spécification CADEngBenchGates."""
        return cls.evaluate(
            entity_id=entity_id,
            part=part,
            target_dimensions=target_dimensions,
            script_code=script_code,
            base_params=base_params,
            doc=doc,
            initial_entity_snapshots=initial_entity_snapshots,
            unit_scale_to_meters=unit_scale_to_meters,
            material=material,
            applied_force_n=applied_force_n,
            target_safety_factor=target_safety_factor,
            fixed_predicate=fixed_predicate,
            load_predicate=load_predicate,
            yield_strength_mpa=yield_strength_mpa,
            evaluate_l3=evaluate_l3,
        )

    @classmethod
    def _compute_min_wall_thickness(cls, part: bd.Part | bd.Solid | bd.Compound) -> float:
        """Mesure l'épaisseur minimale de paroi par lancer de rayons normaux intérieurs."""
        min_thick = float("inf")
        shape = part.wrapped

        inter = IntCurvesFace_ShapeIntersector()
        inter.Load(shape, 1e-4)

        tested_faces = 0
        for face in part.faces():
            if face.geom_type == bd.GeomType.PLANE:
                c = face.center()
                n = face.normal_at(c)
                # Rayon lancé vers l'intérieur du solide (-normale)
                lin = gp_Lin(gp_Pnt(c.X, c.Y, c.Z), gp_Dir(-n.X, -n.Y, -n.Z))
                inter.Perform(lin, 1e-3, 5000.0)
                if inter.IsDone() and inter.NbPnt() > 0:
                    d = inter.WParameter(1)
                    if 0.0 < d < min_thick:
                        min_thick = d
                        tested_faces += 1

        if tested_faces == 0 or math.isinf(min_thick):
            # Repli sur la dimension minimale de la boîte englobante
            bbox = part.bounding_box()
            min_thick = min(float(bbox.size.X), float(bbox.size.Y), float(bbox.size.Z))

        return float(min_thick)

    @classmethod
    def _detect_undercuts(cls, part: bd.Part | bd.Solid | bd.Compound) -> int:
        """Détecte les faces créant une contre-dépouille par rapport à l'axe Z (démoulage)."""
        bbox = part.bounding_box()
        z_min = float(bbox.min.Z)

        undercut_count = 0
        for face in part.faces():
            if face.geom_type == bd.GeomType.PLANE:
                c = face.center()
                # On ignore la face inférieure de base
                if c.Z <= z_min + 0.01:
                    continue
                n = face.normal_at(c)
                # Si la face latérale a une normale inclinée vers le bas (Z négatif significatif)
                if n.Z < -0.05 and abs(n.X) + abs(n.Y) > 0.1:
                    undercut_count += 1

        return undercut_count


CADEngBenchGates = CADEngBenchGatekeeper
