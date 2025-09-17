import os
import json
from flask import Flask, render_template, redirect, url_for, request, flash, session, send_from_directory, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, UserMixin, current_user
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
import re
import unicodedata
import time
from pathlib import Path
from werkzeug.utils import secure_filename
from sqlalchemy import or_, func
import shutil
from uuid import uuid4

# Cesty k obrázkům zpěvníků
SONGBOOK_IMAGES_DIR = Path(__file__).parent.parent / 'data' / 'public' / 'images' / 'songbooks'
PRIVATE_USER_IMAGES_DIR = Path(__file__).parent.parent / 'data' / 'private' / 'users'

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

# Načti konfiguraci z .env
load_dotenv()

app = Flask(__name__, template_folder='../frontend/templates', static_folder='../frontend/static')
# Robust dev default to avoid broken sessions when FLASK_SECRET_KEY is missing
app.config['SECRET_KEY'] = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-me")
basedir = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(basedir, 'instance', 'zpevnik.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

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
    # songbook = Songbook.query.filter_by(id=songbook_id).first_or_404()
    toc = []
    pages = SongbookPage.query.filter_by(songbook_id=songbook_id).order_by(SongbookPage.page_number).all()

    # Calculate correct page numbers for TOC
    seen_images = set()
    current_page_number = 1

    for page in pages:
        song = Song.query.get(page.song_id)
        if not song:
            continue

        # Skip system-generated dummy songs for non-song pages
        if song.title.startswith("Non-song page"):
            # Still count the page in the numbering
            song_images = SongImage.query.filter_by(song_id=song.id).order_by(SongImage.image_path).all()
            current_page_number += len(song_images) if song_images else 1
            continue

        song_images = SongImage.query.filter_by(song_id=song.id).order_by(SongImage.image_path).all()
        if song_images:
            # Use the first image for TOC entry
            first_image = song_images[0]
            if first_image.image_path not in seen_images:
                toc.append({
                    "title": song.title,
                    "author": song.author.name if song.author else "",
                    "page": first_image.image_path,
                    "page_number": current_page_number,
                    "song_id": song.id
                })
                # Mark all images of this song as seen
                for img in song_images:
                    seen_images.add(img.image_path)
                current_page_number += len(song_images)
        else:
            # Handle case with no images
            toc.append({
                "title": song.title,
                "author": song.author.name if song.author else "",
                "page": "",
                "page_number": current_page_number,
                "song_id": song.id
            })
            current_page_number += 1

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
    if current_user.is_authenticated:
        shared_ids = [row.songbook_id for row in UserSongbookAccess.query.filter_by(user_id=current_user.id).all()]

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
        Songbook.is_public.label('is_public')
    ).join(Song, Song.id == first_pages_subq.c.song_id
    ).join(Songbook, Songbook.id == first_pages_subq.c.songbook_id
    ).join(Author, Song.author_id == Author.id, isouter=True)

    # Exclude system-generated dummy entries for non-song pages
    q = q.filter(~or_(Song.title.like('Non-song page%'), Song.title == '<Prázdná strana>'))

    # Filter accessible songbooks
    filters = [Songbook.is_public == 1]
    if current_user.is_authenticated:
        filters.append(Songbook.owner_id == current_user.id)
        if shared_ids:
            filters.append(Songbook.id.in_(shared_ids))

    rows = (
        q.filter(or_(*filters))
         .order_by(Song.title.asc(), Songbook.title.asc(), first_pages_subq.c.first_page_number.asc())
         .all()
    )

    results = []
    for r in rows:
        # Determine book type label: '' for public, 'private' if owned, otherwise 'shared'
        if r.is_public == 1:
            book_type = ''
        elif current_user.is_authenticated and r.owner_id == current_user.id:
            book_type = 'private'
        else:
            book_type = 'shared'

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
            'owned_by_user': (current_user.is_authenticated and r.owner_id == current_user.id)
        })

    is_guest = (current_user.email == "guest@guest.com")
    return render_template('search.html', rows=results, guest=is_guest)

# API: List current user's private songbooks (for adding songs)
@app.route('/api/my-songbooks/options')
@login_required
def list_my_songbooks_options():
    books = db.session.execute(
        db.select(Songbook).where((Songbook.is_public == 0) & (Songbook.owner_id == current_user.id))
    ).scalars().all()
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
                'has_song': (b.id in present_ids)
            } for b in books
        ]
    })

# API: Add a song (all its pages) to target songbook, appended at the end
@app.route('/api/songbooks/<songbook_id>/add-song', methods=['POST'])
@login_required
def add_song_to_songbook(songbook_id):
    sb = Songbook.query.get_or_404(songbook_id)
    # Only owner or admin can modify
    if not (current_user.role == 'admin' or (sb.owner_id and sb.owner_id == current_user.id)):
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
    song_images = SongImage.query.filter_by(song_id=song.id).order_by(SongImage.image_path).all()
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
    if not (current_user.role == 'admin' or (sb.owner_id and sb.owner_id == current_user.id)):
        return jsonify({'ok': False, 'error': 'Forbidden'}), 403

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
    song = Song(id=new_song_id, title=title, author_id=author.id)
    db.session.add(song)
    db.session.flush()

    # Save images under the user's private dir, in a song-specific subfolder
    def resolve_private_dir() -> Path:
        p = sb.img_path_cover_preview or sb.img_path_cover_front_outer or sb.img_path_cover_front_inner
        if p and isinstance(p, str) and p.startswith('users/'):
            parts = Path(p).parts
            if len(parts) >= 4:
                return PRIVATE_USER_IMAGES_DIR / Path(*parts[1:-1]) / 'songs' / new_song_id
        owner = User.query.get(sb.owner_id) if sb.owner_id else None
        owner_email = getattr(owner, 'email', '')
        user_dir = f"{sb.owner_id}_{slugify(owner_email, 50)}"
        book_dir = f"{sb.id}_{slugify(sb.title, 50) if sb.title else 'untitled'}"
        return PRIVATE_USER_IMAGES_DIR / user_dir / book_dir / 'songs' / new_song_id

    abs_dir = resolve_private_dir()
    abs_dir.mkdir(parents=True, exist_ok=True)

    saved = 0
    for idx, file_storage in files:
        orig = secure_filename(Path(file_storage.filename).name) or f"page_{idx}.png"
        abs_path = abs_dir / orig
        file_storage.save(str(abs_path))
        rel_parts = abs_path.relative_to(PRIVATE_USER_IMAGES_DIR)
        rel_path = str(Path('users') / rel_parts)
        db.session.add(SongImage(song_id=new_song_id, image_path=rel_path))
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
    if not (current_user.role == 'admin' or (sb.owner_id and sb.owner_id == current_user.id)):
        return jsonify({'ok': False, 'error': 'Forbidden'}), 403

    song = Song.query.get_or_404(song_id)
    imgs = SongImage.query.filter_by(song_id=song.id).all()

    # If no images or images are public (not under users/), just detach from this songbook
    if not imgs or not any((img.image_path or '').startswith('users/') for img in imgs):
        db.session.query(SongbookPage).filter_by(songbook_id=sb.id, song_id=song.id).delete()
        db.session.commit()
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
        return jsonify({'ok': True, 'deleted_song': True})

@app.route('/public-songbooks')
@login_required
def public_songbooks():
    # Show all public songbooks for everyone
    songbooks = db.session.execute(
        db.select(Songbook).where(Songbook.is_public == 1)
    ).scalars().all()
    is_guest = (current_user.email == "guest@guest.com")
    return render_template('public_songbooks.html', songbooks=songbooks, guest=is_guest)

@app.route('/my-songbooks')
@login_required
def my_songbooks():
    # Guests cannot access "My Songbooks"
    if current_user.role == 'guest':
        return render_template('my_songbooks.html', songbooks=[])
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
    return render_template('my_songbooks.html', songbooks=books)

# API: Create a new private songbook for current user
@app.route('/api/my-songbooks', methods=['POST'])
@login_required
def api_create_songbook():
    if current_user.role == 'guest':
        return jsonify({"ok": False, "error": "Guests cannot create songbooks"}), 403

    title = (request.form.get('title') or '').strip() or 'Můj zpěvník'
    use_cover = request.form.get('use_cover', '1') in ('1', 'true', 'True', 'on')

    # Generate a simple unique ID scoped by user and timestamp
    sid = f"u{current_user.id}-{int(time.time())}"

    user_dir = f"{current_user.id}_{slugify(current_user.email, 50)}"
    book_dir = f"{sid}_{slugify(title, 50) if title else 'untitled'}"

    # Prepare file save helper
    def save_cover(file_storage, name_hint):
        if not file_storage:
            return None
        # Normalize extension
        ext = (Path(file_storage.filename).suffix or '.png').lower()
        if ext not in ['.png', '.jpg', '.jpeg', '.webp', '.svg']:
            ext = '.png'
        rel_dir = Path('users') / user_dir / book_dir
        abs_dir = PRIVATE_USER_IMAGES_DIR / user_dir / book_dir
        abs_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{name_hint}{ext}"
        abs_path = abs_dir / filename
        file_storage.save(str(abs_path))
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

    # Create songbook ORM entry
    sb = Songbook(
        id=sid,
        title=title,
        owner_id=current_user.id,
        is_public=0,
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
    # Only owner or admin can delete
    if not (current_user.role == 'admin' or (sb.owner_id and sb.owner_id == current_user.id)):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    # Remove private cover directory if present
    try:
        target_dir = None
        # Prefer deriving from stored preview path if present
        p = sb.img_path_cover_preview
        if p and isinstance(p, str) and p.startswith('users/'):
            parts = Path(p).parts
            # users/<user_dir>/<book_dir>/<file>
            if len(parts) >= 4:
                target_dir = PRIVATE_USER_IMAGES_DIR / Path(*parts[1:-1])
        if target_dir is None:
            # Fallback to expected directory name pattern
            owner = User.query.get(sb.owner_id) if sb.owner_id else None
            owner_email = getattr(owner, 'email', None) or ''
            user_dir = f"{sb.owner_id}_{slugify(owner_email, 50)}"
            book_dir = f"{sb.id}_{slugify(sb.title, 50) if sb.title else 'untitled'}"
            target_dir = PRIVATE_USER_IMAGES_DIR / user_dir / book_dir
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
    except Exception:
        pass  # ignore file removal errors

    # Delete DB entry (cascade removes pages/intro_outro)
    db.session.delete(sb)
    db.session.commit()

    return jsonify({"ok": True})

# API: Get songbook structure for editing (owner only)
@app.route('/api/my-songbooks/<songbook_id>/structure')
@login_required
def get_songbook_structure(songbook_id):
    sb = Songbook.query.get_or_404(songbook_id)
    if not (current_user.role == 'admin' or (sb.owner_id and sb.owner_id == current_user.id)):
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
            subq_min.c.start_page, subq_min.c.page_count
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

    return jsonify({
        'ok': True,
        'songbook': {
            'id': sb.id,
            'title': sb.title,
            'color': getattr(sb, 'color', '#FFFFFF') or '#FFFFFF',
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
                    # For non-song pages, present a visibly different label. Use escaped angle brackets so HTML stays visible.
                    'title': ("&lt;Prázdná strana&gt;" if (not r[1] or (isinstance(r[1], str) and (r[1].startswith("Non-song page") or r[1] == "<Prázdná strana>"))) else r[1]),
                    'author': ('-' if (not r[1] or (isinstance(r[1], str) and (r[1].startswith("Non-song page") or r[1] == "<Prázdná strana>"))) else (r[2] or '')),
                    'start_page': r[3],
                    'page_count': r[4],
                    'is_private': (r[0] in private_set),
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
    if not (current_user.role == 'admin' or (sb.owner_id and sb.owner_id == current_user.id)):
        return jsonify({'ok': False, 'error': 'Forbidden'}), 403

    title = (request.form.get('title') or sb.title).strip()
    color = (request.form.get('color') or getattr(sb, 'color', '#FFFFFF') or '#FFFFFF').strip()
    auto_numbering = (request.form.get('auto_numbering', '1') in ('1', 'true', 'True', 'on'))

    # Save optional cover files into existing private folder if possible
    def resolve_private_dir() -> Path:
        p = sb.img_path_cover_preview or sb.img_path_cover_front_outer or sb.img_path_cover_front_inner
        if p and isinstance(p, str) and p.startswith('users/'):
            parts = Path(p).parts
            if len(parts) >= 4:
                return PRIVATE_USER_IMAGES_DIR / Path(*parts[1:-1])
        # Fallback to computed path (do not rename based on new title)
        owner = User.query.get(sb.owner_id) if sb.owner_id else None
        owner_email = getattr(owner, 'email', '')
        user_dir = f"{sb.owner_id}_{slugify(owner_email, 50)}"
        book_dir = f"{sb.id}_{slugify(sb.title, 50) if sb.title else 'untitled'}"
        return PRIVATE_USER_IMAGES_DIR / user_dir / book_dir

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
        abs_dir = resolve_private_dir()
        abs_dir.mkdir(parents=True, exist_ok=True)
        abs_path = abs_dir / orig_name
        file_storage.save(str(abs_path))
        # Return relative path
        rel_parts = abs_path.relative_to(PRIVATE_USER_IMAGES_DIR)
        return str(Path('users') / rel_parts)

    # Keep originals to allow cleanup when new files are uploaded (avoid storage bloat)
    old_front_outer = sb.img_path_cover_front_outer
    old_front_inner = sb.img_path_cover_front_inner
    old_back_inner = sb.img_path_cover_back_inner
    old_back_outer = sb.img_path_cover_back_outer

    f_front_outer = request.files.get('front_outer')
    f_front_inner = request.files.get('front_inner')
    f_back_inner = request.files.get('back_inner')
    f_back_outer = request.files.get('back_outer')

    def abs_from_rel(rel_path: str) -> Path:
        try:
            if not rel_path or not isinstance(rel_path, str) or not rel_path.startswith('users/'):
                return None
            return PRIVATE_USER_IMAGES_DIR / Path(rel_path).relative_to('users')
        except Exception:
            return None

    def cleanup_old(old_rel: str, new_rel: str):
        try:
            if old_rel and old_rel != new_rel:
                p = abs_from_rel(old_rel)
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
        incoming_ids = [e.get('song_id') for e in song_entries if e.get('song_id')]
        to_delete = set(existing_ids) - set(incoming_ids)

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

        next_page = 1
        # Helper: ensure 'System' author exists for non-song pages
        def get_system_author_id():
            sys = Author.query.filter_by(name='System').first()
            if not sys:
                sys = Author(name='System')
                db.session.add(sys)
                db.session.flush()
            return sys.id

        from uuid import uuid4

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
                    ns_song = Song(id=ns_song_id, title='<Prázdná strana>', author_id=sys_author_id)
                    db.session.add(ns_song)
                    db.session.flush()
                    db.session.add(SongbookPage(songbook_id=songbook_id, song_id=ns_song_id, page_number=start))
                    next_page = start + page_count if not auto_numbering else (next_page + page_count)
                continue
            page_count = int(counts.get(sid, 0))
            if page_count <= 0:
                continue
            start = next_page if auto_numbering else int(entry.get('start_page') or next_page)

            # Select rows for this song ordered by page_number then id
            rows = (SongbookPage.query
                    .filter_by(songbook_id=songbook_id, song_id=sid)
                    .order_by(SongbookPage.page_number.asc(), SongbookPage.id.asc())
                    .all())
            # Reassign page numbers sequentially from 'start'
            p = start
            for r in rows:
                r.page_number = p
                p += 1

            next_page = start + page_count if not auto_numbering else (next_page + page_count)

    db.session.commit()
    return jsonify({'ok': True})

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

    # Query songbook pages ordered by page_number
    seen_images = set()
    pages = []
    raw_pages = SongbookPage.query.filter_by(songbook_id=book_id).order_by(SongbookPage.page_number).all()
    current_page_number = 1  # Start with page 1

    for page in raw_pages:
        song = Song.query.get(page.song_id)
        if not song:
            continue
        song_images = SongImage.query.filter_by(song_id=song.id).order_by(SongImage.image_path).all()
        if not song_images:
            # Non-song page: represent as a single blank content page and advance numbering
            pages.append({"file": "blank", "page_number": current_page_number, "kind": "content"})
            current_page_number += 1
        else:
            for img in song_images:
                if img.image_path not in seen_images:
                    seen_images.add(img.image_path)
                    pages.append({"file": img.image_path, "page_number": current_page_number, "kind": "content"})
                    current_page_number += 1

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
        song_images = SongImage.query.filter_by(song_id=song.id).order_by(SongImage.image_path).all()
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

    return render_template(
        'songbook_view.html',
        book_id=book_id,
        toc_entries=toc_entries,
        page_files=page_files,
        scroll_page_files=scroll_page_files,
        first_page_side=first_page_side,
        intros=intros,
        outros=outros,
        book_color=book_color
    )

@app.context_processor
def inject_user_status():
    return dict(
        guest=session.get('guest', False),
        logged_in=current_user.is_authenticated
    )

# ---------- START ----------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()

        # Seed admin user if not exists
        admin_email = "admin@test.com"
        admin_password = "admin123"
        admin_role = "admin"

        admin = User.query.filter_by(email=admin_email).first()
        if not admin:
            hashed_password = generate_password_hash(admin_password, method='pbkdf2:sha256', salt_length=16)
            new_admin = User(email=admin_email, password=hashed_password, role=admin_role)
            db.session.add(new_admin)
            db.session.commit()
            print(f"✅ Admin user created: {admin_email}")

    app.run(debug=True)
