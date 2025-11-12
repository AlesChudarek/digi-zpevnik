import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
PRIVATE_USERS_DIR = PROJECT_ROOT / "data" / "private" / "users"
sys.path.insert(0, str(BASE_DIR))

from scripts.seed_db import DEFAULT_DB_PATH as SEED_DB_DEFAULT, seed_database
from scripts.seed_users import seed_test_users

DEFAULT_DB_PATH = Path(SEED_DB_DEFAULT)
DEFAULT_SQL_FILE = BASE_DIR / "scripts" / "create_songbook_db.sql"


def initialize_database(db_path: Path, sql_file_path: Path):
    db_path = Path(db_path)
    sql_file_path = Path(sql_file_path)

    with sql_file_path.open("r", encoding="utf-8") as f:
        sql_script = f.read()

    os.makedirs(db_path.parent, exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.executescript(sql_script)
    conn.commit()
    conn.close()
    print(f"✅ Databáze vytvořena: {db_path}")


def reset_database(db_path: Path, sql_file_path: Path, seed: bool = True):
    db_path = Path(db_path)
    sql_file_path = Path(sql_file_path)

    if db_path.exists():
        db_path.unlink()
        print(f"🗑️  Původní databáze odstraněna: {db_path}")

    if PRIVATE_USERS_DIR.exists():
        shutil.rmtree(PRIVATE_USERS_DIR)
        print(f"🧹 Složka s privátními uživatelskými daty vymazána: {PRIVATE_USERS_DIR}")

    initialize_database(db_path, sql_file_path)

    if seed:
        seed_test_users(db_path)
        seed_database(db_path=db_path)
        print("🌱 Databáze naplněna výchozími daty.")
    else:
        print("ℹ️  Přeskakuji seeding, databáze je prázdná.")


def main():
    parser = argparse.ArgumentParser(description="Resetuje SQLite databázi a naplní ji výchozími daty.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Cesta k SQLite databázi.")
    parser.add_argument("--sql", default=str(DEFAULT_SQL_FILE), help="SQL soubor se schématem.")
    parser.add_argument("--skip-seed", action="store_true", help="Vytvoří databázi bez seed dat.")
    parser.add_argument("-empty", "-e", "--empty", action="store_true", help="Alias pro prázdnou inicializaci (bez seedů).")
    args = parser.parse_args()

    seed_enabled = not (args.skip_seed or args.empty)
    reset_database(args.db, args.sql, seed=seed_enabled)


if __name__ == "__main__":
    main()
