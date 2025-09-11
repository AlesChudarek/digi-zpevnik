import sqlite3
import json
import os
import argparse
from PIL import Image
from collections import Counter

def connect_db(db_path):
    return sqlite3.connect(db_path)

def load_json(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_songbook_color(image_path):
    try:
        img = Image.open(image_path)
        width, height = img.size
        corners = [
            img.getpixel((0, 0)),
            img.getpixel((width - 1, 0)),
            img.getpixel((0, height - 1)),
            img.getpixel((width - 1, height - 1))
        ]
        # Handle RGBA by taking only RGB
        corners = [pixel[:3] if len(pixel) == 4 else pixel for pixel in corners]
        color_counts = Counter(corners)
        most_common = color_counts.most_common(1)[0]
        if most_common[1] >= 3:
            r, g, b = most_common[0]
            return f"#{r:02x}{g:02x}{b:02x}"
        else:
            return "#FFFFFF"
    except Exception as e:
        print(f"Chyba při čtení obrázku {image_path}: {e}")
        return "#FFFFFF"

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

def insert_songbook(cursor, songbook_id, title, is_public=1, owner_id=None,
                   cover_preview=None, cover_front_out=None, cover_front_in=None,
                   cover_back_in=None, cover_back_out=None, color="#FFFFFF"):
    cursor.execute(
        """
        INSERT OR REPLACE INTO songbooks (
            id, title, is_public, owner_id,
            img_path_cover_preview,
            img_path_cover_front_outer,
            img_path_cover_front_inner,
            img_path_cover_back_inner,
            img_path_cover_back_outer,
            color
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            songbook_id, title, is_public, owner_id,
            cover_preview,
            cover_front_out,
            cover_front_in,
            cover_back_in,
            cover_back_out,
            color
        )
    )

def insert_songbook_page(cursor, songbook_id, song_id, page_number):
    cursor.execute(
        "INSERT INTO songbook_pages (songbook_id, song_id, page_number) VALUES (?, ?, ?)",
        (songbook_id, song_id, page_number)
    )

def seed_from_public_seed_folder(seed_path, db_path):
    conn = connect_db(db_path)
    cursor = conn.cursor()
    author_cache = {}

    # Get user ID for user@test.com to make one songbook private
    cursor.execute("SELECT id FROM users WHERE email = ?", ("user@test.com",))
    user_result = cursor.fetchone()
    user_id = user_result[0] if user_result else None

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

        # Make songbook 10101 private and owned by user@test.com
        is_public = 0 if songbook_id == "00101" and user_id else 1
        owner_id = user_id if songbook_id == "00101" and user_id else None

        color = "#FFFFFF"
        if data.get("img_path_cover_front_inner"):
            image_path = os.path.join("backend/static/songbooks", data.get("img_path_cover_front_inner"))
            if os.path.exists(image_path):
                color = get_songbook_color(image_path)
        insert_songbook(
            cursor,
            songbook_id,
            title,
            is_public=is_public,
            owner_id=owner_id,
            cover_preview=data.get("img_path_cover_preview"),
            cover_front_out=data.get("img_path_cover_front_outer"),
            cover_front_in=data.get("img_path_cover_front_inner"),
            cover_back_in=data.get("img_path_cover_back_inner"),
            cover_back_out=data.get("img_path_cover_back_outer"),
            color=color
        )
        total_songbooks += 1

        # First, process songs
        song_data = {}
        for song_entry in data.get("songs", []):
            song_id = f"{songbook_id}_{song_entry['song_id']}"
            title = song_entry.get("title", f"Untitled {song_entry['song_id']}")
            author = song_entry.get("author", "Neznámý autor")

            author_id = insert_author(cursor, author, author_cache)
            insert_song(cursor, song_id, title, author_id)
            song_data[song_entry['song_id']] = song_id

        # Then, process pages
        for page_entry in data.get("pages", []):
            page_number = page_entry.get("page_number")
            image_path = page_entry.get("image_path")
            song_ids = page_entry.get("song_ids", [])
            page_type = page_entry.get("type", "song")

            if not image_path:
                print(f"⚠️  Strana {page_number} ve zpěvníku {songbook_id} nemá definovaný obrázek")
                continue

            # Handle pages with no songs (non-song pages)
            if not song_ids:
                # Skip intro/outro pages as they are handled separately from the "intros"/"outros" arrays
                if page_type in ["intro", "outro"]:
                    continue
                else:
                    dummy_song_id = f"{songbook_id}_page_{page_number or 'none'}"
                    dummy_title = f"Non-song page {page_number or 'none'}"
                    dummy_author_id = insert_author(cursor, "System", author_cache)
                    insert_song(cursor, dummy_song_id, dummy_title, dummy_author_id)
                    insert_songbook_page(cursor, songbook_id, dummy_song_id, page_number)
                    insert_song_image(cursor, dummy_song_id, image_path)
            else:
                # Insert songbook_page entries for each song on this page
                for song_id_num in song_ids:
                    song_id = song_data.get(song_id_num)
                    if song_id:
                        insert_songbook_page(cursor, songbook_id, song_id, page_number)
                        # Insert song_image entry
                        insert_song_image(cursor, song_id, image_path)
                    else:
                        print(f"⚠️  Song ID {song_id_num} not found in songs data for songbook {songbook_id}")

        # Collect intros and outros from the songbook folder
        import re
        songbook_folder = os.path.join("backend", "static", "songbooks", f"1{int(songbook_id):04d}")
        if os.path.exists(songbook_folder):
            images = os.listdir(songbook_folder)
            intros = sorted([f for f in images if re.match(r"intro\d+\.png", f)], key=lambda x: int(re.search(r'\d+', x).group()))
            outros = sorted([f for f in images if re.match(r"outro\d+\.png", f)], key=lambda x: int(re.search(r'\d+', x).group()))

            for i, image_path in enumerate(intros):
                cursor.execute(
                    "INSERT INTO songbook_intro_outro_images (songbook_id, type, image_path, sort_order) VALUES (?, 'intro', ?, ?)",
                    (songbook_id, f"1{int(songbook_id):04d}/{image_path}", i)
                )

            for i, image_path in enumerate(outros):
                cursor.execute(
                    "INSERT INTO songbook_intro_outro_images (songbook_id, type, image_path, sort_order) VALUES (?, 'outro', ?, ?)",
                    (songbook_id, f"1{int(songbook_id):04d}/{image_path}", i)
                )

        total_songs += len(data.get("songs", []))

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
