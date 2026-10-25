from __future__ import annotations

import base64
import hashlib
import json
from contextlib import asynccontextmanager
from typing import AsyncIterator, Literal
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Response
from pydantic import BaseModel, Field

from mono.core.archive_mono import MonoArchive, MonoArchiveError
from mono.core.document import MonoDocument, EntityRecord
from mono.ipc.websocket_manager import WebSocketIPCManager
from mono.agent.cadengbench_gates import CADEngBenchGatekeeper, GateEvaluationResult
from mono.agent.mac_orchestrator import MACOrchestrator, MockLLMProvider, MACExecutionResult
from mono.execution.ecip_executor import execute_ecip_cad_block, ECIPExecutionError
from mono.providers.brep_build123d import BRepBuild123dProvider
from mono.providers.bim_ifc import BimIfcProvider
from mono.providers.textile_xpbd import TextileXpbdProvider
from mono.providers.biomolecule_gemmi import BiomoleculeProvider
from mono.providers.biomolecule_openmm import OpenMMSimulator, OpenMMSimulationResult, TrajectoryFrame
from mono.providers.additive_slicer import AdditiveSlicerProvider
from mono.providers.cam_machining import CamMachiningProvider, ToolDefinition, MachiningParameters
from mono.providers.techdraw_drafting import TechDrawDraftingProvider, TitleBlock, TechDrawReport
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


class TextileObstacleRequest(BaseModel):
    """Schéma de requête pour l'ajout d'obstacles physiques."""

    entity_id: str = "textile_001"
    obstacle_type: Literal["sphere", "box", "cylinder"] = "sphere"
    center: list[float] = Field(default_factory=lambda: [0.0, 0.45, 0.0])
    radius: float = 0.20
    min_corner: list[float] = Field(default_factory=lambda: [-0.1, 0.1, -0.1])
    max_corner: list[float] = Field(default_factory=lambda: [0.1, 0.3, 0.1])
    height: float = 0.5


class TextileDrapeRequest(BaseModel):
    """Schéma de requête pour le drapé automatique au-dessus d'un solide mécanique B-Rep."""

    cloth_entity_id: str = "textile_001"
    solid_entity_id: str = "part_001"
    center_y: float = 1.0
    resolution: int = 16


class BiomoleculeLoadRequest(BaseModel):
    """Schéma de requête pour le chargement d'une structure protéique ou acide nucléique."""

    entity_id: str = "protein_001"
    pdb_code: str | None = None
    pdb_content: str | None = None
    pdb_path: str | None = None
    mode: str = "ribbon"


class BiomoleculeSimulateRequest(BaseModel):
    """Schéma de requête pour la dynamique moléculaire OpenMM."""

    entity_id: str = "protein_001"
    total_steps: int = 100
    report_interval: int = 20
    temperature_k: float = 300.0
    time_step_fs: float = 2.0


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


class TechDrawGenerateRequest(BaseModel):
    """Schéma de requête pour la mise en plan 2D industrielle TechDraw."""

    entity_id: str = "part_001"
    sheet_format: str = "A3"
    orientation: str = "landscape"
    title: str = "PIÈCE INDUSTRIELLE"
    drawing_number: str = "DWG-1001"
    material: str = "Aluminium 6061-T6"
    author: str = "Brandon"
    auto_dimension: bool = True


class MonoArchiveExportRequest(BaseModel):
    """Schéma de requête pour l'export du conteneur unifié .mono."""

    title: str = "Mono Project"
    author: str = "Brandon"
    description: str = "Projet multi-domaines unifié Mono"


class MonoArchiveImportRequest(BaseModel):
    """Schéma de requête pour l'import d'une archive .mono encodée en base64."""

    archive_base64: str


class ServerState:
    """Conteneur d'état global du serveur backend local."""

    def __init__(self) -> None:
        self.doc: MonoDocument = MonoDocument()
        self.ipc: WebSocketIPCManager = WebSocketIPCManager()
        self.active_simulations: dict[str, OpenMMSimulationResult] = {}
        self.last_archive_bytes: bytes = b""


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


@app.get("/api/bim/dxf")
def get_bim_floorplan_dxf(entity_id: str | None = None, elevation_z: float = 1.2) -> Response:
    """Télécharge le plan 2D de coupe d'étage au format vectoriel CAD DXF standard."""
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
    return Response(
        content=floorplan.dxf_content,
        media_type="application/dxf",
        headers={"Content-Disposition": f'attachment; filename="{target_provider.entity_id}_floorplan_z{elevation_z:.2f}.dxf"'},
    )


@app.get("/api/bim/ifc")
def get_bim_ifc(entity_id: str | None = None) -> Response:
    """Exporte et télécharge le modèle architectural au format standard ISO 10303-21 IFC4."""
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

    ifc_text = target_provider.to_ifc_string()
    return Response(
        content=ifc_text,
        media_type="application/x-step",
        headers={"Content-Disposition": f'attachment; filename="{target_provider.entity_id}.ifc"'},
    )


class BimImportRequest(BaseModel):
    entity_id: str = "imported_building"
    ifc_source: str
    project_name: str = "Imported IFC Project"


@app.post("/api/bim/import")
def import_bim_ifc(req: BimImportRequest) -> dict[str, object]:
    """Importe un fichier ou texte IFC4 standard dans la scène."""
    provider = BimIfcProvider(
        entity_id=req.entity_id,
        ifc_source=req.ifc_source,
        project_name=req.project_name,
    )
    report = provider.introspect()
    state.doc.entities[req.entity_id] = EntityRecord(
        entity_id=req.entity_id,
        provider_name="bim_ifc",
        scale_domain="meter",
        provider=provider,
        tags=["bim", "ifc4", "architecture"],
    )
    return {
        "status": "success",
        "entity_id": req.entity_id,
        "is_valid_l0": report.is_valid_l0,
        "storeys": report.topology_counts.get("storeys", 0),
        "walls": report.topology_counts.get("walls", 0),
        "columns": report.topology_counts.get("columns", 0),
        "slabs": report.topology_counts.get("slabs", 0),
        "doors": report.topology_counts.get("doors", 0),
        "windows": report.topology_counts.get("windows", 0),
        "volume_m3": report.volume_m3,
    }


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


@app.post("/api/textile/obstacle")
async def add_textile_obstacle(req: TextileObstacleRequest) -> dict[str, object]:
    """Ajoute un obstacle physique (sphère, boîte AABB ou cylindre) à la simulation textile."""
    if req.entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail="Entité textile non trouvée dans la scène.")

    rec = state.doc.entities[req.entity_id]
    if not isinstance(rec.provider, TextileXpbdProvider):
        raise HTTPException(status_code=400, detail="L'entité spécifiée n'est pas un textile XPBD.")

    if req.obstacle_type == "sphere":
        cx, cy, cz = req.center[0], req.center[1], req.center[2]
        rec.provider.add_sphere_obstacle((cx, cy, cz), req.radius)
    elif req.obstacle_type == "box":
        min_c = (req.min_corner[0], req.min_corner[1], req.min_corner[2])
        max_c = (req.max_corner[0], req.max_corner[1], req.max_corner[2])
        rec.provider.add_box_obstacle(min_c, max_c)
    elif req.obstacle_type == "cylinder":
        cx, cy, cz = req.center[0], req.center[1], req.center[2]
        rec.provider.add_cylinder_obstacle((cx, cy, cz), req.radius, req.height)

    return {
        "status": "obstacle_added",
        "entity_id": req.entity_id,
        "obstacle_type": req.obstacle_type,
    }


@app.post("/api/textile/drape")
async def drape_textile_on_solid(req: TextileDrapeRequest) -> dict[str, object]:
    """Positionne et prépare le drapé automatique du textile au-dessus d'un solide mécanique B-Rep."""
    if req.solid_entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail="Solide B-Rep cible introuvable dans la scène.")

    rec_solid = state.doc.entities[req.solid_entity_id]
    part = getattr(rec_solid.provider, "part", None)
    if part is None:
        raise HTTPException(status_code=400, detail="L'entité spécifiée ne contient pas de solide B-Rep.")

    # Instanciation ou récupération du textile
    if req.cloth_entity_id in state.doc.entities and isinstance(state.doc.entities[req.cloth_entity_id].provider, TextileXpbdProvider):
        provider = state.doc.entities[req.cloth_entity_id].provider
    else:
        provider = TextileXpbdProvider(res_x=32, res_y=32, spacing=0.03)
        state.doc.entities[req.cloth_entity_id] = EntityRecord(
            entity_id=req.cloth_entity_id,
            provider_name="textile_xpbd",
            scale_domain="meter",
            provider=provider,
            tags=["textile", "xpbd", "gpu", "drape"],
        )

    provider.drape_on_solid(part, center_y=req.center_y, resolution=req.resolution)
    return {
        "status": "drape_prepared",
        "cloth_entity_id": req.cloth_entity_id,
        "solid_entity_id": req.solid_entity_id,
        "center_y": req.center_y,
        "resolution": req.resolution,
    }


@app.get("/api/textile/metrics")
async def get_textile_metrics(entity_id: str = "textile_001") -> dict[str, object]:
    """Retourne les métriques physiques temps réel du textile (particules, surface, déformation max)."""
    if entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail="Entité textile non trouvée dans la scène.")

    rec = state.doc.entities[entity_id]
    if not isinstance(rec.provider, TextileXpbdProvider):
        raise HTTPException(status_code=400, detail="L'entité spécifiée n'est pas un textile XPBD.")

    report = rec.provider.introspect()
    vels = rec.provider.get_velocities()
    speeds = np.linalg.norm(vels, axis=1)
    strains = rec.provider.get_strains()

    return {
        "entity_id": entity_id,
        "particles": report.topology_counts.get("particles", 0),
        "triangles": report.topology_counts.get("triangles", 0),
        "surface_area_m2": report.surface_area_m2,
        "center_of_mass": list(report.center_of_mass),
        "bbox_min": list(report.bbox_min),
        "bbox_max": list(report.bbox_max),
        "mean_speed_mps": float(np.mean(speeds)),
        "max_strain": float(np.max(strains)) if len(strains) > 0 else 0.0,
        "is_valid": report.is_valid_l0,
    }


@app.post("/api/biomolecule/load")
def load_biomolecule(req: BiomoleculeLoadRequest) -> dict[str, object]:
    """Charge une macromolécule biologique 3D (PDB / mmCIF) dans la scène."""
    mode_str: Literal["ball_and_stick", "ribbon", "spheres", "sticks"] = (
        req.mode if req.mode in ("ball_and_stick", "ribbon", "spheres", "sticks") else "ribbon"
    )
    provider = BiomoleculeProvider(
        pdb_content=req.pdb_content,
        pdb_path=req.pdb_path,
        pdb_code=req.pdb_code,
        mode=mode_str,
    )
    chains = provider.get_chains()
    primary_chain = chains[0] if chains else "A"
    sequence = provider.get_sequence(primary_chain)
    report = provider.introspect()
    crypto_info = provider.get_crystallographic_info()

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
        "crystallography": crypto_info,
    }


@app.get("/api/biomolecule/crystallography")
def get_biomolecule_crystallography(entity_id: str = "protein_001") -> dict[str, object]:
    """Renvoie les données cristallographiques de la structure (maille, groupe spatial, résolution)."""
    if entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail="Entité biomoléculaire non trouvée dans la scène.")

    rec = state.doc.entities[entity_id]
    if not isinstance(rec.provider, BiomoleculeProvider):
        raise HTTPException(status_code=400, detail="L'entité spécifiée n'est pas une biomolécule.")

    return rec.provider.get_crystallographic_info()


@app.get("/api/biomolecule/pdb")
def get_biomolecule_pdb(entity_id: str = "protein_001") -> Response:
    """Exporte la structure au format PDB textuel standard."""
    if entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail="Entité introuvable dans la scène.")

    rec = state.doc.entities[entity_id]
    if not isinstance(rec.provider, BiomoleculeProvider):
        raise HTTPException(status_code=400, detail="L'entité spécifiée n'est pas une biomolécule.")

    pdb_bytes = rec.provider.serialize_native()
    return Response(
        content=pdb_bytes,
        media_type="chemical/x-pdb",
        headers={"Content-Disposition": f'attachment; filename="{entity_id}.pdb"'},
    )


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


@app.get("/api/biomolecule/bcif")
def get_biomolecule_bcif(entity_id: str = "protein_001") -> Response:
    """Exporte la structure macromoléculaire au format binaire BinaryCIF (.bcif) pour Mol*."""
    if entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail="Entité introuvable dans la scène.")

    rec = state.doc.entities[entity_id]
    if not isinstance(rec.provider, BiomoleculeProvider):
        raise HTTPException(status_code=400, detail="L'entité spécifiée n'est pas une biomolécule.")

    bcif_bytes = rec.provider.serialize_binary_cif()
    return Response(
        content=bcif_bytes,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{entity_id}.bcif"'},
    )


@app.get("/api/biomolecule/cif")
def get_biomolecule_cif(entity_id: str = "protein_001") -> Response:
    """Exporte la structure au format standard mmCIF textuel."""
    if entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail="Entité introuvable dans la scène.")

    rec = state.doc.entities[entity_id]
    if not isinstance(rec.provider, BiomoleculeProvider):
        raise HTTPException(status_code=400, detail="L'entité spécifiée n'est pas une biomolécule.")

    cif_text = rec.provider.serialize_mmcif()
    return Response(
        content=cif_text,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{entity_id}.cif"'},
    )


@app.post("/api/biomolecule/simulate")
def simulate_biomolecule(req: BiomoleculeSimulateRequest) -> dict[str, object]:
    """Lance une simulation de dynamique moléculaire OpenMM sur une structure protéique."""
    if req.entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail=f"Entité '{req.entity_id}' non trouvée.")

    rec = state.doc.entities[req.entity_id]
    if not isinstance(rec.provider, BiomoleculeProvider):
        raise HTTPException(status_code=400, detail="L'entité n'est pas une macromolécule.")

    pdb_text = rec.provider.serialize_native().decode("utf-8")
    sim = OpenMMSimulator(
        pdb_content=pdb_text,
        temperature_k=req.temperature_k,
        time_step_fs=req.time_step_fs,
    )
    sim.minimize_energy(max_iterations=30)
    sim_result = sim.simulate(
        total_steps=req.total_steps,
        report_interval=req.report_interval,
    )
    state.active_simulations[req.entity_id] = sim_result
    return sim_result.to_dict()


@app.get("/api/biomolecule/trajectory/frame")
def get_biomolecule_trajectory_frame(entity_id: str = "protein_001", frame_index: int = 0) -> dict[str, object]:
    """Renvoie les coordonnées 3D et les métriques thermodynamiques d'une frame de trajectoire."""
    if entity_id not in state.active_simulations:
        raise HTTPException(status_code=404, detail=f"Aucune simulation active pour '{entity_id}'.")

    sim_res = state.active_simulations[entity_id]
    if frame_index < 0 or frame_index >= len(sim_res.frames):
        raise HTTPException(status_code=400, detail=f"Index de frame {frame_index} hors bornes (0-{len(sim_res.frames)-1}).")

    frame = sim_res.frames[frame_index]
    if entity_id in state.doc.entities:
        rec = state.doc.entities[entity_id]
        if isinstance(rec.provider, BiomoleculeProvider):
            rec.provider.update_positions(frame.positions)
            rec.is_dirty = True

    return {
        "frame_index": frame.frame_index,
        "time_ps": frame.time_ps,
        "potential_energy_kj_mol": frame.potential_energy_kj_mol,
        "kinetic_energy_kj_mol": frame.kinetic_energy_kj_mol,
        "temperature_k": frame.temperature_k,
        "rmsd_angstrom": frame.rmsd_angstrom,
        "positions": frame.positions.tolist(),
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

    slicer_id = f"{req.entity_id}_slicer"
    state.doc.entities[slicer_id] = EntityRecord(
        entity_id=slicer_id,
        provider_name="additive_slicer",
        scale_domain="milli",
        provider=slicer_prov,
        tags=["slicer", "3dprint"],
    )

    return {
        "entity_id": req.entity_id,
        "slicer_entity_id": slicer_id,
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
    strat_valid = (
        "roughing_spiral",
        "finishing_scallop",
        "drop_cutter_surface",
        "adaptive_pocketing",
        "profile_contouring",
        "probing_cmm",
        "5axis_swarf",
    )
    strat = req.strategy if req.strategy in strat_valid else "finishing_scallop"
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
    """Télécharge le programme G-code ISO 6983 post-processé (Fanuc, Siemens, Heidenhain, Haas, LinuxCNC)."""
    if entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail="Trajectoire FAO introuvable.")

    rec = state.doc.entities[entity_id]
    if not isinstance(rec.provider, CamMachiningProvider):
        raise HTTPException(status_code=400, detail="L'entité spécifiée n'est pas une trajectoire FAO.")

    flv = flavor.lower()
    flavor_valid = ("fanuc", "siemens", "heidenhain", "haas", "linuxcnc")
    flavor_literal: Literal["fanuc", "siemens", "heidenhain", "haas", "linuxcnc"] = (
        flv if flv in flavor_valid else "fanuc"
    )
    gcode_text = rec.provider.post_process_gcode(flavor=flavor_literal)
    return Response(
        content=gcode_text,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{entity_id}_{flavor_literal}.nc"'},
    )


@app.get("/api/slicer/gcode")
def get_slicer_gcode(entity_id: str = "part_001") -> Response:
    """Télécharge le programme G-code FDM/SLA généré par le trancheur 3D."""
    if entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail="Entité introuvable dans la scène.")

    rec = state.doc.entities[entity_id]
    if isinstance(rec.provider, AdditiveSlicerProvider):
        gcode_text = rec.provider.cached_gcode
    elif isinstance(rec.provider, BRepBuild123dProvider):
        slicer = AdditiveSlicerProvider(part=rec.provider.part)
        slicer.slice_to_gcode()
        gcode_text = slicer.cached_gcode
    else:
        raise HTTPException(status_code=400, detail="L'entité spécifiée n'est pas tranchable.")

    if not gcode_text:
        raise HTTPException(status_code=404, detail="Aucun G-code disponible, veuillez d'abord trancher la pièce.")

    return Response(
        content=gcode_text,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{entity_id}_print.gcode"'},
    )


@app.post("/api/techdraw/generate")
def generate_techdraw_endpoint(req: TechDrawGenerateRequest) -> dict[str, object]:
    """Génère la mise en plan 2D industrielle (HLR, cartouche ISO 7200, GD&T, cotation)."""
    if req.entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail="Entité introuvable dans la scène.")

    rec = state.doc.entities[req.entity_id]
    part = getattr(rec.provider, "part", None)
    if part is None:
        raise HTTPException(status_code=400, detail="L'entité ne possède pas de géométrie B-Rep projetable.")

    tb = TitleBlock(
        title=req.title,
        drawing_number=req.drawing_number,
        author=req.author,
        material=req.material,
        scale="1:1",
    )
    sheet_fmt = req.sheet_format.upper()
    valid_fmts = ("A4", "A3", "A2", "A1")
    chosen_fmt = sheet_fmt if sheet_fmt in valid_fmts else "A3"
    chosen_orient = "portrait" if req.orientation.lower() == "portrait" else "landscape"

    provider = TechDrawDraftingProvider(
        entity_id=f"{req.entity_id}_techdraw",
        part=part,
        sheet_format=chosen_fmt,  # type: ignore
        orientation=chosen_orient,  # type: ignore
        title_block=tb,
    )
    report = provider.generate_drawing(auto_dimension=req.auto_dimension)

    td_id = f"{req.entity_id}_techdraw"
    state.doc.entities[td_id] = EntityRecord(
        entity_id=td_id,
        provider_name="techdraw_drafting",
        scale_domain="milli",
        provider=provider,
        tags=["techdraw", "2d", "drawing", "gdt"],
    )

    return {
        "entity_id": req.entity_id,
        "techdraw_entity_id": td_id,
        "sheet_format": report.sheet_format,
        "sheet_width_mm": report.sheet_width_mm,
        "sheet_height_mm": report.sheet_height_mm,
        "views_count": report.views_count,
        "total_visible_segments": report.total_visible_segments,
        "total_hidden_segments": report.total_hidden_segments,
        "dimensions_count": report.dimensions_count,
        "gdt_count": report.gdt_count,
        "svg_url": f"/api/techdraw/svg?entity_id={td_id}",
        "dxf_url": f"/api/techdraw/dxf?entity_id={td_id}",
        "pdf_url": f"/api/techdraw/pdf?entity_id={td_id}",
    }


@app.get("/api/techdraw/svg")
def get_techdraw_svg(entity_id: str = "part_001_techdraw") -> Response:
    """Télécharge ou affiche la planche de dessin technique au format vectoriel SVG."""
    if entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail="Planche TechDraw introuvable.")

    rec = state.doc.entities[entity_id]
    if not isinstance(rec.provider, TechDrawDraftingProvider):
        raise HTTPException(status_code=400, detail="L'entité spécifiée n'est pas un plan TechDraw.")

    svg_content = rec.provider.to_svg()
    return Response(
        content=svg_content,
        media_type="image/svg+xml",
        headers={"Content-Disposition": f'inline; filename="{entity_id}.svg"'},
    )


@app.get("/api/techdraw/dxf")
def get_techdraw_dxf(entity_id: str = "part_001_techdraw") -> Response:
    """Télécharge la planche de dessin technique au format AutoCAD DXF R2010."""
    if entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail="Planche TechDraw introuvable.")

    rec = state.doc.entities[entity_id]
    if not isinstance(rec.provider, TechDrawDraftingProvider):
        raise HTTPException(status_code=400, detail="L'entité spécifiée n'est pas un plan TechDraw.")

    dxf_content = rec.provider.to_dxf()
    return Response(
        content=dxf_content,
        media_type="application/dxf",
        headers={"Content-Disposition": f'attachment; filename="{entity_id}.dxf"'},
    )


@app.get("/api/techdraw/pdf")
def get_techdraw_pdf(entity_id: str = "part_001_techdraw") -> Response:
    """Télécharge la planche de dessin technique cotée au format PDF vectoriel normalisé ISO/ASME."""
    if entity_id not in state.doc.entities:
        raise HTTPException(status_code=404, detail="Planche TechDraw introuvable.")

    rec = state.doc.entities[entity_id]
    if not isinstance(rec.provider, TechDrawDraftingProvider):
        raise HTTPException(status_code=400, detail="L'entité spécifiée n'est pas un plan TechDraw.")

    pdf_content = rec.provider.to_pdf()
    return Response(
        content=pdf_content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{entity_id}.pdf"'},
    )


@app.post("/api/archive/export")
def export_archive_endpoint(req: MonoArchiveExportRequest) -> dict[str, object]:
    """Empaquette la scène complète multi-domaines dans une archive unifiée .mono compressée zstd."""
    meta = {
        "title": req.title,
        "author": req.author,
        "description": req.description,
    }
    archive_bytes = MonoArchive.pack(state.doc, metadata=meta)
    state.last_archive_bytes = archive_bytes
    sha = hashlib.sha256(archive_bytes).hexdigest()

    return {
        "status": "success",
        "archive_size_bytes": len(archive_bytes),
        "archive_sha256": sha,
        "entities_count": len(state.doc.entities),
        "parameters_count": len(state.doc.parameters),
        "download_url": "/api/archive/download",
    }


@app.get("/api/archive/download")
def download_archive_endpoint() -> Response:
    """Télécharge l'archive .mono du projet complet."""
    if not state.last_archive_bytes:
        state.last_archive_bytes = MonoArchive.pack(state.doc, metadata={"title": "Mono Unified Project"})

    return Response(
        content=state.last_archive_bytes,
        media_type="application/zstd",
        headers={"Content-Disposition": 'attachment; filename="project.mono"'},
    )


@app.post("/api/archive/import")
def import_archive_endpoint(req: MonoArchiveImportRequest) -> dict[str, object]:
    """Importe et désarchive un conteneur .mono encodé en base64."""
    try:
        raw_bytes = base64.b64decode(req.archive_base64)
        doc, manifest = MonoArchive.unpack(raw_bytes)
        state.doc = doc
        return {
            "status": "success",
            "title": str(manifest.get("title", "")),
            "author": str(manifest.get("author", "")),
            "entities_restored": len(doc.entities),
            "parameters_restored": len(doc.parameters),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Échec d'import de l'archive .mono : {exc}") from exc


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

            elif msg_type == "load_biomolecule":
                eid = str(msg.get("entity_id", "protein_001"))
                pdb_code = str(msg.get("pdb_code", "1CRN")) if msg.get("pdb_code") else None
                pdb_txt = str(msg.get("pdb_content", "")) if msg.get("pdb_content") else None
                mode = str(msg.get("mode", "ribbon"))
                mode_str: Literal["ball_and_stick", "ribbon", "spheres", "sticks"] = (
                    mode if mode in ("ball_and_stick", "ribbon", "spheres", "sticks") else "ribbon"
                )
                provider = BiomoleculeProvider(
                    pdb_code=pdb_code if not pdb_txt else None,
                    pdb_content=pdb_txt,
                    mode=mode_str,
                )
                state.doc.entities[eid] = EntityRecord(
                    entity_id=eid,
                    provider_name="biomolecule_gemmi",
                    scale_domain="nano",
                    provider=provider,
                    tags=["biology", "protein", "pdb"],
                )
                payloads = state.doc.collect_dirty_binary_payloads()
                for payload in payloads:
                    await state.ipc.broadcast_binary_mesh(payload)
                await state.ipc.send_json(websocket, {
                    "type": "biomolecule_loaded",
                    "entity_id": eid,
                    "chains": provider.get_chains(),
                    "sequence": provider.get_sequence(),
                    "crystallography": provider.get_crystallographic_info(),
                })

            elif msg_type == "step_biomolecule_dynamics":
                eid = str(msg.get("entity_id", "protein_001"))
                frame_idx = int(msg.get("frame_index", 0))
                if eid in state.active_simulations and eid in state.doc.entities:
                    sim_res = state.active_simulations[eid]
                    if 0 <= frame_idx < len(sim_res.frames):
                        frame = sim_res.frames[frame_idx]
                        rec = state.doc.entities[eid]
                        if isinstance(rec.provider, BiomoleculeProvider):
                            rec.provider.update_positions(frame.positions)
                            rec.is_dirty = True
                            dirty_payloads = state.doc.collect_dirty_binary_payloads()
                            for payload in dirty_payloads:
                                await state.ipc.broadcast_binary_mesh(payload)
                            await state.ipc.send_json(websocket, {
                                "type": "biomolecule_stepped",
                                "entity_id": eid,
                                "frame_index": frame_idx,
                                "time_ps": frame.time_ps,
                                "rmsd_angstrom": frame.rmsd_angstrom,
                                "potential_energy_kj_mol": frame.potential_energy_kj_mol,
                            })

            elif msg_type == "run_biomolecule_simulation":
                eid = str(msg.get("entity_id", "protein_001"))
                if eid in state.doc.entities:
                    rec = state.doc.entities[eid]
                    if isinstance(rec.provider, BiomoleculeProvider):
                        pdb_text = rec.provider.serialize_native().decode("utf-8")
                        sim = OpenMMSimulator(
                            pdb_content=pdb_text,
                            temperature_k=float(msg.get("temperature_k", 300.0)),
                            time_step_fs=float(msg.get("time_step_fs", 2.0)),
                        )
                        sim.minimize_energy(max_iterations=20)
                        sim_res = sim.simulate(
                            total_steps=int(msg.get("total_steps", 100)),
                            report_interval=int(msg.get("report_interval", 20)),
                        )
                        state.active_simulations[eid] = sim_res
                        await state.ipc.send_json(websocket, {
                            "type": "biomolecule_simulated",
                            "entity_id": eid,
                            "result": sim_res.to_dict(),
                        })

            elif msg_type == "generate_cam_toolpath":
                eid = str(msg.get("entity_id", "part_001"))
                if eid in state.doc.entities:
                    rec = state.doc.entities[eid]
                    part = getattr(rec.provider, "part", None)
                    if part is not None:
                        tool_diam = float(msg.get("tool_diameter_mm", 10.0))
                        strat = str(msg.get("strategy", "drop_cutter_surface"))
                        strat_valid = (
                            "roughing_spiral",
                            "finishing_scallop",
                            "drop_cutter_surface",
                            "adaptive_pocketing",
                            "profile_contouring",
                            "probing_cmm",
                            "5axis_swarf",
                        )
                        chosen_strat: Literal[
                            "roughing_spiral",
                            "finishing_scallop",
                            "drop_cutter_surface",
                            "adaptive_pocketing",
                            "profile_contouring",
                            "probing_cmm",
                            "5axis_swarf",
                        ] = strat if strat in strat_valid else "drop_cutter_surface"  # type: ignore[assignment]

                        tool = ToolDefinition(
                            diameter_mm=tool_diam,
                            length_mm=float(msg.get("tool_length_mm", 75.0)),
                            corner_radius_mm=tool_diam * 0.5,
                        )
                        params = MachiningParameters(
                            spindle_rpm=float(msg.get("spindle_rpm", 12000.0)),
                            feedrate_mm_min=float(msg.get("feedrate_mm_min", 3600.0)),
                        )
                        cam_prov = CamMachiningProvider(part=part, tool=tool, params=params)
                        cam_prov.generate_toolpath(strategy=chosen_strat)
                        audit = cam_prov.audit_cam()

                        toolpath_id = f"{eid}_cam_toolpath"
                        state.doc.entities[toolpath_id] = EntityRecord(
                            entity_id=toolpath_id,
                            provider_name="cam_machining",
                            scale_domain="milli",
                            provider=cam_prov,
                            tags=["cam", "toolpath", "5axis"],
                        )
                        dirty_payloads = state.doc.collect_dirty_binary_payloads()
                        for payload in dirty_payloads:
                            await state.ipc.broadcast_binary_mesh(payload)

                        await state.ipc.send_json(websocket, {
                            "type": "cam_toolpath_generated",
                            "entity_id": eid,
                            "toolpath_entity_id": toolpath_id,
                            "strategy": chosen_strat,
                            "total_points": audit.total_points,
                            "toolpath_length_mm": audit.toolpath_length_mm,
                            "estimated_machining_time_seconds": audit.estimated_machining_time_seconds,
                            "scallop_height_um": audit.scallop_height_um,
                            "is_collision_free": audit.is_collision_free,
                        })

            elif msg_type == "slice_additive_part":
                eid = str(msg.get("entity_id", "part_001"))
                if eid in state.doc.entities:
                    rec = state.doc.entities[eid]
                    part = getattr(rec.provider, "part", None)
                    if part is not None:
                        slicer = AdditiveSlicerProvider(part=part)
                        analysis = slicer.slice_to_gcode(
                            layer_height_mm=float(msg.get("layer_height_mm", 0.20)),
                            infill_percent=int(msg.get("infill_percent", 20)),
                        )
                        slicer_id = f"{eid}_slicer"
                        state.doc.entities[slicer_id] = EntityRecord(
                            entity_id=slicer_id,
                            provider_name="additive_slicer",
                            scale_domain="milli",
                            provider=slicer,
                            tags=["slicer", "additive", "dfam"],
                        )
                        dirty_payloads = state.doc.collect_dirty_binary_payloads()
                        for payload in dirty_payloads:
                            await state.ipc.broadcast_binary_mesh(payload)

                        await state.ipc.send_json(websocket, {
                            "type": "additive_part_sliced",
                            "entity_id": eid,
                            "layer_count": analysis.layer_count,
                            "filament_length_mm": analysis.filament_length_mm,
                            "estimated_mass_grams": analysis.estimated_mass_grams,
                            "estimated_time_seconds": analysis.estimated_time_seconds,
                            "is_valid": analysis.is_valid,
                        })

            elif msg_type == "generate_techdraw":
                eid = str(msg.get("entity_id", "part_001"))
                if eid in state.doc.entities:
                    rec = state.doc.entities[eid]
                    part = getattr(rec.provider, "part", None)
                    if part is not None:
                        sheet_fmt = str(msg.get("sheet_format", "A3")).upper()
                        tb = TitleBlock(
                            title=str(msg.get("title", "PIÈCE INDUSTRIELLE")),
                            drawing_number=str(msg.get("drawing_number", "DWG-1001")),
                            author=str(msg.get("author", "Brandon")),
                            material=str(msg.get("material", "Aluminium 6061-T6")),
                        )
                        td_prov = TechDrawDraftingProvider(
                            entity_id=f"{eid}_techdraw",
                            part=part,
                            sheet_format=sheet_fmt if sheet_fmt in ("A4", "A3", "A2", "A1") else "A3",  # type: ignore
                            title_block=tb,
                        )
                        report = td_prov.generate_drawing(auto_dimension=True)
                        td_id = f"{eid}_techdraw"
                        state.doc.entities[td_id] = EntityRecord(
                            entity_id=td_id,
                            provider_name="techdraw_drafting",
                            scale_domain="milli",
                            provider=td_prov,
                            tags=["techdraw", "2d", "drawing", "gdt"],
                        )
                        dirty_payloads = state.doc.collect_dirty_binary_payloads()
                        for payload in dirty_payloads:
                            await state.ipc.broadcast_binary_mesh(payload)

                        await state.ipc.send_json(websocket, {
                            "type": "techdraw_generated",
                            "entity_id": eid,
                            "techdraw_entity_id": td_id,
                            "sheet_format": report.sheet_format,
                            "sheet_width_mm": report.sheet_width_mm,
                            "sheet_height_mm": report.sheet_height_mm,
                            "views_count": report.views_count,
                            "total_visible_segments": report.total_visible_segments,
                            "total_hidden_segments": report.total_hidden_segments,
                            "dimensions_count": report.dimensions_count,
                            "gdt_count": report.gdt_count,
                            "svg_content": td_prov.to_svg(),
                        })

            elif msg_type == "export_mono_archive":
                meta = {
                    "title": str(msg.get("title", "Mono Project")),
                    "author": str(msg.get("author", "Brandon")),
                    "description": str(msg.get("description", "Conteneur unifié")),
                }
                archive_bytes = MonoArchive.pack(state.doc, metadata=meta)
                state.last_archive_bytes = archive_bytes
                sha = hashlib.sha256(archive_bytes).hexdigest()
                b64_str = base64.b64encode(archive_bytes).decode("ascii")

                await state.ipc.send_json(websocket, {
                    "type": "mono_archive_exported",
                    "archive_size_bytes": len(archive_bytes),
                    "archive_sha256": sha,
                    "entities_count": len(state.doc.entities),
                    "parameters_count": len(state.doc.parameters),
                    "archive_base64": b64_str,
                })

    except WebSocketDisconnect:
        state.ipc.disconnect(websocket)
