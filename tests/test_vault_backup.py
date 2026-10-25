from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.vault_backup_sqlite import parse_frontmatter, extract_sections, init_database, run_hybrid_backup


def test_parse_frontmatter():
    sample = """---
date: 2026-10-25 15:30
category: python
tags:
  - task-log
  - mono
status: completed
type: new-feature
audit_score: 98
iterations: 1
---

# Titre Principal

## Résumé
Ceci est un test de parsing frontmatter.
"""
    data, body = parse_frontmatter(sample)
    assert data["date"] == "2026-10-25 15:30"
    assert data["category"] == "python"
    assert data["status"] == "completed"
    assert data["audit_score"] == 98
    assert data["iterations"] == 1
    assert "task-log" in data["tags"]
    assert "mono" in data["tags"]
    assert "# Titre Principal" in body


def test_extract_sections():
    body = """
## Résumé
Contenu du résumé.

## Fichiers Modifiés
- file1.py: modification
- file2.ts: ajout

## Erreurs & Résolutions
- Erreur 1: résolue
"""
    sections = extract_sections(body)
    assert "Résumé" in sections
    assert "Contenu du résumé." in sections["Résumé"]
    assert "Fichiers Modifiés" in sections
    assert "Erreurs & Résolutions" in sections


def test_init_database_and_backup(tmp_path: Path):
    vault_dir = tmp_path / "mock_vault"
    vault_dir.mkdir()

    note1 = vault_dir / "note1.md"
    note1.write_text("""---
date: 2026-10-25 10:00
category: python
tags:
  - test-tag
status: completed
type: bug-fixed
audit_score: 100
iterations: 1
---

# Test Note 1

## Résumé
Première note de test unitaire.
""", encoding="utf-8")

    backup_dir = tmp_path / "mock_backup"

    res = run_hybrid_backup(vault_dir=vault_dir, backup_dir=backup_dir)

    assert res["status"] == "success"
    assert res["indexed_notes"] == 1

    db_path = backup_dir / "gemini_logs_vault.sqlite"
    assert db_path.exists()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    row = cur.execute("SELECT title, category, audit_score FROM notes WHERE rel_path = 'note1.md'").fetchone()
    assert row is not None
    assert row[0] == "Test Note 1"
    assert row[1] == "python"
    assert row[2] == 100

    tags = [r[0] for r in cur.execute("SELECT tag FROM tags").fetchall()]
    assert "test-tag" in tags
    conn.close()
