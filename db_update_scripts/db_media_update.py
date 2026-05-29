import sqlite3
# db_update
"""CREATE TABLE IF NOT EXISTS media (
                media_id TEXT PRIMARY KEY,
                post_id TEXT,
                file_name TEXT,
                uid TEXT,
                type TEXT,
                ext INTEGER,
)"""
# add size and duration to media table, in data.db
"""size INTEGER,
duration INTEGER"""

def update_media_table():
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    cursor.execute("ALTER TABLE media ADD COLUMN size INTEGER")
    cursor.execute("ALTER TABLE media ADD COLUMN duration INTEGER")
    conn.commit()
    cursor.close()
    conn.close()

if __name__ == "__main__":
    update_media_table()