import json
import build123d as bd
from starlette.testclient import TestClient

from mono.core.document import MonoDocument
from mono.providers.brep_build123d import BRepBuild123dProvider
from mono.ipc.server import app, state


def test_document_parameters_and_entities() -> None:
    """Vérifie l'enregistrement des paramètres, le bornage et la collecte des payloads."""
    doc = MonoDocument()
    doc.register_parameter("thickness", 5.0, 1.0, 20.0, "mm", "Épaisseur")

    assert doc.parameters["thickness"].value == 5.0

    # Test bornage (clamping)
    doc.update_parameter("thickness", 50.0)
    assert doc.parameters["thickness"].value == 20.0  # Limité à max_value

    doc.update_parameter("thickness", -5.0)
    assert doc.parameters["thickness"].value == 1.0  # Limité à min_value

    # Ajout d'une entité
    box = bd.Box(10.0, 20.0, 30.0)
    provider = BRepBuild123dProvider("ent_01", box)
    doc.add_entity("ent_01", provider)

    # Collecte des payloads sales
    payloads = doc.collect_dirty_binary_payloads()
    assert len(payloads) == 1
    assert payloads[0].entity_id == "ent_01"

    # Deuxième collecte : rien de nouveau car déjà collecté
    empty_payloads = doc.collect_dirty_binary_payloads()
    assert len(empty_payloads) == 0

    # Résumé sémantique
    summary = doc.to_semantic_summary()
    assert summary["entities_count"] == 1
    assert "ent_01" in summary["entities"]


def test_fastapi_rest_and_websocket() -> None:
    """Vérifie les endpoints REST et le canal WebSocket bidirectionnel."""
    client = TestClient(app)

    # 1. Health check
    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json()["status"] == "ok"

    # 2. Exécution ECIP
    ecip_code = """
import build123d as bd
w = PARAMS.get("w", 30.0)
with bd.BuildPart() as part:
    bd.Box(w, 20.0, 10.0)
RESULT_PART = part.part
PARAMS["w"] = w
"""
    res_exec = client.post(
        "/api/ecip/execute",
        json={
            "entity_id": "test_box",
            "script_code": ecip_code,
            "param_overrides": {"w": 45.0},
        },
    )
    assert res_exec.status_code == 200
    data = res_exec.json()
    assert data["entity_id"] == "test_box"
    assert data["is_valid_l0"] is True

    # 3. Récupération de la scène
    res_scene = client.get("/api/scene")
    assert res_scene.status_code == 200
    scene_json = res_scene.json()
    assert "test_box" in scene_json["entities"]

    # 4. Modification de paramètre via REST
    res_param = client.post("/api/parameters/w", json={"value": 40.0})
    assert res_param.status_code == 200
    assert res_param.json()["value"] == 40.0

    # Purge des entités sales existantes pour isoler le test WebSocket
    _ = state.doc.collect_dirty_binary_payloads()

    # 5. WebSocket IPC complet
    with client.websocket_connect("/ws") as ws:
        # Message d'accueil
        welcome = ws.receive_json()
        assert welcome["type"] == "welcome"

        # Demande de résumé de scène
        ws.send_text(json.dumps({"type": "get_scene"}))
        scene_resp = ws.receive_json()
        assert scene_resp["type"] == "scene_summary"

        # Mise à jour de paramètre via WebSocket -> Recomputation et diffusion binaire immédiate (< 50 ms)
        ws.send_text(json.dumps({"type": "update_param", "name": "w", "value": 35.0}))

        # Réception du maillage recalculé à chaud en temps réel
        hot_header = ws.receive_json()
        assert hot_header["type"] == "binary_mesh_header"
        hot_bytes = ws.receive_bytes()
        assert len(hot_bytes) == hot_header["byte_size"]

        param_resp = ws.receive_json()
        assert param_resp["type"] == "param_updated"
        assert param_resp["value"] == 35.0

        # Exécution ECIP via WebSocket
        ws.send_text(json.dumps({
            "type": "execute_ecip",
            "entity_id": "ws_box",
            "script_code": ecip_code,
            "parameters": {"w": 25.0},
        }))

        # Réception de l'en-tête de maillage binaire
        header_resp = ws.receive_json()
        assert header_resp["type"] == "binary_mesh_header"
        assert header_resp["entity_id"] == "ws_box"

        # Réception des octets binaires du maillage
        mesh_bytes = ws.receive_bytes()
        assert len(mesh_bytes) == header_resp["byte_size"]

        # Notification de succès
        exec_success = ws.receive_json()
        assert exec_success["type"] == "execution_success"
        assert exec_success["entity_id"] == "ws_box"

        # Test Undo et Redo via WebSocket
        ws.send_text(json.dumps({"type": "undo"}))
        undo_resp = ws.receive_json()
        assert undo_resp["type"] == "undo_performed"

        ws.send_text(json.dumps({"type": "redo"}))
        redo_resp = ws.receive_json()
        assert redo_resp["type"] == "redo_performed"
