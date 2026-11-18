"""Read-only probe of the live pulse.db (no writes, no schema changes)."""
import sqlite3

c = sqlite3.connect("file:pulse.db?mode=ro", uri=True)
c.row_factory = sqlite3.Row

tables = [r[0] for r in c.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
print("tables:", tables)

for t in tables:
    n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {n} rows")

print("\nservices:", [r[0] for r in c.execute("SELECT name FROM services")])

print("\nusers table exists?", bool(
    c.execute("SELECT 1 FROM sqlite_master WHERE name='users'").fetchone()))

# journal mode / files in use
print("journal_mode:", c.execute("PRAGMA journal_mode").fetchone()[0])
print("foreign_keys:", c.execute("PRAGMA foreign_keys").fetchone()[0])

# schema version info (relevant for migrations)
print("user_version:", c.execute("PRAGMA user_version").fetchone()[0])

# schema of one existing table for style reference
print("\nservices DDL:")
print(c.execute("SELECT sql FROM sqlite_master WHERE name='services'").fetchone()[0])
