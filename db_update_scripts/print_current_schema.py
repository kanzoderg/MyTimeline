import sqlite3

def print_schema():
    try:
        conn = sqlite3.connect('data.db')
        cursor = conn.cursor()
        
        print("--- Tables ---")
        # Query for all tables and their CREATE statements
        cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        
        if not tables:
            print("No tables found.")
        else:
            for table in tables:
                name = table[0]
                sql = table[1]
                print(f"\nTable: {name}")
                print(f"SQL: {sql}")
                print("-" * 40)

        print("\n--- Triggers ---")
        # Query for all triggers and their CREATE statements
        cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger';")
        triggers = cursor.fetchall()
        
        if not triggers:
            print("No triggers found.")
        else:
            for trigger in triggers:
                name = trigger[0]
                sql = trigger[1]
                print(f"\nTrigger: {name}")
                print(f"SQL: {sql}")
                print("-" * 40)

        print("\n--- Indexes ---")
        # Query for all indexes
        cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='index';")
        indexes = cursor.fetchall()
        
        if not indexes:
            print("No indexes found.")
        else:
            for index in indexes:
                name = index[0]
                sql = index[1]
                print(f"\nIndex: {name}")
                print(f"SQL: {sql}")
                print("-" * 40)

    except sqlite3.Error as e:
        print(f"Database error: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    print_schema()