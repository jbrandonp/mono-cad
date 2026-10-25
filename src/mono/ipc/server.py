from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import AsyncIterator
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Response
from pydantic import BaseModel

from mono.core.document import MonoDocument
from mono.ipc.websocket_manager import WebSocketIPCManager
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

    except WebSocketDisconnect:
        state.ipc.disconnect(websocket)
