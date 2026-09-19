import sqlite3, sys
tid = sys.argv[1]
rows = sqlite3.connect("traces.db").execute(
    "SELECT node, duration_ms, detail, created_at FROM traces"
    " WHERE thread_id=? ORDER BY id", (tid,)).fetchall()
total = 0
for node, ms, detail, ts in rows:
    total += ms
    print(f"{ts}  {node:<10} {ms:>6}ms  {detail}")
print(f"总耗时 {total/1000:.1f}s")