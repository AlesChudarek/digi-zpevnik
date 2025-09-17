
import sqlite3

import os
import sqlite3

def initialize_database(db_path: str, sql_file_path: str):
    with open(sql_file_path, 'r', encoding='utf-8') as f:
        sql_script = f.read()

    # Ensure directory exists
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.executescript(sql_script)
    conn.commit()
    conn.close()
    print(f"Databáze vytvořena: {db_path}")

# Příklad použití
if __name__ == "__main__":
    initialize_database("instance/zpevnik.db", "backend/scripts/create_songbook_db.sql")
