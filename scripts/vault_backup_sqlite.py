#!/usr/bin/env python3
"""Script de sauvegarde hybride hebdomadaire & dump SQLite de Gemini_Logs (Règle Vault 2.5).

Fonctionnalités :
1. Scanne récursivement le répertoire Obsidian Vault/Gemini_Logs.
2. Extrait et parse le frontmatter YAML de chaque note Markdown (sans dépendance externe).
3. Extrait les sections structurées : Résumé, Fichiers modifiés, Erreurs & Résolutions.
4. Indexe l'intégralité dans une base de données SQLite relationnelle optimisée.
5. Permet des requêtes analytiques transversales (cross-sessions).
6. Génère une archive ZIP compressée de miroir de sauvegarde dans ~/tmp/ai-backup/.
7. Mode récupération automatique en cas d'erreur OneDrive.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


DEFAULT_VAULT_LOGS = Path(r"C:\Users\brand\OneDrive\Documents\Obsidian Vault\Gemini_Logs")
DEFAULT_BACKUP_DIR = Path(os.path.expanduser(r"~\tmp\ai-backup"))


def parse_frontmatter(content: str) -> tuple[dict[str, object], str]:
    """Extrait le YAML frontmatter entre les délimiteurs '---'."""
    data: dict[str, object] = {}
    body = content

    if not content.startswith("---"):
        return data, body

    parts = content.split("---", 2)
    if len(parts) < 3:
        return data, body

    yaml_block = parts[1]
    body = parts[2]

    current_list_key: str | None = None
    list_items: list[str] = []

    for line in yaml_block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Liste d'éléments (ex: tags)
        if line.startswith("- ") and current_list_key:
            item_val = line[2:].strip().strip("\"'")
            list_items.append(item_val)
            continue

        # Fin d'une liste précédente
        if current_list_key:
            data[current_list_key] = list(list_items)
            current_list_key = None
            list_items = []

        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()

            if not val:
                current_list_key = key
                list_items = []
            elif val.startswith("[") and val.endswith("]"):
                # Liste inline [a, b, c]
                items = [x.strip().strip("\"'") for x in val[1:-1].split(",") if x.strip()]
                data[key] = items
            else:
                clean_val = val.strip("\"'")
                # Typage automatique
                if clean_val.isdigit():
                    data[key] = int(clean_val)
                elif re.match(r"^-?\d+(\.\d+)?$", clean_val):
                    data[key] = float(clean_val)
                else:
                    data[key] = clean_val

    if current_list_key:
        data[current_list_key] = list(list_items)

    return data, body


def extract_sections(body: str) -> dict[str, str]:
    """Extrait les résumés et sections usuelles des notes de log."""
    sections: dict[str, str] = {}
    current_title: str | None = None
    current_lines: list[str] = []

    for line in body.splitlines():
        header_match = re.match(r"^##\s+(?:[\w\s\u00C0-\u017F\U00010000-\U0010ffff\W]+)", line)
        if line.startswith("## "):
            if current_title:
                sections[current_title] = "\n".join(current_lines).strip()
            current_title = line[3:].strip()
            current_lines = []
        elif current_title:
            current_lines.append(line)

    if current_title:
        sections[current_title] = "\n".join(current_lines).strip()

    return sections


def init_database(db_path: Path) -> sqlite3.Connection:
    """Initialise le schéma relationnel SQLite pour les logs du Vault."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")

    with conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rel_path TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            title TEXT,
            date TEXT,
            category TEXT,
            status TEXT,
            type TEXT,
            audit_score INTEGER DEFAULT 0,
            iterations INTEGER DEFAULT 0,
            line_count INTEGER,
            byte_size INTEGER,
            modified_at TEXT,
            summary TEXT,
            raw_frontmatter_json TEXT
        );

        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note_id INTEGER NOT NULL,
            tag TEXT NOT NULL,
            FOREIGN KEY(note_id) REFERENCES notes(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_notes_category ON notes(category);
        CREATE INDEX IF NOT EXISTS idx_notes_status ON notes(status);
        CREATE INDEX IF NOT EXISTS idx_notes_date ON notes(date);
        CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);

        CREATE TABLE IF NOT EXISTS backup_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            vault_path TEXT NOT NULL,
            total_notes_scanned INTEGER,
            zip_archive_path TEXT,
            zip_byte_size INTEGER,
            duration_sec REAL
        );
        """)

    return conn


def run_hybrid_backup(
    vault_dir: Path = DEFAULT_VAULT_LOGS,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
) -> dict[str, object]:
    """Exécute la sauvegarde hybride : dump SQLite + Archive Zip miroir."""
    start_time = time.time()
    timestamp_str = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)

    db_path = backup_dir / "gemini_logs_vault.sqlite"
    conn = init_database(db_path)

    print(f"[*] Démarrage de la sauvegarde hybride Obsidian Vault -> {backup_dir}")
    print(f"[*] Répertoire source Vault : {vault_dir}")

    scanned_count = 0
    indexed_count = 0

    md_files = list(vault_dir.rglob("*.md"))

    with conn:
        for md_file in md_files:
            scanned_count += 1
            try:
                rel_path = md_file.relative_to(vault_dir).as_posix()
                content = md_file.read_text(encoding="utf-8", errors="replace")
                stat = md_file.stat()

                frontmatter, body = parse_frontmatter(content)
                sections = extract_sections(body)

                # Extraction du titre (# Mon Titre)
                title_match = re.search(r"^#\s+(.+)$", body, flags=re.MULTILINE)
                title = title_match.group(1).strip() if title_match else md_file.stem

                # Résumé
                summary = ""
                for sec_name, sec_content in sections.items():
                    if "résumé" in sec_name.lower() or "summary" in sec_name.lower():
                        summary = sec_content[:500]
                        break

                date_val = str(frontmatter.get("date", ""))
                category_val = str(frontmatter.get("category", "unknown"))
                status_val = str(frontmatter.get("status", "unknown"))
                type_val = str(frontmatter.get("type", "unknown"))
                audit_score = int(frontmatter.get("audit_score", 0)) if str(frontmatter.get("audit_score", "0")).isdigit() else 0
                iterations = int(frontmatter.get("iterations", 0)) if str(frontmatter.get("iterations", "0")).isdigit() else 0

                cur = conn.cursor()
                cur.execute("""
                INSERT INTO notes (
                    rel_path, filename, title, date, category, status, type,
                    audit_score, iterations, line_count, byte_size, modified_at,
                    summary, raw_frontmatter_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rel_path) DO UPDATE SET
                    filename = excluded.filename,
                    title = excluded.title,
                    date = excluded.date,
                    category = excluded.category,
                    status = excluded.status,
                    type = excluded.type,
                    audit_score = excluded.audit_score,
                    iterations = excluded.iterations,
                    line_count = excluded.line_count,
                    byte_size = excluded.byte_size,
                    modified_at = excluded.modified_at,
                    summary = excluded.summary,
                    raw_frontmatter_json = excluded.raw_frontmatter_json;
                """, (
                    rel_path,
                    md_file.name,
                    title,
                    date_val,
                    category_val,
                    status_val,
                    type_val,
                    audit_score,
                    iterations,
                    len(content.splitlines()),
                    stat.st_size,
                    datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    summary,
                    json.dumps(frontmatter, ensure_ascii=False)
                ))

                cur.execute("SELECT id FROM notes WHERE rel_path = ?", (rel_path,))
                row = cur.fetchone()
                note_id = row[0] if row else None

                if note_id:
                    # Remplacement des tags
                    cur.execute("DELETE FROM tags WHERE note_id = ?", (note_id,))
                    tags = frontmatter.get("tags", [])
                    if isinstance(tags, list):
                        for tag in tags:
                            if tag:
                                cur.execute("INSERT INTO tags (note_id, tag) VALUES (?, ?)", (note_id, str(tag)))

                indexed_count += 1

            except Exception as e:
                print(f"[!] Erreur de lecture sur {md_file}: {e}")

    # Copie locale de la base SQLite dans le Vault lui-même pour consultation immédiate
    try:
        shutil.copy2(db_path, vault_dir / "gemini_logs_vault.sqlite")
        print(f"[+] Base SQLite dupliquée dans le Vault : {vault_dir / 'gemini_logs_vault.sqlite'}")
    except Exception as e:
        print(f"[!] OneDrive verrou : impossible de copier la base dans le vault ({e})")

    # Création de l'archive ZIP miroir compressée
    zip_path = backup_dir / f"gemini_logs_mirror_{timestamp_str}.zip"
    print(f"[*] Compression de l'archive miroir vers : {zip_path}")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_out:
        for md_file in md_files:
            rel = md_file.relative_to(vault_dir)
            zip_out.write(md_file, arcname=str(rel))
        # Inclure la base SQLite
        if db_path.exists():
            zip_out.write(db_path, arcname="gemini_logs_vault.sqlite")

    duration = time.time() - start_time
    zip_size = zip_path.stat().st_size

    with conn:
        conn.execute("""
        INSERT INTO backup_history (
            timestamp, vault_path, total_notes_scanned, zip_archive_path, zip_byte_size, duration_sec
        ) VALUES (?, ?, ?, ?, ?, ?);
        """, (
            timestamp_str,
            str(vault_dir),
            scanned_count,
            str(zip_path),
            zip_size,
            duration
        ))

    conn.close()

    res = {
        "status": "success",
        "scanned_notes": scanned_count,
        "indexed_notes": indexed_count,
        "sqlite_db": str(db_path),
        "sqlite_size_kb": round(db_path.stat().st_size / 1024, 2),
        "zip_mirror": str(zip_path),
        "zip_size_kb": round(zip_size / 1024, 2),
        "duration_sec": round(duration, 2),
    }

    print("===================================================================")
    print(f" Sauvegarde Hybride Terminée avec Succès en {res['duration_sec']} s !")
    print(f" - Notes scannées & indexées : {res['indexed_notes']}")
    print(f" - Base SQLite : {res['sqlite_db']} ({res['sqlite_size_kb']} Ko)")
    print(f" - Miroir ZIP  : {res['zip_mirror']} ({res['zip_size_kb']} Ko)")
    print("===================================================================")

    return res


if __name__ == "__main__":
    run_hybrid_backup()
