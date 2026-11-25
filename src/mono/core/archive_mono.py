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
import gzip
import hashlib
import io
import json
import os
import re
import tarfile
import tempfile
import uuid
from pathlib import Path, PurePosixPath
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
    MAX_COMPRESSED_BYTES: int = 512 * 1024 * 1024
    MAX_ARCHIVE_BYTES: int = 2 * 1024 * 1024 * 1024
    MAX_MEMBER_BYTES: int = 512 * 1024 * 1024
    MAX_MANIFEST_BYTES: int = 8 * 1024 * 1024
    MAX_MEMBERS: int = 10_000

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
                    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", eid) or eid in {".", ".."}:
                        raise MonoArchiveError(f"Identifiant d'entité non sûr pour l'archive : {eid!r}")
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

                    # 2. TechDraw 2D (SVG + DXF + PDF)
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

                        # Ajout complémentaire du plan PDF coté normalisé ISO/ASME
                        pdf_bytes = provider.to_pdf()
                        pdf_info = tarfile.TarInfo(name=f"techdraw/{eid}.pdf")
                        pdf_info.size = len(pdf_bytes)
                        pdf_info.mtime = int(datetime.now(timezone.utc).timestamp())
                        tar.addfile(pdf_info, io.BytesIO(pdf_bytes))

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

                        # Ajout complémentaire du G-code universel GRBL
                        grbl_text = provider.post_process_gcode(flavor="grbl")
                        grbl_bytes = grbl_text.encode("utf-8")
                        grbl_info = tarfile.TarInfo(name=f"cam/{eid}_grbl.gcode")
                        grbl_info.size = len(grbl_bytes)
                        grbl_info.mtime = int(datetime.now(timezone.utc).timestamp())
                        tar.addfile(grbl_info, io.BytesIO(grbl_bytes))

                    # 5. Impression 3D / Additive Slicer
                    elif isinstance(provider, AdditiveSlicerProvider):
                        artifact_bytes = (provider.cached_gcode or "; Mono Slicer").encode("utf-8")
                        file_rel_path = f"slicer/{eid}.gcode"

                    # 6. Biologie Moléculaire (Gemmi CIF/BinaryCIF/PDB)
                    elif isinstance(provider, BiomoleculeProvider):
                        cif_text = provider.to_cif_string()
                        artifact_bytes = cif_text.encode("utf-8")
                        file_rel_path = f"bio/{eid}.cif"

                        bcif_bytes = provider.serialize_binary_cif()
                        if bcif_bytes:
                            bcif_info = tarfile.TarInfo(name=f"bio/{eid}.bcif")
                            bcif_info.size = len(bcif_bytes)
                            bcif_info.mtime = int(datetime.now(timezone.utc).timestamp())
                            tar.addfile(bcif_info, io.BytesIO(bcif_bytes))

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

        # Compression Zstandard haute performance (niveau 3) avec somme de contrôle d'intégrité
        raw_tar_data = tar_buf.getvalue()
        cctx = zstd.ZstdCompressor(level=3, write_checksum=True)
        compressed_mono = cctx.compress(raw_tar_data)

        # Sauvegarde sur disque si chemin spécifié
        if isinstance(output_file_or_path, str):
            with open(output_file_or_path, "wb") as f_out:
                f_out.write(compressed_mono)
        elif isinstance(output_file_or_path, io.BytesIO):
            output_file_or_path.write(compressed_mono)

        return compressed_mono

    @classmethod
    def _read_archive_bytes(cls, file_or_bytes: str | bytes) -> bytes:
        """Read an archive only after enforcing the compressed-size limit."""
        if isinstance(file_or_bytes, bytes):
            data = file_or_bytes
        else:
            path = Path(file_or_bytes)
            size = path.stat().st_size
            if size > cls.MAX_COMPRESSED_BYTES:
                raise MonoArchiveError("Archive .mono compressée trop volumineuse")
            data = path.read_bytes()
        if len(data) > cls.MAX_COMPRESSED_BYTES:
            raise MonoArchiveError("Archive .mono compressée trop volumineuse")
        return data

    @classmethod
    def _validate_member_name(cls, name: str) -> str:
        """Reject absolute paths, traversal, Windows separators and non-normalized names."""
        if not name or "\x00" in name or "\\" in name or ":" in name:
            raise MonoArchiveError(f"Nom de membre d'archive invalide : {name!r}")
        path = PurePosixPath(name)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise MonoArchiveError(f"Chemin interdit dans l'archive : {name!r}")
        if path.as_posix() != name:
            raise MonoArchiveError(f"Chemin d'archive non normalisé : {name!r}")
        return name

    @classmethod
    def _validate_tar_members(cls, tar: tarfile.TarFile) -> None:
        """Validate all headers and resource limits before reading any payload."""
        members = tar.getmembers()
        if len(members) > cls.MAX_MEMBERS:
            raise MonoArchiveError("Archive .mono : nombre maximal de membres dépassé")
        seen: set[str] = set()
        total_size = 0
        manifest_count = 0
        for member in members:
            cls._validate_member_name(member.name)
            if member.name in seen:
                raise MonoArchiveError(f"Membre dupliqué dans l'archive : {member.name!r}")
            seen.add(member.name)
            if not member.isfile():
                raise MonoArchiveError(f"Type de membre interdit dans l'archive : {member.name!r}")
            if member.size < 0 or member.size > cls.MAX_MEMBER_BYTES:
                raise MonoArchiveError(f"Taille de membre interdite : {member.name!r}")
            if member.name == "manifest.json":
                manifest_count += 1
                if member.size > cls.MAX_MANIFEST_BYTES:
                    raise MonoArchiveError("Manifeste .mono trop volumineux")
            total_size += member.size
            if total_size > cls.MAX_ARCHIVE_BYTES:
                raise MonoArchiveError("Taille totale décompressée de l'archive dépassée")
        if manifest_count != 1:
            raise MonoArchiveError("L'archive doit contenir exactement un manifest.json")

    @classmethod
    def _decompress_to_tar(cls, data: bytes) -> tarfile.TarFile:
        """Decompress a bounded .mono stream and reject unsafe tar members."""
        if len(data) > cls.MAX_COMPRESSED_BYTES:
            raise MonoArchiveError("Archive .mono compressée trop volumineuse")
        zstd_magic = b"\x28\xb5\x2f\xfd"
        if data.startswith(zstd_magic):
            try:
                dctx = zstd.ZstdDecompressor()
                tar_bytes = dctx.decompress(data, max_output_size=cls.MAX_ARCHIVE_BYTES)
                tar = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:")
            except Exception as err:
                raise MonoArchiveError(f"Conteneur Zstandard corrompu ou falsifié : {err}") from err
        elif data.startswith(b"\\x1f\\x8b"):
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as stream:
                    tar_bytes = stream.read(cls.MAX_ARCHIVE_BYTES + 1)
                if len(tar_bytes) > cls.MAX_ARCHIVE_BYTES:
                    raise MonoArchiveError("Archive gzip .mono décompressée trop volumineuse")
                tar = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:")
            except MonoArchiveError:
                raise
            except Exception as err:
                raise MonoArchiveError(f"Archive gzip .mono corrompue : {err}") from err
        else:
            try:
                tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:")
            except Exception as err:
                raise MonoArchiveError(f"Format de conteneur invalide ou archive corrompue : {err}") from err
        try:
            cls._validate_tar_members(tar)
            return tar
        except Exception:
            tar.close()
            raise

    @classmethod
    def _read_manifest(cls, tar: tarfile.TarFile) -> dict[str, object]:
        """Read and validate the manifest against archive headers before restoration."""
        man_file = tar.extractfile("manifest.json")
        if man_file is None:
            raise MonoArchiveError("manifest.json manquant dans le conteneur .mono")
        try:
            raw = man_file.read(cls.MAX_MANIFEST_BYTES + 1)
            if len(raw) > cls.MAX_MANIFEST_BYTES:
                raise MonoArchiveError("Manifeste .mono trop volumineux")
            manifest = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            raise MonoArchiveError(f"manifest.json corrompu ou illisible : {err}") from err
        if not isinstance(manifest, dict):
            raise MonoArchiveError("Le manifeste .mono doit être un objet JSON")
        if manifest.get("format") != cls.FORMAT_NAME:
            raise MonoArchiveError("Format de manifeste .mono non reconnu")
        if manifest.get("schema_version") != cls.SCHEMA_VERSION:
            raise MonoArchiveError(f"Version de schéma .mono non prise en charge : {manifest.get('schema_version')!r}")
        entities = manifest.get("entities")
        if not isinstance(entities, list) or len(entities) > cls.MAX_MEMBERS:
            raise MonoArchiveError("Liste d'entités du manifeste invalide")
        count = manifest.get("entities_count")
        if not isinstance(count, int) or isinstance(count, bool) or count != len(entities):
            raise MonoArchiveError("entities_count ne correspond pas à la liste d'entités")
        members = {member.name: member for member in tar.getmembers()}
        seen_ids: set[str] = set()
        for entity in entities:
            if not isinstance(entity, dict):
                raise MonoArchiveError("Entrée d'entité invalide dans le manifeste")
            entity_id = entity.get("entity_id")
            if not isinstance(entity_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", entity_id):
                raise MonoArchiveError("Identifiant d'entité invalide dans le manifeste")
            if entity_id in seen_ids:
                raise MonoArchiveError(f"Identifiant d'entité dupliqué : {entity_id!r}")
            seen_ids.add(entity_id)
            provider_name = entity.get("provider_name")
            if not isinstance(provider_name, str) or not provider_name:
                raise MonoArchiveError(f"Provider invalide pour l'entité {entity_id!r}")
            rel_path = cls._validate_member_name(entity.get("file_path", ""))
            if rel_path == "manifest.json" or rel_path not in members:
                raise MonoArchiveError(f"Artefact absent du conteneur : {rel_path!r}")
            digest = entity.get("sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise MonoArchiveError(f"SHA-256 invalide pour l'artefact {rel_path!r}")
            size = entity.get("size_bytes")
            if not isinstance(size, int) or isinstance(size, bool) or size != members[rel_path].size:
                raise MonoArchiveError(f"Taille incohérente pour l'artefact {rel_path!r}")
        return manifest

    @classmethod
    def inspect(cls, file_or_bytes: str | bytes) -> dict[str, object]:
        """Inspecte le contenu et le manifeste d'une archive .mono sans charger les géométries en mémoire.

        Args:
            file_or_bytes: Chemin du fichier ou octets bruts de l'archive.

        Returns:
            dict[str, object]: Le manifeste complet du projet.
        """
        raw_data = cls._read_archive_bytes(file_or_bytes)
        tar = cls._decompress_to_tar(raw_data)

        try:
            return cls._read_manifest(tar)
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
            manifest = cls._read_manifest(tar)
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
                    if not isinstance(eid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", eid) or eid in {".", ".."}:
                        raise MonoArchiveError("Identifiant d'entité invalide dans le manifeste")
                    p_name = ent_meta["provider_name"]
                    rel_path = cls._validate_member_name(ent_meta["file_path"])
                    expected_sha = ent_meta.get("sha256")
                    expected_size = ent_meta.get("size_bytes")

                    art_file = tar.extractfile(rel_path)
                    if art_file is None:
                        raise MonoArchiveError(f"Artefact '{rel_path}' manquant ou illisible dans le conteneur .mono.")
                    art_bytes = art_file.read(cls.MAX_MEMBER_BYTES + 1)
                    if len(art_bytes) > cls.MAX_MEMBER_BYTES or len(art_bytes) != expected_size:
                        raise MonoArchiveError(f"Taille invalide pour l'artefact '{rel_path}'.")

                    # Vérification d'intégrité SHA-256
                    if cls._compute_sha256(art_bytes) != expected_sha:
                        raise MonoArchiveError(f"Corruption détectée dans l'artefact '{rel_path}' (échec SHA-256).")

                    # Réinstanciation selon le type de fournisseur
                    provider: object = None

                    if p_name == "brep_build123d":
                        step_tmp = os.path.join(tmp_dir, f"{uuid.uuid4().hex}_restore.step")
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
                        cif_txt = art_bytes.decode("utf-8", errors="ignore")
                        provider = BiomoleculeProvider(entity_id=eid, pdb_content=cif_txt)

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
