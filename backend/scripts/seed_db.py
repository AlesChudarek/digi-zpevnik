import argparse
import json
import os
import re
import sqlite3
from collections import Counter

from PIL import Image

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
                   first_page_side="right", cover_preview=None, cover_front_out=None,
                   cover_front_in=None, cover_back_in=None, cover_back_out=None,
                   color="#FFFFFF"):
    cursor.execute(
        """
        INSERT OR REPLACE INTO songbooks (
            id, title, is_public, owner_id,
            first_page_side,
            img_path_cover_preview,
            img_path_cover_front_outer,
            img_path_cover_front_inner,
            img_path_cover_back_inner,
            img_path_cover_back_outer,
            color
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            songbook_id, title, is_public, owner_id,
            first_page_side,
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

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def is_valid_hex_color(value: str) -> bool:
    return bool(isinstance(value, str) and HEX_COLOR_RE.match(value.strip()))


def resolve_color(data, default_color: str, image_base_path: str) -> str:
    color = data.get("color")
    if is_valid_hex_color(color):
        return color.strip()

    front_inner = data.get("img_path_cover_front_inner")
    if front_inner:
        image_path = os.path.join(image_base_path, front_inner)
        if os.path.exists(image_path):
            return get_songbook_color(image_path)
    return default_color


def reset_songbook(cursor, songbook_id: str):
    cursor.execute("DELETE FROM songbook_pages WHERE songbook_id = ?", (songbook_id,))
    cursor.execute("DELETE FROM song_images WHERE song_id LIKE ?", (f"{songbook_id}_%",))
    cursor.execute("DELETE FROM songs WHERE id LIKE ?", (f"{songbook_id}_%",))
    cursor.execute("DELETE FROM songbook_intro_outro_images WHERE songbook_id = ?", (songbook_id,))
    cursor.execute("DELETE FROM songbooks WHERE id = ?", (songbook_id,))


def seed_from_public_seed_folder(seed_path, db_path):
    # Ensure directory exists
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

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

    for filename in sorted(os.listdir(seed_path)):
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

        image_base_path = os.path.join(script_dir, "../../data/public/images/songbooks")
        color = resolve_color(data, "#FFFFFF", image_base_path)
        reset_songbook(cursor, songbook_id)
        insert_songbook(
            cursor,
            songbook_id,
            title,
            is_public=is_public,
            owner_id=owner_id,
            first_page_side=data.get("first_page_side", "right"),
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
                # Handle intro/outro pages
                if page_type in ["intro", "outro"]:
                    # Check if already inserted to avoid duplicates
                    cursor.execute(
                        "SELECT COUNT(*) FROM songbook_intro_outro_images WHERE songbook_id=? AND type=? AND image_path=?",
                        (songbook_id, page_type, image_path)
                    )
                    count = cursor.fetchone()[0]
                    if count == 0:
                        cursor.execute(
                            "INSERT INTO songbook_intro_outro_images (songbook_id, type, image_path, sort_order) VALUES (?, ?, ?, ?)",
                            (songbook_id, page_type, image_path, 0)  # sort_order can be adjusted if needed
                        )
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
        songbook_folder = os.path.join(image_base_path, f"{int(songbook_id):05d}")
        if os.path.exists(songbook_folder):
            images = os.listdir(songbook_folder)
            intros = sorted([f for f in images if re.match(r"intro\d+\.png", f)], key=lambda x: int(re.search(r'\d+', x).group()))
            outros = sorted([f for f in images if re.match(r"outro\d+\.png", f)], key=lambda x: int(re.search(r'\d+', x).group()))

            for i, image_path in enumerate(intros):
                full_path = f"{int(songbook_id):05d}/{image_path}"
                cursor.execute(
                    "SELECT COUNT(*) FROM songbook_intro_outro_images WHERE songbook_id=? AND type=? AND image_path=?",
                    (songbook_id, 'intro', full_path)
                )
                count = cursor.fetchone()[0]
                if count == 0:
                    cursor.execute(
                        "INSERT INTO songbook_intro_outro_images (songbook_id, type, image_path, sort_order) VALUES (?, 'intro', ?, ?)",
                        (songbook_id, full_path, i)
                    )

            for i, image_path in enumerate(outros):
                full_path = f"{int(songbook_id):05d}/{image_path}"
                cursor.execute(
                    "SELECT COUNT(*) FROM songbook_intro_outro_images WHERE songbook_id=? AND type=? AND image_path=?",
                    (songbook_id, 'outro', full_path)
                )
                count = cursor.fetchone()[0]
                if count == 0:
                    cursor.execute(
                        "INSERT INTO songbook_intro_outro_images (songbook_id, type, image_path, sort_order) VALUES (?, 'outro', ?, ?)",
                        (songbook_id, full_path, i)
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


def seed_from_private_seed_folder(seed_root, db_path):
    if not os.path.isdir(seed_root):
        print(f"ℹ️  Složka s privátními seedy neexistuje: {seed_root}")
        return

    conn = connect_db(db_path)
    cursor = conn.cursor()
    author_cache = {}

    total_songbooks = 0
    total_songs = 0

    seed_directories = [d for d in sorted(os.listdir(seed_root)) if os.path.isdir(os.path.join(seed_root, d))]

    for directory in seed_directories:
        seed_dir = os.path.join(seed_root, directory)
        seed_file = os.path.join(seed_dir, "seed.json")
        if not os.path.isfile(seed_file):
            print(f"⚠️  Přeskakuji {directory}: soubor seed.json nenalezen")
            continue

        data = load_json(seed_file)
        songbook_id = data.get("id", directory)
        owner_email = data.get("owner")

        if not owner_email:
            print(f"⚠️  Přeskakuji {songbook_id}: chybí pole owner")
            continue

        cursor.execute("SELECT id FROM users WHERE email = ?", (owner_email,))
        owner_row = cursor.fetchone()
        if not owner_row:
            print(f"⚠️  Přeskakuji {songbook_id}: uživatel {owner_email} neexistuje")
            continue

        owner_id = owner_row[0]

        image_base_path = seed_root
        color = resolve_color(data, "#FFFFFF", image_base_path)

        reset_songbook(cursor, songbook_id)
        insert_songbook(
            cursor,
            songbook_id,
            data.get("title", songbook_id),
            is_public=0,
            owner_id=owner_id,
            first_page_side=data.get("first_page_side", "right"),
            cover_preview=data.get("img_path_cover_preview"),
            cover_front_out=data.get("img_path_cover_front_outer"),
            cover_front_in=data.get("img_path_cover_front_inner"),
            cover_back_in=data.get("img_path_cover_back_inner"),
            cover_back_out=data.get("img_path_cover_back_outer"),
            color=color
        )

        song_data = {}
        for song_entry in data.get("songs", []):
            raw_song_id = song_entry.get("song_id")
            if raw_song_id is None:
                print(f"⚠️  Přeskakuji píseň bez song_id ve zpěvníku {songbook_id}")
                continue

            song_id = f"{songbook_id}_{raw_song_id}"
            title = song_entry.get("title", f"Untitled {raw_song_id}")
            author = song_entry.get("author", "Neznámý autor")

            author_id = insert_author(cursor, author, author_cache)
            insert_song(cursor, song_id, title, author_id)
            song_data[raw_song_id] = song_id
            total_songs += 1

        for page_entry in data.get("pages", []):
            page_number = page_entry.get("page_number")
            image_path = page_entry.get("image_path")
            song_ids = page_entry.get("song_ids", [])
            page_type = page_entry.get("type", "song")

            if not image_path:
                print(f"⚠️  Strana {page_number} ve zpěvníku {songbook_id} nemá definovaný obrázek")
                continue

            if not song_ids:
                if page_type in ["intro", "outro"]:
                    cursor.execute(
                        "SELECT COUNT(*) FROM songbook_intro_outro_images WHERE songbook_id=? AND type=? AND image_path=?",
                        (songbook_id, page_type, image_path)
                    )
                    count = cursor.fetchone()[0]
                    if count == 0:
                        cursor.execute(
                            "INSERT INTO songbook_intro_outro_images (songbook_id, type, image_path, sort_order) VALUES (?, ?, ?, ?)",
                            (songbook_id, page_type, image_path, 0)
                        )
                else:
                    dummy_song_id = f"{songbook_id}_page_{page_number or 'none'}"
                    dummy_title = f"Non-song page {page_number or 'none'}"
                    dummy_author_id = insert_author(cursor, "System", author_cache)
                    insert_song(cursor, dummy_song_id, dummy_title, dummy_author_id)
                    insert_songbook_page(cursor, songbook_id, dummy_song_id, page_number)
                    insert_song_image(cursor, dummy_song_id, image_path)
            else:
                for song_id_num in song_ids:
                    song_id = song_data.get(song_id_num)
                    if song_id:
                        insert_songbook_page(cursor, songbook_id, song_id, page_number)
                        insert_song_image(cursor, song_id, image_path)
                    else:
                        print(f"⚠️  Song ID {song_id_num} not found in songs data for songbook {songbook_id}")

        shared_emails = data.get("shared", []) or []
        for email in shared_emails:
            if not email or email == owner_email:
                continue
            cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
            shared_row = cursor.fetchone()
            if not shared_row:
                print(f"⚠️  Nelze sdílet {songbook_id} s {email}: uživatel neexistuje")
                continue
            cursor.execute(
                "INSERT OR IGNORE INTO user_songbook_access (user_id, songbook_id, permission) VALUES (?, ?, 'view')",
                (shared_row[0], songbook_id)
            )

        total_songbooks += 1

    conn.commit()
    conn.close()
    print(f"✅ Privátní seedy: {total_songbooks} zpěvníků zpracováno.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed public data into the database.")
    parser.add_argument("--reset", action="store_true", help="Reset public data tables before seeding")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(script_dir, "../instance/zpevnik.db")
    seed_path = os.path.join(script_dir, "../../data/public/seeds/")
    private_seed_path = os.path.join(script_dir, "../../data/private/seeds/")

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
    seed_from_private_seed_folder(private_seed_path, db_path)
