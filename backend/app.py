import os
import json
from flask import Flask, render_template, redirect, url_for, request, flash, session, send_from_directory, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, UserMixin, current_user
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
import re

from models import Song, SongImage, SongbookPage, SongbookIntroOutroImage, Songbook, Author, User, UserSongbookAccess, db, init_app

# Načti konfiguraci z .env
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv("FLASK_SECRET_KEY")
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

# ---------- VALIDACE ----------
def is_valid_email(email):
    return re.match(r"[^@]+@[^@]+\.[^@]+", email)

# ---------- MODELY ----------
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)

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
    session.clear()
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

@app.route('/public-songbooks')
@login_required
def public_songbooks():
    # Load public songbooks from the database
    songbooks = db.session.execute(
        db.select(Songbook).where(Songbook.is_public == 1)
    ).scalars().all()
    is_guest = (current_user.email == "guest@guest.com")
    return render_template('public_songbooks.html', songbooks=songbooks, guest=is_guest)

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
        print(page.song_id, page.song_id[1])
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
        list_of_pages = []
        if cover_front_outer:
            list_of_pages.append("none")
            list_of_pages.append(cover_front_outer)
            if cover_front_inner: 
                list_of_pages.append(cover_front_inner)
            else:
                list_of_pages.append("blank")
            if first_side == "left":
                list_of_pages.append("blank")
        elif first_side == "right":
            list_of_pages.append("blank")
        
        list_of_pages.extend(intros)
        list_of_pages.extend(pages)
        list_of_pages.extend(outros)

        if len(list_of_pages) % 2 != 0:
            if cover_back_outer and cover_back_inner:
                list_of_pages.append(cover_back_inner)
            else:
                list_of_pages.append("blank")

        if cover_back_outer:
            list_of_pages.append(cover_back_outer)
            list_of_pages.append("none")

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
        logged_in=bool(session.get('user_id'))
    )

# ---------- START ----------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)