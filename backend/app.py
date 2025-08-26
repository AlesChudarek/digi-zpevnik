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
    page_files = [f for f in os.listdir(pages_dir) if f.startswith("page") and f.endswith(".png")]
    total_pages = len(page_files)
    return render_template('songbook_view.html', book_id=book_id, total_pages=total_pages)

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