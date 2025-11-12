import argparse
import json
import os
import re
import shutil
import sqlite3
import unicodedata
from collections import Counter
from pathlib import Path

from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent
DEFAULT_DB_PATH = BACKEND_DIR / "instance" / "zpevnik.db"
PUBLIC_SEED_PATH = PROJECT_ROOT / "data" / "public" / "seeds"
PUBLIC_IMAGES_PATH = PROJECT_ROOT / "data" / "public" / "images" / "songbooks"
PRIVATE_SEED_PATH = PROJECT_ROOT / "data" / "private" / "seeds"

def connect_db(db_path):
    return sqlite3.connect(str(db_path))

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


def slugify(value: str, maxlen: int = 60) -> str:
    if not value:
        return ""
    value = unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode('ascii')
    value = value.lower().replace('@', '-')
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"[-_.]{2,}", lambda m: m.group(0)[0], value).strip("-._")
    return value[:maxlen] or "_"


def seed_from_public_seed_folder(seed_path, db_path, image_base_path):
    seed_path = Path(seed_path)
    db_path = Path(db_path)
    image_base_path = os.fspath(image_base_path)

    db_path.parent.mkdir(parents=True, exist_ok=True)

    if not seed_path.is_dir():
        print(f"ℹ️  Složka s veřejnými seedy neexistuje: {seed_path}")
        return

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

    json_files = sorted(p for p in seed_path.iterdir() if p.is_file() and p.suffix == ".json")

    for json_file in json_files:
        data = load_json(json_file)

        raw_id = json_file.stem
        songbook_id = data.get("id", raw_id[-5:] if raw_id[-5:].isdigit() else raw_id)
        title = data.get("title", songbook_id)

        # Make songbook 10101 private and owned by user@test.com
        is_public = 0 if songbook_id == "00101" and user_id else 1
        owner_id = user_id if songbook_id == "00101" and user_id else None

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
    seed_root_path = Path(seed_root)
    if not seed_root_path.is_dir():
        print(f"ℹ️  Složka s privátními seedy neexistuje: {seed_root}")
        return

    private_root = seed_root_path.parent / 'users'
    private_root.mkdir(parents=True, exist_ok=True)

    conn = connect_db(db_path)
    cursor = conn.cursor()
    author_cache = {}

    total_songbooks = 0
    total_songs = 0

    seed_directories = [p for p in sorted(seed_root_path.iterdir()) if p.is_dir()]

    for directory_path in seed_directories:
        seed_file = directory_path / "seed.json"
        if not seed_file.is_file():
            print(f"⚠️  Přeskakuji {directory_path.name}: soubor seed.json nenalezen")
            continue

        data = load_json(seed_file)
        songbook_id = data.get("id", directory_path.name)
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

        image_base_path = str(seed_root_path)
        color = resolve_color(data, "#FFFFFF", image_base_path)

        owner_slug = slugify(owner_email, 50)
        if not owner_slug:
            owner_slug = "_"
        title_value = data.get("title", songbook_id)
        book_slug = slugify(title_value, 50) or "untitled"
        owner_dir = f"{owner_id}_{owner_slug}"
        book_dir = f"{songbook_id}_{book_slug or 'untitled'}"
        book_rel_dir = Path('users') / owner_dir / book_dir
        book_abs_dir = private_root / owner_dir / book_dir

        if book_abs_dir.exists():
            shutil.rmtree(book_abs_dir, ignore_errors=True)
        book_abs_dir.mkdir(parents=True, exist_ok=True)

        asset_cache = {}

        def copy_asset(rel_path, dest_name=None):
            if not rel_path:
                return None
            if isinstance(rel_path, str) and rel_path.startswith('users/'):
                return rel_path

            key = (rel_path, dest_name)
            if key in asset_cache:
                return asset_cache[key]

            rel_path_str = str(rel_path)
            src_path = seed_root_path / rel_path_str
            if not src_path.exists():
                candidate = directory_path / Path(rel_path_str).name
                if candidate.exists():
                    src_path = candidate
                else:
                    print(f"⚠️  Nenalezen obrázek {rel_path_str} pro zpěvník {songbook_id}")
                    return None

            dest_filename = dest_name or Path(rel_path_str).name
            dest_path = book_abs_dir / dest_filename
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src_path, dest_path)
            except Exception as exc:
                print(f"⚠️  Kopírování obrázku {src_path} selhalo: {exc}")

            rel_result = str(book_rel_dir / dest_filename)
            asset_cache[key] = rel_result
            return rel_result

        cover_preview = copy_asset(data.get("img_path_cover_preview"))
        cover_front_out = copy_asset(data.get("img_path_cover_front_outer"))
        cover_front_in = copy_asset(data.get("img_path_cover_front_inner"))
        cover_back_in = copy_asset(data.get("img_path_cover_back_inner"))
        cover_back_out = copy_asset(data.get("img_path_cover_back_outer"))

        reset_songbook(cursor, songbook_id)
        insert_songbook(
            cursor,
            songbook_id,
            title_value,
            is_public=0,
            owner_id=owner_id,
            first_page_side=data.get("first_page_side", "right"),
            cover_preview=cover_preview,
            cover_front_out=cover_front_out,
            cover_front_in=cover_front_in,
            cover_back_in=cover_back_in,
            cover_back_out=cover_back_out,
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
            image_path = page_entry.get("image_path")
            new_image_path = copy_asset(image_path)
            page_entry["image_path"] = new_image_path

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
        desired_permission = 'edit'
        for email in shared_emails:
            if not email or email == owner_email:
                continue
            cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
            shared_row = cursor.fetchone()
            if not shared_row:
                print(f"⚠️  Nelze sdílet {songbook_id} s {email}: uživatel neexistuje")
                continue

            shared_user_id = shared_row[0]
            cursor.execute(
                "SELECT permission FROM user_songbook_access WHERE user_id = ? AND songbook_id = ?",
                (shared_user_id, songbook_id)
            )
            existing_perm = cursor.fetchone()
            if existing_perm:
                if existing_perm[0] != desired_permission:
                    cursor.execute(
                        "UPDATE user_songbook_access SET permission = ? WHERE user_id = ? AND songbook_id = ?",
                        (desired_permission, shared_user_id, songbook_id)
                    )
            else:
                cursor.execute(
                    "INSERT INTO user_songbook_access (user_id, songbook_id, permission) VALUES (?, ?, ?)",
                    (shared_user_id, songbook_id, desired_permission)
                )

        total_songbooks += 1

    conn.commit()
    conn.close()
    print(f"✅ Privátní seedy: {total_songbooks} zpěvníků zpracováno.")


def seed_database(
    db_path=DEFAULT_DB_PATH,
    public_seed_path=PUBLIC_SEED_PATH,
    private_seed_path=PRIVATE_SEED_PATH,
    public_image_path=PUBLIC_IMAGES_PATH,
    reset_public=False,
):
    db_path = Path(db_path)
    public_seed_path = Path(public_seed_path)
    private_seed_path = Path(private_seed_path)

    if reset_public and db_path.exists():
        conn = connect_db(db_path)
        reset_public_data(conn)
        conn.close()

    seed_from_public_seed_folder(public_seed_path, db_path, public_image_path)
    seed_from_private_seed_folder(private_seed_path, db_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed public and private data into the database.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Cesta k SQLite databázi.")
    parser.add_argument("--public-seed", default=str(PUBLIC_SEED_PATH), help="Složka s veřejnými JSON seedy.")
    parser.add_argument("--private-seed", default=str(PRIVATE_SEED_PATH), help="Složka s privátními seedy.")
    parser.add_argument("--public-images", default=str(PUBLIC_IMAGES_PATH), help="Složka s obrázky veřejných obálek.")
    parser.add_argument("--reset", action="store_true", help="Resetuje veřejné tabulky před naplněním.")
    parser.add_argument("--yes", action="store_true", help="Přeskočí potvrzení při použití --reset.")
    args = parser.parse_args()

    if args.reset and not args.yes:
        print("⚠️  POZOR: Tato operace vymaže všechny veřejné tabulky: songbook_pages, song_images, songs, songbooks, authors.")
        confirm = input('Pro potvrzení napiš "YES": ')
        if confirm != "YES":
            print("Mazání zrušeno.")
            exit(0)

    seed_database(
        db_path=args.db,
        public_seed_path=args.public_seed,
        private_seed_path=args.private_seed,
        public_image_path=args.public_images,
        reset_public=args.reset,
    )
