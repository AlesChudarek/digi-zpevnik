import sqlite3
import json
import os
import argparse

def connect_db(db_path):
    return sqlite3.connect(db_path)

def load_json(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

def insert_author(cursor, name, cache):
    if name in cache:
        return cache[name]
    cursor.execute("INSERT OR IGNORE INTO authors (name) VALUES (?)", (name,))
    cursor.execute("SELECT id FROM authors WHERE name = ?", (name,))
    author_id = cursor.fetchone()[0]
    cache[name] = author_id
    return author_id

def insert_song(cursor, song_id, title, author_id):
    cursor.execute(
        "INSERT OR IGNORE INTO songs (id, title, author_id) VALUES (?, ?, ?)",
        (song_id, title, author_id)
    )

def insert_song_image(cursor, song_id, image_path):
    cursor.execute(
        "INSERT INTO song_images (song_id, image_path) VALUES (?, ?)",
        (song_id, image_path)
    )

def insert_songbook(cursor, songbook_id, title, is_public=1,
                   cover_preview=None, cover_front_out=None, cover_front_in=None,
                   cover_back_in=None, cover_back_out=None):
    cursor.execute(
        """
        INSERT OR REPLACE INTO songbooks (
            id, title, is_public,
            img_path_cover_preview,
            img_path_cover_front_outer,
            img_path_cover_front_inner,
            img_path_cover_back_inner,
            img_path_cover_back_outer
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            songbook_id, title, is_public,
            cover_preview,
            cover_front_out,
            cover_front_in,
            cover_back_in,
            cover_back_out
        )
    )

def insert_songbook_page(cursor, songbook_id, song_id, page_number):
    cursor.execute(
        "INSERT OR IGNORE INTO songbook_pages (songbook_id, song_id, page_number) VALUES (?, ?, ?)",
        (songbook_id, song_id, page_number)
    )

def seed_from_public_seed_folder(seed_path, db_path):
    conn = connect_db(db_path)
    cursor = conn.cursor()
    author_cache = {}

    total_songbooks = 0
    total_songs = 0

    print(f"📂 Načítám data ze složky: {seed_path}")

    for filename in os.listdir(seed_path):
        if not filename.endswith(".json"):
            continue
        filepath = os.path.join(seed_path, filename)
        data = load_json(filepath)

        raw_id = os.path.splitext(filename)[0]
        songbook_id = data.get("id", raw_id[-5:] if raw_id[-5:].isdigit() else raw_id)
        title = data.get("title", songbook_id)
        insert_songbook(
            cursor,
            songbook_id,
            title,
            is_public=1,
            cover_preview=data.get("img_path_cover_preview"),
            cover_front_out=data.get("img_path_cover_front_outer"),
            cover_front_in=data.get("img_path_cover_front_inner"),
            cover_back_in=data.get("img_path_cover_back_inner"),
            cover_back_out=data.get("img_path_cover_back_outer"),
        )
        total_songbooks += 1

        for i, entry in enumerate(data.get("pages", [])):
            song_id = f"{songbook_id}_{i+1}"
            title = entry.get("title", f"Untitled {i+1}")
            author = entry.get("author", "Neznámý autor")
            page_number = i + 1

            author_id = insert_author(cursor, author, author_cache)
            insert_song(cursor, song_id, title, author_id)
            image_paths = entry.get("image_paths", [])
            if not image_paths or not all(image_paths):
                print(f"⚠️  Píseň '{title}' ve zpěvníku {songbook_id} nemá definovaný žádný platný obrázek (image_paths): {image_paths}")
            for path in image_paths:
                insert_song_image(cursor, song_id, path)
            insert_songbook_page(cursor, songbook_id, song_id, page_number)

        for i, image_path in enumerate(data.get("intros", [])):
            cursor.execute(
                "INSERT INTO songbook_intro_outro_images (songbook_id, type, image_path, sort_order) VALUES (?, 'intro', ?, ?)",
                (songbook_id, image_path, i)
            )

        for i, image_path in enumerate(data.get("outros", [])):
            cursor.execute(
                "INSERT INTO songbook_intro_outro_images (songbook_id, type, image_path, sort_order) VALUES (?, 'outro', ?, ?)",
                (songbook_id, image_path, i)
            )

        total_songs += len(data.get("pages", []))

    conn.commit()
    print(f"✅ Databáze byla naplněna daty.")
    print(f"   ➤ Zpěvníků přidáno: {total_songbooks}")
    print(f"   ➤ Písní celkem: {total_songs}")
    conn.close()

def reset_public_data(conn):
    cursor = conn.cursor()
    cursor.execute("DELETE FROM songbook_pages")
    cursor.execute("DELETE FROM song_images")
    cursor.execute("DELETE FROM songs")
    cursor.execute("DELETE FROM songbooks")
    cursor.execute("DELETE FROM authors")
    cursor.execute("DELETE FROM songbook_intro_outro_images") 
    conn.commit()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed public data into the database.")
    parser.add_argument("--reset", action="store_true", help="Reset public data tables before seeding")
    args = parser.parse_args()

    db_path = "backend/db/zpevnik.db"
    seed_path = "backend/db/public_seed/"

    if args.reset:
        print("⚠️  POZOR: Tato operace vymaže všechny veřejné tabulky: songbook_pages, song_images, songs, songbooks, authors.")
        confirm = input('Pro potvrzení napiš "YES": ')
        if confirm == "YES":
            conn = connect_db(db_path)
            reset_public_data(conn)
            conn.close()
            print("Veřejná data byla smazána.")
        else:
            print("Mazání zrušeno.")
            exit(0)

    seed_from_public_seed_folder(seed_path, db_path)
