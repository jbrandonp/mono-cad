from __future__ import annotations

from dataclasses import dataclass
import build123d as bd
from OCP.BRepCheck import BRepCheck_Analyzer

from mono.security.ast_validator import validate_cad_script, SecurityViolationError, ALLOWED_MODULES


def _safe_import(
    name: str,
    globals_dict: dict[str, object] | None = None,
    locals_dict: dict[str, object] | None = None,
    fromlist: tuple[str, ...] = (),
    level: int = 0,
) -> object:
    """Intercepteur d'importation garantissant que seuls les modules autorisés sont chargés."""
    base_module = name.split(".")[0]
    if base_module not in ALLOWED_MODULES:
        raise SecurityViolationError(
            f"Tentative d'importation du module non autorisé : '{name}'"
        )
    return __import__(name, globals_dict, locals_dict, fromlist, level)


@dataclass(frozen=True)
class ECIPExecutionResult:
    """Résultat synthétique de l'évaluation sécurisée d'un script CAO ECIP."""

    part: bd.Part | bd.Solid | bd.Compound
    parameters: dict[str, float]
    named_faces: dict[str, list[int]]
    is_valid_l0: bool
    volume_m3: float
    bbox_dims: tuple[float, float, float]


class ECIPExecutionError(RuntimeError):
    """Exception levée lors de l'exécution ou de l'échec géométrique d'un bloc ECIP."""


def execute_ecip_cad_block(
    script_code: str,
    param_overrides: dict[str, float] | None = None,
    unit_scale_to_meters: float = 0.001,
) -> ECIPExecutionResult:
    """Exécute un bloc de code CAO déclaratif build123d/ECIP dans un bac à sable sécurisé.

    Args:
        script_code: Code source Python généré par l'IA ou chargé depuis un fichier.
        param_overrides: Surcharges dynamiques des paramètres issus des curseurs UI.
        unit_scale_to_meters: Facteur d'échelle vers les mètres SI.

    Returns:
        ECIPExecutionResult: Solide généré, paramètres exposés et validation L0.

    Raises:
        SecurityViolationError: Si le script enfreint les règles du bac à sable AST.
        ECIPExecutionError: Si le script produit une erreur ou un solide B-Rep invalide.
    """
    # 1. Analyse statique obligatoire du code source AST
    validate_cad_script(script_code)

    # 2. Préparation du bac à sable restreint (zéro accès OS, réseau ou introspection)
    sandbox_globals: dict[str, object] = {
        "bd": bd,
        "__builtins__": {
            "__import__": _safe_import,
            "range": range,
            "len": len,
            "min": min,
            "max": max,
            "abs": abs,
            "float": float,
            "int": int,
            "round": round,
            "sum": sum,
            "bool": bool,
        },
    }

    initial_params: dict[str, float] = dict(param_overrides or {})
    sandbox_locals: dict[str, object] = {
        "PARAMS": initial_params,
        "SELECTORS": {},
    }

    # 3. Compilation et exécution sécurisée
    try:
        compiled_code = compile(script_code, filename="<ecip_block>", mode="exec")
        exec(compiled_code, sandbox_globals, sandbox_locals)
    except Exception as exc:
        raise ECIPExecutionError(f"Erreur d'exécution du script ECIP : {exc}") from exc

    # 4. Vérification de la présence de RESULT_PART
    if "RESULT_PART" not in sandbox_locals:
        raise ECIPExecutionError(
            "Le script ECIP doit obligatoirement définir une variable nommée 'RESULT_PART' (ex: RESULT_PART = mon_solide)."
        )

    raw_part = sandbox_locals["RESULT_PART"]
    if not isinstance(raw_part, (bd.Part, bd.Solid, bd.Compound)):
        raise ECIPExecutionError(
            f"'RESULT_PART' doit être de type build123d.Part, Solid ou Compound, reçu : {type(raw_part).__name__}."
        )

    part: bd.Part | bd.Solid | bd.Compound = raw_part

    # 5. Porte déterministe L0 (Intégrité topologique et volume positif)
    analyzer = BRepCheck_Analyzer(part.wrapped)
    is_valid_occt = bool(analyzer.IsValid())
    vol_local = float(part.volume)
    vol_m3 = vol_local * (unit_scale_to_meters ** 3)
    is_l0 = is_valid_occt and vol_m3 > 1e-15 and part.is_valid

    if not is_l0:
        raise ECIPExecutionError(
            f"Échec de la Porte L0 : le solide produit est invalide ou non-fermé (Volume={vol_m3:.2e} m³, IsValid={is_valid_occt})."
        )

    # 6. Dimensions hors-tout
    bbox = part.bounding_box()
    dims = (
        float(bbox.size.X) * unit_scale_to_meters,
        float(bbox.size.Y) * unit_scale_to_meters,
        float(bbox.size.Z) * unit_scale_to_meters,
    )

    # 7. Résolution des sélecteurs déclarés
    all_faces = part.faces()
    resolved_named_faces: dict[str, list[int]] = {}

    raw_selectors = sandbox_locals.get("SELECTORS")
    if isinstance(raw_selectors, dict):
        for sel_name, sel_callable in raw_selectors.items():
            if callable(sel_callable):
                try:
                    selection = sel_callable(part)
                    indices: list[int] = []
                    if isinstance(selection, bd.Face):
                        for idx, face in enumerate(all_faces):
                            if face.is_same(selection):
                                indices.append(idx)
                    elif isinstance(selection, (list, tuple, bd.ShapeList)):
                        for item in selection:
                            if isinstance(item, bd.Face):
                                for idx, face in enumerate(all_faces):
                                    if face.is_same(item) and idx not in indices:
                                        indices.append(idx)
                    resolved_named_faces[str(sel_name)] = indices
                except Exception:
                    resolved_named_faces[str(sel_name)] = []

    # Extraction des paramètres finaux
    final_params: dict[str, float] = {}
    raw_params = sandbox_locals.get("PARAMS")
    if isinstance(raw_params, dict):
        for k, v in raw_params.items():
            if isinstance(v, (int, float)):
                final_params[str(k)] = float(v)

    return ECIPExecutionResult(
        part=part,
        parameters=final_params,
        named_faces=resolved_named_faces,
        is_valid_l0=is_l0,
        volume_m3=vol_m3,
        bbox_dims=dims,
    )
