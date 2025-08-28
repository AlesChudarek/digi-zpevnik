import os
import json
from flask import Flask, render_template, redirect, url_for, request, flash, session, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, UserMixin, current_user
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
import re

# Načti konfiguraci z .env
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv("FLASK_SECRET_KEY")
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Inicializace databáze
db = SQLAlchemy(app)

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

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.email == "guest@guest.com":
        return render_template('dashboard.html', guest=True)
    return render_template('dashboard.html', guest=False)

@app.route('/public-songbooks')
@login_required
def public_songbooks():
    metadata_path = os.path.join(app.static_folder, 'songbooks', 'metadata.json')
    with open(metadata_path, 'r', encoding='utf-8') as f:
        songbooks = json.load(f)
    is_guest = (current_user.email == "guest@guest.com")
    return render_template('public_songbooks.html', songbooks=songbooks, guest=is_guest)

@app.route('/songbook/<int:book_id>')
@login_required
def songbook_detail(book_id):
    pages_dir = os.path.join(app.static_folder, 'songbooks', str(book_id))
    metadata_path = os.path.join(pages_dir, 'toc.json')

    # Load TOC info
    first_page_side = 'left'
    toc_data = {}
    if os.path.exists(metadata_path):
        with open(metadata_path, 'r', encoding='utf-8') as f:
            toc_data = json.load(f)
            first_page_side = toc_data.get('first_page_side', 'left')

    # Collect files
    all_files = os.listdir(pages_dir)

    # If fron cover exists, load it and check if inner cover exists
    coverfrontout = ["none","coverfrontout.png"] if 'coverfrontout.png' in all_files else []
    coverfrontin = ["coverfrontin.png"] if 'coverfrontin.png' in all_files and coverfrontout else []
    coverfrontin = ["blank"] if coverfrontout and not coverfrontin else coverfrontin

    intro_pages = sorted([f for f in all_files if f.startswith('intro') and f.endswith('.png')],
                        key=lambda x: int(x.replace('intro', '').replace('.png', '')))

    add_blank = ["blank"] if (len(intro_pages) + ((first_page_side == "right") != bool(coverfrontout))) % 2 else []

    page_pages = sorted([f for f in all_files if f.startswith('page') and f.endswith('.png')],
                        key=lambda x: int(x.replace('page', '').replace('.png', '')))

    outro_pages = sorted([f for f in all_files if f.startswith('outro') and f.endswith('.png')],
                        key=lambda x: int(x.replace('outro', '').replace('.png', '')))

    full_songbook = coverfrontout + coverfrontin + intro_pages + add_blank + page_pages + outro_pages
    
    # full_songbook = [page for page in full_songbook if page is not None]

    coverbackout = 'coverbackout.png' if 'coverbackout.png' in all_files else None
    coverbackin = 'coverbackin.png' if 'coverbackin.png' in all_files and coverbackout else "blank"

    if (len(full_songbook) % 2 == 0) == bool(coverbackout):
        full_songbook.append("blank")

    if coverbackout:   
        full_songbook.append(coverbackin)
        full_songbook.append(coverbackout)
        full_songbook.append("none")

    page_files = list(zip(full_songbook[::2], full_songbook[1::2]))
    # Odstraníme "blank" pro scrollovací režim
    scroll_page_files = [p for pair in page_files for p in pair if p != "blank"]
    print(page_files)

    toc_entries = toc_data.get("pages", []) if isinstance(toc_data, dict) else []

    return render_template(
        'songbook_view.html',
        book_id=book_id,
        page_files=page_files,
        scroll_page_files=scroll_page_files,
        toc_entries=toc_entries
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