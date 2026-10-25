from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from mono.core.document import MonoDocument
from mono.agent.mac_orchestrator import MACOrchestrator, MockLLMProvider, MACExecutionResult
from mono.agent.cadengbench_gates import CADEngBenchGatekeeper
from mono.ipc.server import app, state


@pytest.mark.asyncio
async def test_mac_orchestrator_successful_flow() -> None:
    """Vérifie le déroulement nominal du pipeline MAC en 5 étapes."""
    doc = MonoDocument()
    llm = MockLLMProvider()
    orchestrator = MACOrchestrator(llm_provider=llm)

    res = await orchestrator.run(
        user_prompt="Create an aluminum mounting bracket 100x50x20 with 8mm hole",
        doc=doc,
    )

    assert isinstance(res, MACExecutionResult)
    assert res.success is True
    assert res.iterations == 1
    assert res.spec_plan is not None
    assert res.spec_plan.target_dimensions["length"] == 100.0
    assert res.gate_result is not None
    assert res.gate_result.overall_passed is True
    assert res.vlm_approved is True
    assert res.error_message is None

    # Vérification que le document unifié intègre l'entité et ses paramètres réactifs
    target_id = res.spec_plan.target_entity_id
    assert target_id in doc.entities
    entity = doc.entities[target_id]
    assert entity.script_code is not None
    assert "length" in entity.bound_parameters
    assert "length" in doc.parameters

    # Vérification de la collecte du maillage binaire zero-copy
    dirty_payloads = doc.collect_dirty_binary_payloads()
    assert len(dirty_payloads) == 1
    assert dirty_payloads[0].entity_id == target_id
    assert dirty_payloads[0].vertices.shape[0] > 0


@pytest.mark.asyncio
async def test_mac_orchestrator_vlm_rejection_long_loop() -> None:
    """Vérifie la boucle longue avec rétroaction visuelle suite à un rejet du juge VLM."""
    doc = MonoDocument()
    # Simule 1 rejet VLM qui réussit à l'itération suivante
    llm = MockLLMProvider(simulate_vlm_rejection_count=1)
    orchestrator = MACOrchestrator(llm_provider=llm)

    res = await orchestrator.run(
        user_prompt="Create a mechanical box 80x40x15",
        doc=doc,
    )

    assert res.success is True
    assert res.iterations == 2  # Itération 1 rejetée par VLM, itération 2 acceptée
    assert res.vlm_approved is True


def test_rest_api_gates_and_agent_run() -> None:
    """Vérifie les nouveaux endpoints REST POST /api/gates/evaluate et POST /api/agent/run."""
    state.doc.entities.clear()
    state.doc.parameters.clear()
    state.doc.history.clear()

    client = TestClient(app)

    # 1. Exécution de l'agent via l'API REST
    run_resp = client.post(
        "/api/agent/run",
        json={"prompt": "Create a mounting plate 120x60x25", "max_iterations": 5},
    )
    assert run_resp.status_code == 200
    run_data = run_resp.json()
    assert run_data["success"] is True
    assert run_data["iterations"] >= 1
    target_id = run_data["spec_plan"]["target_entity_id"]

    # 2. Évaluation des portes déterministes sur l'entité générée
    eval_resp = client.post(
        "/api/gates/evaluate",
        json={
            "entity_id": target_id,
            "target_dimensions": {"l": 120.0, "w": 60.0, "h": 25.0},
        },
    )
    assert eval_resp.status_code == 200
    eval_data = eval_resp.json()
    assert eval_data["overall_passed"] is True
    assert eval_data["score"] == 1.0
    assert eval_data["l0"]["is_watertight"] is True
    assert eval_data["l1"]["min_wall_thickness_mm"] >= 1.0
