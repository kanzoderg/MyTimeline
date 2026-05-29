import sqlite3

def update_schema():
    try:
        # Connect to the database
        conn = sqlite3.connect('data.db')
        cursor = conn.cursor()

        # 1. Remove triggers
        triggers = ['posts_ad', 'posts_ai', 'posts_au']
        for trigger in triggers:
            cursor.execute(f"DROP TRIGGER IF EXISTS {trigger}")
            print(f"Dropped trigger: {trigger}")

        # 2. Delete VIRTUAL TABLE posts_fts
        cursor.execute("DROP TABLE IF EXISTS posts_fts")
        print("Dropped table: posts_fts")

        # 3. Add column 'tags TEXT' to table 'posts'
        # We check if the column exists first to prevent errors
        cursor.execute("PRAGMA table_info(posts)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'tags' not in columns:
            cursor.execute("ALTER TABLE posts ADD COLUMN tags TEXT")
            print("Added column 'tags' to 'posts'.")
        else:
            print("Column 'tags' already exists.")

        # Save changes
        conn.commit()
        print("Schema update completed successfully.")

    except sqlite3.Error as e:
        print(f"Database error: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    update_schema()