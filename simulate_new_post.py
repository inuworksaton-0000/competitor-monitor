"""Test helper: delete the newest stored article so the next scan treats it as new."""

import sqlite3

from config import AppConfig

config = AppConfig.from_env()
conn = sqlite3.connect(config.sqlite_db_path)
cur = conn.execute(
    "DELETE FROM articles WHERE id = "
    "(SELECT id FROM articles ORDER BY published_at DESC, id DESC LIMIT 1)"
)
conn.commit()
conn.close()
print(f"Deleted {cur.rowcount} article row. Run the check again to see it flagged as new.")