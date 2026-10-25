import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

conn = sqlite3.connect(r"C:\Users\brand\tmp\ai-backup\gemini_logs_vault.sqlite")
cur = conn.cursor()

print("=== 📊 STATISTIQUES GLOBALES DU VAULT OBSIDIAN ===")
total_notes = cur.execute("SELECT count(*) FROM notes").fetchone()[0]
total_tags = cur.execute("SELECT count(*) FROM tags").fetchone()[0]
print(f"Total fiches indexées : {total_notes}")
print(f"Total tags référencés : {total_tags}")

print("\n=== 🏷️ RÉPARTITION PAR CATÉGORIE ===")
for row in cur.execute("SELECT category, count(*) FROM notes GROUP BY category ORDER BY count(*) DESC LIMIT 8"):
    print(f"  {row[0]:<20} : {row[1]} notes")

print("\n=== 🚦 RÉPARTITION PAR STATUT ===")
for row in cur.execute("SELECT status, count(*) FROM notes GROUP BY status ORDER BY count(*) DESC"):
    print(f"  {row[0]:<20} : {row[1]} notes")

print("\n=== 📁 LES FICHES DU PROJET MONO (_Active) ===")
for row in cur.execute("SELECT date, title, audit_score FROM notes WHERE rel_path LIKE '%Projects/_Active%' AND (rel_path LIKE '%mono%' OR title LIKE '%Mono%') ORDER BY date DESC LIMIT 15"):
    print(f"  [{row[0]}] {row[1][:48]:<48} | Score: {row[2]}%")

print("\n=== 🏷️ TOP 10 DES TAGS ===")
for row in cur.execute("SELECT tag, count(*) FROM tags GROUP BY tag ORDER BY count(*) DESC LIMIT 10"):
    print(f"  #{row[0]:<20} : {row[1]} occurrences")

conn.close()
