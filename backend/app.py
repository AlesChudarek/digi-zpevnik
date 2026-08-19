import os
import sys
import json
import click
from flask import Flask, render_template, redirect, url_for, request, flash, session, send_from_directory, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, UserMixin, current_user
from flask.cli import with_appcontext
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
import re
import unicodedata
import time
import hashlib
import threading
import zipfile
from pathlib import Path
from werkzeug.utils import secure_filename
from sqlalchemy import or_, func
import shutil
from uuid import uuid4
from io import BytesIO

from PIL import Image, ImageOps

# Cesty k obrázkům zpěvníků
SONGBOOK_IMAGES_DIR = Path(__file__).parent.parent / 'data' / 'public' / 'images' / 'songbooks'
PRIVATE_USER_IMAGES_DIR = Path(__file__).parent.parent / 'data' / 'private' / 'users'

try:
    MAX_IMAGE_UPLOAD_MB = max(0.5, float(os.getenv("MAX_IMAGE_UPLOAD_MB", "2.0")))
except Exception:
    MAX_IMAGE_UPLOAD_MB = 2.0
MAX_IMAGE_UPLOAD_BYTES = int(MAX_IMAGE_UPLOAD_MB * 1024 * 1024)
MIN_RESIZE_DIMENSION = max(320, int(os.getenv("MIN_RESIZE_DIMENSION", "640")))
RESIZE_SCALE_FACTOR = 0.85
RESIZE_MAX_STEPS = 8
ALLOWED_RESIZE_FORMATS = {'JPEG', 'PNG', 'WEBP'}

# Export zpěvníků do PDF a ZIP.
# Leží záměrně MIMO oba obrazové kořeny: route /songbooks/<path> servíruje cokoliv pod
# nimi bez jakékoli autorizace, takže hotový export soukromého zpěvníku by se dal
# stáhnout uhodnutím URL. Sem se dostane jen přes routu, která práva kontroluje.
EXPORTS_DIR = Path(__file__).parent.parent / 'data' / 'exports'
# Skenované strany jsou A4 při 210 DPI. Jedno místo, ne trojí zopakované 1748 v kódu.
PAGE_DPI = 210
PAGE_PX = (1748, 2480)
A4_INCHES = (8.268, 11.693)
# Dvě varianty PDF. Čísla jsou naměřená na zpěvníku 00101 (123 stran, 51 MB originálů):
#   menší    q75 + zmenšení na 1754 px  ->  25,5 MB
#   kvalitní q85 v plném rozlišení      ->  49,8 MB
# Kvalita 95 se nepoužívá schválně: vyšla na 73,6 MB, tedy víc než originály, a přitom
# ztrátově. Bezztrátovou cestu plní stažení obrázků v ZIP, ne PDF - Pillow vkládá RGB do
# PDF vždycky jako JPEG, takže bezztrátové PDF by chtělo další závislost.
EXPORT_VARIANTS = {
    'small': {'quality': 75, 'max_edge': 1754},
    'high': {'quality': 85, 'max_edge': 0},
}
EXPORT_MAX_PAGES = 400
MAX_CONCURRENT_EXPORTS = 2
EXPORTS_TOTAL_LIMIT_BYTES = 500 * 1024 * 1024
EXPORT_LOCK_STALE_SECONDS = 600
EXPORT_GENERATOR_VERSION = b'v1'


def _ext_to_format(ext_hint, detected):
    ext = (ext_hint or '').lower()
    mapping = {
        '.jpg': 'JPEG',
        '.jpeg': 'JPEG',
        '.png': 'PNG',
        '.webp': 'WEBP',
    }
    if ext in mapping:
        fmt = mapping[ext]
    else:
        fmt = (detected or '').upper()
    if fmt in ALLOWED_RESIZE_FORMATS:
        return fmt
    return None


def _prepare_image_bytes(file_storage, ext_hint=None, max_bytes=None):
    if not file_storage:
        return b''
    max_bytes = max_bytes or MAX_IMAGE_UPLOAD_BYTES
    try:
        file_storage.stream.seek(0)
    except Exception:
        pass
    data = file_storage.read()
    try:
        file_storage.stream.seek(0)
    except Exception:
        pass
    if not data or len(data) <= max_bytes:
        return data
    try:
        with Image.open(BytesIO(data)) as pil_image:
            if getattr(pil_image, "is_animated", False):
                return data  # skip GIFs/animated formats to avoid breaking them
            pil_image = ImageOps.exif_transpose(pil_image)
            fmt = _ext_to_format(ext_hint, pil_image.format)
            if not fmt:
                return data
            if fmt == 'JPEG':
                current = pil_image.convert('RGB')
            elif fmt == 'PNG':
                current = pil_image.convert('RGBA') if 'A' in pil_image.getbands() else pil_image.convert('RGB')
            else:  # WEBP
                current = pil_image.convert('RGBA') if 'A' in pil_image.getbands() else pil_image.convert('RGB')
    except Exception:
        return data

    quality = 95
    result = data
    for _ in range(RESIZE_MAX_STEPS):
        buf = BytesIO()
        save_kwargs = {}
        if fmt == 'JPEG':
            save_kwargs = {'quality': quality, 'optimize': True, 'progressive': True}
        elif fmt == 'WEBP':
            save_kwargs = {'quality': quality, 'method': 5}
        else:  # PNG
            save_kwargs = {'optimize': True}
        current.save(buf, format=fmt, **save_kwargs)
        result = buf.getvalue()
        if len(result) <= max_bytes:
            break
        if current.width <= MIN_RESIZE_DIMENSION and current.height <= MIN_RESIZE_DIMENSION:
            break
        new_w = max(1, int(current.width * RESIZE_SCALE_FACTOR))
        new_h = max(1, int(current.height * RESIZE_SCALE_FACTOR))
        if new_w == current.width and new_h == current.height:
            break
        current = current.resize((new_w, new_h), Image.LANCZOS)
        if fmt in {'JPEG', 'WEBP'}:
            quality = max(60, int(quality * RESIZE_SCALE_FACTOR))
    return result


def _save_image_with_limit(file_storage, dest_path: Path, ext_hint=None):
    data = _prepare_image_bytes(file_storage, ext_hint=ext_hint)
    with open(dest_path, 'wb') as fh:
        fh.write(data)

try:
    # Prefer balíčkové importy pro nasazení (backend.app jako modul)
    from .models import (
        Song,
        SongImage,
        SongbookPage,
        SongbookIntroOutroImage,
        Songbook,
        Author,
        User,
        UserSongbookAccess,
        db,
        init_app,
    )
except ImportError:  # fallback pro přímé spuštění skriptu
    from models import Song, SongImage, SongbookPage, SongbookIntroOutroImage, Songbook, Author, User, UserSongbookAccess, db, init_app

# Permission functions
def can_view_songbook(user, songbook):
    if not user.is_authenticated:
        return False
    # Admin can view all songbooks
    if user.role == 'admin':
        return True
    if songbook.is_public:
        return True
    if songbook.owner_id == user.id:
        return True
    access = UserSongbookAccess.query.filter_by(user_id=user.id, songbook_id=songbook.id).first()
    if access:
        return True
    return False

def can_edit_songbook(user, songbook):
    if not user.is_authenticated:
        return False
    # Admin can edit all songbooks
    if user.role == 'admin':
        return True
    if songbook.owner_id == user.id:
        return True
    access = UserSongbookAccess.query.filter_by(user_id=user.id, songbook_id=songbook.id).first()
    if access and access.permission in ['edit', 'admin']:
        return True
    return False

def is_admin(user):
    return user.is_authenticated and user.role == 'admin'

def is_guest(user):
    return user.is_authenticated and user.role == 'guest'


# ---------- Non-song pages ----------
# Pages that belong to a songbook but carry no song: intros, dividers, indexes.
# They order and move exactly like song pages, but stay out of the table of
# contents and out of global search. Rows created before the is_non_song column
# existed are still recognised by their generated titles.
NON_SONG_TITLE = '<Prázdná strana>'
NON_SONG_AUTHOR = 'System'


def _is_non_song(song) -> bool:
    if getattr(song, 'is_non_song', 0):
        return True
    title = getattr(song, 'title', '') or ''
    return title == NON_SONG_TITLE or title.startswith('Non-song page')

# Načti konfiguraci z .env
load_dotenv()

def _str_to_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {'1', 'true', 't', 'yes', 'on'}


app = Flask(__name__, template_folder='../frontend/templates', static_folder='../frontend/static')

# Nastavení tajného klíče a databáze z prostředí s bezpečným fallbackem pro vývoj
app.config['SECRET_KEY'] = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-me")
basedir = os.path.abspath(os.path.dirname(__file__))
project_root = Path(basedir).parent
backend_instance_dir = Path(basedir) / 'instance'
default_sqlite_path = Path(os.getenv("SQLITE_PATH", backend_instance_dir / 'zpevnik.db'))
legacy_sqlite_path = project_root / 'instance' / 'zpevnik.db'
database_url = os.getenv("DATABASE_URL")

def _normalize_sqlite_url(url: str) -> str:
    raw_path = url.replace("sqlite:///", "", 1)
    if not raw_path or raw_path == ":memory:":
        return url
    sqlite_path = Path(raw_path)
    if not sqlite_path.is_absolute():
        sqlite_path = (project_root / sqlite_path).resolve()
    else:
        sqlite_path = sqlite_path.resolve()
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{sqlite_path.as_posix()}"

if database_url:
    if database_url.startswith("sqlite:///"):
        database_url = _normalize_sqlite_url(database_url)
else:
    target_path = default_sqlite_path
    if not target_path.exists() and legacy_sqlite_path.exists():
        target_path = legacy_sqlite_path
    target_path = target_path.resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    database_url = f"sqlite:///{target_path.as_posix()}"

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['DEBUG'] = _str_to_bool(os.getenv("FLASK_DEBUG"), False)

# Inicializace databáze
init_app(app)

# Správa loginu
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'




# --------- STATIC CACHE BUSTING ---------
def static_bust(filename: str) -> str:
    try:
        static_dir = Path(app.static_folder)
        mtime = int((static_dir / filename).stat().st_mtime)
    except Exception:
        mtime = int(time.time())
    return url_for('static', filename=filename, v=mtime)

app.jinja_env.globals['static_bust'] = static_bust

# Route pro servírování obrázků zpěvníků z data/public/images/songbooks/
@app.route('/songbooks/<path:filename>')
def serve_songbook_image(filename):
    # If path starts with 'users/', serve from private users directory; otherwise from public songbooks
    try:
        if filename.startswith('users/'):
            return send_from_directory(str(PRIVATE_USER_IMAGES_DIR), filename.replace('users/', '', 1))
        return send_from_directory(str(SONGBOOK_IMAGES_DIR), filename)
    except Exception:
        # Fallback 404-like behavior without exposing internals
        return ("Not Found", 404)


# ---------- Storage layout helpers (public vs private songbooks) ----------
def _book_storage_base(book: Songbook):
    """Return (abs_dir, rel_prefix) of the directory holding this songbook's images.

    Public songbooks live in data/public/images/songbooks/<id> and store paths
    relative to that root ("00030/page1.png"), matching the seeded songbooks.
    Private songbooks live in data/private/users/<user_dir>/<book_dir> and store
    paths prefixed with "users/". The prefix is what serve_songbook_image()
    dispatches on, so both roots stay servable without extra routing.
    """
    if getattr(book, 'is_public', 0):
        return SONGBOOK_IMAGES_DIR / book.id, book.id
    try:
        p = book.img_path_cover_preview or book.img_path_cover_front_outer or book.img_path_cover_front_inner
        if p and isinstance(p, str) and p.startswith('users/'):
            parts = Path(p).parts
            if len(parts) >= 4:
                return PRIVATE_USER_IMAGES_DIR / Path(*parts[1:-1]), str(Path(*parts[:-1]))
    except Exception:
        pass
    owner = User.query.get(book.owner_id) if getattr(book, 'owner_id', None) else None
    owner_email = getattr(owner, 'email', '') if owner else ''
    user_dir = f"{book.owner_id}_{slugify(owner_email, 50)}"
    book_dir = f"{book.id}_{slugify(book.title, 50) if book.title else 'untitled'}"
    return PRIVATE_USER_IMAGES_DIR / user_dir / book_dir, str(Path('users') / user_dir / book_dir)


def _rel_for_stored_file(abs_path: Path, book: Songbook) -> str:
    """DB path for a file already written somewhere under the book's base dir."""
    base_abs, rel_prefix = _book_storage_base(book)
    return str(Path(rel_prefix) / abs_path.relative_to(base_abs))


def _abs_image_path(rel_path: str):
    """Resolve a stored image path to an absolute file, for either storage root."""
    try:
        if not rel_path or not isinstance(rel_path, str):
            return None
        if rel_path.startswith('users/'):
            return PRIVATE_USER_IMAGES_DIR / Path(rel_path).relative_to('users')
        return SONGBOOK_IMAGES_DIR / rel_path
    except Exception:
        return None


def _next_public_songbook_id() -> str:
    """Lowest free 5-digit id, so admin-created books keep the 00001.. numbering."""
    taken = {sid for (sid,) in db.session.query(Songbook.id).all()}
    n = 1
    while f"{n:05d}" in taken:
        n += 1
    return f"{n:05d}"


# ---------- Helpers for song file ownership/migration ----------
def _base_rel_for_book(book: Songbook) -> str:
    """Return the base relative path for a songbook's image directory."""
    return _book_storage_base(book)[1]


def _handle_song_delete_for_book(sb: Songbook, song: Song):
    """Apply origin/reference deletion logic for a song in a given songbook.

    - If song has no private images -> detach only from this book
    - If this book is not the origin (files live elsewhere) -> detach only
    - If origin and there are other books -> move files to first other book and detach here
    - If origin and no other books -> delete song and files entirely

    Returns a dict with details; does not commit.
    """
    imgs = SongImage.query.filter_by(song_id=song.id).all()
    if not imgs or not any((img.image_path or '').startswith('users/') for img in imgs):
        db.session.query(SongbookPage).filter_by(songbook_id=sb.id, song_id=song.id).delete()
        return {'detached_only': True}

    this_base_rel = _base_rel_for_book(sb)
    origin_dir_rel = str(Path(this_base_rel) / 'songs' / song.id)
    is_origin_here = all((img.image_path or '').startswith(origin_dir_rel + '/') for img in imgs)

    other_ids = [sid for (sid,) in db.session.query(SongbookPage.songbook_id).filter(
        (SongbookPage.song_id == song.id) & (SongbookPage.songbook_id != sb.id)
    ).distinct().all()]

    if not is_origin_here:
        db.session.query(SongbookPage).filter_by(songbook_id=sb.id, song_id=song.id).delete()
        return {'detached_only': True}

    if other_ids:
        new_sb = Songbook.query.get(other_ids[0])
        new_base_rel = _base_rel_for_book(new_sb)
        src_abs = PRIVATE_USER_IMAGES_DIR / Path(origin_dir_rel).relative_to('users')
        dst_abs = PRIVATE_USER_IMAGES_DIR / Path(new_base_rel).relative_to('users') / 'songs' / song.id
        dst_abs.mkdir(parents=True, exist_ok=True)
        for img in imgs:
            try:
                fname = Path(img.image_path).name
                src_file = src_abs / fname
                dst_file = dst_abs / fname
                if src_file.exists():
                    shutil.move(str(src_file), str(dst_file))
                img.image_path = str(Path('users') / dst_file.relative_to(PRIVATE_USER_IMAGES_DIR))
            except Exception:
                pass
        try:
            shutil.rmtree(src_abs, ignore_errors=True)
        except Exception:
            pass
        db.session.query(SongbookPage).filter_by(songbook_id=sb.id, song_id=song.id).delete()
        return {'moved_origin_to': new_sb.id}
    else:
        try:
            src_abs = PRIVATE_USER_IMAGES_DIR / Path(origin_dir_rel).relative_to('users')
            shutil.rmtree(src_abs, ignore_errors=True)
        except Exception:
            pass
        db.session.query(SongbookPage).filter_by(song_id=song.id).delete()
        db.session.query(SongImage).filter_by(song_id=song.id).delete()
        db.session.delete(song)
        return {'deleted_song': True}

def slugify(value: str, maxlen: int = 60) -> str:
    """Create filesystem-friendly slug from arbitrary string.

    - Normalizes unicode to ASCII
    - Lowercases, replaces non [a-z0-9._-] with '-'
    - Collapses duplicate separators and trims length
    """
    if not value:
        return ""
    value = unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode('ascii')
    value = value.lower()
    # replace '@' with '-' explicitly to keep email readable
    value = value.replace('@', '-')
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"[-_.]{2,}", lambda m: m.group(0)[0], value).strip("-._")
    return value[:maxlen] or "_"


def _lighten_hex(hex_color: str, pct: float) -> str:
    try:
        h = (hex_color or '').strip()
        if not h:
            return '#ffffff'
        if h.startswith('#'):
            h = h[1:]
        if len(h) == 3:
            h = ''.join(c*2 for c in h)
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
        p = max(0.0, min(100.0, float(pct))) / 100.0
        mix = lambda c: int(round(c + (255 - c) * p))
        to2 = lambda n: format(n, '02x')
        return f"#{to2(mix(r))}{to2(mix(g))}{to2(mix(b))}"
    except Exception:
        return '#ffffff'

# ---------- VALIDACE ----------
def is_valid_email(email):
    return re.match(r"[^@]+@[^@]+\.[^@]+", email)

# ---------- MODELY ----------
# Používej modely pouze z backend/models.py (viz import výše)

# ---------- LOGIN ----------
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ---------- ROUTY ----------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            session['guest'] = (user.email == "guest@guest.com")
            return redirect(url_for('dashboard'))
        else:
            flash('Nesprávné přihlašovací údaje', 'error')
    return render_template('auth.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        if not is_valid_email(email):
            flash('Neplatná e-mailová adresa', 'error')
            return redirect(url_for('register'))

        if User.query.filter_by(email=email).first():
            flash('Účet už existuje', 'error')
            return redirect(url_for('register'))
        else:
            hashed_password = generate_password_hash(password, method='pbkdf2:sha256', salt_length=16)
            new_user = User(email=email, password=hashed_password, role='user')
            db.session.add(new_user)
            db.session.commit()
            flash('Registrace proběhla úspěšně. Přihlas se.', 'success')
            return redirect(url_for('login'))

    return render_template('auth.html')

@app.route('/logout', methods=['POST'])
def logout():
    # Korektní odhlášení přes Flask-Login + uklid session flagu guest
    try:
        logout_user()
    finally:
        session.pop('guest', None)
    return redirect(url_for('login'))

@app.route('/guest-login')
def guest_login():
    guest_email = "guest@guest.com"
    guest_password = "guest"  # může být cokoliv

    user = User.query.filter_by(email=guest_email).first()
    if not user:
        hashed_password = generate_password_hash(guest_password, method='pbkdf2:sha256')
        user = User(email=guest_email, password=hashed_password, role='guest')
        db.session.add(user)
        db.session.commit()

    login_user(user)
    session['guest'] = True
    # flash('Přihlášen jako host.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/api/songbook/<songbook_id>/toc')
def get_songbook_toc(songbook_id):
    """Table of contents: one entry per song, in page order.

    A page can hold several short songs, so entries are counted per page rather
    than per song image. Deduplicating by image used to drop every song after the
    first on a shared page.
    """
    pages = SongbookPage.query.filter_by(songbook_id=songbook_id).order_by(
        SongbookPage.page_number.asc(), SongbookPage.id.asc()
    ).all()
    if not pages:
        return jsonify({"pages": []})

    song_ids = {p.song_id for p in pages}
    songs = {s.id: s for s in Song.query.filter(Song.id.in_(song_ids)).all()}
    images_by_song = {}
    for img in (SongImage.query.filter(SongImage.song_id.in_(song_ids))
                .order_by(SongImage.id.asc()).all()):
        images_by_song.setdefault(img.song_id, []).append(img)

    # Songs sharing a page_number sit on the same physical page, so that page
    # advances the running number once, no matter how many songs it carries.
    songs_by_page = {}
    for page in pages:
        songs_by_page.setdefault(page.page_number, []).append(page.song_id)

    toc = []
    listed = set()

    # Use the stored page number rather than the page's position. Some songbooks were
    # numbered starting from the title page, so their first song sits on page 3 and
    # that is what is printed on the scan; counting positions would show 1 instead.
    for page_number in sorted(songs_by_page):
        for song_id in songs_by_page[page_number]:
            song = songs.get(song_id)
            if not song:
                continue
            if _is_non_song(song) or song_id in listed:
                continue
            listed.add(song_id)
            song_images = images_by_song.get(song_id, [])
            author_name = song.author.name if song.author else ""
            author_display = author_name or "-"
            if song.title == NON_SONG_TITLE or author_name.strip().lower() == 'system':
                author_display = '-'
            toc.append({
                "title": song.title,
                "author": author_display,
                "page": song_images[0].image_path if song_images else "",
                "page_number": page_number,
                "song_id": song.id,
            })

    return jsonify({"pages": toc})

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.email == "guest@guest.com":
        return render_template('dashboard.html', guest=True)
    return render_template('dashboard.html', guest=False)

@app.route('/search')
@login_required
def search():
    """Global search page listing all songs across accessible songbooks.

    Accessible songbooks include:
    - Public songbooks (is_public == 1)
    - Songbooks owned by the current user
    - Songbooks shared with the current user (UserSongbookAccess)
    """
    # Collect shared songbook ids for the current user
    shared_ids = []
    shared_access = {}
    if current_user.is_authenticated:
        access_rows = UserSongbookAccess.query.filter_by(user_id=current_user.id).all()
        shared_ids = [row.songbook_id for row in access_rows]
        for row in access_rows:
            shared_access[row.songbook_id] = (row.permission or 'view')

    # Subquery to get the first (minimum) page for each song within a songbook
    first_pages_subq = (
        db.session.query(
            SongbookPage.songbook_id.label('songbook_id'),
            SongbookPage.song_id.label('song_id'),
            func.min(SongbookPage.page_number).label('first_page_number')
        )
        .group_by(SongbookPage.songbook_id, SongbookPage.song_id)
        .subquery()
    )

    shared_counts_subq = (
        db.session.query(
            UserSongbookAccess.songbook_id.label('songbook_id'),
            func.count(UserSongbookAccess.user_id).label('shared_count')
        )
        .group_by(UserSongbookAccess.songbook_id)
        .subquery()
    )

    # Build query across first-pages -> song -> author -> songbook
    q = db.session.query(
        first_pages_subq.c.first_page_number.label('page_number'),
        Song.title.label('song_title'),
        Song.id.label('song_id'),
        Author.name.label('author_name'),
        Songbook.id.label('songbook_id'),
        Songbook.title.label('songbook_title'),
        Songbook.color.label('songbook_color'),
        Songbook.owner_id.label('owner_id'),
        Songbook.is_public.label('is_public'),
        shared_counts_subq.c.shared_count.label('shared_count')
    ).join(Song, Song.id == first_pages_subq.c.song_id
    ).join(Songbook, Songbook.id == first_pages_subq.c.songbook_id
    ).outerjoin(shared_counts_subq, shared_counts_subq.c.songbook_id == Songbook.id
    ).join(Author, Song.author_id == Author.id, isouter=True)

    # Non-song pages never show up in search (legacy rows are matched by title too)
    q = q.filter(Song.is_non_song == 0)
    q = q.filter(~or_(Song.title.like('Non-song page%'), Song.title == NON_SONG_TITLE))

    # Filter accessible songbooks. Admins search across every songbook, matching
    # can_view_songbook() and the admin branch of /my-songbooks.
    if not is_admin(current_user):
        filters = [Songbook.is_public == 1]
        if current_user.is_authenticated:
            filters.append(Songbook.owner_id == current_user.id)
            if shared_ids:
                filters.append(Songbook.id.in_(shared_ids))
        q = q.filter(or_(*filters))

    rows = (
        q.order_by(Song.title.asc(), Songbook.title.asc(), first_pages_subq.c.first_page_number.asc())
         .all()
    )

    results = []
    for r in rows:
        shared_count = r.shared_count or 0

        # Determine book type label: '' for public, 'shared' if the songbook has any shares,
        # otherwise 'private' when owned solely by the current user.
        if r.is_public == 1:
            book_type = ''
        elif shared_count > 0:
            book_type = 'shared'
        elif current_user.is_authenticated and r.owner_id == current_user.id:
            book_type = 'private'
        else:
            book_type = 'shared'

        can_edit = False
        if current_user.is_authenticated:
            if current_user.role == 'admin':
                can_edit = True
            elif r.owner_id == current_user.id:
                can_edit = True
            else:
                perm = (shared_access.get(r.songbook_id) or '').lower()
                if perm in ('edit', 'admin'):
                    can_edit = True

        base_color = (r.songbook_color or '#FFFFFF')
        # Compute lighter tints for row background and accents
        bg = _lighten_hex(base_color, 85)
        hover = _lighten_hex(base_color, 78)
        accent = _lighten_hex(base_color, 62)
        divider = _lighten_hex(base_color, 50)
        # Special handling for pure white to make accent visible
        if str(base_color).strip().lower() in ('#ffffff', '#fff'):
            accent = '#e6e6e6'
            divider = '#cacaca'
        results.append({
            'song_title': r.song_title,
            'song_id': r.song_id,
            'author_name': r.author_name or '',
            'songbook_id': r.songbook_id,
            'songbook_title': r.songbook_title,
            'songbook_color': r.songbook_color or '#FFFFFF',
            'tint_bg': bg,
            'tint_hover': hover,
            'tint_accent': accent,
            'tint_divider': divider,
            'book_type': book_type,
            'page_number': r.page_number,
            'owned_by_user': (current_user.is_authenticated and r.owner_id == current_user.id),
            'can_edit': can_edit
        })

    is_guest = (current_user.email == "guest@guest.com")
    return render_template('search.html', rows=results, guest=is_guest)

# API: List current user's private songbooks (for adding songs)
@app.route('/api/my-songbooks/options')
@login_required
def list_my_songbooks_options():
    if current_user.role == 'guest':
        return jsonify({'ok': True, 'items': []})

    books_by_id = {}

    def add_books(rows):
        for book in rows:
            if book and book.id not in books_by_id:
                books_by_id[book.id] = book

    if current_user.role == 'admin':
        add_books(
            db.session.execute(
                db.select(Songbook).where(Songbook.is_public == 0)
            ).scalars().all()
        )
    else:
        owned_books = db.session.execute(
            db.select(Songbook).where(
                (Songbook.is_public == 0) & (Songbook.owner_id == current_user.id)
            )
        ).scalars().all()
        add_books(owned_books)

        shared_books = db.session.execute(
            db.select(Songbook)
            .join(UserSongbookAccess, UserSongbookAccess.songbook_id == Songbook.id)
            .where(
                (Songbook.is_public == 0)
                & (UserSongbookAccess.user_id == current_user.id)
                & (UserSongbookAccess.permission.in_(('edit', 'admin')))
            )
        ).scalars().all()
        add_books(shared_books)

    books = list(books_by_id.values())
    song_id = request.args.get('song_id')
    present_ids = set()
    if song_id:
        ids = [b.id for b in books]
        if ids:
            present_rows = db.session.query(SongbookPage.songbook_id).filter(
                (SongbookPage.song_id == song_id) & (SongbookPage.songbook_id.in_(ids))
            ).all()
            present_ids = {row[0] for row in present_rows}
    return jsonify({
        'ok': True,
        'items': [
            {
                'id': b.id,
                'title': b.title,
                'color': getattr(b, 'color', '#FFFFFF') or '#FFFFFF',
                'has_song': (b.id in present_ids),
                'owned': (b.owner_id == current_user.id)
            } for b in sorted(books, key=lambda sb: (sb.title or '').lower())
        ]
    })

# API: Add a song (all its pages) to target songbook, appended at the end
@app.route('/api/songbooks/<songbook_id>/add-song', methods=['POST'])
@login_required
def add_song_to_songbook(songbook_id):
    sb = Songbook.query.get_or_404(songbook_id)
    # Require edit permission (owner, admin, or shared with edit)
    if not can_edit_songbook(current_user, sb):
        return jsonify({'ok': False, 'error': 'Forbidden'}), 403

    song_id = request.form.get('song_id') or (request.json.get('song_id') if request.is_json else None)
    if not song_id:
        return jsonify({'ok': False, 'error': 'Missing song_id'}), 400

    song = Song.query.get(song_id)
    if not song:
        return jsonify({'ok': False, 'error': 'Song not found'}), 404

    # If already present in this songbook, do nothing
    exists = db.session.query(SongbookPage.id).filter_by(songbook_id=sb.id, song_id=song.id).first()
    if exists:
        return jsonify({'ok': True, 'already_present': True, 'added_pages': 0})

    # Determine next page number in target songbook
    max_page = db.session.query(func.max(SongbookPage.page_number)).filter_by(songbook_id=sb.id).scalar()
    next_page = (max_page or 0) + 1

    # Append entries for all images of the song, in order
    song_images = SongImage.query.filter_by(song_id=song.id).order_by(SongImage.id.asc()).all()
    added = 0
    for img in song_images:
        db.session.add(SongbookPage(songbook_id=sb.id, song_id=song.id, page_number=next_page))
        next_page += 1
        added += 1

    db.session.commit()
    return jsonify({'ok': True, 'added_pages': added})

# API: Create a new custom song with uploaded page images and append to songbook
@app.route('/api/my-songbooks/<songbook_id>/custom-song', methods=['POST'])
@login_required
def create_custom_song(songbook_id):
    sb = Songbook.query.get_or_404(songbook_id)
    if not can_edit_songbook(current_user, sb):
        return jsonify({'ok': False, 'error': 'Forbidden'}), 403

    # A non-song page is an ordinary page with images that carries no song: it may
    # have a title for the editor's benefit, but never an author, and it stays out
    # of the table of contents and out of global search.
    non_song = request.form.get('non_song') in ('1', 'true', 'True', 'on')

    if non_song:
        title = (request.form.get('title') or '').strip() or NON_SONG_TITLE
        author_name = NON_SONG_AUTHOR
    else:
        title = (request.form.get('title') or 'Moje písnička').strip() or 'Moje písnička'
        author_name = (request.form.get('author') or '-').strip() or '-'
    try:
        page_count = int(request.form.get('page_count') or '1')
    except Exception:
        page_count = 1
    page_count = max(1, min(20, page_count))

    # Collect uploaded pages
    files = []
    for i in range(1, page_count + 1):
        f = request.files.get(f'page_{i}')
        if f:
            files.append((i, f))

    if not files:
        return jsonify({'ok': False, 'error': 'No files'}), 400

    # Get or create author
    author = Author.query.filter_by(name=author_name).first()
    if not author:
        author = Author(name=author_name)
        db.session.add(author)
        db.session.flush()

    # Create song
    new_song_id = f"custom_{uuid4().hex[:12]}"
    song = Song(id=new_song_id, title=title, author_id=author.id, is_non_song=1 if non_song else 0)
    db.session.add(song)
    db.session.flush()

    # Save images in a song-specific subfolder of this book's image directory
    abs_dir = _book_storage_base(sb)[0] / 'songs' / new_song_id
    abs_dir.mkdir(parents=True, exist_ok=True)

    saved = 0
    for idx, file_storage in files:
        orig = secure_filename(Path(file_storage.filename).name) or f"page_{idx}.png"
        abs_path = abs_dir / orig
        ext_hint = Path(orig).suffix.lower() or None
        _save_image_with_limit(file_storage, abs_path, ext_hint=ext_hint)
        db.session.add(SongImage(song_id=new_song_id, image_path=_rel_for_stored_file(abs_path, sb)))
        saved += 1

    if saved == 0:
        return jsonify({'ok': False, 'error': 'No valid files'}), 400

    # Append to songbook at the end
    max_page = db.session.query(func.max(SongbookPage.page_number)).filter_by(songbook_id=sb.id).scalar()
    next_page = (max_page or 0) + 1
    # Use saved count for number of pages
    for _ in range(saved):
        db.session.add(SongbookPage(songbook_id=sb.id, song_id=new_song_id, page_number=next_page))
        next_page += 1

    db.session.commit()
    return jsonify({'ok': True, 'song_id': new_song_id, 'added_pages': saved})

# API: Delete song from songbook with origin/reference logic for private songs
@app.route('/api/my-songbooks/<songbook_id>/songs/<song_id>', methods=['DELETE'])
@login_required
def delete_song_from_songbook(songbook_id, song_id):
    sb = Songbook.query.get_or_404(songbook_id)
    if not can_edit_songbook(current_user, sb):
        return jsonify({'ok': False, 'error': 'Forbidden'}), 403

    song = Song.query.get_or_404(song_id)
    imgs = SongImage.query.filter_by(song_id=song.id).all()

    # If no images or images are public (not under users/), just detach from this songbook
    if not imgs or not any((img.image_path or '').startswith('users/') for img in imgs):
        db.session.query(SongbookPage).filter_by(songbook_id=sb.id, song_id=song.id).delete()
        db.session.commit()
        # Předpřipravit nové PDF: tuhle cestu volá obsah zpěvníku ve čtečce
        # i editor, takže bez toho by po smazání písničky zůstalo ke stažení
        # staré PDF, které už neodpovídá webu.
        schedule_export_warm(songbook_id)
        return jsonify({'ok': True, 'detached_only': True})

    # Build base rel path for a songbook: users/<...>/<...>
    def base_rel_for_book(book: Songbook) -> str:
        p = book.img_path_cover_preview or book.img_path_cover_front_outer or book.img_path_cover_front_inner
        if p and isinstance(p, str) and p.startswith('users/'):
            parts = Path(p).parts
            if len(parts) >= 4:
                return str(Path(*parts[: -1]))  # users/<user>/<book>
        owner = User.query.get(book.owner_id) if book.owner_id else None
        owner_email = getattr(owner, 'email', '')
        user_dir = f"{book.owner_id}_{slugify(owner_email, 50)}"
        book_dir = f"{book.id}_{slugify(book.title, 50) if book.title else 'untitled'}"
        return str(Path('users') / user_dir / book_dir)

    this_base_rel = base_rel_for_book(sb)
    origin_dir_rel = str(Path(this_base_rel) / 'songs' / song.id)
    is_origin_here = all((img.image_path or '').startswith(origin_dir_rel + '/') for img in imgs)

    # Count other references
    others = db.session.query(SongbookPage.songbook_id).filter(
        (SongbookPage.song_id == song.id) & (SongbookPage.songbook_id != sb.id)
    ).distinct().all()
    other_ids = [sid for (sid,) in others]

    if not is_origin_here:
        # Only detach from this songbook
        db.session.query(SongbookPage).filter_by(songbook_id=sb.id, song_id=song.id).delete()
        db.session.commit()
        schedule_export_warm(songbook_id)
        return jsonify({'ok': True, 'detached_only': True})

    if other_ids:
        # Move files to first other songbook and repoint paths
        new_sb = Songbook.query.get(other_ids[0])
        new_base_rel = base_rel_for_book(new_sb)
        src_abs = PRIVATE_USER_IMAGES_DIR / Path(origin_dir_rel).relative_to('users')
        dst_abs = PRIVATE_USER_IMAGES_DIR / Path(new_base_rel).relative_to('users') / 'songs' / song.id
        dst_abs.mkdir(parents=True, exist_ok=True)

        # Move all files and update DB paths
        for img in imgs:
            try:
                fname = Path(img.image_path).name
                src_file = src_abs / fname
                dst_file = dst_abs / fname
                if src_file.exists():
                    shutil.move(str(src_file), str(dst_file))
                img.image_path = str(Path('users') / dst_file.relative_to(PRIVATE_USER_IMAGES_DIR))
            except Exception:
                # Best-effort: if move fails, skip updating this image
                pass
        # Remove old directory if empty
        try:
            shutil.rmtree(src_abs, ignore_errors=True)
        except Exception:
            pass

        # Detach from this songbook only
        db.session.query(SongbookPage).filter_by(songbook_id=sb.id, song_id=song.id).delete()
        db.session.commit()
        schedule_export_warm(songbook_id)
        return jsonify({'ok': True, 'moved_origin_to': new_sb.id})
    else:
        # Delete song entirely (no other references)
        # Remove files directory
        try:
            src_abs = PRIVATE_USER_IMAGES_DIR / Path(origin_dir_rel).relative_to('users')
            shutil.rmtree(src_abs, ignore_errors=True)
        except Exception:
            pass
        # Remove DB rows
        db.session.query(SongbookPage).filter_by(song_id=song.id).delete()
        db.session.query(SongImage).filter_by(song_id=song.id).delete()
        db.session.delete(song)
        db.session.commit()
        schedule_export_warm(songbook_id)
        return jsonify({'ok': True, 'deleted_song': True})

@app.route('/public-songbooks')
@login_required
def public_songbooks():
    # Show all public songbooks for everyone
    songbooks = db.session.execute(
        db.select(Songbook).where(Songbook.is_public == 1)
    ).scalars().all()
    is_guest = (current_user.email == "guest@guest.com")
    return render_template(
        'public_songbooks.html',
        songbooks=songbooks,
        guest=is_guest,
        can_manage=is_admin(current_user),
    )


@app.route('/public-songbooks/manage')
@login_required
def manage_public_songbooks():
    """Admin-only management of the public "Naše zpěvníky" section.

    Reuses the private songbook editor: public and private books share the
    songbooks table, and can_edit_songbook() already grants admins access, so
    the only difference is which books are listed and where images are stored.
    """
    if not is_admin(current_user):
        flash("Na správu veřejných zpěvníků nemáš právo.", "error")
        return redirect(url_for('public_songbooks'))
    books = db.session.execute(
        db.select(Songbook).where(Songbook.is_public == 1)
    ).scalars().all()
    return render_template(
        'my_songbooks.html',
        songbooks=books,
        shared_users_map={},
        max_upload_bytes=MAX_IMAGE_UPLOAD_BYTES,
        max_upload_mb=MAX_IMAGE_UPLOAD_MB,
        manage_public=True,
    )

@app.route('/my-songbooks')
@login_required
def my_songbooks():
    # Guests cannot access "My Songbooks"
    if current_user.role == 'guest':
        return render_template(
            'my_songbooks.html',
            songbooks=[],
            shared_users_map={},
            max_upload_bytes=MAX_IMAGE_UPLOAD_BYTES,
            max_upload_mb=MAX_IMAGE_UPLOAD_MB,
        )
    # Admin can see all private songbooks
    if current_user.role == 'admin':
        books = db.session.execute(
            db.select(Songbook).where(Songbook.is_public == 0)
        ).scalars().all()
    else:
        # Users see their own and shared private songbooks
        shared_ids = [row.songbook_id for row in UserSongbookAccess.query.filter_by(user_id=current_user.id).all()]
        books = db.session.execute(
            db.select(Songbook).where(
                (Songbook.owner_id == current_user.id) | (Songbook.id.in_(shared_ids))
            )
        ).scalars().all()
    book_ids = [book.id for book in books]
    user_map_by_book = {bid: {} for bid in book_ids}
    if book_ids:
        shared_rows = db.session.execute(
            db.select(UserSongbookAccess.songbook_id, User.email)
            .join(User, UserSongbookAccess.user_id == User.id)
            .where(UserSongbookAccess.songbook_id.in_(book_ids))
            .where(User.role != 'admin')
        ).all()
        for songbook_id, email in shared_rows:
            if email:
                per_book = user_map_by_book.setdefault(songbook_id, {})
                per_book[email] = {"email": email, "is_owner": False}
        owner_rows = db.session.execute(
            db.select(Songbook.id, User.email)
            .join(User, Songbook.owner_id == User.id)
            .where(Songbook.id.in_(book_ids))
            .where(User.role != 'admin')
        ).all()
        for songbook_id, owner_email in owner_rows:
            if owner_email:
                per_book = user_map_by_book.setdefault(songbook_id, {})
                info = per_book.get(owner_email, {"email": owner_email, "is_owner": False})
                info["is_owner"] = True
                per_book[owner_email] = info

    shared_users_map = {}
    current_email = getattr(current_user, "email", None)
    for book_id, entries in user_map_by_book.items():
        filtered = [
            info for email, info in entries.items()
            if email and (not current_email or email != current_email)
        ]
        filtered.sort(key=lambda info: (0 if info.get("is_owner") else 1, info.get("email", "").lower()))
        shared_users_map[book_id] = filtered
    return render_template(
        'my_songbooks.html',
        songbooks=books,
        shared_users_map=shared_users_map,
        max_upload_bytes=MAX_IMAGE_UPLOAD_BYTES,
        max_upload_mb=MAX_IMAGE_UPLOAD_MB,
    )

# API: Create a new songbook — private for the current user, or public for an admin
@app.route('/api/my-songbooks', methods=['POST'])
@login_required
def api_create_songbook():
    if current_user.role == 'guest':
        return jsonify({"ok": False, "error": "Guests cannot create songbooks"}), 403

    want_public = request.form.get('is_public') in ('1', 'true', 'True', 'on')
    if want_public and not is_admin(current_user):
        return jsonify({"ok": False, "error": "Veřejný zpěvník může vytvořit jen admin."}), 403

    title = (request.form.get('title') or '').strip() or ('Nový zpěvník' if want_public else 'Můj zpěvník')
    use_cover = request.form.get('use_cover', '1') in ('1', 'true', 'True', 'on')

    if want_public:
        # Keep the seeded 00001.. numbering so public books stay consistent
        sid = _next_public_songbook_id()
        rel_dir = Path(sid)
        abs_dir = SONGBOOK_IMAGES_DIR / sid
    else:
        # Generate a simple unique ID scoped by user and timestamp
        sid = f"u{current_user.id}-{int(time.time())}"
        user_dir = f"{current_user.id}_{slugify(current_user.email, 50)}"
        book_dir = f"{sid}_{slugify(title, 50) if title else 'untitled'}"
        rel_dir = Path('users') / user_dir / book_dir
        abs_dir = PRIVATE_USER_IMAGES_DIR / user_dir / book_dir

    # Prepare file save helper
    def save_cover(file_storage, name_hint):
        if not file_storage:
            return None
        # Normalize extension
        ext = (Path(file_storage.filename).suffix or '.png').lower()
        if ext not in ['.png', '.jpg', '.jpeg', '.webp', '.svg']:
            ext = '.png'
        abs_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{name_hint}{ext}"
        abs_path = abs_dir / filename
        _save_image_with_limit(file_storage, abs_path, ext_hint=ext)
        # Return path relative to the /songbooks route root
        return str(rel_dir / filename)

    img_front_outer = None
    img_front_inner = None
    img_back_inner = None
    img_back_outer = None
    # Color (hex) from UI; fallback to white
    color = (request.form.get('color') or '').strip() or '#FFFFFF'

    if use_cover:
        img_front_outer = save_cover(request.files.get('front_outer'), 'coverfrontout')
        img_front_inner = save_cover(request.files.get('front_inner'), 'coverfrontin')
        img_back_inner = save_cover(request.files.get('back_inner'), 'coverbackin')
        img_back_outer = save_cover(request.files.get('back_outer'), 'coverbackout')

    # Create songbook ORM entry. Public books are owner-less, like the seeded ones.
    sb = Songbook(
        id=sid,
        title=title,
        owner_id=None if want_public else current_user.id,
        is_public=1 if want_public else 0,
        first_page_side='right',
        color=color,
        img_path_cover_preview=img_front_outer,
        img_path_cover_front_outer=img_front_outer,
        img_path_cover_front_inner=img_front_inner,
        img_path_cover_back_inner=img_back_inner,
        img_path_cover_back_outer=img_back_outer,
    )

    db.session.add(sb)
    db.session.commit()

    return jsonify({
        "ok": True,
        "songbook": {
            "id": sb.id,
            "title": sb.title,
            "img_path_cover_preview": sb.img_path_cover_preview,
        }
    }), 201


# API: Delete a private songbook (owner or admin)
@app.route('/api/my-songbooks/<songbook_id>', methods=['DELETE'])
@login_required
def api_delete_songbook(songbook_id):
    sb = Songbook.query.get_or_404(songbook_id)
    access = UserSongbookAccess.query.filter_by(user_id=current_user.id, songbook_id=sb.id).first()
    has_edit_share = bool(access and access.permission in ('edit', 'admin'))
    is_owner = bool(sb.owner_id and sb.owner_id == current_user.id)
    is_admin = current_user.role == 'admin'

    if not (is_admin or is_owner or has_edit_share):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    # Shared user removing the songbook from their list simply revokes access
    if has_edit_share and not is_owner and not is_admin:
        db.session.delete(access)
        db.session.commit()
        return jsonify({"ok": True, "unshared": True})

    # If owner removes songbook but it is still shared, transfer ownership to the first valid shared user
    if current_user.role != 'admin' and is_owner:
        shared_entries = UserSongbookAccess.query.filter_by(songbook_id=sb.id).all()
        valid_shared = []

        for entry in shared_entries:
            if entry.user_id == current_user.id:
                db.session.delete(entry)
                continue
            user = User.query.get(entry.user_id)
            if not user or user.role in ('admin', 'guest'):
                db.session.delete(entry)
                continue
            valid_shared.append((entry, user))

        if valid_shared:
            valid_shared.sort(key=lambda item: item[1].email.lower())
            chosen_entry, new_owner = valid_shared[0]

            old_rel = _base_rel_for_book(sb)
            old_rel_str = old_rel if isinstance(old_rel, str) else str(old_rel or '')

            new_user_dir = f"{new_owner.id}_{slugify(new_owner.email, 50)}"
            book_dir = f"{sb.id}_{slugify(sb.title, 50) if sb.title else 'untitled'}"
            new_rel_path = Path('users') / new_user_dir / book_dir
            new_rel_str = str(new_rel_path)

            if old_rel_str.startswith('users/'):
                old_rel_path = Path(old_rel_str)
                if len(old_rel_path.parts) > 1:
                    old_abs = PRIVATE_USER_IMAGES_DIR / Path(*old_rel_path.parts[1:])
                    new_abs = PRIVATE_USER_IMAGES_DIR / Path(new_user_dir) / book_dir
                    try:
                        new_abs.parent.mkdir(parents=True, exist_ok=True)
                        if old_abs.exists():
                            if new_abs.exists():
                                shutil.rmtree(new_abs, ignore_errors=True)
                            shutil.move(str(old_abs), str(new_abs))
                    except Exception:
                        pass

            def rewrite_path(value: str) -> str:
                if not value or not old_rel_str or not isinstance(value, str):
                    return value
                if not value.startswith(old_rel_str):
                    return value
                suffix = value[len(old_rel_str):].lstrip('/')
                return new_rel_str if not suffix else f"{new_rel_str}/{suffix}"

            sb.img_path_cover_preview = rewrite_path(sb.img_path_cover_preview)
            sb.img_path_cover_front_outer = rewrite_path(sb.img_path_cover_front_outer)
            sb.img_path_cover_front_inner = rewrite_path(sb.img_path_cover_front_inner)
            sb.img_path_cover_back_inner = rewrite_path(sb.img_path_cover_back_inner)
            sb.img_path_cover_back_outer = rewrite_path(sb.img_path_cover_back_outer)

            for intro_outro in sb.intros_outros:
                intro_outro.image_path = rewrite_path(intro_outro.image_path)

            song_ids = {row.song_id for row in SongbookPage.query.filter_by(songbook_id=sb.id).all()}
            if song_ids:
                for img in SongImage.query.filter(SongImage.song_id.in_(list(song_ids))).all():
                    img.image_path = rewrite_path(img.image_path)

            sb.owner_id = new_owner.id
            db.session.delete(chosen_entry)
            db.session.commit()

            return jsonify({"ok": True})

    # Remove the songbook's image directory (private user dir, or public 000NN dir)
    try:
        target_dir = _book_storage_base(sb)[0]
        # Never let a bad path resolution take out a whole storage root
        if target_dir.exists() and target_dir.resolve() not in (
            PRIVATE_USER_IMAGES_DIR.resolve(), SONGBOOK_IMAGES_DIR.resolve()
        ):
            shutil.rmtree(target_dir, ignore_errors=True)
    except Exception:
        pass  # ignore file removal errors

    # Delete DB entry (cascade removes pages/intro_outro)
    db.session.delete(sb)
    db.session.commit()

    return jsonify({"ok": True})


# API: Share a private songbook with another user
@app.route('/api/my-songbooks/<songbook_id>/share', methods=['POST'])
@login_required
def api_share_songbook(songbook_id):
    sb = Songbook.query.get_or_404(songbook_id)
    if not can_edit_songbook(current_user, sb):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    email = (payload.get('email') or request.form.get('email') or '').strip()

    if not email:
        return jsonify({"ok": False, "error": "Zadej e-mail uživatele."}), 400

    normalized = email.lower()
    target = User.query.filter(func.lower(User.email) == normalized).first()

    if not target or target.role in ('admin', 'guest'):
        return jsonify({"ok": False, "error": "Uživatel s tímto e-mailem neexistuje."}), 404

    if target.id == sb.owner_id:
        return jsonify({"ok": False, "error": "Tento uživatel již zpěvník sdílí."}), 400

    existing = UserSongbookAccess.query.filter_by(user_id=target.id, songbook_id=sb.id).first()
    if existing:
        if existing.permission != 'edit':
            existing.permission = 'edit'
            db.session.commit()
            return jsonify({"ok": True, "message": f"Zpěvník je už sdílen s {target.email}. Oprávnění bylo aktualizováno na úpravy a mazání."})
        return jsonify({"ok": True, "message": f"Zpěvník je už sdílen s {target.email}. Uživatel má právo upravovat i mazat."})

    access = UserSongbookAccess(user_id=target.id, songbook_id=sb.id, permission='edit')
    db.session.add(access)
    db.session.commit()

    return jsonify({"ok": True, "message": f"Zpěvník byl sdílen s {target.email}."}), 200

# API: Get songbook structure for editing (owner only)
@app.route('/api/my-songbooks/<songbook_id>/structure')
@login_required
def get_songbook_structure(songbook_id):
    sb = Songbook.query.get_or_404(songbook_id)
    if not can_edit_songbook(current_user, sb):
        return jsonify({'ok': False, 'error': 'Forbidden'}), 403

    # Distinct songs in this songbook with start page and page count (count rows in this book)
    subq_min = (
        db.session.query(
            SongbookPage.song_id.label('song_id'),
            func.min(SongbookPage.page_number).label('start_page'),
            func.count(SongbookPage.id).label('page_count')
        )
        .filter(SongbookPage.songbook_id == songbook_id)
        .group_by(SongbookPage.song_id)
        .subquery()
    )

    rows = (
        db.session.query(
            Song.id, Song.title, Author.name.label('author'),
            subq_min.c.start_page, subq_min.c.page_count, Song.is_non_song
        )
        .join(subq_min, subq_min.c.song_id == Song.id)
        .join(Author, Song.author_id == Author.id, isouter=True)
        .order_by(subq_min.c.start_page.asc())
        .all()
    )

    # Determine which songs are private (have images under users/)
    song_ids = [r[0] for r in rows]
    private_set = set()
    if song_ids:
        priv_rows = db.session.query(SongImage.song_id).filter(
            SongImage.song_id.in_(song_ids), SongImage.image_path.like('users/%')
        ).distinct().all()
        private_set = {sid for (sid,) in priv_rows}

    def filename_or_none(path):
        try:
            return Path(path).name if path else None
        except Exception:
            return None

    def _row_is_non_song(row):
        """Row order is (id, title, author, start_page, page_count, is_non_song)."""
        if row[5]:
            return True
        title = row[1] or ''
        return title == NON_SONG_TITLE or str(title).startswith('Non-song page')

    # Several short songs can share one physical page. Group them so the editor
    # shows one row per page and keeps them together when saving.
    page_rows = (db.session.query(SongbookPage.song_id, SongbookPage.page_number)
                 .filter(SongbookPage.songbook_id == songbook_id).all())
    songs_on_page = {}
    for song_id, page_number in page_rows:
        songs_on_page.setdefault(page_number, set()).add(song_id)
    group_of = {}
    for page_number in sorted(songs_on_page):
        sharing = songs_on_page[page_number]
        # Reuse a group id if any song on this page already belongs to one
        existing = next((group_of[s] for s in sharing if s in group_of), None)
        group_id = existing if existing is not None else len(set(group_of.values()))
        for song_id in sharing:
            group_of[song_id] = group_id

    return jsonify({
        'ok': True,
        'songbook': {
            'id': sb.id,
            'title': sb.title,
            'color': getattr(sb, 'color', '#FFFFFF') or '#FFFFFF',
            # Printed number of the first page; not always 1 (title page counted in)
            'first_page_number': (db.session.query(func.min(SongbookPage.page_number))
                                  .filter_by(songbook_id=songbook_id).scalar() or 1),
            'covers': {
                'front_outer': sb.img_path_cover_front_outer,
                'front_inner': sb.img_path_cover_front_inner,
                'back_inner': sb.img_path_cover_back_inner,
                'back_outer': sb.img_path_cover_back_outer,
                'front_outer_name': filename_or_none(sb.img_path_cover_front_outer),
                'front_inner_name': filename_or_none(sb.img_path_cover_front_inner),
                'back_inner_name': filename_or_none(sb.img_path_cover_back_inner),
                'back_outer_name': filename_or_none(sb.img_path_cover_back_outer),
            },
            'songs': [
                {
                    'song_id': r[0],
                    # Non-song pages show their own title if they have one, otherwise a
                    # placeholder. Escaped angle brackets keep the placeholder visible.
                    'title': (
                        ("&lt;Prázdná strana&gt;" if (not r[1] or r[1] == NON_SONG_TITLE
                                                      or str(r[1]).startswith("Non-song page")) else r[1])
                        if _row_is_non_song(r) else r[1]
                    ),
                    'author': ('' if _row_is_non_song(r) else (r[2] or '')),
                    'start_page': r[3],
                    'page_count': r[4],
                    'is_private': (r[0] in private_set),
                    'is_non_song': bool(_row_is_non_song(r)),
                    # Songs with the same page_group sit on the same page(s)
                    'page_group': group_of.get(r[0]),
                }
                for r in rows
            ]
        }
    })

# API: Update songbook structure: title/color/covers + song order and page numbers
@app.route('/api/my-songbooks/<songbook_id>/structure', methods=['POST'])
@login_required
def update_songbook_structure(songbook_id):
    sb = Songbook.query.get_or_404(songbook_id)
    if not can_edit_songbook(current_user, sb):
        return jsonify({'ok': False, 'error': 'Forbidden'}), 403

    title = (request.form.get('title') or sb.title).strip()
    color = (request.form.get('color') or getattr(sb, 'color', '#FFFFFF') or '#FFFFFF').strip()
    auto_numbering = (request.form.get('auto_numbering', '1') in ('1', 'true', 'True', 'on'))

    # Where the printed numbering starts. Some songbooks count the title page as 1,
    # so their first song is page 3; renumbering from a hardcoded 1 would lose that.
    # Read before any deletion, so dropping the first song does not shift the book.
    try:
        first_page_number = int(request.form.get('first_page_number'))
    except (TypeError, ValueError):
        first_page_number = None
    if first_page_number is None:
        first_page_number = db.session.query(func.min(SongbookPage.page_number)).filter_by(
            songbook_id=songbook_id).scalar()
    first_page_number = max(1, int(first_page_number or 1))

    # Save optional cover files into the book's existing image folder
    def resolve_book_dir() -> Path:
        return _book_storage_base(sb)[0]

    def save_cover(file_storage, name_hint):
        if not file_storage:
            return None
        # Keep user's original filename (sanitized). Overwrite if exists.
        orig_name = secure_filename(Path(file_storage.filename).name)
        # Fallback if empty after sanitization
        if not orig_name:
            ext = (Path(file_storage.filename).suffix or '.png').lower()
            if ext not in ['.png', '.jpg', '.jpeg', '.webp', '.svg']:
                ext = '.png'
            orig_name = f"{name_hint}{ext}"
        abs_dir = resolve_book_dir()
        abs_dir.mkdir(parents=True, exist_ok=True)
        abs_path = abs_dir / orig_name
        ext_hint = Path(orig_name).suffix.lower() or None
        _save_image_with_limit(file_storage, abs_path, ext_hint=ext_hint)
        return _rel_for_stored_file(abs_path, sb)

    # Keep originals to allow cleanup when new files are uploaded (avoid storage bloat)
    old_front_outer = sb.img_path_cover_front_outer
    old_front_inner = sb.img_path_cover_front_inner
    old_back_inner = sb.img_path_cover_back_inner
    old_back_outer = sb.img_path_cover_back_outer

    f_front_outer = request.files.get('front_outer')
    f_front_inner = request.files.get('front_inner')
    f_back_inner = request.files.get('back_inner')
    f_back_outer = request.files.get('back_outer')

    def cleanup_old(old_rel: str, new_rel: str):
        try:
            if old_rel and old_rel != new_rel:
                p = _abs_image_path(old_rel)
                if p and p.exists():
                    p.unlink()
        except Exception:
            # Best-effort cleanup only
            pass

    if f_front_outer:
        new_rel = save_cover(f_front_outer, 'coverfrontout')
        cleanup_old(old_front_outer, new_rel)
        sb.img_path_cover_front_outer = new_rel
        sb.img_path_cover_preview = sb.img_path_cover_front_outer
    if f_front_inner:
        new_rel = save_cover(f_front_inner, 'coverfrontin')
        cleanup_old(old_front_inner, new_rel)
        sb.img_path_cover_front_inner = new_rel
    if f_back_inner:
        new_rel = save_cover(f_back_inner, 'coverbackin')
        cleanup_old(old_back_inner, new_rel)
        sb.img_path_cover_back_inner = new_rel
    if f_back_outer:
        new_rel = save_cover(f_back_outer, 'coverbackout')
        cleanup_old(old_back_outer, new_rel)
        sb.img_path_cover_back_outer = new_rel

    # Handle removal flags from clear buttons
    if request.form.get('remove_front_outer') == '1':
        cleanup_old(sb.img_path_cover_front_outer, None)
        sb.img_path_cover_front_outer = None
        if sb.img_path_cover_preview == old_front_outer:
            sb.img_path_cover_preview = None
    if request.form.get('remove_front_inner') == '1':
        cleanup_old(sb.img_path_cover_front_inner, None)
        sb.img_path_cover_front_inner = None
    if request.form.get('remove_back_inner') == '1':
        cleanup_old(sb.img_path_cover_back_inner, None)
        sb.img_path_cover_back_inner = None
    if request.form.get('remove_back_outer') == '1':
        cleanup_old(sb.img_path_cover_back_outer, None)
        sb.img_path_cover_back_outer = None

    # Update basic fields
    sb.title = title or sb.title
    sb.color = color or sb.color

    # Order parsing
    import json as _json
    order_raw = request.form.get('order')
    song_entries = []
    if order_raw:
        try:
            song_entries = _json.loads(order_raw)
        except Exception:
            song_entries = []

    new_songs_raw = request.form.get('new_songs')
    new_songs_list = []
    if new_songs_raw:
        try:
            parsed_new = _json.loads(new_songs_raw)
            if isinstance(parsed_new, list):
                new_songs_list = parsed_new
        except Exception:
            new_songs_list = []
    new_songs_map = {s.get('temp_id'): s for s in new_songs_list if isinstance(s, dict) and s.get('temp_id')}

    # Create new songs (with uploaded pages) referenced in order, assign real IDs
    referenced_new_ids = []
    for entry in song_entries:
        if not isinstance(entry, dict):
            continue
        sid = entry.get('song_id')
        if sid and sid in new_songs_map and sid not in referenced_new_ids:
            referenced_new_ids.append(sid)

    created_new_songs = {}
    if referenced_new_ids:
        base_dir = resolve_book_dir()
        base_dir.mkdir(parents=True, exist_ok=True)
        next_page_number = db.session.query(func.max(SongbookPage.page_number)).filter_by(songbook_id=songbook_id).scalar() or 0
        payloads = []
        for temp_id in referenced_new_ids:
            meta = new_songs_map.get(temp_id) or {}
            non_song = bool(meta.get('non_song'))
            # Short songs can share one page: 'songs' carries a title/author per song
            # and they all end up on the same uploaded page images.
            members_raw = meta.get('songs')
            if isinstance(members_raw, list) and members_raw:
                members = []
                for m in members_raw:
                    if not isinstance(m, dict):
                        continue
                    if non_song:
                        members.append(((m.get('title') or '').strip() or NON_SONG_TITLE, NON_SONG_AUTHOR))
                    else:
                        members.append(((m.get('title') or 'Moje písnička').strip() or 'Moje písnička',
                                        (m.get('author') or '-').strip() or '-'))
                members = members or None
            else:
                members = None
            if members is None:
                if non_song:
                    members = [((meta.get('title') or '').strip() or NON_SONG_TITLE, NON_SONG_AUTHOR)]
                else:
                    members = [((meta.get('title') or 'Moje písnička').strip() or 'Moje písnička',
                                (meta.get('author') or '-').strip() or '-')]
            title = members[0][0]
            try:
                requested_pages = int(meta.get('page_count') or 1)
            except Exception:
                requested_pages = 1
            requested_pages = max(1, min(20, requested_pages))
            files = []
            for idx in range(1, requested_pages + 1):
                field = f'new_song_{temp_id}_page_{idx}'
                file_obj = request.files.get(field)
                if file_obj:
                    files.append(file_obj)
            if not files:
                label = 'nové stránky' if non_song else f'novou písničku: {title}'
                return jsonify({'ok': False, 'error': f'Chybí soubory pro {label}'}), 400
            payloads.append((temp_id, members, files, non_song))

        for temp_id, members, files, non_song in payloads:
            shared = len(members) > 1
            member_ids = [f"custom_{uuid4().hex[:12]}" for _ in members]

            # Files are stored once. A shared page lives under pages/<id> rather than
            # songs/<id> so no single song of the group owns the images.
            if shared:
                store_dir = base_dir / 'pages' / uuid4().hex[:12]
            else:
                store_dir = base_dir / 'songs' / member_ids[0]
            store_dir.mkdir(parents=True, exist_ok=True)

            saved_paths = []
            for offset, file_storage in enumerate(files, start=1):
                orig_name = secure_filename(Path(file_storage.filename).name) or f"page_{offset}.png"
                abs_path = store_dir / orig_name
                ext_hint = Path(orig_name).suffix.lower() or None
                _save_image_with_limit(file_storage, abs_path, ext_hint=ext_hint)
                saved_paths.append(_rel_for_stored_file(abs_path, sb))

            if not saved_paths:
                return jsonify({'ok': False, 'error': f'Nepodařilo se uložit soubory nové písničky: {members[0][0]}'}), 400

            # Every song of the group points at the same images and the same pages
            page_numbers = []
            for _ in saved_paths:
                next_page_number += 1
                page_numbers.append(next_page_number)

            for song_id, (title, author_name) in zip(member_ids, members):
                author = Author.query.filter_by(name=author_name).first()
                if not author:
                    author = Author(name=author_name)
                    db.session.add(author)
                    db.session.flush()
                db.session.add(Song(id=song_id, title=title, author_id=author.id,
                                    is_non_song=1 if non_song else 0))
                db.session.flush()
                for rel_path in saved_paths:
                    db.session.add(SongImage(song_id=song_id, image_path=rel_path))
                for page_number in page_numbers:
                    db.session.add(SongbookPage(songbook_id=songbook_id, song_id=song_id,
                                                page_number=page_number))

            created_new_songs[temp_id] = {'song_id': member_ids[0], 'song_ids': member_ids,
                                          'page_count': len(saved_paths)}

        # Replace placeholder IDs in order entries with real song IDs
        for entry in song_entries:
            if not isinstance(entry, dict):
                continue
            sid = entry.get('song_id')
            if sid and sid in created_new_songs:
                entry['song_id'] = created_new_songs[sid]['song_id']
                entry['song_ids'] = list(created_new_songs[sid]['song_ids'])

    # Build mapping for updates
    # song_entries: list of {song_id, start_page?}
    # Apply deletions of songs removed from the order, then renumber remaining
    # Execute this block whenever 'order' was provided (even if empty => delete all)
    if order_raw is not None:
        # Determine which songs currently exist in this songbook
        existing_ids = [sid for (sid,) in (
            db.session.query(SongbookPage.song_id)
            .filter(SongbookPage.songbook_id == songbook_id)
            .distinct()
            .all()
        )]
        # Count every member of a shared page as submitted, not just the entry's
        # primary song, or the others would look removed and get deleted.
        incoming_ids = set()
        for e in song_entries:
            if e.get('song_id'):
                incoming_ids.add(e.get('song_id'))
            for member in (e.get('song_ids') or []):
                incoming_ids.add(member)
        to_delete = set(existing_ids) - incoming_ids

        if to_delete:
            # Delete all pages for songs that are no longer present in the submitted order
            (db.session.query(SongbookPage)
             .filter(SongbookPage.songbook_id == songbook_id, SongbookPage.song_id.in_(list(to_delete)))
             .delete(synchronize_session=False))

        # Prepare counts per remaining song within this songbook (after deletion)
        counts = dict(
            db.session.query(SongbookPage.song_id, func.count(SongbookPage.id))
            .filter(SongbookPage.songbook_id == songbook_id)
            .group_by(SongbookPage.song_id)
            .all()
        )

        next_page = first_page_number
        # Helper: ensure 'System' author exists for non-song pages
        def get_system_author_id():
            sys = Author.query.filter_by(name='System').first()
            if not sys:
                sys = Author(name='System')
                db.session.add(sys)
                db.session.flush()
            return sys.id

        for entry in song_entries:
            sid = entry.get('song_id')
            if not sid:
                # Possibly a request to add a new non-song page
                if entry.get('non_song'):
                    page_count = 1
                    start = next_page if auto_numbering else int(entry.get('start_page') or next_page)
                    # Create dummy song + one page
                    ns_song_id = f"{songbook_id}_ns_{uuid4().hex[:8]}"
                    sys_author_id = get_system_author_id()
                    ns_song = Song(id=ns_song_id, title=NON_SONG_TITLE, author_id=sys_author_id, is_non_song=1)
                    db.session.add(ns_song)
                    db.session.flush()
                    db.session.add(SongbookPage(songbook_id=songbook_id, song_id=ns_song_id, page_number=start))
                    next_page = start + page_count if not auto_numbering else (next_page + page_count)
                continue
            # Several short songs can share one page. Such a group is renumbered as
            # a single unit: every member gets the same page numbers and the page
            # counter advances only once, otherwise saving would split the page.
            group_ids = entry.get('song_ids')
            if not isinstance(group_ids, list) or not group_ids:
                group_ids = [sid]
            group_ids = [g for g in group_ids if int(counts.get(g, 0)) > 0]
            if not group_ids:
                continue
            page_count = max(int(counts.get(g, 0)) for g in group_ids)
            start = next_page if auto_numbering else int(entry.get('start_page') or next_page)

            for member_id in group_ids:
                # Select rows for this song ordered by page_number then id
                rows = (SongbookPage.query
                        .filter_by(songbook_id=songbook_id, song_id=member_id)
                        .order_by(SongbookPage.page_number.asc(), SongbookPage.id.asc())
                        .all())
                # Reassign page numbers sequentially from 'start'
                p = start
                for r in rows:
                    r.page_number = p
                    p += 1

            next_page = start + page_count if not auto_numbering else (next_page + page_count)

    # Handle explicit delete requests with full origin/reference logic (staged deletes)
    delete_raw = request.form.get('delete_songs')
    if delete_raw:
        try:
            to_delete = _json.loads(delete_raw)
        except Exception:
            to_delete = []
        if isinstance(to_delete, list):
            for sid in to_delete:
                s = Song.query.get(sid)
                if s:
                    _handle_song_delete_for_book(sb, s)

    db.session.commit()
    # Až po commitu: předpřipravit ke stažení novou podobu zpěvníku a zahodit tu starou.
    # Bez toho by první, kdo si zpěvník stáhne po úpravě, čekal na skládání - a hlavně
    # by hrozilo, že se stáhne jiný stav, než je na webu, kdyby na to někdo zapomněl.
    schedule_export_warm(songbook_id)
    return jsonify({'ok': True})

def _drop_stale_exports(songbook, sequence):
    """Delete every download of this songbook that no longer matches its content.

    The cache key already makes a stale file unreachable, so this is not needed for
    correctness - but leaving old builds around until the size cap sweeps them means
    paying disk for versions nobody can ever ask for again.
    """
    safe_id = re.sub(r'[^A-Za-z0-9_]', '_', songbook.id)
    platne = set()
    for variant in EXPORT_VARIANTS:
        platne.add(_export_paths(songbook.id, variant, 'pdf',
                                 songbook_export_key(sequence, variant))['final'].name)
    platne.add(_export_paths(songbook.id, 'orig', 'zip',
                             songbook_export_key(sequence, 'orig'))['final'].name)
    try:
        for path in EXPORTS_DIR.glob(f"{safe_id}-*"):
            if path.is_file() and path.suffix in ('.pdf', '.zip') and path.name not in platne:
                path.unlink(missing_ok=True)
    except OSError:
        pass


def schedule_export_warm(book_id):
    """Rebuild the downloadable PDF for a songbook in the background.

    Called after a save, so it must never make saving fail or wait: the whole thing is
    wrapped in a thread and swallows its own errors. If the rebuild does not happen, the
    next download simply builds it the usual way - nothing breaks, it is just slower.

    The stale file needs no deleting: the cache key is derived from the page list and the
    files' mtimes, so an edited songbook resolves to a different name and the old build is
    pruned as a superseded sibling.
    """
    def prace():
        try:
            with app.app_context():
                songbook = Songbook.query.get(book_id)
                if songbook is None:
                    return
                sequence = build_songbook_export_sequence(songbook)
                if not sequence or len(sequence) > EXPORT_MAX_PAGES:
                    return
                variant = 'small'
                key = songbook_export_key(sequence, variant)
                paths = _export_paths(book_id, variant, 'pdf', key)
                if paths['final'].exists() or paths['lock'].exists():
                    return
                EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
                render_songbook_pdf(sequence, paths['part'], variant)
                os.replace(paths['part'], paths['final'])
                _prune_exports(paths['final'], paths['siblings'])
                _drop_stale_exports(songbook, sequence)
        except Exception:  # noqa: BLE001 - uložení zpěvníku tím nesmí být dotčené
            pass

    threading.Thread(target=prace, daemon=False).start()


def build_songbook_content_pages(book_id):
    """Ordered content pages of a songbook: [{"file", "page_number", "kind"}].

    The single source of truth for page order, moved out of songbook_detail() so the
    reader and the export cannot drift apart. There is no page entity in the model: a
    physical page is a page_number paired with an image that belongs to a *song*, so
    two things have to be untangled here. Several short songs sharing one printed page
    collapse to a single entry, and a song spanning several pages takes its images in
    order. A page with no image becomes the literal "blank".
    """
    raw_pages = SongbookPage.query.filter_by(songbook_id=book_id).order_by(
        SongbookPage.page_number.asc(), SongbookPage.id.asc()
    ).all()

    pages_by_song = {}
    for page in raw_pages:
        pages_by_song.setdefault(page.song_id, []).append(page.page_number)

    # A page number maps to one image; several short songs can share that one page.
    image_for_page = {}
    for song_id, page_numbers in pages_by_song.items():
        song_images = SongImage.query.filter_by(song_id=song_id).order_by(SongImage.id.asc()).all()
        for offset, page_number in enumerate(sorted(set(page_numbers))):
            if page_number in image_for_page:
                continue  # already provided by another song on this same page
            # A multi-page song has one page per image, in order
            image_for_page[page_number] = (
                song_images[offset].image_path if offset < len(song_images) else "blank"
            )

    return [
        {"file": image_for_page[page_number], "page_number": page_number, "kind": "content"}
        for page_number in sorted(image_for_page)
    ]


def build_songbook_export_sequence(songbook):
    """Physical pages of a songbook in print order, for PDF and ZIP export.

    Deliberately different from what the reader renders:
      - no double-page pairing and no first_page_side offset. Which page falls on the
        left is a property of the viewer, not of the document.
      - a cover that does not exist is skipped rather than replaced by a blank sheet.
      - "blank" inside the content is kept and drawn as an empty page, so printed page
        numbers still line up.

    This is the only place the export learns where pages come from. When PDF import
    lands, "use the archived source page instead of the image" belongs here and the
    renderer will not have to change.
    """
    sequence = []

    def add(rel_path, kind):
        if rel_path:
            sequence.append({"file": rel_path, "kind": kind})

    add(songbook.img_path_cover_front_outer, "cover")
    add(songbook.img_path_cover_front_inner, "cover")

    for image in SongbookIntroOutroImage.query.filter_by(
        songbook_id=songbook.id, type='intro'
    ).order_by(SongbookIntroOutroImage.sort_order).all():
        add(image.image_path, "intro")

    sequence.extend(
        {"file": page["file"], "kind": "content", "page_number": page["page_number"]}
        for page in build_songbook_content_pages(songbook.id)
    )

    for image in SongbookIntroOutroImage.query.filter_by(
        songbook_id=songbook.id, type='outro'
    ).order_by(SongbookIntroOutroImage.sort_order).all():
        add(image.image_path, "outro")

    add(songbook.img_path_cover_back_inner, "cover")
    add(songbook.img_path_cover_back_outer, "cover")

    return sequence


def _flatten_to_rgb(image):
    """Drop the alpha channel onto white.

    Every scanned page is stored as RGBA and PDF has no plain alpha: left as it is, the
    pages come out on a black background. White rather than a transparency mask, because
    an SMask inflates the file and prints unpredictably.
    """
    if image.mode == 'RGB':
        return image
    if image.mode not in ('RGBA', 'LA', 'PA'):
        image = image.convert('RGBA')
    canvas = Image.new('RGB', image.size, (255, 255, 255))
    canvas.paste(image, mask=image.split()[-1])
    return canvas


def songbook_export_key(sequence, variant):
    """Cache key derived from what the export actually reads.

    Content-addressed on purpose: no invalidation hook anywhere in the editor, nothing
    to forget to call. Any edit changes the order or a file's mtime, which changes the
    key, which means a different file. The old one is simply never asked for again.
    """
    digest = hashlib.sha256()
    digest.update(EXPORT_GENERATOR_VERSION)
    digest.update(variant.encode())
    for item in sequence:
        rel = item['file']
        digest.update(rel.encode())
        abs_path = _abs_image_path(rel) if rel != 'blank' else None
        try:
            stat = abs_path.stat() if abs_path else None
        except OSError:
            stat = None
        digest.update(f"|{stat.st_mtime_ns if stat else 0}|{stat.st_size if stat else 0}\n".encode())
    return digest.hexdigest()[:16]


def _open_export_page(rel_path):
    """One page as an RGB image. A missing file must not sink the whole export."""
    abs_path = None if rel_path == 'blank' else _abs_image_path(rel_path)
    if abs_path is None or not abs_path.exists():
        return Image.new('RGB', PAGE_PX, (255, 255, 255))
    with Image.open(abs_path) as raw:
        # Načíst pixely, dokud je soubor otevřený. U stran s alfou je stáhne až
        # skládání na bílou, ale strana, která je rovnou RGB, se vrací tak jak je -
        # a po zavření souboru by z ní nešlo číst.
        raw.load()
        return _flatten_to_rgb(raw)


def render_songbook_pdf(sequence, out_path, variant, on_page=None):
    """Write the songbook to a PDF, one page at a time.

    Streamed deliberately. Pillow's save_all with append_images holds every page decoded
    at once, and a 123-page book at 1748x2480 RGBA is over 2 GB - instant OOM on a 1 GB
    box. Appending page by page keeps memory flat: measured 170-250 MB peak regardless
    of whether the book has 26 pages or 123.

    The physical size is pinned to A4 by deriving DPI from each page's own pixel size,
    so scans at other resolutions still come out A4 and no bitmap is rescaled unless the
    variant asks for it.
    """
    settings = EXPORT_VARIANTS[variant]
    quality, max_edge = settings['quality'], settings['max_edge']

    first = True
    for item in sequence:
        started = time.time()
        page = _open_export_page(item['file'])
        if max_edge and max(page.size) > max_edge:
            page.thumbnail((max_edge, max_edge), Image.LANCZOS)
        width, height = page.size
        page.save(
            out_path,
            'PDF',
            dpi=(width / A4_INCHES[0], height / A4_INCHES[1]),
            quality=quality,
            append=not first,
        )
        page.close()
        first = False
        if on_page:
            on_page(time.time() - started)

    if first:
        # Prázdný zpěvník: PDF bez jediné strany uložit nejde, tak aspoň jednu bílou
        Image.new('RGB', PAGE_PX, (255, 255, 255)).save(out_path, 'PDF')


def render_songbook_zip(sequence, out_path):
    """Pack the original page files, without touching the pixels.

    This is the lossless route: the PDF re-encodes to JPEG, the ZIP does not.

    Names lead with a zero-padded sequence number so the archive always opens in reading
    order, and then say what the page is. A bare number would sort right but lose which
    file is a cover and which printed page a scan actually is - and the two do not match,
    because a songbook can start numbering at 3.
    """
    with zipfile.ZipFile(out_path, 'w', compression=zipfile.ZIP_STORED) as archive:
        # ZIP_STORED, ne DEFLATE: PNG i JPEG jsou už komprimované, takže by se procesor
        # spálil za setiny procenta.
        for index, item in enumerate(sequence, start=1):
            abs_path = None if item['file'] == 'blank' else _abs_image_path(item['file'])
            if abs_path is None or not abs_path.exists():
                continue
            if item['kind'] == 'content' and item.get('page_number') is not None:
                popis = f"strana-{item['page_number']}"
            else:
                popis = {'cover': 'obalka', 'intro': 'uvod', 'outro': 'zaver'}.get(
                    item['kind'], item['kind'])
            archive.write(abs_path, f"{index:04d}-{popis}{abs_path.suffix.lower()}")


def _prune_exports(keep_path, sibling_glob):
    """Keep the exports directory from growing without bound.

    sibling_glob matches only *older builds of the same songbook in the same variant and
    format* - superseded the moment the key changed. It must not be widened to the whole
    songbook: doing that made a request for the full-resolution PDF delete the smaller one
    somebody was still waiting for, and their tab then polled a file that would never come.
    """
    try:
        for path in EXPORTS_DIR.glob(sibling_glob):
            if path.is_file() and path != keep_path:
                path.unlink(missing_ok=True)
        files = [p for p in EXPORTS_DIR.glob('*') if p.is_file() and p.suffix in ('.pdf', '.zip')]
    except OSError:
        return

    files = [p for p in EXPORTS_DIR.glob('*') if p.is_file() and p.suffix in ('.pdf', '.zip')]
    total = sum(p.stat().st_size for p in files if p.exists())
    for path in sorted(files, key=lambda p: p.stat().st_mtime if p.exists() else 0):
        if total <= EXPORTS_TOTAL_LIMIT_BYTES:
            break
        if path == keep_path:
            continue
        try:
            total -= path.stat().st_size
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _export_paths(book_id, variant, kind, key):
    """Files for one build. Everything hangs off one name, so nothing can drift apart."""
    safe_id = re.sub(r'[^A-Za-z0-9_]', '_', book_id)
    stem = EXPORTS_DIR / f"{safe_id}-{variant}-{key}"
    return {
        'final': Path(f"{stem}.{kind}"),
        'part': Path(f"{stem}.{kind}.part"),
        'lock': Path(f"{stem}.{kind}.lock"),
        'err': Path(f"{stem}.{kind}.err"),
        # Jen starší buildy TÉŽE varianty a formátu. Širší vzor by mazal soubory,
        # na které někdo jiný zrovna čeká.
        'siblings': f"{safe_id}-{variant}-*.{kind}",
    }


def _build_export_file(book_id, variant, kind, paths):
    """Run one export to completion. Runs in a thread, so it must not raise."""
    try:
        with app.app_context():
            songbook = Songbook.query.get(book_id)
            if songbook is None:
                raise RuntimeError(f"zpěvník {book_id} mezitím zmizel")
            sequence = build_songbook_export_sequence(songbook)
            if kind == 'pdf':
                render_songbook_pdf(sequence, paths['part'], variant)
            else:
                render_songbook_zip(sequence, paths['part'])
        # Až tady je soubor hotový. Přejmenování je atomické, takže hotový export se
        # nikdy neobjeví rozepsaný - kdo ho najde, najde ho celý.
        os.replace(paths['part'], paths['final'])
        paths['err'].unlink(missing_ok=True)
        _prune_exports(paths['final'], paths['siblings'])
    except Exception as exc:  # noqa: BLE001 - vlákno nesmí spadnout potichu
        paths['part'].unlink(missing_ok=True)
        try:
            paths['err'].write_text(str(exc)[:500], encoding='utf-8')
        except OSError:
            pass
    finally:
        paths['lock'].unlink(missing_ok=True)


def _start_export_build(book_id, variant, kind, paths):
    """Claim the build and start it. Returns the state to report back."""
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if paths['lock'].exists():
        # Po zabitém workeru by tu zámek zůstal navěky a export by už nikdy nevznikl
        try:
            stale = time.time() - paths['lock'].stat().st_mtime > EXPORT_LOCK_STALE_SECONDS
        except OSError:
            stale = False
        if not stale:
            return 'building'
        paths['lock'].unlink(missing_ok=True)

    try:
        # O_EXCL je atomické napříč procesy. Workerů jsou čtyři a nesdílejí paměť,
        # takže zámek nemůže být v proměnné - musí být na disku.
        fd = os.open(str(paths['lock']), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return 'building'
    os.close(fd)

    if len(list(EXPORTS_DIR.glob('*.lock'))) > MAX_CONCURRENT_EXPORTS:
        paths['lock'].unlink(missing_ok=True)
        return 'busy'

    threading.Thread(
        target=_build_export_file, args=(book_id, variant, kind, paths), daemon=False
    ).start()
    return 'building'


def _resolve_export_request(book_id, kind):
    """Shared by the download and the status route: authorise, then locate the file."""
    songbook = Songbook.query.get_or_404(book_id)
    if not can_view_songbook(current_user, songbook):
        return None, ("Access denied", 403)

    if kind == 'pdf':
        variant = request.args.get('q', 'small')
        if variant not in EXPORT_VARIANTS:
            return None, (jsonify({'error': 'neznámá varianta'}), 400)
    else:
        variant = 'orig'  # ZIP se nepřekóduje, varianta kvality pro něj nedává smysl

    sequence = build_songbook_export_sequence(songbook)
    if len(sequence) > EXPORT_MAX_PAGES:
        return None, (jsonify({'error': 'zpěvník je příliš velký'}), 413)

    key = songbook_export_key(sequence, variant)
    return {
        'songbook': songbook,
        'variant': variant,
        'kind': kind,
        'paths': _export_paths(book_id, variant, kind, key),
    }, None


@app.route('/songbook/<book_id>/export.<kind>')
@login_required
def songbook_export(book_id, kind):
    """Download the songbook, building it in the background on first ask.

    Not synchronous: a gunicorn sync worker only reports liveness between requests, so
    even a streamed response would not survive the 30s timeout on a long book. The
    client gets 202 and polls instead.
    """
    if kind not in ('pdf', 'zip'):
        return jsonify({'error': 'neznámý formát'}), 404

    resolved, error = _resolve_export_request(book_id, kind)
    if error:
        return error

    paths, songbook = resolved['paths'], resolved['songbook']
    if paths['final'].exists():
        return send_file(
            paths['final'],
            as_attachment=True,
            download_name=f"{slugify(songbook.title) or songbook.id}.{kind}",
            mimetype='application/pdf' if kind == 'pdf' else 'application/zip',
            conditional=True,
        )

    state = _start_export_build(book_id, resolved['variant'], kind, paths)
    return jsonify({'state': state}), 429 if state == 'busy' else 202


@app.route('/songbook/<book_id>/export-status/<kind>')
@login_required
def songbook_export_status(book_id, kind):
    if kind not in ('pdf', 'zip'):
        return jsonify({'error': 'neznámý formát'}), 404

    resolved, error = _resolve_export_request(book_id, kind)
    if error:
        return error

    paths = resolved['paths']
    if paths['final'].exists():
        return jsonify({'state': 'ready'})
    if paths['err'].exists():
        return jsonify({'state': 'error'})
    if paths['lock'].exists():
        return jsonify({'state': 'building'})
    return jsonify({'state': 'idle'})


@app.route('/songbook/<book_id>')
@login_required
def songbook_detail(book_id):
    songbook = Songbook.query.get_or_404(book_id)

    # Permission check: can current user view this songbook?
    if not can_view_songbook(current_user, songbook):
        return "Access denied", 403

    # Determine first_page_side from songbook attribute or default
    first_page_side = getattr(songbook, 'first_page_side', 'left')

    # Query intro pages ordered by page_number
    intros = SongbookIntroOutroImage.query.filter_by(songbook_id=book_id, type='intro').order_by(SongbookIntroOutroImage.sort_order).all()

    # Query outro pages ordered by page_number
    outros = SongbookIntroOutroImage.query.filter_by(songbook_id=book_id, type='outro').order_by(SongbookIntroOutroImage.sort_order).all()

    # Build the page list from the stored page numbers, so a songbook numbered from
    # its title page keeps showing what is printed on the scans. Counting positions
    # here made the viewer disagree with the table of contents.
    raw_pages = SongbookPage.query.filter_by(songbook_id=book_id).order_by(
        SongbookPage.page_number.asc(), SongbookPage.id.asc()
    ).all()

    # raw_pages above stays: the table of contents further down still walks it.
    pages = build_songbook_content_pages(book_id)

    def pair_pages(intro_images, pages, outro_images, first_side, cover_front_outer, cover_front_inner, cover_back_inner, cover_back_outer):
        """Build double-page spreads according to simplified print-like rules.

        - With any cover provided: auto-complete missing cover parts with 'blank' and render:
          none|CFO, then CFI|offset(or content), then intros/pages/outros, then ensure CBI on right,
          then CBO|none.
        - Without cover: optionally offset start if first page should be right, then content,
          and if total pages end on left, add a trailing blank to show full last spread.
        """
        list_of_pages = []

        has_any_cover = any([cover_front_outer, cover_front_inner, cover_back_inner, cover_back_outer])

        if has_any_cover:
            # Auto-complete missing parts with 'blank'
            cfo = {"file": cover_front_outer or "blank", "page_number": None, "kind": "cover"}
            cfi = {"file": cover_front_inner or "blank", "page_number": None, "kind": "cover"}
            cbi = {"file": cover_back_inner or "blank", "page_number": None, "kind": "cover"}
            cbo = {"file": cover_back_outer or "blank", "page_number": None, "kind": "cover"}

            # Closed front cover
            list_of_pages.append({"file": "none", "page_number": None, "kind": "cover"})
            list_of_pages.append(cfo)

            # Open inner front
            list_of_pages.append(cfi)
            if first_side == "left":
                # Offset so first intro/content starts on left on the next spread
                # This is not part of cover; keep it white
                list_of_pages.append({"file": "blank", "page_number": None, "kind": "content"})

            # Main content
            list_of_pages.extend([{"file": img, "page_number": None, "kind": "intro"} for img in intro_images])
            # Ensure kinds for content pages
            list_of_pages.extend([{**p, "kind": p.get("kind", "content")} for p in pages])
            list_of_pages.extend([{"file": img, "page_number": None, "kind": "outro"} for img in outro_images])

            # Ensure back inner cover (CBI) lands on right page
            if len(list_of_pages) % 2 == 0:
                # Next slot would be left -> insert a white blank to shift (not a cover)
                list_of_pages.append({"file": "blank", "page_number": None, "kind": "content"})
            list_of_pages.append(cbi)

            # Closed back cover
            list_of_pages.append(cbo)
            list_of_pages.append({"file": "none", "page_number": None, "kind": "cover"})

        else:
            # No cover: only offset start if needed and place content
            if first_side == "right":
                # Add blank so first content appears on right
                list_of_pages.append({"file": "blank", "page_number": None, "kind": "content"})

            list_of_pages.extend([{"file": img, "page_number": None, "kind": "intro"} for img in intro_images])
            list_of_pages.extend([{**p, "kind": p.get("kind", "content")} for p in pages])
            list_of_pages.extend([{"file": img, "page_number": None, "kind": "outro"} for img in outro_images])

            # If we end on a single left page (odd count), add a trailing blank
            if len(list_of_pages) % 2 != 0:
                list_of_pages.append({"file": "blank", "page_number": None, "kind": "content"})

        return list(zip(list_of_pages[::2], list_of_pages[1::2]))

    # Získej obrázky intro a outro stran
    intro_images = [img.image_path for img in intros]
    outro_images = [img.image_path for img in outros]

    # Sestav page_files přes pomocnou funkci
    page_files = pair_pages(
        intro_images,
        pages,
        outro_images,
        first_page_side,
        getattr(songbook, 'img_path_cover_front_outer', None),
        getattr(songbook, 'img_path_cover_front_inner', None),
        getattr(songbook, 'img_path_cover_back_inner', None),
        getattr(songbook, 'img_path_cover_back_outer', None)
    )

    # Pro scroll mód stačí seznam všech obrázků kromě blank
    scroll_page_files = [img for img in pages if img["file"] != "blank"]

    # Build toc_entries: one entry per song with correct page numbering
    toc_entries = []
    processed_songs = set()
    seen_images_for_toc = set()
    current_toc_page = 1

    for page in raw_pages:
        if page.song_id in processed_songs:
            continue

        song = Song.query.get(page.song_id)
        if not song:
            continue

        # Skip system-generated dummy songs for non-song pages
        if song.title.startswith("Non-song page") or song.title == '<Prázdná strana>':
            # Still count the page in the numbering
            song_images = SongImage.query.filter_by(song_id=song.id).order_by(SongImage.image_path).all()
            current_toc_page += len(song_images) if song_images else 1
            processed_songs.add(page.song_id)
            continue

        # Get all images for this song
        song_images = SongImage.query.filter_by(song_id=song.id).order_by(SongImage.id.asc()).all()
        if song_images:
            # Calculate page range for this song
            start_page = current_toc_page
            end_page = current_toc_page + len(song_images) - 1
            page_display = f"{start_page}" if start_page == end_page else f"{start_page}-{end_page}"

            # Mark images as processed
            for img in song_images:
                seen_images_for_toc.add(img.image_path)
            current_toc_page += len(song_images)
        else:
            # Handle case with no images
            page_display = str(current_toc_page)
            current_toc_page += 1

        # Only add to TOC if not a dummy non-song page
        if not (song.title.startswith("Non-song page") or song.title == '<Prázdná strana>'):
            toc_entries.append({
                'page_number': page_display,
                'title': song.title,
                'author': song.author.name if song.author else ""
            })

        processed_songs.add(page.song_id)

    # Default color fallback
    book_color = getattr(songbook, 'color', '#FFFFFF') or '#FFFFFF'

    # Derive songbook type and edit capabilities for the viewer
    is_public = bool(getattr(songbook, 'is_public', 0))
    is_owner = current_user.is_authenticated and songbook.owner_id == current_user.id
    book_type = 'public' if is_public else ('private' if is_owner else 'shared')
    can_manage = can_edit_songbook(current_user, songbook)

    return render_template(
        'songbook_view.html',
        book_id=book_id,
        toc_entries=toc_entries,
        page_files=page_files,
        scroll_page_files=scroll_page_files,
        first_page_side=first_page_side,
        intros=intros,
        outros=outros,
        book_color=book_color,
        songbook_type=book_type,
        songbook_is_private=(not is_public),
        can_manage_songbook=can_manage
    )

@app.context_processor
def inject_user_status():
    return dict(
        guest=session.get('guest', False),
        logged_in=current_user.is_authenticated
    )

# ---------- CLI PŘÍKAZY ----------

@app.cli.command("export-bench")
@click.argument("book_id")
@click.option("--variant", default="small", type=click.Choice(sorted(EXPORT_VARIANTS)))
@click.option("--keep", is_flag=True, help="nechat vygenerovaný soubor na disku")
@with_appcontext
def export_bench(book_id, variant, keep):
    """Změří generování PDF: čas na stranu, celkový čas, velikost a špičku paměti.

    Existuje proto, aby se o kvalitě a případném zmenšování rozhodovalo z čísel a hlavně
    aby se to samé dalo spustit na serveru, kde to poběží - tam rozhoduje špička paměti,
    ne rychlost Macu.
    """
    import resource

    songbook = Songbook.query.get(book_id)
    if not songbook:
        raise SystemExit(f"❌ zpěvník {book_id} neexistuje")

    sequence = build_songbook_export_sequence(songbook)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = EXPORTS_DIR / f"bench-{book_id}-{variant}.pdf"
    out_path.unlink(missing_ok=True)

    # Měří se ta samá funkce, která poběží v provozu - vlastní kopie smyčky by se s ní
    # dřív nebo později rozešla a měřilo by se něco jiného, než co dělá server.
    casy = []
    zacatek = time.time()
    render_songbook_pdf(sequence, out_path, variant, on_page=casy.append)
    celkem = time.time() - zacatek
    casy_ms = sorted(round(c * 1000) for c in casy)
    velikost = out_path.stat().st_size
    # ru_maxrss je na Linuxu v kB, na macOS v bajtech
    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    maxrss_mb = maxrss / 1024 / 1024 if sys.platform == 'darwin' else maxrss / 1024

    nastaveni = EXPORT_VARIANTS[variant]
    print(f"zpěvník {book_id}: {len(sequence)} stran, varianta {variant} "
          f"(q{nastaveni['quality']}, delší hrana "
          f"{nastaveni['max_edge'] or 'beze změny'})")
    print(f"  celkem      {celkem:.1f} s")
    if casy_ms:
        print(f"  na stranu   min {casy_ms[0]} ms, medián {casy_ms[len(casy_ms) // 2]} ms, "
              f"max {casy_ms[-1]} ms")
        # Linearita: kdyby append přepisoval celý soubor, posledních pět stran bude
        # výrazně pomalejších než prvních pět
        prvnich5 = sum(casy[:5]) / max(1, len(casy[:5]))
        poslednich5 = sum(casy[-5:]) / max(1, len(casy[-5:]))
        print(f"  linearita   prvních 5 {prvnich5 * 1000:.0f} ms, "
              f"posledních 5 {poslednich5 * 1000:.0f} ms "
              f"({'lineární' if poslednich5 < prvnich5 * 3 else '⚠️ ROSTE, nejspíš O(n²)'})")
    print(f"  PDF         {velikost / 1024 / 1024:.1f} MB")
    print(f"  špička RAM  {maxrss_mb:.0f} MB")

    if not keep:
        out_path.unlink(missing_ok=True)


@app.cli.command("export-warm")
@click.option("--variant", default="small", type=click.Choice(sorted(EXPORT_VARIANTS)))
@click.option("--public-only/--all", default=True,
              help="jen naše veřejné zpěvníky, nebo i uživatelské")
@with_appcontext
def export_warm(variant, public_only):
    """Předpřipraví PDF, aby první stažení nečekalo na skládání.

    Cache je klíčovaná obsahem, takže tenhle příkaz nedělá nic zvláštního - postaví
    přesně ty soubory, které by jinak vznikly při prvním stažení. Když se zpěvník změní,
    klíč se změní taky a soubor se prostě přestane používat; stačí příkaz spustit znovu.

    Hodí se po nasazení a po hromadné úpravě veřejných zpěvníků. ZIP se schválně
    nepředpřipravuje: jeho složení je jen zabalení hotových souborů (naměřeno pod
    sekundu), zatímco uložený by zabral tolik místa jako všechny obrázky dohromady.
    """
    query = Songbook.query
    if public_only:
        query = query.filter(Songbook.is_public == 1)
    songbooks = query.order_by(Songbook.id.asc()).all()

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    zacatek = time.time()
    postaveno = preskoceno = 0
    celkem_bytu = 0

    for songbook in songbooks:
        sequence = build_songbook_export_sequence(songbook)
        if not sequence or len(sequence) > EXPORT_MAX_PAGES:
            print(f"  {songbook.id}  přeskočeno ({len(sequence)} stran)")
            continue
        key = songbook_export_key(sequence, variant)
        paths = _export_paths(songbook.id, variant, 'pdf', key)
        if paths['final'].exists():
            preskoceno += 1
            celkem_bytu += paths['final'].stat().st_size
            print(f"  {songbook.id}  už hotové")
            continue

        t0 = time.time()
        render_songbook_pdf(sequence, paths['part'], variant)
        os.replace(paths['part'], paths['final'])
        _prune_exports(paths['final'], paths['siblings'])
        velikost = paths['final'].stat().st_size
        celkem_bytu += velikost
        postaveno += 1
        print(f"  {songbook.id}  {len(sequence):>3} stran  "
              f"{velikost / 1024 / 1024:5.1f} MB  za {time.time() - t0:4.1f} s")

    print(f"\npostaveno {postaveno}, už bylo {preskoceno}, "
          f"celkem {celkem_bytu / 1024 / 1024:.0f} MB, "
          f"trvalo {time.time() - zacatek:.0f} s")
    if celkem_bytu > EXPORTS_TOTAL_LIMIT_BYTES:
        print(f"⚠️  strop na adresář je {EXPORTS_TOTAL_LIMIT_BYTES / 1024 / 1024:.0f} MB, "
              f"úklid začne předpřipravené soubory mazat")


@app.cli.command("init-db")
@with_appcontext
def init_db_command():
    """Vytvoří tabulky podle aktuálních SQLAlchemy modelů."""
    db.create_all()
    click.echo("✅ Databáze inicializována.")


@app.cli.command("create-admin")
@click.option("--email", prompt=True, help="E-mail účtu, který bude vytvořen nebo povýšen na admina.")
@click.option(
    "--password",
    prompt=True,
    hide_input=True,
    confirmation_prompt=True,
    help="Heslo nového admina.",
)
@click.option(
    "--role",
    default="admin",
    show_default=True,
    help="Role přiřazená uživateli (typicky admin).",
)
@with_appcontext
def create_admin_command(email, password, role):
    """Vytvoří nového uživatele s admin právy."""
    user = User.query.filter_by(email=email).first()
    if user:
        click.echo(f"❌ Uživatel {email} už existuje, nic se nezměnilo.")
        return

    hashed_password = generate_password_hash(password, method='pbkdf2:sha256', salt_length=16)
    new_admin = User(email=email, password=hashed_password, role=role)
    db.session.add(new_admin)
    db.session.commit()
    click.echo(f"✅ Admin účet vytvořen: {email} (role: {role})")

# ---------- START ----------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()

    app.run(debug=app.config.get('DEBUG', False))
