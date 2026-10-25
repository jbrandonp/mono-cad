from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import AsyncIterator
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Response
from pydantic import BaseModel

from mono.core.document import MonoDocument, EntityRecord
from mono.ipc.websocket_manager import WebSocketIPCManager
from mono.agent.cadengbench_gates import CADEngBenchGatekeeper, GateEvaluationResult
from mono.agent.mac_orchestrator import MACOrchestrator, MockLLMProvider, MACExecutionResult
from mono.execution.ecip_executor import execute_ecip_cad_block, ECIPExecutionError
from mono.providers.brep_build123d import BRepBuild123dProvider
from mono.providers.bim_ifc import BimIfcProvider
from mono.providers.textile_xpbd import TextileXpbdProvider
from mono.providers.biomolecule_gemmi import BiomoleculeProvider
from mono.providers.additive_slicer import AdditiveSlicerProvider
from mono.providers.cam_machining import CamMachiningProvider, ToolDefinition, MachiningParameters
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


class TextileCreateRequest(BaseModel):
    """Schéma de requête pour l'instanciation d'un textile GPU XPBD."""

    entity_id: str = "textile_001"
    res_x: int = 30
    res_y: int = 30
    spacing: float = 0.03
    compliance_stretch: float = 1e-6
    compliance_bending: float = 5e-4
    center_y: float = 1.0
    orientation: str = "horizontal"
    pin: str = "top"


class TextileStepRequest(BaseModel):
    """Schéma de requête pour faire avancer la simulation textile."""

    entity_id: str = "textile_001"
    n_frames: int = 1
    ground_y: float = 0.0
    gravity_y: float = -9.81
    wind_x: float = 0.0
    wind_y: float = 0.0
    wind_z: float = 0.0


class BiomoleculeLoadRequest(BaseModel):
    """Schéma de requête pour le chargement d'une structure protéique ou acide nucléique."""

    entity_id: str = "protein_001"
    pdb_content: str | None = None
    pdb_path: str | None = None
    mode: str = "ball_and_stick"


class SlicerAuditRequest(BaseModel):
    """Schéma de requête pour l'audit DfAM de fabrication additive."""

    entity_id: str = "part_001"
    max_overhang_angle_deg: float = 45.0
    nozzle_diameter_mm: float = 0.4


class SlicerSliceRequest(BaseModel):
    """Schéma de requête pour le tranchage et la génération de G-code."""

    entity_id: str = "part_001"
    layer_height_mm: float = 0.20
    infill_percent: int = 20
    nozzle_temp_c: int = 215
    bed_temp_c: int = 60


class CamGenerateRequest(BaseModel):
    """Schéma de requête pour la génération de trajectoires FAO 5-axes."""

    entity_id: str = "part_001"
    tool_diameter_mm: float = 10.0
    tool_length_mm: float = 75.0
    spindle_rpm: float = 12000.0
    feedrate_mm_min: float = 3600.0
    target_roughness_ra_um: float = 0.8
    strategy: str = "finishing_scallop"


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


@app.get("/api/bim/floorplan")
def get_bim_floorplan(entity_id: str | None = None, elevation_z: float = 1.2) -> Response:
    """Génère la planche 2D de coupe d'étage architecturale (SVG) via l'algorithme HLR."""
    target_provider = None
    if entity_id and entity_id in state.doc.entities:
        rec = state.doc.entities[entity_id]
        if isinstance(rec.provider, BimIfcProvider):
            target_provider = rec.provider
    else:
        for rec in state.doc.entities.values():
            if isinstance(rec.provider, BimIfcProvider):
                target_provider = rec.provider
                break

    if target_provider is None:
        raise HTTPException(status_code=404, detail="Aucun modèle BIM IFC trouvé dans la scène.")

    floorplan = target_provider.generate_2d_floorplan(elevation_z=elevation_z)
    return Response(content=floorplan.svg_content, media_type="image/svg+xml")


@app.post("/api/textile/create")
async def create_textile(req: TextileCreateRequest) -> dict[str, object]:
    """Instancie un textile ou une membrane souple simulée par GPU XPBD."""
    provider = TextileXpbdProvider(
        res_x=req.res_x,
        res_y=req.res_y,
        spacing=req.spacing,
        compliance_stretch=req.compliance_stretch,
        compliance_bending=req.compliance_bending,
    )
    if req.orientation == "vertical":
        provider.reset_vertical(center_y=req.center_y)
    else:
        provider.reset_horizontal(center_y=req.center_y)

    if req.pin == "top":
        provider.pin_top_edge()
    elif req.pin == "corners":
        provider.pin_corners()
    elif req.pin == "none":
        provider.unpin_all()

    state.doc.entities[req.entity_id] = EntityRecord(
        entity_id=req.entity_id,
        provider_name="textile_xpbd",
        scale_domain="meter",
        provider=provider,
        tags=["textile", "xpbd", "gpu"],
    )
    return {
        "status": "created",
        "entity_id": req.entity_id,
        "particles": provider.engine.n_particles,
        "triangles": len(provider.indices),
    }


@app.post("/api/textile/step")
async def step_textile(req: TextileStepRequest) -> dict[str, object]:
    """Avance d'un ou plusieurs pas temporels la simulation textile XPBD."""
    if req.entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail="Entité textile non trouvée dans la scène.")

    rec = state.doc.entities[req.entity_id]
    if not isinstance(rec.provider, TextileXpbdProvider):
        raise HTTPException(status_code=400, detail="L'entité spécifiée n'est pas un textile XPBD.")

    if req.wind_x != 0.0 or req.wind_y != 0.0 or req.wind_z != 0.0:
        rec.provider.set_wind(req.wind_x, req.wind_y, req.wind_z)

    rec.provider.step(
        n_frames=req.n_frames,
        ground_y=req.ground_y,
        gravity_y=req.gravity_y,
    )
    rec.is_dirty = True
    return {
        "status": "stepped",
        "entity_id": req.entity_id,
        "n_frames": req.n_frames,
    }


@app.post("/api/biomolecule/load")
def load_biomolecule(req: BiomoleculeLoadRequest) -> dict[str, object]:
    """Charge une macromolécule biologique 3D (PDB / mmCIF) dans la scène."""
    mode_str: Literal["ball_and_stick", "ribbon", "spheres", "sticks"] = (
        req.mode if req.mode in ("ball_and_stick", "ribbon", "spheres", "sticks") else "ball_and_stick"
    )
    provider = BiomoleculeProvider(
        pdb_content=req.pdb_content,
        pdb_path=req.pdb_path,
        mode=mode_str,
    )
    chains = provider.get_chains()
    primary_chain = chains[0] if chains else "A"
    sequence = provider.get_sequence(primary_chain)
    report = provider.introspect()

    state.doc.entities[req.entity_id] = EntityRecord(
        entity_id=req.entity_id,
        provider_name="biomolecule_gemmi",
        scale_domain="nano",
        provider=provider,
        tags=["biology", "protein", "pdb"],
    )
    return {
        "status": "loaded",
        "entity_id": req.entity_id,
        "chains": chains,
        "sequence": sequence,
        "topology": report.topology_counts,
    }


@app.get("/api/biomolecule/ramachandran")
def get_biomolecule_ramachandran(entity_id: str = "protein_001") -> dict[str, object]:
    """Calcule et renvoie les angles de torsion dièdres (phi, psi) de Ramachandran."""
    if entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail="Entité biomoléculaire non trouvée dans la scène.")

    rec = state.doc.entities[entity_id]
    if not isinstance(rec.provider, BiomoleculeProvider):
        raise HTTPException(status_code=400, detail="L'entité spécifiée n'est pas une biomolécule.")

    points = rec.provider.compute_ramachandran()
    disulfides = rec.provider.find_disulfide_bridges()
    return {
        "entity_id": entity_id,
        "ramachandran_points": [
            {
                "chain": pt.chain,
                "res_name": pt.res_name,
                "res_num": pt.res_num,
                "phi": pt.phi_deg,
                "psi": pt.psi_deg,
                "region": pt.region,
            }
            for pt in points
        ],
        "disulfide_bridges": [
            {
                "chain1": b.chain1,
                "res1": b.res1_num,
                "chain2": b.chain2,
                "res2": b.res2_num,
                "distance": b.distance_angstrom,
            }
            for b in disulfides
        ],
    }


@app.post("/api/slicer/audit")
def audit_slicer(req: SlicerAuditRequest) -> dict[str, object]:
    """Exécute l'audit DfAM sur une pièce du document."""
    if req.entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail="Entité introuvable dans la scène.")

    rec = state.doc.entities[req.entity_id]
    if isinstance(rec.provider, BRepBuild123dProvider):
        slicer_prov = AdditiveSlicerProvider(part=rec.provider.part)
    elif isinstance(rec.provider, AdditiveSlicerProvider):
        slicer_prov = rec.provider
    else:
        raise HTTPException(status_code=400, detail="L'entité n'est pas un solide B-Rep ou tranchable.")

    report = slicer_prov.audit_dfam(
        nozzle_diameter_mm=req.nozzle_diameter_mm,
        max_overhang_angle_deg=req.max_overhang_angle_deg,
    )
    return {
        "entity_id": req.entity_id,
        "overhang_area_mm2": report.overhang_area_mm2,
        "total_area_mm2": report.total_area_mm2,
        "overhang_ratio": report.overhang_ratio,
        "max_overhang_deg": report.max_overhang_angle_deg,
        "fits_build_plate": report.fits_build_plate,
        "dimensions_xyz_mm": report.dimensions_xyz_mm,
        "warnings": report.warnings,
        "is_manufacturable": report.is_manufacturable,
    }


@app.post("/api/slicer/slice")
def slice_model_endpoint(req: SlicerSliceRequest) -> dict[str, object]:
    """Tranche le modèle 3D et retourne l'analyse métrologique du G-code."""
    if req.entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail="Entité introuvable dans la scène.")

    rec = state.doc.entities[req.entity_id]
    if isinstance(rec.provider, BRepBuild123dProvider):
        slicer_prov = AdditiveSlicerProvider(part=rec.provider.part)
    elif isinstance(rec.provider, AdditiveSlicerProvider):
        slicer_prov = rec.provider
    else:
        raise HTTPException(status_code=400, detail="L'entité n'est pas un solide B-Rep ou tranchable.")

    analysis = slicer_prov.slice_to_gcode(
        layer_height_mm=req.layer_height_mm,
        infill_percent=req.infill_percent,
        nozzle_temp_c=req.nozzle_temp_c,
        bed_temp_c=req.bed_temp_c,
    )
    return {
        "entity_id": req.entity_id,
        "layer_count": analysis.layer_count,
        "max_z_mm": analysis.max_z_mm,
        "filament_length_mm": analysis.filament_length_mm,
        "estimated_mass_grams": analysis.estimated_mass_grams,
        "estimated_time_seconds": analysis.estimated_time_seconds,
        "is_valid": analysis.is_valid,
    }


@app.post("/api/cam/generate")
def generate_cam_toolpath(req: CamGenerateRequest) -> dict[str, object]:
    """Génère la trajectoire d'usinage FAO 5-axes RTCP pour une pièce."""
    if req.entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail="Entité introuvable dans la scène.")

    rec = state.doc.entities[req.entity_id]
    if isinstance(rec.provider, BRepBuild123dProvider):
        part = rec.provider.part
    elif isinstance(rec.provider, AdditiveSlicerProvider):
        part = rec.provider.part
    else:
        raise HTTPException(status_code=400, detail="L'entité n'est pas usinable.")

    tool = ToolDefinition(
        diameter_mm=req.tool_diameter_mm,
        length_mm=req.tool_length_mm,
        corner_radius_mm=req.tool_diameter_mm * 0.5,
    )
    params = MachiningParameters(
        spindle_rpm=req.spindle_rpm,
        feedrate_mm_min=req.feedrate_mm_min,
        target_roughness_ra_um=req.target_roughness_ra_um,
    )
    strat = req.strategy if req.strategy in ("roughing_spiral", "finishing_scallop", "5axis_swarf") else "finishing_scallop"
    cam_prov = CamMachiningProvider(part=part, tool=tool, params=params)
    cam_prov.generate_toolpath(strategy=strat)
    audit = cam_prov.audit_cam()

    # Enregistrement de l'entité trajectoire dans la scène
    toolpath_id = f"{req.entity_id}_cam_toolpath"
    state.doc.entities[toolpath_id] = EntityRecord(
        entity_id=toolpath_id,
        provider_name="cam_machining",
        scale_domain="milli",
        provider=cam_prov,
        tags=["cam", "toolpath", "5axis"],
    )

    return {
        "entity_id": req.entity_id,
        "toolpath_entity_id": toolpath_id,
        "total_points": audit.total_points,
        "toolpath_length_mm": audit.toolpath_length_mm,
        "estimated_machining_time_seconds": audit.estimated_machining_time_seconds,
        "optimal_stepover_mm": audit.optimal_stepover_mm,
        "scallop_height_um": audit.scallop_height_um,
        "is_collision_free": audit.is_collision_free,
        "gouge_warnings": audit.gouge_warnings,
    }


@app.get("/api/cam/gcode")
def get_cam_gcode(entity_id: str = "part_001_cam_toolpath", flavor: str = "fanuc") -> Response:
    """Télécharge le programme G-code ISO 6983 post-processé (Fanuc, Siemens, Heidenhain, Haas)."""
    if entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail="Trajectoire FAO introuvable.")

    rec = state.doc.entities[entity_id]
    if not isinstance(rec.provider, CamMachiningProvider):
        raise HTTPException(status_code=400, detail="L'entité spécifiée n'est pas une trajectoire FAO.")

    flv = flavor.lower()
    flavor_literal = flv if flv in ("fanuc", "siemens", "heidenhain", "haas") else "fanuc"
    gcode_text = rec.provider.post_process_gcode(flavor=flavor_literal)
    return Response(content=gcode_text, media_type="text/plain")


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

            elif msg_type == "step_textile":
                eid = str(msg.get("entity_id", "textile_001"))
                n_frames = int(msg.get("n_frames", 1))
                if eid in state.doc.entities:
                    rec = state.doc.entities[eid]
                    if isinstance(rec.provider, TextileXpbdProvider):
                        rec.provider.step(n_frames=n_frames)
                        rec.is_dirty = True
                        payloads = state.doc.collect_dirty_binary_payloads()
                        for payload in payloads:
                            await state.ipc.broadcast_binary_mesh(payload)
                        await state.ipc.send_json(websocket, {
                            "type": "textile_stepped",
                            "entity_id": eid,
                        })

            elif msg_type == "init_textile":
                eid = str(msg.get("entity_id", "textile_001"))
                rx = int(msg.get("res_x", 32))
                ry = int(msg.get("res_y", 32))
                sp = float(msg.get("spacing", 0.03))
                provider = TextileXpbdProvider(res_x=rx, res_y=ry, spacing=sp)
                provider.reset_horizontal(center_y=float(msg.get("center_y", 1.0)))
                if msg.get("pin", "top") == "top":
                    provider.pin_top_edge()
                elif msg.get("pin") == "corners":
                    provider.pin_corners()
                else:
                    provider.unpin_all()

                state.doc.entities[eid] = EntityRecord(
                    entity_id=eid,
                    provider_name="textile_xpbd",
                    scale_domain="meter",
                    provider=provider,
                    tags=["textile", "xpbd", "gpu"],
                )
                payloads = state.doc.collect_dirty_binary_payloads()
                for payload in payloads:
                    await state.ipc.broadcast_binary_mesh(payload)
                await state.ipc.send_json(websocket, {
                    "type": "textile_initialized",
                    "entity_id": eid,
                    "particles": provider.engine.n_particles,
                })

            elif msg_type == "set_textile_wind":
                eid = str(msg.get("entity_id", "textile_001"))
                if eid in state.doc.entities:
                    rec = state.doc.entities[eid]
                    if isinstance(rec.provider, TextileXpbdProvider):
                        rec.provider.set_wind(
                            float(msg.get("vx", 0.0)),
                            float(msg.get("vy", 0.0)),
                            float(msg.get("vz", 0.0)),
                        )
                        await state.ipc.send_json(websocket, {
                            "type": "textile_wind_updated",
                            "entity_id": eid,
                        })

            elif msg_type == "unpin_textile":
                eid = str(msg.get("entity_id", "textile_001"))
                if eid in state.doc.entities:
                    rec = state.doc.entities[eid]
                    if isinstance(rec.provider, TextileXpbdProvider):
                        rec.provider.unpin_all()
                        await state.ipc.send_json(websocket, {
                            "type": "textile_unpinned",
                            "entity_id": eid,
                        })

            elif msg_type == "add_textile_sphere":
                eid = str(msg.get("entity_id", "textile_001"))
                if eid in state.doc.entities:
                    rec = state.doc.entities[eid]
                    if isinstance(rec.provider, TextileXpbdProvider):
                        cx = float(msg.get("cx", 0.0))
                        cy = float(msg.get("cy", 0.45))
                        cz = float(msg.get("cz", 0.0))
                        r = float(msg.get("radius", 0.22))
                        rec.provider.add_sphere_obstacle((cx, cy, cz), r)
                        await state.ipc.send_json(websocket, {
                            "type": "textile_sphere_added",
                            "entity_id": eid,
                        })

            elif msg_type == "drape_on_brep_sdf":
                eid = str(msg.get("entity_id", "textile_001"))
                solid_eid = str(msg.get("solid_entity_id", "part_001"))
                if eid in state.doc.entities and solid_eid in state.doc.entities:
                    rec_cloth = state.doc.entities[eid]
                    rec_solid = state.doc.entities[solid_eid]
                    if isinstance(rec_cloth.provider, TextileXpbdProvider):
                        part = getattr(rec_solid.provider, "part", None)
                        if part is not None:
                            rec_cloth.provider.load_sdf_from_brep(part, resolution=16)
                            await state.ipc.send_json(websocket, {
                                "type": "textile_sdf_loaded",
                                "entity_id": eid,
                                "solid_entity_id": solid_eid,
                            })

    except WebSocketDisconnect:
        state.ipc.disconnect(websocket)
