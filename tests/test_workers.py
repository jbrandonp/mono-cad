from __future__ import annotations

import tempfile
from pathlib import Path
from mono.execution.workers import (
    get_ccx_path,
    get_orcaslicer_path,
    is_ccx_available,
    is_orcaslicer_available,
    run_ccx_job,
)


def test_workers_presence() -> None:
    """Vérifie la présence et le chemin des binaires portables ccx.exe et orca-slicer.exe."""
    assert is_ccx_available() is True
    ccx_path = get_ccx_path()
    assert ccx_path.exists()
    assert ccx_path.name.lower() in ("ccx.exe", "ccx")

    assert is_orcaslicer_available() is True
    orca_path = get_orcaslicer_path()
    assert orca_path.exists()
    assert orca_path.name.lower() in ("orca-slicer.exe", "orca-slicer")


def test_calculix_solve_end_to_end() -> None:
    """Exécute un cas de calcul linéaire-statique réel sous CalculiX ccx.exe."""
    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        job_name = "test_bracket_fea"

        # Écriture d'un cas élément fini minimal (Cube C3D8 sous traction uniaxiale)
        inp_content = """*HEADING
Mono Integration Test Job
*NODE, NSET=NALL
1, 0.,0.,0.
2, 10.,0.,0.
3, 10.,10.,0.
4, 0.,10.,0.
5, 0.,0.,10.
6, 10.,0.,10.
7, 10.,10.,10.
8, 0.,10.,10.
*ELEMENT,TYPE=C3D8,ELSET=EALL
1, 1,2,3,4,5,6,7,8
*MATERIAL,NAME=ALUMINUM
*ELASTIC
70000.,0.33
*SOLID SECTION,ELSET=EALL,MATERIAL=ALUMINUM
*STEP
*STATIC
*BOUNDARY
1,1,3,0.
4,1,3,0.
5,1,3,0.
8,1,3,0.
*CLOAD
2,1,500.
3,1,500.
6,1,500.
7,1,500.
*NODE FILE
U
*EL FILE
S
*END STEP
"""
        (tmp_dir / f"{job_name}.inp").write_text(inp_content, encoding="utf-8")

        proc = run_ccx_job(job_name=job_name, working_dir=tmp_dir, timeout_seconds=15.0)

        assert proc.returncode == 0
        assert "Job finished" in proc.stdout

        # Vérification des fichiers de résultats générés par CalculiX
        frd_file = tmp_dir / f"{job_name}.frd"
        dat_file = tmp_dir / f"{job_name}.dat"
        cvg_file = tmp_dir / f"{job_name}.cvg"

        assert frd_file.exists()
        assert frd_file.stat().st_size > 0
        assert dat_file.exists()
        assert cvg_file.exists()
