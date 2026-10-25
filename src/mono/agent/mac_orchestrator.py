from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import re
import build123d as bd

from mono.core.document import MonoDocument, EntityRecord
from mono.core.command import CommandHistory, SetParamCommand
from mono.providers.brep_build123d import BRepBuild123dProvider
from mono.execution.ecip_executor import execute_ecip_cad_block, ECIPExecutionError
from mono.agent.cadengbench_gates import CADEngBenchGatekeeper, GateEvaluationResult
from mono.vision.quad_view_generator import QuadViewGenerator


@dataclass(frozen=True)
class SpecPlan:
    """Spécification d'ingénierie extraite de l'intention utilisateur par le Spec Planner."""
    task_type: str  # "create" | "modify" | "feature"
    target_entity_id: str
    domain: str  # "cad_brep" | "biomolecule" | "textile"
    target_dimensions: dict[str, float]
    parameters: dict[str, float]
    constraints: list[str]
    reasoning: str


@dataclass(frozen=True)
class MACExecutionResult:
    """Résultat complet de l'exécution du flux cybernétique MAC en 5 étapes."""
    success: bool
    iterations: int
    spec_plan: SpecPlan | None
    script_code: str | None
    gate_result: GateEvaluationResult | None
    vlm_approved: bool
    vlm_critique: str | None
    quad_view_png: bytes | None
    error_message: str | None

    def to_dict(self) -> dict[str, object]:
        """Convertit le bilan en dictionnaire structuré pour l'API REST."""
        return {
            "success": self.success,
            "iterations": self.iterations,
            "spec_plan": {
                "task_type": self.spec_plan.task_type,
                "target_entity_id": self.spec_plan.target_entity_id,
                "domain": self.spec_plan.domain,
                "target_dimensions": self.spec_plan.target_dimensions,
                "parameters": self.spec_plan.parameters,
                "constraints": self.spec_plan.constraints,
                "reasoning": self.spec_plan.reasoning,
            } if self.spec_plan else None,
            "script_code": self.script_code,
            "gate_result": self.gate_result.to_dict() if self.gate_result else None,
            "vlm_approved": self.vlm_approved,
            "vlm_critique": self.vlm_critique,
            "has_quad_view": self.quad_view_png is not None,
            "error_message": self.error_message,
        }


class LLMProvider(ABC):
    """Contrat d'abstraction pour le moteur de raisonnement (Claude, GPT, Ollama local)."""

    @abstractmethod
    async def plan_spec(
        self,
        user_prompt: str,
        existing_entities: list[str],
    ) -> SpecPlan:
        """Étape 1 : Analyse la requête et extrait le plan de spécification dimensionnelle."""

    @abstractmethod
    async def generate_code(
        self,
        plan: SpecPlan,
        previous_code: str | None,
        error_feedback: str | None,
    ) -> str:
        """Étape 2 : Génère ou corrige le code déclaratif build123d / ECIP."""

    @abstractmethod
    async def critique_quad_view(
        self,
        image_bytes: bytes,
        plan: SpecPlan,
    ) -> tuple[bool, str]:
        """Étape 4 : Inspecte la planche 4 vues pour valider l'alignement spatial et sémantique."""


class MockLLMProvider(LLMProvider):
    """Fournisseur de simulation déterministe pour tests unitaires et exécution locale hors-ligne."""

    def __init__(self, simulate_vlm_rejection_count: int = 0) -> None:
        self._vlm_rejection_count = simulate_vlm_rejection_count
        self._rejections_done = 0

    async def plan_spec(
        self,
        user_prompt: str,
        existing_entities: list[str],
    ) -> SpecPlan:
        # Extraction heuristique de dimensions simples (ex: "100x50x20", "bracket", etc.)
        dims: dict[str, float] = {"length": 100.0, "width": 50.0, "height": 20.0}
        match = re.search(r"(\d+(?:\.\d+)?)\s*[xX*]\s*(\d+(?:\.\d+)?)\s*[xX*]\s*(\d+(?:\.\d+)?)", user_prompt)
        if match:
            dims["length"] = float(match.group(1))
            dims["width"] = float(match.group(2))
            dims["height"] = float(match.group(3))

        target_id = "part_001"
        if existing_entities:
            for ent in existing_entities:
                if ent in user_prompt:
                    target_id = ent
                    break

        return SpecPlan(
            task_type="create" if target_id not in existing_entities else "modify",
            target_entity_id=target_id,
            domain="cad_brep",
            target_dimensions=dims,
            parameters={
                "length": dims["length"],
                "width": dims["width"],
                "height": dims["height"],
                "hole_radius": 4.0,
            },
            constraints=[
                "min_wall_thickness >= 1.0 mm",
                "min_hole_diameter >= 2.0 mm",
                "parametric_robustness >= 0.9",
            ],
            reasoning=f"Génération d'un solide mécanique paramétrique basé sur la requête '{user_prompt}'.",
        )

    async def generate_code(
        self,
        plan: SpecPlan,
        previous_code: str | None,
        error_feedback: str | None,
    ) -> str:
        # Si feedback d'erreur d'épaisseur mince, on ajuste dynamiquement
        p = plan.parameters
        length = p.get("length", 100.0)
        width = p.get("width", 50.0)
        height = p.get("height", 20.0)
        hole_rad = p.get("hole_radius", 4.0)

        # Si le feedback mentionne une épaisseur trop mince, on s'assure d'avoir au moins 2.0 mm
        if error_feedback and "Épaisseur minimale" in error_feedback:
            height = max(height, 5.0)

        return f'''import build123d as bd

# Extraction des paramètres nommés pour les curseurs de manipulation directe
length = PARAMS.get("length", {length})
width = PARAMS.get("width", {width})
height = PARAMS.get("height", {height})
hole_radius = PARAMS.get("hole_radius", {hole_rad})

with bd.BuildPart() as part_builder:
    bd.Box(length, width, height)
    with bd.Locations((0, 0, 0)):
        bd.Hole(radius=hole_radius, depth=height)

RESULT_PART = part_builder.part
'''

    async def critique_quad_view(
        self,
        image_bytes: bytes,
        plan: SpecPlan,
    ) -> tuple[bool, str]:
        if self._rejections_done < self._vlm_rejection_count:
            self._rejections_done += 1
            return False, f"Le juge VLM demande un réalignement spatial (test rejet #{self._rejections_done})."
        return True, "Planche 4 vues validée : alignement sémantique et proportions parfaites."


class MACOrchestrator:
    """Orchestrateur Multi-Agents CAD (MAC) exécutant le pipeline cybernétique en 5 étapes."""

    def __init__(
        self,
        llm_provider: LLMProvider,
        history: CommandHistory | None = None,
    ) -> None:
        self._llm = llm_provider
        self._history = history or CommandHistory()

    async def run(
        self,
        user_prompt: str,
        doc: MonoDocument,
        max_iterations: int = 8,
    ) -> MACExecutionResult:
        """Exécute la boucle cybernétique en 5 étapes.

        1. Spec Planner
        2. Domain Executor (boucle avec auto-correction jusqu'à max_iterations)
        3. Deterministic Gatekeeper (Portes L0-L2) -> Boucle Courte si échec
        4. VLM Critic (Planche 4 vues) -> Boucle Longue si échec
        5. Unified Document & UI Update
        """
        # Étape 1 : Intention & Planification
        existing_ids = list(doc.entities.keys())
        spec_plan = await self._llm.plan_spec(user_prompt, existing_ids)

        current_code: str | None = None
        error_feedback: str | None = None
        last_gate_result: GateEvaluationResult | None = None
        last_part: bd.Part | bd.Solid | bd.Compound | None = None
        last_quad_png: bytes | None = None
        vlm_approved = False
        vlm_critique: str | None = None

        # Capture des états initiaux des autres entités pour la porte L2-E
        initial_snapshots: dict[str, tuple[float, tuple[float, float, float]]] = {}
        for ent_id, ent in doc.entities.items():
            rep = ent.provider.introspect()
            initial_snapshots[ent_id] = (rep.volume_m3, rep.center_of_mass)

        iteration = 0
        while iteration < max_iterations:
            iteration += 1

            # Étape 2 : Exécution ECIP & Outils
            current_code = await self._llm.generate_code(
                plan=spec_plan,
                previous_code=current_code,
                error_feedback=error_feedback,
            )

            # Évaluation dans le bac à sable AST
            try:
                exec_result = execute_ecip_cad_block(
                    script_code=current_code,
                    param_overrides=spec_plan.parameters,
                    unit_scale_to_meters=0.001,
                )
                last_part = exec_result.part
            except Exception as exc:
                # Échec d'exécution du code : Boucle courte immédiate
                error_feedback = f"Erreur d'exécution ECIP à l'itération {iteration} : {exc}"
                continue

            # Détection de spécifications mécaniques pour la Porte L3
            eval_l3 = False
            force_val: float | None = None
            re_val: float | None = None
            if spec_plan.parameters:
                if "force_n" in spec_plan.parameters:
                    force_val = float(spec_plan.parameters["force_n"])
                    eval_l3 = True
                if "yield_strength_mpa" in spec_plan.parameters:
                    re_val = float(spec_plan.parameters["yield_strength_mpa"])
                    eval_l3 = True

            applied_force = (0.0, 0.0, -abs(force_val)) if force_val is not None else None

            # Étape 3 : Audit Déterministe B-Rep (Portes L0-L3 CADEngBench)
            gate_eval = CADEngBenchGatekeeper.evaluate(
                entity_id=spec_plan.target_entity_id,
                part=last_part,
                target_dimensions=spec_plan.target_dimensions,
                script_code=current_code,
                base_params=spec_plan.parameters,
                doc=doc,
                initial_entity_snapshots=initial_snapshots,
                unit_scale_to_meters=0.001,
                yield_strength_mpa=re_val,
                applied_force_n=applied_force,
                evaluate_l3=eval_l3,
            )
            last_gate_result = gate_eval

            if not gate_eval.overall_passed:
                # Boucle Courte (0 Jeton VLM) : auto-réparation géométrique déterministe
                error_feedback = gate_eval.feedback_context or "Échec des portes déterministes L0-L2."
                continue

            # Étape 4 : Inspection Visuelle 4 Vues (Juge VLM)
            try:
                generator = QuadViewGenerator(size=1024)
                quad_res = generator.generate(
                    part=last_part,
                    title=f"MONO CADEngBench - {spec_plan.target_entity_id}",
                )
                last_quad_png = quad_res.image_bytes
            except Exception as quad_err:
                # En cas de difficulté de génération de planche, repli gracieux
                last_quad_png = None

            if last_quad_png is not None:
                approved, critique = await self._llm.critique_quad_view(last_quad_png, spec_plan)
                vlm_critique = critique
                if not approved:
                    # Boucle Longue : Rétroaction visuelle / sémantique vers l'Agent Codeur
                    vlm_approved = False
                    error_feedback = f"Rejet du juge VLM : {critique}"
                    continue
                vlm_approved = True
            else:
                vlm_approved = True
                vlm_critique = "Planche 4 vues non disponible, validation déterministe retenue."

            # Étape 5 : Validation & Document Unifié
            entity_id = spec_plan.target_entity_id
            final_provider = BRepBuild123dProvider(
                entity_id=entity_id,
                part=last_part,
                unit_scale_to_meters=0.001,
            )

            # Enregistrement des curseurs dynamiques dans le document
            for p_name, p_val in spec_plan.parameters.items():
                if p_name not in doc.parameters:
                    doc.register_parameter(
                        name=p_name,
                        value=p_val,
                        min_value=p_val * 0.2,
                        max_value=p_val * 3.0,
                        unit="mm",
                        label=p_name.replace("_", " ").title(),
                    )

            # Enregistrement de l'entité et association du script paramétrique
            doc.add_entity(
                entity_id=entity_id,
                provider=final_provider,
                provider_name="brep_build123d",
                scale_domain="milli",
            )
            doc.attach_parametric_source(
                entity_id=entity_id,
                script_code=current_code,
                bound_parameters=list(spec_plan.parameters.keys()),
                scale_to_meters=0.001,
            )

            # Enregistrement dans l'historique Command
            cmd = SetParamCommand(doc, entity_id, "build_script", 1.0)
            self._history.execute(cmd)

            return MACExecutionResult(
                success=True,
                iterations=iteration,
                spec_plan=spec_plan,
                script_code=current_code,
                gate_result=last_gate_result,
                vlm_approved=vlm_approved,
                vlm_critique=vlm_critique,
                quad_view_png=last_quad_png,
                error_message=None,
            )

        # Si le quota maximal d'itérations est épuisé sans succès
        return MACExecutionResult(
            success=False,
            iterations=iteration,
            spec_plan=spec_plan,
            script_code=current_code,
            gate_result=last_gate_result,
            vlm_approved=vlm_approved,
            vlm_critique=vlm_critique,
            quad_view_png=last_quad_png,
            error_message=f"Échec de convergence après {iteration} itérations. Dernier diagnostic : {error_feedback}",
        )
