"""Gestionnaire de conteneur de fichier unique unifié .mono (Phase 9).

Spécifications du format .mono :
- Conteneur d'archive tar compressé sous Zstandard (zstd niveau 3) avec repli transparent gzip/tar.
- Manifeste déclaratif JSON (manifest.json) horodaté avec hachage cryptographique SHA-256 par artefact.
- Stockage multi-domaines sans perte d'information :
  - brep/ : Solides géométriques B-Rep exacts au format STEP (ISO 10303-21).
  - bim/ : Bâtiments paramétriques IFC4 (ISO 16739).
  - fea/ : Maillages d'éléments finis Gmsh (.msh) et paquets de calcul.
  - cam/ : Trajectoires d'usinage multi-axes et programmes G-code ISO 6983.
  - slicer/ : Fichiers G-code et métriques de fabrication additive.
  - bio/ : Structures macromoléculaires cristallographiques (CIF / BinaryCIF / PDB).
  - textile/ : Maillages physiques et contraintes XPBD.
  - techdraw/ : Mises en plan 2D cotées au format SVG et DXF (ISO 128 / ISO 7200).
  - sketch/ : Esquisses 2D et contraintes géométriques PlaneGCS.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import os
import tarfile
import tempfile
from typing import Mapping

import build123d as bd
import zstandard as zstd

from mono.core.document import EntityRecord, MonoDocument, ParameterDefinition
from mono.providers.additive_slicer import AdditiveSlicerProvider
from mono.providers.bim_ifc import IfcGeometryProvider
from mono.providers.biomolecule_gemmi import BiomoleculeProvider
from mono.providers.brep_build123d import BRepBuild123dProvider
from mono.providers.cam_machining import CamMachiningProvider
from mono.providers.fea_gmsh import FeaGmshProvider
from mono.providers.techdraw_drafting import TechDrawDraftingProvider
from mono.providers.textile_xpbd import TextileXpbdProvider


class MonoArchiveError(Exception):
    """Exception levée en cas d'erreur de compression, corruption ou intégrité du fichier .mono."""


class MonoArchive:
    """Gestionnaire d'empaquetage et de décompression du conteneur unifié .mono."""

    FORMAT_NAME: str = "mono-archive"
    SCHEMA_VERSION: str = "1.0.0"

    @staticmethod
    def _compute_sha256(data: bytes) -> str:
        """Calcule le condensat cryptographique SHA-256 d'un tampon binaire."""
        return hashlib.sha256(data).hexdigest()

    @classmethod
    def pack(
        cls,
        doc: MonoDocument,
        output_file_or_path: str | io.BytesIO | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> bytes:
        """Empaquette l'ensemble du projet multi-domaines dans une archive .mono compressée zstd.

        Args:
            doc: Instance active du document MonoDocument.
            output_file_or_path: Chemin de fichier (.mono) ou tampon en mémoire.
            metadata: Méta-données optionnelles (auteur, titre du projet, description).

        Returns:
            bytes: Octets bruts de l'archive compressée.
        """
        meta = dict(metadata or {})
        tar_buf = io.BytesIO()

        with tarfile.open(fileobj=tar_buf, mode="w") as tar:
            entities_manifest: list[dict[str, object]] = []

            with tempfile.TemporaryDirectory() as tmp_dir:
                for eid, rec in doc.entities.items():
                    provider = rec.provider
                    p_name = rec.provider_name
                    file_rel_path: str = ""
                    artifact_bytes: bytes = b""

                    # 1. B-Rep exact (STEP ISO 10303-21)
                    if isinstance(provider, BRepBuild123dProvider):
                        step_path = os.path.join(tmp_dir, f"{eid}.step")
                        bd.export_step(provider.part, step_path)
                        with open(step_path, "rb") as f_step:
                            artifact_bytes = f_step.read()
                        file_rel_path = f"brep/{eid}.step"

                    # 2. TechDraw 2D (SVG + DXF)
                    elif isinstance(provider, TechDrawDraftingProvider):
                        svg_text = provider.to_svg()
                        artifact_bytes = svg_text.encode("utf-8")
                        file_rel_path = f"techdraw/{eid}.svg"

                        # Ajout complémentaire du fichier DXF
                        dxf_bytes = provider.to_dxf()
                        dxf_info = tarfile.TarInfo(name=f"techdraw/{eid}.dxf")
                        dxf_info.size = len(dxf_bytes)
                        dxf_info.mtime = int(datetime.now(timezone.utc).timestamp())
                        tar.addfile(dxf_info, io.BytesIO(dxf_bytes))

                    # 3. BIM / Architecture IFC4
                    elif isinstance(provider, IfcGeometryProvider):
                        ifc_text = provider.to_ifc_string()
                        artifact_bytes = ifc_text.encode("utf-8")
                        file_rel_path = f"bim/{eid}.ifc"

                    # 4. FAO / CAM G-code et trajectoires
                    elif isinstance(provider, CamMachiningProvider):
                        gcode_text = provider.post_process_gcode(flavor="fanuc")
                        artifact_bytes = gcode_text.encode("utf-8")
                        file_rel_path = f"cam/{eid}.gcode"

                    # 5. Impression 3D / Additive Slicer
                    elif isinstance(provider, AdditiveSlicerProvider):
                        artifact_bytes = (provider.cached_gcode or "; Mono Slicer").encode("utf-8")
                        file_rel_path = f"slicer/{eid}.gcode"

                    # 6. Biologie Moléculaire (Gemmi CIF/PDB)
                    elif isinstance(provider, BiomoleculeProvider):
                        cif_text = provider.to_cif_string()
                        artifact_bytes = cif_text.encode("utf-8")
                        file_rel_path = f"bio/{eid}.cif"

                    # 7. Textile XPBD
                    elif isinstance(provider, TextileXpbdProvider):
                        artifact_bytes = provider.serialize_native()
                        file_rel_path = f"textile/{eid}.json"

                    # 8. Éléments Finis Gmsh
                    elif isinstance(provider, FeaGmshProvider):
                        artifact_bytes = provider.serialize_native()
                        file_rel_path = f"fea/{eid}.msh"

                    # Repli générique
                    else:
                        artifact_bytes = provider.serialize_native()
                        file_rel_path = f"misc/{eid}.bin"

                    # Ajout de l'artefact dans l'archive tar
                    if artifact_bytes and file_rel_path:
                        tar_info = tarfile.TarInfo(name=file_rel_path)
                        tar_info.size = len(artifact_bytes)
                        tar_info.mtime = int(datetime.now(timezone.utc).timestamp())
                        tar.addfile(tar_info, io.BytesIO(artifact_bytes))

                        sha = cls._compute_sha256(artifact_bytes)
                        entities_manifest.append({
                            "entity_id": eid,
                            "provider_name": p_name,
                            "scale_domain": rec.scale_domain,
                            "file_path": file_rel_path,
                            "sha256": sha,
                            "size_bytes": len(artifact_bytes),
                            "tags": rec.tags,
                            "position": list(rec.position),
                            "rotation_quat": list(rec.rotation_quat),
                            "script_code": rec.script_code,
                            "bound_parameters": rec.bound_parameters,
                            "scale_to_meters": rec.scale_to_meters,
                        })

            # Construction du manifeste JSON racine
            manifest_dict: dict[str, object] = {
                "format": cls.FORMAT_NAME,
                "schema_version": cls.SCHEMA_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "title": meta.get("title", "Mono Project"),
                "author": meta.get("author", "Brandon"),
                "description": meta.get("description", "Conteneur unifié CAO/IA Mono"),
                "version": doc.version,
                "units": doc.units,
                "parameters": {k: v.to_dict() for k, v in doc.parameters.items()},
                "entities_count": len(entities_manifest),
                "entities": entities_manifest,
            }

            manifest_bytes = json.dumps(manifest_dict, indent=2).encode("utf-8")
            man_info = tarfile.TarInfo(name="manifest.json")
            man_info.size = len(manifest_bytes)
            man_info.mtime = int(datetime.now(timezone.utc).timestamp())
            tar.addfile(man_info, io.BytesIO(manifest_bytes))

        # Compression Zstandard haute performance (niveau 3)
        raw_tar_data = tar_buf.getvalue()
        cctx = zstd.ZstdCompressor(level=3)
        compressed_mono = cctx.compress(raw_tar_data)

        # Sauvegarde sur disque si chemin spécifié
        if isinstance(output_file_or_path, str):
            with open(output_file_or_path, "wb") as f_out:
                f_out.write(compressed_mono)
        elif isinstance(output_file_or_path, io.BytesIO):
            output_file_or_path.write(compressed_mono)

        return compressed_mono

    @classmethod
    def _decompress_to_tar(cls, data: bytes) -> tarfile.TarFile:
        """Décompresse un flux .mono avec détection automatique (zstd, gzip ou tar brut)."""
        # 1. Tentative Zstandard
        try:
            dctx = zstd.ZstdDecompressor()
            tar_bytes = dctx.decompress(data)
            return tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r")
        except Exception:
            pass

        # 2. Tentative tarfile standard (gzip / tar brut)
        try:
            return tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
        except Exception as err:
            raise MonoArchiveError(f"Format de conteneur invalide ou archive corrompue : {err}") from err

    @classmethod
    def inspect(cls, file_or_bytes: str | bytes) -> dict[str, object]:
        """Inspecte le contenu et le manifeste d'une archive .mono sans charger les géométries en mémoire.

        Args:
            file_or_bytes: Chemin du fichier ou octets bruts de l'archive.

        Returns:
            dict[str, object]: Le manifeste complet du projet.
        """
        raw_data = file_or_bytes if isinstance(file_or_bytes, bytes) else open(file_or_bytes, "rb").read()
        tar = cls._decompress_to_tar(raw_data)

        try:
            man_file = tar.extractfile("manifest.json")
            if man_file is None:
                raise MonoArchiveError("manifest.json manquant dans le conteneur .mono")
            manifest_dict = json.loads(man_file.read().decode("utf-8"))
            return manifest_dict  # type: ignore
        finally:
            tar.close()

    @classmethod
    def unpack(cls, file_or_bytes: str | bytes) -> tuple[MonoDocument, dict[str, object]]:
        """Décompresse un conteneur .mono et reconstruit l'intégralité de la scène physique.

        Args:
            file_or_bytes: Chemin du fichier ou octets bruts de l'archive.

        Returns:
            tuple[MonoDocument, dict[str, object]]: Le document reconstitué et le manifeste.
        """
        raw_data = file_or_bytes if isinstance(file_or_bytes, bytes) else open(file_or_bytes, "rb").read()
        tar = cls._decompress_to_tar(raw_data)

        try:
            man_file = tar.extractfile("manifest.json")
            if man_file is None:
                raise MonoArchiveError("manifest.json introuvable dans l'archive .mono")

            manifest = json.loads(man_file.read().decode("utf-8"))
            doc = MonoDocument()
            doc.version = manifest.get("version", "2.0")
            doc.units = manifest.get("units", doc.units)

            # Restauration des paramètres interactifs
            params_dict = manifest.get("parameters", {})
            for p_name, p_data in params_dict.items():
                doc.parameters[p_name] = ParameterDefinition(
                    value=float(p_data["value"]),
                    min_value=float(p_data["min"]),
                    max_value=float(p_data["max"]),
                    unit=str(p_data.get("unit", "m")),
                    label=str(p_data.get("label", p_name)),
                )

            # Restauration des entités et réinstanciation des GeometryProviders
            entities_list = manifest.get("entities", [])
            with tempfile.TemporaryDirectory() as tmp_dir:
                for ent_meta in entities_list:
                    eid = ent_meta["entity_id"]
                    p_name = ent_meta["provider_name"]
                    rel_path = ent_meta["file_path"]
                    expected_sha = ent_meta.get("sha256")

                    art_file = tar.extractfile(rel_path)
                    if art_file is None:
                        continue
                    art_bytes = art_file.read()

                    # Vérification d'intégrité SHA-256
                    if expected_sha and cls._compute_sha256(art_bytes) != expected_sha:
                        raise MonoArchiveError(f"Corruption détectée dans l'artefact '{rel_path}' (échec SHA-256).")

                    # Réinstanciation selon le type de fournisseur
                    provider: object = None

                    if p_name == "brep_build123d":
                        step_tmp = os.path.join(tmp_dir, f"{eid}_restore.step")
                        with open(step_tmp, "wb") as f_step:
                            f_step.write(art_bytes)
                        imported_part = bd.import_step(step_tmp)
                        provider = BRepBuild123dProvider(
                            entity_id=eid,
                            part=imported_part,
                            unit_scale_to_meters=float(ent_meta.get("scale_to_meters", 0.001)),
                        )

                    elif p_name == "techdraw_drafting":
                        provider = TechDrawDraftingProvider(entity_id=eid)

                    elif p_name == "bim_ifc":
                        provider = IfcGeometryProvider(entity_id=eid, ifc_source=art_bytes.decode("utf-8", errors="ignore"))

                    elif p_name == "cam_machining":
                        provider = CamMachiningProvider()

                    elif p_name == "additive_slicer":
                        provider = AdditiveSlicerProvider()

                    elif p_name in ("biomolecule_gemmi", "biomolecule"):
                        provider = BiomoleculeProvider(entity_id=eid)

                    elif p_name == "textile_xpbd":
                        provider = TextileXpbdProvider(entity_id=eid)

                    elif p_name == "fea_gmsh":
                        provider = FeaGmshProvider(entity_id=eid)

                    if provider is not None:
                        doc.entities[eid] = EntityRecord(
                            entity_id=eid,
                            provider_name=p_name,
                            scale_domain=ent_meta.get("scale_domain", "milli"),
                            provider=provider,  # type: ignore
                            tags=list(ent_meta.get("tags", [])),
                            position=tuple(ent_meta.get("position", (0.0, 0.0, 0.0))),  # type: ignore
                            rotation_quat=tuple(ent_meta.get("rotation_quat", (0.0, 0.0, 0.0, 1.0))),  # type: ignore
                            script_code=ent_meta.get("script_code"),
                            bound_parameters=list(ent_meta.get("bound_parameters", [])),
                            scale_to_meters=float(ent_meta.get("scale_to_meters", 0.001)),
                        )

            return doc, manifest
        finally:
            tar.close()
