"""Zavede příznak songs.is_non_song a převede intro/outro obrázky na nepísničkové stránky.

Nepísničková stránka je běžná stránka zpěvníku bez písničky (úvod, oddělovač,
rejstřík). Dřív se poznávala podle názvu a extra stránky se držely ve zvláštní
tabulce songbook_intro_outro_images, která umí jen začátek a konec zpěvníku.
Po migraci je to jedna a ta samá věc jako ostatní stránky, takže se dá řadit
kamkoliv mezi písničky.

Skript je idempotentní - opakované spuštění už nic nezmění.

Použití:
    python backend/scripts/migrate_non_song_pages.py [--db CESTA] [--dry-run]
"""
import argparse
import sqlite3
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB = PROJECT_ROOT / "backend" / "instance" / "zpevnik.db"

NON_SONG_TITLE = "<Prázdná strana>"
NON_SONG_AUTHOR = "System"


def column_exists(cur, table, column):
    return any(row[1] == column for row in cur.execute(f"PRAGMA table_info({table})"))


def add_flag_column(cur):
    if column_exists(cur, "songs", "is_non_song"):
        print("  • sloupec songs.is_non_song už existuje, přeskakuji")
        return False
    cur.execute("ALTER TABLE songs ADD COLUMN is_non_song INTEGER NOT NULL DEFAULT 0")
    print("  ✅ přidán sloupec songs.is_non_song")
    return True


def backfill_flag(cur):
    cur.execute(
        "UPDATE songs SET is_non_song = 1 "
        "WHERE is_non_song = 0 AND (title LIKE 'Non-song page%' OR title = ?)",
        (NON_SONG_TITLE,),
    )
    if cur.rowcount:
        print(f"  ✅ příznak dopočítán u {cur.rowcount} stávajících prázdných stránek")
    else:
        print("  • žádné stávající prázdné stránky k dopočítání")


def system_author_id(cur):
    row = cur.execute("SELECT id FROM authors WHERE name = ?", (NON_SONG_AUTHOR,)).fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO authors (name) VALUES (?)", (NON_SONG_AUTHOR,))
    return cur.lastrowid


def convert_intro_outro(cur):
    rows = cur.execute(
        "SELECT id, songbook_id, type, image_path, COALESCE(sort_order, 0) "
        "FROM songbook_intro_outro_images ORDER BY songbook_id, type, sort_order, id"
    ).fetchall()
    if not rows:
        print("  • žádné intro/outro stránky k převodu")
        return 0

    by_book = {}
    for row_id, book_id, kind, image_path, sort_order in rows:
        by_book.setdefault(book_id, {"intro": [], "outro": []})[kind].append(
            (row_id, image_path, sort_order)
        )

    author_id = system_author_id(cur)
    converted = 0

    for book_id, buckets in sorted(by_book.items()):
        intros, outros = buckets["intro"], buckets["outro"]

        # Intro pages belong in front, so existing pages shift back by that many.
        if intros:
            cur.execute(
                "UPDATE songbook_pages SET page_number = page_number + ? WHERE songbook_id = ?",
                (len(intros), book_id),
            )

        max_page = cur.execute(
            "SELECT COALESCE(MAX(page_number), 0) FROM songbook_pages WHERE songbook_id = ?",
            (book_id,),
        ).fetchone()[0]

        plan = [(page_no, item) for page_no, item in enumerate(intros, start=1)]
        plan += [(max_page + offset, item) for offset, item in enumerate(outros, start=1)]

        for page_number, (row_id, image_path, _sort) in plan:
            song_id = f"{book_id}_ns_{uuid.uuid4().hex[:8]}"
            cur.execute(
                "INSERT INTO songs (id, title, author_id, is_non_song) VALUES (?, ?, ?, 1)",
                (song_id, NON_SONG_TITLE, author_id),
            )
            cur.execute(
                "INSERT INTO song_images (song_id, image_path) VALUES (?, ?)",
                (song_id, image_path),
            )
            cur.execute(
                "INSERT INTO songbook_pages (songbook_id, song_id, page_number) VALUES (?, ?, ?)",
                (book_id, song_id, page_number),
            )
            cur.execute("DELETE FROM songbook_intro_outro_images WHERE id = ?", (row_id,))
            converted += 1

        print(f"  ✅ {book_id}: převedeno {len(intros)} intro + {len(outros)} outro")

    return converted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Cesta k SQLite databázi.")
    parser.add_argument("--dry-run", action="store_true", help="Nic neuloží, jen vypíše, co by udělal.")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"❌ Databáze neexistuje: {db_path}")

    print(f"Databáze: {db_path}")
    if args.dry_run:
        print("(dry-run: na konci se změny zahodí)\n")

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        add_flag_column(cur)
        backfill_flag(cur)
        converted = convert_intro_outro(cur)

        total = cur.execute("SELECT COUNT(*) FROM songs WHERE is_non_song = 1").fetchone()[0]
        left = cur.execute("SELECT COUNT(*) FROM songbook_intro_outro_images").fetchone()[0]
        print(f"\nNepísničkových stránek celkem: {total}")
        print(f"Zbývá v songbook_intro_outro_images: {left}")

        if args.dry_run:
            conn.rollback()
            print("\n↩️  dry-run, změny zahozeny")
        else:
            conn.commit()
            print(f"\n✅ hotovo, převedeno {converted} stránek")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
