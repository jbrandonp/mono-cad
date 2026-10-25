from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import AsyncIterator
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Response
from pydantic import BaseModel

from mono.core.document import MonoDocument
from mono.ipc.websocket_manager import WebSocketIPCManager
from mono.agent.cadengbench_gates import CADEngBenchGatekeeper, GateEvaluationResult
from mono.agent.mac_orchestrator import MACOrchestrator, MockLLMProvider, MACExecutionResult
from mono.execution.ecip_executor import execute_ecip_cad_block, ECIPExecutionError
from mono.providers.brep_build123d import BRepBuild123dProvider
from mono.security.ast_validator import SecurityViolationError
from mono.vision.quad_view_generator import QuadViewGenerator


class ECIPExecuteRequest(BaseModel):
    """Schéma de requête pour l'exécution d'un bloc de code ECIP."""

    entity_id: str
    script_code: str
    param_overrides: dict[str, float] | None = None
    scale_to_meters: float = 0.001


class ParameterUpdateRequest(BaseModel):
    """Schéma de requête pour la modification d'un curseur paramétrique."""

    value: float


class GateEvaluationRequest(BaseModel):
    """Schéma de requête pour l'audit des portes déterministes CADEngBench."""

    entity_id: str
    target_dimensions: dict[str, float] | None = None


class AgentRunRequest(BaseModel):
    """Schéma de requête pour l'exécution de la boucle d'agent MAC."""

    prompt: str
    max_iterations: int = 8


class FEASimulateRequest(BaseModel):
    """Schéma de requête pour l'exécution d'une simulation éléments finis FEA."""

    entity_id: str
    material_name: str = "ALUMINUM_6061_T6"
    young_modulus_gpa: float = 70.0
    poisson_ratio: float = 0.33
    yield_strength_mpa: float = 275.0
    force_x_n: float = 0.0
    force_y_n: float = 0.0
    force_z_n: float = -2500.0
    fixed_predicate: str = "min_z"
    load_predicate: str = "max_z"
    element_size_max: float = 6.0
    target_safety_factor: float = 1.5


class ServerState:
    """Conteneur d'état global du serveur backend local."""

    def __init__(self) -> None:
        self.doc: MonoDocument = MonoDocument()
        self.ipc: WebSocketIPCManager = WebSocketIPCManager()


state = ServerState()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Gestionnaire de cycle de vie du serveur FastAPI."""
    # Démarrage
    yield
    # Arrêt / nettoyage


app = FastAPI(
    title="Mono Engineering Backend",
    version="2.0",
    description="Backend local de l'environnement d'ingénierie multidisciplinaire unifié Mono",
    lifespan=lifespan,
)


@app.get("/health")
def health_check() -> dict[str, str]:
    """Endpoint de santé pour la vérification du serveur."""
    return {"status": "ok", "version": "2.0", "service": "mono-backend"}


@app.get("/api/scene")
def get_scene_summary() -> dict[str, object]:
    """Retourne l'arbre sémantique et la liste des entités du document actuel."""
    return state.doc.to_semantic_summary()


@app.get("/api/scene/quad-view")
def get_scene_quad_view() -> Response:
    """Génère la planche composite 4 vues orthographiques/isométrique (1024x1024) pour le juge VLM."""
    if not state.doc.entities:
        raise HTTPException(status_code=404, detail="Aucune entité géométrique dans la scène.")

    # Sélection de la première entité B-Rep disponible
    target_provider = None
    for rec in state.doc.entities.values():
        if isinstance(rec.provider, BRepBuild123dProvider):
            target_provider = rec.provider
            break

    if target_provider is None:
        raise HTTPException(status_code=400, detail="Aucune entité B-Rep éligible pour l'inspection 4 vues.")

    generator = QuadViewGenerator(size=1024)
    result = generator.generate(target_provider.part, title="MONO CADEngBench Inspection")
    return Response(content=result.image_bytes, media_type="image/png")


@app.post("/api/ecip/execute")
def execute_ecip(req: ECIPExecuteRequest) -> dict[str, object]:
    """Exécute un script CAO ECIP dans le bac à sable et instancie l'entité géométrique.

    Args:
        req: Requête contenant le code et l'identifiant d'entité.

    Returns:
        dict[str, object]: Rapport d'exécution L0 et dimensions.
    """
    try:
        result = execute_ecip_cad_block(
            script_code=req.script_code,
            param_overrides=req.param_overrides,
            unit_scale_to_meters=req.scale_to_meters,
        )
    except SecurityViolationError as sec_err:
        raise HTTPException(status_code=403, detail=str(sec_err)) from sec_err
    except ECIPExecutionError as exec_err:
        raise HTTPException(status_code=400, detail=str(exec_err)) from exec_err

    # Enregistrement des paramètres dynamiques exposés par le script
    for param_name, param_val in result.parameters.items():
        if param_name not in state.doc.parameters:
            state.doc.register_parameter(
                name=param_name,
                value=param_val,
                min_value=param_val * 0.2,
                max_value=param_val * 3.0,
                unit="mm",
                label=param_name.replace("_", " ").title(),
            )

    # Création du provider et ajout au document
    provider = BRepBuild123dProvider(
        entity_id=req.entity_id,
        part=result.part,
        unit_scale_to_meters=req.scale_to_meters,
    )
    state.doc.add_entity(
        entity_id=req.entity_id,
        provider=provider,
        provider_name="brep_build123d",
        scale_domain="milli",
    )
    state.doc.attach_parametric_source(
        entity_id=req.entity_id,
        script_code=req.script_code,
        bound_parameters=list(result.parameters.keys()),
        scale_to_meters=req.scale_to_meters,
    )

    return {
        "entity_id": req.entity_id,
        "is_valid_l0": result.is_valid_l0,
        "volume_m3": result.volume_m3,
        "bbox_dims": result.bbox_dims,
        "parameters": result.parameters,
        "named_faces": result.named_faces,
    }


@app.post("/api/parameters/{name}")
def update_parameter(name: str, req: ParameterUpdateRequest) -> dict[str, object]:
    """Met à jour un paramètre interactif depuis l'API REST."""
    try:
        state.doc.update_parameter(name, req.value)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"status": "updated", "name": name, "value": state.doc.parameters[name].value}


@app.post("/api/gates/evaluate")
def evaluate_gates(req: GateEvaluationRequest) -> dict[str, object]:
    """Évalue les 4 portes déterministes CADEngBench (L0–L2) sur une entité."""
    if req.entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail=f"Entité '{req.entity_id}' introuvable dans la scène.")

    ent = state.doc.entities[req.entity_id]
    part = getattr(ent.provider, "_part", None)
    if part is None:
        raise HTTPException(status_code=400, detail=f"L'entité '{req.entity_id}' ne possède pas de géométrie B-Rep valide.")

    base_params = {p: state.doc.parameters[p].value for p in ent.bound_parameters if p in state.doc.parameters}
    res = CADEngBenchGatekeeper.evaluate(
        entity_id=req.entity_id,
        part=part,
        target_dimensions=req.target_dimensions,
        script_code=ent.script_code,
        base_params=base_params,
        doc=state.doc,
    )
    return res.to_dict()


@app.post("/api/agent/run")
async def run_agent(req: AgentRunRequest) -> dict[str, object]:
    """Exécute l'orchestrateur cybernétique Multi-Agents CAD (MAC) en 5 étapes."""
    orchestrator = MACOrchestrator(
        llm_provider=MockLLMProvider(),
        history=state.doc.history,
    )
    result = await orchestrator.run(
        user_prompt=req.prompt,
        doc=state.doc,
        max_iterations=req.max_iterations,
    )

    # Si succès, diffusion immédiate des nouveaux maillages binaires
    if result.success:
        payloads = state.doc.collect_dirty_binary_payloads()
        for payload in payloads:
            await state.ipc.broadcast_binary_mesh(payload)

    return result.to_dict()


@app.post("/api/fea/simulate")
async def simulate_fea(req: FEASimulateRequest) -> dict[str, object]:
    """Lance une simulation structurelle FEA (Gmsh C3D10 + CalculiX ccx) sur une entité."""
    if req.entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail=f"Entité '{req.entity_id}' introuvable.")

    from mono.providers.fea_gmsh import FEAMaterial, FEABoundaryCondition, FEAResult

    mat = FEAMaterial(
        name=req.material_name,
        young_modulus_gpa=req.young_modulus_gpa,
        poisson_ratio=req.poisson_ratio,
        yield_strength_mpa=req.yield_strength_mpa,
    )
    bc = FEABoundaryCondition(
        fixed_predicate=req.fixed_predicate,
        load_predicate=req.load_predicate,
        applied_force_n=(req.force_x_n, req.force_y_n, req.force_z_n),
    )

    try:
        raw_res = state.doc.run_fea_simulation(
            entity_id=req.entity_id,
            material=mat,
            boundary=bc,
            element_size_max=req.element_size_max,
            target_safety_factor=req.target_safety_factor,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Échec simulation FEA : {exc}") from exc

    fea_res = raw_res if isinstance(raw_res, FEAResult) else None
    assert fea_res is not None

    # Diffusion immédiate du maillage avec le champ scalaire de Von Mises sur le WebSocket
    payloads = state.doc.collect_dirty_binary_payloads()
    for payload in payloads:
        await state.ipc.broadcast_binary_mesh(payload)

    return {
        "entity_id": req.entity_id,
        "max_von_mises_mpa": fea_res.max_von_mises_mpa,
        "mean_von_mises_mpa": fea_res.mean_von_mises_mpa,
        "max_displacement_mm": fea_res.max_displacement_mm,
        "safety_factor": fea_res.safety_factor,
        "is_structurally_safe": fea_res.is_structurally_safe,
        "num_nodes": fea_res.num_nodes,
        "num_elements": fea_res.num_elements,
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Point de terminaison WebSocket pour la communication réactive temps réel avec Tauri/Three.js."""
    await state.ipc.connect(websocket)
    try:
        # Envoi initial de l'état de la scène
        await state.ipc.send_json(
            websocket,
            {
                "type": "welcome",
                "message": "Connecté au backend Mono v2.0",
                "scene": state.doc.to_semantic_summary(),
            },
        )

        while True:
            text_data = await websocket.receive_text()
            try:
                msg = json.loads(text_data)
            except json.JSONDecodeError:
                await state.ipc.send_json(websocket, {"type": "error", "message": "Format JSON invalide"})
                continue

            msg_type = msg.get("type")

            if msg_type == "get_scene":
                await state.ipc.send_json(
                    websocket,
                    {"type": "scene_summary", "scene": state.doc.to_semantic_summary()},
                )

            elif msg_type == "update_param":
                p_name = str(msg.get("name", ""))
                p_val = float(msg.get("value", 0.0))
                if p_name in state.doc.parameters:
                    state.doc.update_parameter(p_name, p_val)
                    # Diffusion immédiate des maillages recalculés par re-tessellation B-Rep (< 50 ms)
                    payloads = state.doc.collect_dirty_binary_payloads()
                    for payload in payloads:
                        await state.ipc.broadcast_binary_mesh(payload)

                    await state.ipc.broadcast_json({
                        "type": "param_updated",
                        "name": p_name,
                        "value": state.doc.parameters[p_name].value,
                    })

            elif msg_type == "execute_ecip":
                entity_id = str(msg.get("entity_id", "part_001"))
                script_code = str(msg.get("script_code", ""))
                overrides = msg.get("parameters")
                param_dict = {str(k): float(v) for k, v in overrides.items()} if isinstance(overrides, dict) else None

                try:
                    result = execute_ecip_cad_block(script_code, param_overrides=param_dict)
                    provider = BRepBuild123dProvider(entity_id=entity_id, part=result.part)
                    state.doc.add_entity(entity_id, provider)
                    state.doc.attach_parametric_source(
                        entity_id=entity_id,
                        script_code=script_code,
                        bound_parameters=list(result.parameters.keys()),
                    )

                    # Transmission des maillages binaires
                    payloads = state.doc.collect_dirty_binary_payloads()
                    for payload in payloads:
                        await state.ipc.broadcast_binary_mesh(payload)

                    await state.ipc.broadcast_json({
                        "type": "execution_success",
                        "entity_id": entity_id,
                        "is_valid_l0": result.is_valid_l0,
                        "volume_m3": result.volume_m3,
                    })
                except Exception as exc:
                    await state.ipc.send_json(websocket, {
                        "type": "execution_failed",
                        "error": str(exc),
                    })

            elif msg_type == "undo":
                state.doc.history.undo()
                await state.ipc.broadcast_json({"type": "undo_performed", "can_undo": state.doc.history.can_undo})

            elif msg_type == "redo":
                state.doc.history.redo()
                await state.ipc.broadcast_json({"type": "redo_performed", "can_redo": state.doc.history.can_redo})

            elif msg_type == "run_agent":
                prompt_text = str(msg.get("prompt", ""))
                max_iter = int(msg.get("max_iterations", 8))
                orchestrator = MACOrchestrator(
                    llm_provider=MockLLMProvider(),
                    history=state.doc.history,
                )
                agent_res = await orchestrator.run(
                    user_prompt=prompt_text,
                    doc=state.doc,
                    max_iterations=max_iter,
                )
                if agent_res.success:
                    dirty_payloads = state.doc.collect_dirty_binary_payloads()
                    for payload in dirty_payloads:
                        await state.ipc.broadcast_binary_mesh(payload)

                await state.ipc.send_json(websocket, {
                    "type": "agent_result",
                    "result": agent_res.to_dict(),
                })

            elif msg_type == "run_fea":
                eid = str(msg.get("entity_id", "part_001"))
                if eid in state.doc.entities:
                    from mono.providers.fea_gmsh import FEAMaterial, FEABoundaryCondition, FEAResult
                    mat = FEAMaterial(
                        young_modulus_gpa=float(msg.get("young_modulus_gpa", 70.0)),
                        yield_strength_mpa=float(msg.get("yield_strength_mpa", 275.0)),
                    )
                    bc = FEABoundaryCondition(
                        applied_force_n=(
                            float(msg.get("force_x", 0.0)),
                            float(msg.get("force_y", 0.0)),
                            float(msg.get("force_z", -2500.0)),
                        )
                    )
                    try:
                        raw_fres = state.doc.run_fea_simulation(
                            entity_id=eid,
                            material=mat,
                            boundary=bc,
                            element_size_max=float(msg.get("element_size_max", 6.0)),
                        )
                        f_res = raw_fres if isinstance(raw_fres, FEAResult) else None
                        assert f_res is not None

                        # Diffusion des nouveaux maillages binaires avec champ scalaire
                        dirty_payloads = state.doc.collect_dirty_binary_payloads()
                        for payload in dirty_payloads:
                            await state.ipc.broadcast_binary_mesh(payload)

                        await state.ipc.send_json(websocket, {
                            "type": "fea_completed",
                            "entity_id": eid,
                            "max_von_mises_mpa": f_res.max_von_mises_mpa,
                            "max_displacement_mm": f_res.max_displacement_mm,
                            "safety_factor": f_res.safety_factor,
                            "is_structurally_safe": f_res.is_structurally_safe,
                        })
                    except Exception as err:
                        await state.ipc.send_json(websocket, {
                            "type": "fea_failed",
                            "error": str(err),
                        })

    except WebSocketDisconnect:
        state.ipc.disconnect(websocket)
