import os
import re
import sqlite3
from pathlib import Path
import argparse


def slug_to_title(slug: str) -> str:
    if not slug:
        return ''
    # Replace separators with space and title-case
    s = re.sub(r'[-_]+', ' ', slug)
    return s.strip().title()


def pick_file(folder: Path, prefix: str):
    if not folder.exists():
        return None
    # Find first file that starts with prefix (case-insensitive)
    for f in sorted(folder.iterdir()):
        if not f.is_file():
            continue
        name = f.name.lower()
        if name.startswith(prefix.lower()):
            return f.name
    return None


def ensure_user(conn, user_id: int, create_missing: bool) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    if row:
        return True
    if not create_missing:
        return False
    # Create placeholder user with deterministic email; password not used for login
    email = f"user{user_id}@recovered.local"
    # Simple placeholder password hash (not used); match app’s pbkdf2 format minimally
    placeholder_hash = "pbkdf2:sha256:260000$recovered$placeholder"
    cur.execute(
        "INSERT INTO users (id, email, password, role) VALUES (?, ?, ?, 'user')",
        (user_id, email, placeholder_hash)
    )
    conn.commit()
    return True


def upsert_songbook(conn, sb_id: str, title: str, owner_id: int, covers: dict):
    cur = conn.cursor()
    # Use front_outer as preview if available
    preview = covers.get('front_outer')
    cur.execute(
        """
        INSERT INTO songbooks (
            id, title, owner_id, is_public,
            img_path_cover_preview,
            img_path_cover_front_outer,
            img_path_cover_front_inner,
            img_path_cover_back_inner,
            img_path_cover_back_outer
        ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title,
            owner_id=excluded.owner_id,
            is_public=excluded.is_public,
            img_path_cover_preview=excluded.img_path_cover_preview,
            img_path_cover_front_outer=excluded.img_path_cover_front_outer,
            img_path_cover_front_inner=excluded.img_path_cover_front_inner,
            img_path_cover_back_inner=excluded.img_path_cover_back_inner,
            img_path_cover_back_outer=excluded.img_path_cover_back_outer
        """,
        (
            sb_id, title, owner_id,
            preview,
            covers.get('front_outer'),
            covers.get('front_inner'),
            covers.get('back_inner'),
            covers.get('back_outer'),
        )
    )
    conn.commit()


def rebuild(db_path: Path, users_root: Path, create_missing_users: bool):
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    users_processed = 0
    books_processed = 0

    if not users_root.exists():
        print(f"❌ Složka neexistuje: {users_root}")
        return

    for user_dir in sorted(p for p in users_root.iterdir() if p.is_dir()):
        # user_dir: either '<id>_<emailSlug>' or legacy '<id>'
        parts = user_dir.name.split('_', 1)
        try:
            user_id = int(parts[0])
        except ValueError:
            print(f"⚠️ Přeskakuji složku bez ID uživatele: {user_dir}")
            continue

        if not ensure_user(conn, user_id, create_missing_users):
            print(f"⚠️ Uživatel {user_id} neexistuje (přepínač --create-missing-users nepovolen), přeskočeno")
            continue

        users_processed += 1

        for book_dir in sorted(p for p in user_dir.iterdir() if p.is_dir()):
            # book_dir: either '<sbid>_<titleSlug>' or legacy '<sbid>'
            bparts = book_dir.name.split('_', 1)
            sb_id = bparts[0]
            title_guess = slug_to_title(bparts[1]) if len(bparts) > 1 else sb_id

            # Find cover files
            fn_front_outer = pick_file(book_dir, 'coverfrontout')
            fn_front_inner = pick_file(book_dir, 'coverfrontin')
            fn_back_inner = pick_file(book_dir, 'coverbackin')
            fn_back_outer = pick_file(book_dir, 'coverbackout')

            rel_base = Path('users') / user_dir.name / book_dir.name
            covers = {
                'front_outer': str(rel_base / fn_front_outer) if fn_front_outer else None,
                'front_inner': str(rel_base / fn_front_inner) if fn_front_inner else None,
                'back_inner': str(rel_base / fn_back_inner) if fn_back_inner else None,
                'back_outer': str(rel_base / fn_back_outer) if fn_back_outer else None,
            }

            upsert_songbook(conn, sb_id, title_guess, user_id, covers)
            books_processed += 1

    print(f"✅ Hotovo. Uživatelů zpracováno: {users_processed}, zpěvníků zpracováno: {books_processed}.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Rebuild private songbooks in DB from data/private/users folder")
    parser.add_argument('--db', default=str(Path(__file__).resolve().parent.parent / 'instance' / 'zpevnik.db'), help='Path to SQLite DB')
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[2] / 'data' / 'private' / 'users'), help='Path to data/private/users root')
    parser.add_argument('--create-missing-users', action='store_true', help='Create placeholder users if missing in DB')
    args = parser.parse_args()

    rebuild(Path(args.db), Path(args.root), args.create_missing_users)

