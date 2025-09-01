
import sqlite3

def initialize_database(db_path: str, sql_file_path: str):
    with open(sql_file_path, 'r', encoding='utf-8') as f:
        sql_script = f.read()

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.executescript(sql_script)
    conn.commit()
    conn.close()
    print(f"Databáze vytvořena: {db_path}")

# Příklad použití
if __name__ == "__main__":
    initialize_database("backend/db/zpevnik.db", "backend/db/create_songbook_db.sql")
