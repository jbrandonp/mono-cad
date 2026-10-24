import pytest
from mono.security.ast_validator import validate_cad_script, SecurityViolationError


def test_valid_cad_script_passes() -> None:
    """Vérifie qu'un script CAO légitime avec build123d et math est accepté."""
    valid_code = """
import build123d as bd
import math

length = PARAMS.get("length", 10.0)
radius = math.sqrt(length)
with bd.BuildPart() as part:
    bd.Box(length, 20.0, 5.0)
RESULT_PART = part.part
"""
    # Ne doit lever aucune exception
    validate_cad_script(valid_code)


def test_forbidden_system_import_fails() -> None:
    """Vérifie que l'importation de modules système (os, sys) est interceptée."""
    malicious_code = """
import os
os.system("echo hacked")
"""
    with pytest.raises(SecurityViolationError, match="Importation non autorisée"):
        validate_cad_script(malicious_code)


def test_forbidden_from_import_fails() -> None:
    """Vérifie que 'from subprocess import Popen' est bloqué."""
    malicious_code = """
from subprocess import Popen
"""
    with pytest.raises(SecurityViolationError, match="Importation non autorisée"):
        validate_cad_script(malicious_code)


def test_forbidden_builtins_fail() -> None:
    """Vérifie que les fonctions eval, exec, open sont rigoureusement bannies."""
    for func_name in ["eval", "exec", "open", "__import__"]:
        code = f"{func_name}('secret.txt')"
        with pytest.raises(SecurityViolationError, match="Appel de fonction système interdit"):
            validate_cad_script(code)


def test_forbidden_dunder_attribute_fails() -> None:
    """Vérifie que l'accès aux attributs privés ou dunder (__class__, __subclasses__) est bloqué."""
    code = "obj = ().__class__.__subclasses__()"
    with pytest.raises(SecurityViolationError, match="Accès formellement interdit à l'attribut privé"):
        validate_cad_script(code)
