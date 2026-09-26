"""Dump the NCERT database's schema so NCERT_SQL can be written correctly.
Run: python schema.py   (reads NCERT_DSN from .env — no hardcoded password)
"""
import psycopg

from simgen.__main__ import load_env

load_env()
import os
conn = psycopg.connect(os.environ["NCERT_DSN"])
cur = conn.cursor()

print("Tables in the database (grouped by schema):\n")

cur.execute("""
    SELECT table_schema, table_name
    FROM information_schema.tables
    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
    ORDER BY table_schema, table_name
""")
for schema, table in cur.fetchall():
    print(f"### {schema}.{table}")
    cur.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
    """, (schema, table))
    for col, dtype in cur.fetchall():
        print(f"  {col}: {dtype}")
    print()
