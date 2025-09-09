import os
import json
from flask import Flask, render_template, redirect, url_for, request, flash, session, send_from_directory, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, UserMixin, current_user
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
import re
import time
from pathlib import Path
from sqlalchemy import or_

from models import Song, SongImage, SongbookPage, SongbookIntroOutroImage, Songbook, Author, User, UserSongbookAccess, db, init_app

# Načti konfiguraci z .env
load_dotenv()

app = Flask(__name__)
# Robust dev default to avoid broken sessions when FLASK_SECRET_KEY is missing
app.config['SECRET_KEY'] = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-me")
basedir = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(basedir, 'db', 'zpevnik.db')
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
            new_user = User(email=email, password=hashed_password)
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
        user = User(email=guest_email, password=hashed_password)
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
    for page in pages:
        song = Song.query.get(page.song_id)
        image = SongImage.query.filter_by(song_id=song.id).first()
        toc.append({
            "title": song.title,
            "author": song.author.name if song.author else "",
            "page": image.image_path if image else "",
            "page_number": page.page_number
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
    if current_user.is_authenticated:
        shared_ids = [row.songbook_id for row in UserSongbookAccess.query.filter_by(user_id=current_user.id).all()]

    # Build query across pages -> song -> author -> songbook
    q = db.session.query(
        SongbookPage.page_number.label('page_number'),
        Song.title.label('song_title'),
        Author.name.label('author_name'),
        Songbook.id.label('songbook_id'),
        Songbook.title.label('songbook_title'),
        Songbook.owner_id.label('owner_id'),
        Songbook.is_public.label('is_public')
    ).join(Song, SongbookPage.song_id == Song.id
    ).join(Songbook, SongbookPage.songbook_id == Songbook.id
    ).join(Author, Song.author_id == Author.id, isouter=True)

    # Filter accessible songbooks
    filters = [Songbook.is_public == 1]
    if current_user.is_authenticated:
        filters.append(Songbook.owner_id == current_user.id)
        if shared_ids:
            filters.append(Songbook.id.in_(shared_ids))

    rows = (
        q.filter(or_(*filters))
         .order_by(Song.title.asc(), Songbook.title.asc(), SongbookPage.page_number.asc())
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

        results.append({
            'song_title': r.song_title,
            'author_name': r.author_name or '',
            'songbook_id': r.songbook_id,
            'songbook_title': r.songbook_title,
            'book_type': book_type,
            'page_number': r.page_number,
            'owned_by_user': (current_user.is_authenticated and r.owner_id == current_user.id)
        })

    is_guest = (current_user.email == "guest@guest.com")
    return render_template('search.html', rows=results, guest=is_guest)

@app.route('/public-songbooks')
@login_required
def public_songbooks():
    # Load public songbooks from the database
    songbooks = db.session.execute(
        db.select(Songbook).where(Songbook.is_public == 1)
    ).scalars().all()
    is_guest = (current_user.email == "guest@guest.com")
    return render_template('public_songbooks.html', songbooks=songbooks, guest=is_guest)

@app.route('/my-songbooks')
@login_required
def my_songbooks():
    # Only songbooks owned by the current user
    books = db.session.execute(
        db.select(Songbook).where(Songbook.owner_id == current_user.id)
    ).scalars().all()
    return render_template('my_songbooks.html', songbooks=books)

@app.route('/songbook/<book_id>')
@login_required
def songbook_detail(book_id):
    songbook = Songbook.query.get_or_404(book_id)

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
    for page in raw_pages:
        song = Song.query.get(page.song_id)
        if not song:
            continue
        song_images = SongImage.query.filter_by(song_id=song.id).order_by(SongImage.image_path).all()
        if not song_images:
            pages.append("blank")
        else:
            for img in song_images:
                if img.image_path not in seen_images:
                    seen_images.add(img.image_path)
                    pages.append(img.image_path)

    def pair_pages(intros, pages, outros, first_side, cover_front_outer, cover_front_inner, cover_back_inner, cover_back_outer):
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
            cfo = cover_front_outer or "blank"
            cfi = cover_front_inner or "blank"
            cbi = cover_back_inner or "blank"
            cbo = cover_back_outer or "blank"

            # Closed front cover
            list_of_pages.append("none")
            list_of_pages.append(cfo)

            # Open inner front
            list_of_pages.append(cfi)
            if first_side == "left":
                # Offset so first intro starts on left on the next spread
                list_of_pages.append("blank")

            # Main content
            list_of_pages.extend(intros)
            list_of_pages.extend(pages)
            list_of_pages.extend(outros)

            # Ensure back inner cover (CBI) lands on right page
            if len(list_of_pages) % 2 == 0:
                # Next slot would be left -> insert blank to shift
                list_of_pages.append("blank")
            list_of_pages.append(cbi)

            # Closed back cover
            list_of_pages.append(cbo)
            list_of_pages.append("none")

        else:
            # No cover: only offset start if needed and place content
            if first_side == "right":
                # Add blank so first content appears on right
                list_of_pages.append("blank")

            list_of_pages.extend(intros)
            list_of_pages.extend(pages)
            list_of_pages.extend(outros)

            # If we end on a single left page (odd count), add a trailing blank
            if len(list_of_pages) % 2 != 0:
                list_of_pages.append("blank")

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
    scroll_page_files = [img for img in pages if img != "blank"]

    # Build toc_entries: one entry per song, use correct page_number from SongbookPage
    toc_entries = []
    for page in raw_pages:
        song = Song.query.get(page.song_id)
        toc_entries.append({
            'page_number': page.page_number,
            'title': song.title,
            'author': song.author.name if song.author else ""
        })

    return render_template(
        'songbook_view.html',
        book_id=book_id,
        toc_entries=toc_entries,
        page_files=page_files,
        scroll_page_files=scroll_page_files,
        first_page_side=first_page_side,
        intros=intros,
        outros=outros
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
    app.run(debug=True)
