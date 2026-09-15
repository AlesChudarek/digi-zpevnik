from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from sqlalchemy.ext.associationproxy import association_proxy

db = SQLAlchemy()


class Image(db.Model):
    """Jeden obrázkový soubor. Identitou je řádek, ne řetězec s cestou.

    Dřív byla identitou obrázku jeho cesta, uložená jako text na šesti různých místech
    (`song_images` a pět sloupců obálek v `songbooks`). Otázka „ukazuje na tenhle soubor
    ještě někdo?" se pak musela ptát šestkrát a porovnávat řetězce, nic nehlídalo, že
    cesta vůbec existuje, a přesun souboru znamenal přepsat šest sloupců.

    Sdílení vychází samo: strana se dvěma písněmi je jeden řádek `images` a dva řádky
    `song_images`; táž strana ve dvou zpěvnících je pořád jeden řádek `images`.

    Kód dál pracuje s `image_path` a `img_path_cover_*` jako s textem - drží to
    `association_proxy` níž, takže se kvůli téhle změně nemuselo přepsat 227 míst.
    """

    __tablename__ = "images"

    id = db.Column(db.Integer, primary_key=True)
    cesta = db.Column(db.String, unique=True, nullable=False, index=True)

    @classmethod
    def ziskej(cls, cesta):
        """Řádek pro danou cestu; když není, založí ho.

        Hledá i mezi ještě nezapsanými objekty v session. Bez toho by dvě strany nahrané
        v jednom požadavku pod stejnou cestou založily dva řádky a unikátní index by to
        shodil až při commitu.
        """
        if not cesta:
            return None
        radek = cls.query.filter_by(cesta=cesta).first()
        if radek is not None:
            return radek
        for cekajici in db.session.new:
            if isinstance(cekajici, cls) and cekajici.cesta == cesta:
                return cekajici
        radek = cls(cesta=cesta)
        db.session.add(radek)
        return radek


def _cesta_obrazku(cesta):
    """Tvůrce pro association_proxy: z textu udělá řádek `images`."""
    return Image.ziskej(cesta)

class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String, unique=True, nullable=False)
    password = db.Column(db.String, nullable=False)
    role = db.Column(db.String, default='user')
    # Prokázal uživatel, že mu ta adresa patří? Schválně samostatný příznak, ne další role:
    # role říká, čím uživatel je, tohle jestli je jeho adresa ověřená. Kdyby to byla role,
    # nebylo by po ověření kam ho vrátit.
    email_verified = db.Column(db.Boolean, nullable=False, default=False,
                               server_default='0')
    # Vybraný barevný motiv. Prázdno znamená "nic uloženého" - pak platí volba
    # z prohlížeče. Díky tomu si host i nepřihlášený barvu pořád mění jako dřív.
    theme = db.Column(db.String, nullable=True)

class LoginAttempt(db.Model):
    """Neúspěšné pokusy o přihlášení, kvůli omezení jejich frekvence.

    Ukládá se do databáze, ne do paměti procesu: gunicorn běží ve dvou workerech a
    každý by měl vlastní počítadlo, takže by povolený počet pokusů byl ve skutečnosti
    dvojnásobný. Úspěšné přihlášení své záznamy smaže.
    """
    __tablename__ = "login_attempts"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String, index=True)
    ip = db.Column(db.String, index=True)
    cas = db.Column(db.DateTime, index=True, nullable=False)


class Author(db.Model):
    __tablename__ = "authors"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, unique=True, nullable=False)

class Song(db.Model):
    __tablename__ = "songs"

    id = db.Column(db.String, primary_key=True)
    title = db.Column(db.String, nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey("authors.id"))
    # Non-song pages (intros, dividers, indexes) are ordinary pages that carry no
    # song: they are hidden from the table of contents and from global search.
    is_non_song = db.Column(db.Integer, default=0, nullable=False, server_default="0")
    author = db.relationship("Author", backref="songs")
    images = db.relationship("SongImage", backref="song", cascade="all, delete-orphan")

class SongImage(db.Model):
    """Která strana patří které písni. Je to vazební tabulka, ne vlastnictví.

    Obojí je potřeba umět zároveň: píseň se může táhnout přes víc stran (dnes 32 písní)
    a na jedné straně můžou být dvě písně (dnes 18 stran). Řádek tedy neříká "tohle je
    obrázek té písně", ale "tahle strana nese tuhle píseň, a je to její N-tá strana".

    `poradi` je pořadí strany **v rámci té písně**, ne ve zpěvníku. Číslo strany ve
    zpěvníku drží `songbook_pages.page_number` a mění se s každým přidáním strany, aniž
    by se sahalo na obrázky. U sdílené strany má každá z písní své vlastní `poradi`.
    """

    __tablename__ = "song_images"

    id = db.Column(db.Integer, primary_key=True)
    song_id = db.Column(db.String, db.ForeignKey("songs.id"), nullable=False)
    image_id = db.Column(db.Integer, db.ForeignKey("images.id"), nullable=False, index=True)
    poradi = db.Column(db.Integer, nullable=False, default=1)

    image = db.relationship("Image")
    # Kód dál čte i zapisuje `image_path` jako text, jen pod tím leží řádek v `images`.
    image_path = association_proxy("image", "cesta", creator=_cesta_obrazku)

class Songbook(db.Model):
    __tablename__ = "songbooks"

    id = db.Column(db.String, primary_key=True)
    title = db.Column(db.String, nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    first_page_side = db.Column(db.String, default="right")
    color = db.Column(db.String, default="#FFFFFF")
    # Čtyři obálky plus `preview`, což není pátý obrázek, ale ukazatel na tu z nich,
    # která se zobrazuje v přehledech.
    cover_preview_id = db.Column(db.Integer, db.ForeignKey("images.id"), nullable=True)
    cover_front_outer_id = db.Column(db.Integer, db.ForeignKey("images.id"), nullable=True)
    cover_front_inner_id = db.Column(db.Integer, db.ForeignKey("images.id"), nullable=True)
    cover_back_inner_id = db.Column(db.Integer, db.ForeignKey("images.id"), nullable=True)
    cover_back_outer_id = db.Column(db.Integer, db.ForeignKey("images.id"), nullable=True)

    cover_preview = db.relationship("Image", foreign_keys=[cover_preview_id])
    cover_front_outer = db.relationship("Image", foreign_keys=[cover_front_outer_id])
    cover_front_inner = db.relationship("Image", foreign_keys=[cover_front_inner_id])
    cover_back_inner = db.relationship("Image", foreign_keys=[cover_back_inner_id])
    cover_back_outer = db.relationship("Image", foreign_keys=[cover_back_outer_id])

    # Jména `img_path_cover_*` zůstávají, aby se kvůli téhle změně nepřepisovaly šablony
    # a sto dalších míst; pod nimi je teď řádek v `images` místo textu.
    img_path_cover_preview = association_proxy("cover_preview", "cesta", creator=_cesta_obrazku)
    img_path_cover_front_outer = association_proxy("cover_front_outer", "cesta", creator=_cesta_obrazku)
    img_path_cover_front_inner = association_proxy("cover_front_inner", "cesta", creator=_cesta_obrazku)
    img_path_cover_back_inner = association_proxy("cover_back_inner", "cesta", creator=_cesta_obrazku)
    img_path_cover_back_outer = association_proxy("cover_back_outer", "cesta", creator=_cesta_obrazku)
    is_public = db.Column(db.Integer, default=0)
    pages = db.relationship("SongbookPage", backref="songbook", cascade="all, delete-orphan")
    intros_outros = db.relationship("SongbookIntroOutroImage", backref="songbook", cascade="all, delete-orphan")

class SongbookIntroOutroImage(db.Model):
    __tablename__ = "songbook_intro_outro_images"

    id = db.Column(db.Integer, primary_key=True)
    songbook_id = db.Column(db.String, db.ForeignKey("songbooks.id"), nullable=False)
    type = db.Column(db.String, nullable=False)  # 'intro' nebo 'outro'
    image_id = db.Column(db.Integer, db.ForeignKey("images.id"), nullable=False, index=True)
    sort_order = db.Column(db.Integer, default=0)

    image = db.relationship("Image")
    image_path = association_proxy("image", "cesta", creator=_cesta_obrazku)

class UserSongbookAccess(db.Model):
    __tablename__ = "user_songbook_access"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    songbook_id = db.Column(db.String, db.ForeignKey("songbooks.id"), primary_key=True)
    permission = db.Column(db.String, default='view')

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
