"""Tests unitaires et d'intégration du conteneur unifié d'archive .mono (Phase 9)."""

from __future__ import annotations

import io
import build123d as bd
import pytest

from mono.core.archive_mono import MonoArchive, MonoArchiveError
from mono.core.document import MonoDocument
from mono.providers.brep_build123d import BRepBuild123dProvider
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

    # Falsification d'octets au cœur de l'archive compressée
    corrupt_idx = len(data) // 2
    data[corrupt_idx] ^= 0xFF

    with pytest.raises(MonoArchiveError):
        MonoArchive.unpack(bytes(data))


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
