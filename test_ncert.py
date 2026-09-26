import os
import psycopg
dsn = os.getenv("NCERT_DSN", "postgresql://prayog_ro:1MXmvkHVZCwXzt2RyCYbVFHwIpi9cyfD@localhost:5432/gyansetu")
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM knowledge_graph.graph_nodes")
    print(f"Total graph nodes: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM knowledge_graph.graph_nodes WHERE ground_truth_content IS NOT NULL")
    print(f"Nodes with content: {cur.fetchone()[0]}")
