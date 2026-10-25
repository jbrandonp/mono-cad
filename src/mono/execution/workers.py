from __future__ import annotations

from pathlib import Path
import subprocess
import shutil


def get_workers_dir() -> Path:
    """Retourne le chemin absolu du répertoire des exécutables autonomes `bin/workers`."""
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    return project_root / "bin" / "workers"


def get_ccx_path() -> Path:
    """Retourne le chemin vers le binaire autonome du solveur CalculiX."""
    workers_dir = get_workers_dir()
    candidate = workers_dir / "ccx.exe"
    if candidate.exists():
        return candidate
    # Repli sur le PATH système
    sys_ccx = shutil.which("ccx") or shutil.which("ccx.exe")
    if sys_ccx:
        return Path(sys_ccx)
    return candidate


def is_ccx_available() -> bool:
    """Vérifie si le solveur CalculiX `ccx.exe` est présent et exécutable."""
    path = get_ccx_path()
    return path.exists() and path.is_file()


def get_orcaslicer_path() -> Path:
    """Retourne le chemin vers l'exécutable portable d'OrcaSlicer CLI."""
    workers_dir = get_workers_dir()
    candidate = workers_dir / "orcaslicer" / "orca-slicer.exe"
    if candidate.exists():
        return candidate
    sys_orca = shutil.which("orca-slicer") or shutil.which("orca-slicer.exe")
    if sys_orca:
        return Path(sys_orca)
    return candidate


def is_orcaslicer_available() -> bool:
    """Vérifie si le moteur de tranchage OrcaSlicer est présent et exécutable."""
    path = get_orcaslicer_path()
    return path.exists() and path.is_file()


def run_ccx_job(
    job_name: str,
    working_dir: Path,
    timeout_seconds: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    """Exécute un calcul par éléments finis sous CalculiX dans un sous-processus isolé.

    Args:
        job_name: Nom du cas de calcul sans extension (le fichier `.inp` doit exister dans `working_dir`).
        working_dir: Répertoire de travail contenant `[job_name].inp`.
        timeout_seconds: Délai limite d'exécution en secondes.

    Returns:
        subprocess.CompletedProcess: Résultat d'exécution avec stdout, stderr et code retour.

    Raises:
        FileNotFoundError: Si l'exécutable `ccx.exe` ou le fichier d'entrée `.inp` est introuvable.
        subprocess.TimeoutExpired: Si le temps de résolution excède la limite allouée.
    """
    ccx_exe = get_ccx_path()
    if not ccx_exe.exists():
        raise FileNotFoundError(f"Solveur CalculiX introuvable au chemin : {ccx_exe}")

    inp_file = working_dir / f"{job_name}.inp"
    if not inp_file.exists():
        raise FileNotFoundError(f"Fichier de calcul CalculiX introuvable : {inp_file}")

    cmd: list[str] = [str(ccx_exe), "-i", job_name]

    return subprocess.run(
        cmd,
        cwd=str(working_dir),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )


def run_orcaslicer_slice(
    model_path: Path,
    output_gcode_path: Path,
    timeout_seconds: float = 120.0,
) -> subprocess.CompletedProcess[str]:
    """Exécute un tranchage 3D FDM en ligne de commande sous OrcaSlicer dans un processus isolé.

    Args:
        model_path: Fichier géométrique 3D en entrée (`.stl`, `.step`, ou `.3mf`).
        output_gcode_path: Chemin cible du fichier G-code généré.
        timeout_seconds: Délai limite d'exécution.

    Returns:
        subprocess.CompletedProcess: Résultat d'exécution.
    """
    orca_exe = get_orcaslicer_path()
    if not orca_exe.exists():
        raise FileNotFoundError(f"Moteur de tranchage OrcaSlicer introuvable au chemin : {orca_exe}")

    if not model_path.exists():
        raise FileNotFoundError(f"Modèle 3D introuvable : {model_path}")

    output_gcode_path.parent.mkdir(parents=True, exist_ok=True)

    cmd: list[str] = [
        str(orca_exe),
        "--slice", "0",
        "--export-gcode", str(model_path),
        "--output", str(output_gcode_path),
    ]

    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
