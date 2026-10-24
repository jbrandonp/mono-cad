import pytest
from mono.execution.ecip_executor import execute_ecip_cad_block, ECIPExecutionError
from mono.security.ast_validator import SecurityViolationError


def test_execute_ecip_success() -> None:
    """Vérifie l'exécution réussie d'un bloc ECIP avec paramètres et sélecteurs."""
    script = """
import build123d as bd

# Déclaration des paramètres
w = PARAMS.get("width", 40.0)
h = PARAMS.get("height", 20.0)
d = PARAMS.get("depth", 10.0)

with bd.BuildPart() as part:
    bd.Box(w, h, d)

RESULT_PART = part.part
PARAMS["width"] = w
PARAMS["height"] = h
PARAMS["depth"] = d

# Sélecteur sémantique de la face supérieure
SELECTORS["top_face"] = lambda p: p.faces().sort_by(bd.Axis.Z)[-1]
"""
    result = execute_ecip_cad_block(script, param_overrides={"width": 50.0})
    assert result.is_valid_l0 is True
    assert result.parameters["width"] == 50.0
    assert result.parameters["height"] == 20.0
    assert "top_face" in result.named_faces
    assert len(result.named_faces["top_face"]) == 1


def test_execute_ecip_missing_result_part() -> None:
    """Vérifie qu'un script sans RESULT_PART est rejeté."""
    script = """
import build123d as bd
x = 10
"""
    with pytest.raises(ECIPExecutionError, match="doit obligatoirement définir une variable nommée 'RESULT_PART'"):
        execute_ecip_cad_block(script)


def test_execute_ecip_security_violation() -> None:
    """Vérifie que les violations de sécurité bloquent immédiatement l'exécution."""
    script = """
import os
RESULT_PART = None
"""
    with pytest.raises(SecurityViolationError):
        execute_ecip_cad_block(script)
