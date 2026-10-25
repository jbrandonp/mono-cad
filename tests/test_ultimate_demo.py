"""Tests d'intégrité et de validation du Chef-d'œuvre Démo Ultime Multi-Échelle."""

from __future__ import annotations

from pathlib import Path
import pytest

from mono.core.archive_mono import MonoArchive


def test_ultimate_multidisciplinary_demo_archive_exists_and_unpacks() -> None:
    """Vérifie que l'archive démo multi-échelle existe, est valide et contient les 6 domaines."""
    archive_path = Path("demo_multidisciplinaire.mono")
    assert archive_path.exists(), "L'archive demo_multidisciplinaire.mono doit exister à la racine."

    # Inspection sans décompression
    manifest = MonoArchive.inspect(str(archive_path))
    assert manifest.get("format") == "mono-archive"
    assert manifest.get("schema_version") == "1.0.0"
    assert manifest.get("author") == "Brandon"
    assert "parameters" in manifest
    assert len(manifest["parameters"]) >= 5

    entities = manifest.get("entities", [])
    assert len(entities) == 6, f"Attendu 6 entités multi-échelles, obtenu {len(entities)}"

    entity_ids = {e["entity_id"] for e in entities}
    expected_ids = {
        "bim_lab_building",
        "part_robot_arm_base",
        "techdraw_robot_arm_base",
        "cam_robot_arm_base",
        "textile_cleanroom_tarp",
        "bio_macromolecule_1crn",
    }
    assert expected_ids.issubset(entity_ids), f"Entités manquantes: {expected_ids - entity_ids}"

    # Déballage et chargement dans un MonoDocument
    doc, unpacked_manifest = MonoArchive.unpack(str(archive_path))
    assert len(doc.entities) == 6
    assert unpacked_manifest.get("format") == "mono-archive"

    # Vérification des domaines d'échelle
    assert doc.entities["bim_lab_building"].scale_domain == "meter"
    assert doc.entities["part_robot_arm_base"].scale_domain == "milli"
    assert doc.entities["techdraw_robot_arm_base"].scale_domain == "milli"
    assert doc.entities["cam_robot_arm_base"].scale_domain == "milli"
    assert doc.entities["textile_cleanroom_tarp"].scale_domain == "meter"
    assert doc.entities["bio_macromolecule_1crn"].scale_domain == "nano"
