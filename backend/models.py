from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String, unique=True, nullable=False)
    password = db.Column(db.String, nullable=False)

class Author(db.Model):
    __tablename__ = "authors"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, unique=True, nullable=False)

class Song(db.Model):
    __tablename__ = "songs"

    id = db.Column(db.String, primary_key=True)
    title = db.Column(db.String, nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey("authors.id"))
    author = db.relationship("Author", backref="songs")
    images = db.relationship("SongImage", backref="song", cascade="all, delete-orphan")

class SongImage(db.Model):
    __tablename__ = "song_images"

    id = db.Column(db.Integer, primary_key=True)
    song_id = db.Column(db.String, db.ForeignKey("songs.id"), nullable=False)
    image_path = db.Column(db.String, nullable=False)

class Songbook(db.Model):
    __tablename__ = "songbooks"

    id = db.Column(db.String, primary_key=True)
    title = db.Column(db.String, nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    first_page_side = db.Column(db.String, default="right")
    img_path_cover_preview = db.Column(db.String, nullable=True)
    img_path_cover_front_outer = db.Column(db.String, nullable=True)
    img_path_cover_front_inner = db.Column(db.String, nullable=True)
    img_path_cover_back_inner = db.Column(db.String, nullable=True)
    img_path_cover_back_outer = db.Column(db.String, nullable=True)
    is_public = db.Column(db.Integer, default=0)
    pages = db.relationship("SongbookPage", backref="songbook", cascade="all, delete-orphan")
    intros_outros = db.relationship("SongbookIntroOutroImage", backref="songbook", cascade="all, delete-orphan")

class SongbookIntroOutroImage(db.Model):
    __tablename__ = "songbook_intro_outro_images"

    id = db.Column(db.Integer, primary_key=True)
    songbook_id = db.Column(db.String, db.ForeignKey("songbooks.id"), nullable=False)
    type = db.Column(db.String, nullable=False)  # 'intro' nebo 'outro'
    image_path = db.Column(db.String, nullable=False)
    sort_order = db.Column(db.Integer, default=0)

class UserSongbookAccess(db.Model):
    __tablename__ = "user_songbook_access"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    songbook_id = db.Column(db.String, db.ForeignKey("songbooks.id"), primary_key=True)

class SongbookPage(db.Model):
    __tablename__ = "songbook_pages"

    id = db.Column(db.Integer, primary_key=True)
    songbook_id = db.Column(db.String, db.ForeignKey("songbooks.id"), nullable=False)
    song_id = db.Column(db.String, db.ForeignKey("songs.id"), nullable=False)
    page_number = db.Column(db.Integer, nullable=False)
    song = db.relationship("Song", backref="songbook_pages")


# Funkce pro propojení db s Flask aplikací
def init_app(app):
    db.init_app(app)


# Nová třída reprezentující části písně (např. pro více stran nebo více písní na stránce)
class SongPart(db.Model):
    __tablename__ = "song_parts"

    id = db.Column(db.Integer, primary_key=True)
    song_id = db.Column(db.String, db.ForeignKey("songs.id"), nullable=False)
    title = db.Column(db.String, nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey("authors.id"), nullable=True)
    image_path = db.Column(db.String, nullable=False)
    song = db.relationship("Song", backref="parts")
    author = db.relationship("Author", backref="song_parts")
