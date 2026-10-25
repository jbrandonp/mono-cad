"""Tests unitaires et d'intégration du conteneur unifié d'archive .mono (Phase 9)."""

from __future__ import annotations

import io
import json
import tarfile
import build123d as bd
import pytest
import zstandard as zstd

from mono.core.archive_mono import MonoArchive, MonoArchiveError
from mono.core.document import MonoDocument
from mono.providers.brep_build123d import BRepBuild123dProvider
from mono.providers.cam_machining import CamMachiningProvider
from mono.providers.biomolecule_gemmi import BiomoleculeProvider
from mono.providers.techdraw_drafting import TechDrawDraftingProvider, TitleBlock


def test_mono_archive_pack_inspect_and_unpack(tmp_path) -> None:
    """Vérifie l'empaquetage Zstandard, l'inspection rapide et la restauration sans perte d'état."""
    doc = MonoDocument()
    doc.register_parameter("length_mm", 60.0, 10.0, 200.0, "mm", "Longueur Pièce")
    doc.register_parameter("thickness_mm", 12.0, 2.0, 50.0, "mm", "Épaisseur")

    # Solide B-Rep 3D
    box = bd.Box(60, 40, 12)
    doc.add_entity("bracket_01", BRepBuild123dProvider("bracket_01", box), "brep_build123d", tags=["milli", "bracket"])

    # Mise en plan 2D TechDraw associée
    tb = TitleBlock(title="BRACKET V1", drawing_number="DWG-2001", author="Brandon")
    td_prov = TechDrawDraftingProvider(entity_id="bracket_01_techdraw", part=box, title_block=tb)
    td_prov.generate_drawing()
    doc.add_entity("bracket_01_techdraw", td_prov, "techdraw_drafting", tags=["2d", "drawing"])

    # 1. Empaquetage sur disque
    archive_file = str(tmp_path / "project_test.mono")
    archive_bytes = MonoArchive.pack(
        doc,
        output_file_or_path=archive_file,
        metadata={"title": "Projet Aéronautique", "author": "Brandon"},
    )
    assert len(archive_bytes) > 0

    # 2. Inspection rapide sans décompression intégrale
    manifest = MonoArchive.inspect(archive_file)
    assert manifest["format"] == "mono-archive"
    assert manifest["title"] == "Projet Aéronautique"
    assert manifest["author"] == "Brandon"
    assert manifest["entities_count"] == 2
    assert "length_mm" in manifest["parameters"]

    # 3. Restauration complète (Round-Trip)
    restored_doc, restored_manifest = MonoArchive.unpack(archive_file)
    assert len(restored_doc.entities) == 2
    assert "bracket_01" in restored_doc.entities
    assert "bracket_01_techdraw" in restored_doc.entities
    assert restored_doc.parameters["length_mm"].value == 60.0
    assert restored_doc.parameters["thickness_mm"].value == 12.0

    # Vérification que le solide B-Rep restauré possède la bonne géométrie
    restored_brep = restored_doc.entities["bracket_01"].provider
    assert isinstance(restored_brep, BRepBuild123dProvider)
    assert pytest.approx(restored_brep.part.volume, rel=1e-2) == box.volume


def test_mono_archive_sha256_corruption_detection() -> None:
    """Vérifie que la falsification d'un octet ou artefact lève une exception MonoArchiveError."""
    doc = MonoDocument()
    box = bd.Box(20, 20, 20)
    doc.add_entity("cube", BRepBuild123dProvider("cube", box), "brep_build123d")

    data = bytearray(MonoArchive.pack(doc))

    # 1. Falsification d'octets au cœur de l'archive compressée (détection Zstandard CRC32)
    corrupt_idx = len(data) // 2
    data[corrupt_idx] ^= 0xFF

    with pytest.raises(MonoArchiveError):
        MonoArchive.unpack(bytes(data))

    # 2. Falsification d'un artefact provoquant un rejet de validation SHA-256
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w") as tar:
        cube_step = b"ISO-10303-21; FAKE STEP DATA; ENDSEC;"
        fake_manifest = {
            "format": "mono-archive",
            "schema_version": "1.0.0",
            "entities": [
                {
                    "entity_id": "cube",
                    "provider_name": "brep_build123d",
                    "file_path": "brep/cube.step",
                    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
                }
            ],
        }
        man_bytes = json.dumps(fake_manifest).encode("utf-8")
        ti_m = tarfile.TarInfo("manifest.json")
        ti_m.size = len(man_bytes)
        tar.addfile(ti_m, io.BytesIO(man_bytes))

        ti_s = tarfile.TarInfo("brep/cube.step")
        ti_s.size = len(cube_step)
        tar.addfile(ti_s, io.BytesIO(cube_step))

    raw_tar = tar_buf.getvalue()
    cctx = zstd.ZstdCompressor(level=3, write_checksum=True)
    corrupted_archive = cctx.compress(raw_tar)

    with pytest.raises(MonoArchiveError, match="Corruption détectée"):
        MonoArchive.unpack(corrupted_archive)


def test_multidisciplinary_mono_archive_roundtrip() -> None:
    """Vérifie le packaging multi-domaines complet (B-Rep STEP, TechDraw SVG/DXF/PDF, CAM Fanuc/GRBL, Bio CIF/BCIF)."""
    doc = MonoDocument()
    box = bd.Box(40, 30, 15)

    # 1. B-Rep 3D
    doc.add_entity("box_cad", BRepBuild123dProvider("box_cad", box), "brep_build123d")

    # 2. Plan 2D TechDraw
    td = TechDrawDraftingProvider(entity_id="box_dwg", part=box)
    td.generate_drawing()
    doc.add_entity("box_dwg", td, "techdraw_drafting")

    # 3. FAO / CAM Machining
    cam = CamMachiningProvider(part=box)
    cam.generate_toolpath(strategy="finishing_scallop")
    doc.add_entity("box_cam", cam, "cam_machining")

    # 4. Biologie Macromoléculaire
    bio = BiomoleculeProvider(entity_id="box_bio", pdb_code="1CRN")
    doc.add_entity("box_bio", bio, "biomolecule_gemmi")

    # Empaquetage
    archive_bytes = MonoArchive.pack(
        doc,
        metadata={"title": "Projet Multi-Physique", "author": "Brandon"},
    )
    assert len(archive_bytes) > 0

    # Inspection manifeste
    manifest = MonoArchive.inspect(archive_bytes)
    assert manifest["entities_count"] == 4
    file_paths = {ent["file_path"] for ent in manifest["entities"]}
    assert "brep/box_cad.step" in file_paths
    assert "techdraw/box_dwg.svg" in file_paths
    assert "cam/box_cam.gcode" in file_paths
    assert "bio/box_bio.cif" in file_paths

    # Restauration Round-Trip
    restored_doc, restored_manifest = MonoArchive.unpack(archive_bytes)
    assert len(restored_doc.entities) == 4
    assert "box_cad" in restored_doc.entities
    assert "box_dwg" in restored_doc.entities
    assert "box_cam" in restored_doc.entities
    assert "box_bio" in restored_doc.entities
    assert isinstance(restored_doc.entities["box_cad"].provider, BRepBuild123dProvider)
    assert isinstance(restored_doc.entities["box_dwg"].provider, TechDrawDraftingProvider)
    assert isinstance(restored_doc.entities["box_cam"].provider, CamMachiningProvider)
    assert isinstance(restored_doc.entities["box_bio"].provider, BiomoleculeProvider)


@pytest.mark.asyncio
async def test_mono_archive_api_endpoints() -> None:
    """Vérifie les endpoints REST d'export, de téléchargement et d'import de projet .mono."""
    import base64
    import httpx
    from mono.core.document import EntityRecord
    from mono.ipc.server import app, state

    box = bd.Box(30, 30, 30)
    state.doc.entities["box_archive_test"] = EntityRecord(
        entity_id="box_archive_test",
        provider_name="brep_build123d",
        scale_domain="milli",
        provider=BRepBuild123dProvider("box_archive_test", box),
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Export .mono
        res_export = await client.post("/api/archive/export", json={
            "title": "API Project",
            "author": "Brandon",
            "description": "Test unifié",
        })
        assert res_export.status_code == 200
        data_exp = res_export.json()
        assert data_exp["status"] == "success"
        assert data_exp["archive_size_bytes"] > 0
        assert len(data_exp["archive_sha256"]) == 64

        # 2. Téléchargement direct
        res_dl = await client.get("/api/archive/download")
        assert res_dl.status_code == 200
        assert "application/zstd" in res_dl.headers["content-type"]
        assert len(res_dl.content) > 0

        # 3. Import récursif via Base64
        b64_content = base64.b64encode(res_dl.content).decode("ascii")
        res_import = await client.post("/api/archive/import", json={
            "archive_base64": b64_content,
        })
        assert res_import.status_code == 200
        data_imp = res_import.json()
        assert data_imp["status"] == "success"
        assert data_imp["entities_restored"] >= 1
