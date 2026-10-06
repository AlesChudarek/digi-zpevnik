from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()


class Obrazek(db.Model):
    """Jeden obrázkový soubor. Identitou je řádek, ne řetězec s cestou.

    Jmenuje se česky schválně. Jako `Image` přebíral jméno PIL `Image`, protože se
    v `app.py` importuje až po něm - a tiše tím rozbil všechno, co kreslí: export do PDF
    i zmenšování nahraných obrázků nad 2 MB. Tabulka se dál jmenuje `images`.

    Dřív byla identitou obrázku jeho cesta, uložená jako text na šesti různých místech
    (`song_images` a pět sloupců obálek v `songbooks`). Otázka „ukazuje na tenhle soubor
    ještě někdo?" se pak musela ptát šestkrát a porovnávat řetězce, nic nehlídalo, že
    cesta vůbec existuje, a přesun souboru znamenal přepsat šest sloupců.

    Sdílení vychází samo: táž strana ve dvou zpěvnících je dva řádky `strany`, ale
    pořád jeden řádek `images`.

    Kód dál pracuje s `image_path` a `img_path_cover_*` jako s textem - drží to
    vlastnost `cesta_obrazku` níž, takže se kvůli téhle změně nemuselo přepsat 227 míst.
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


def cesta_obrazku(vztah):
    """Vlastnost, která se tváří jako text s cestou, ale sahá na vazbu do `images`.

    Dřív to byl `association_proxy`. Ten ale při přiřazení `None` nezrušil vazbu, nýbrž
    přepsal `cesta` na NULL v samotném řádku `images` - tedy i pro všechny ostatní, kdo
    na tentýž obrázek ukazují. Odebrání jedné obálky tak skončilo na NOT NULL a při
    troše smůly by poškodilo cizí data. Proto vlastní vlastnost: prázdná hodnota ruší
    vazbu, řádku `images` se nedotkne.
    """

    def cti(self):
        obraz = getattr(self, vztah)
        return obraz.cesta if obraz is not None else None

    def zapis(self, cesta):
        setattr(self, vztah, Obrazek.ziskej(cesta) if cesta else None)

    return property(cti, zapis)

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
    """Píseň jako taková: název a autor. Kde ve zpěvníku leží, říká `PisenNaStrane`.

    Jedna píseň může být ve víc zpěvnících (veřejnou si lidé přidávají do svých), proto
    nenese ani obrázky, ani čísla stran. Píseň, která není v žádném zpěvníku, se maže.
    """

    __tablename__ = "songs"

    id = db.Column(db.String, primary_key=True)
    title = db.Column(db.String, nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey("authors.id"))
    author = db.relationship("Author", backref="songs")


class Strana(db.Model):
    """Jedna fyzická strana obsahu zpěvníku (obálky jsou zvlášť, ve sloupcích `Songbook`).

    Tohle je to, co uživatel vidí a s čím pracuje: zpěvník je řada stran a na každé
    něco je. Dřív strana jako věc v databázi neexistovala - dopočítávala se z písní, a
    všechno, co písní nebylo, se za píseň muselo přestrojit: prázdná strana byla
    „píseň“ bez obrázku, osmisměrka „píseň“ s příznakem `is_non_song`.

    - `poradi` je pozice ve zpěvníku od nuly. Číslo, které je vytištěné na skenu, je
      `songbook.prvni_cislo_strany + poradi`.
    - `image_id` NULL = prázdná strana.
    - Strana bez písní je nepísňová (úvod, osmisměrka, předěl). `popisek` jí může dát
      jméno, které se ukáže v editoru; do obsahu ani hledání nepatří.
    """

    __tablename__ = "strany"
    __table_args__ = (db.UniqueConstraint("songbook_id", "poradi", name="uq_strana_poradi"),)

    id = db.Column(db.Integer, primary_key=True)
    songbook_id = db.Column(db.String, db.ForeignKey("songbooks.id"), nullable=False, index=True)
    poradi = db.Column(db.Integer, nullable=False)
    image_id = db.Column(db.Integer, db.ForeignKey("images.id"), nullable=True, index=True)
    popisek = db.Column(db.String, nullable=True)

    image = db.relationship("Obrazek")
    image_path = cesta_obrazku("image")
    # Pořadí písní na straně je pořadí, v jakém na ni přibyly - tak to bylo i dřív
    # (podle id řádku) a tak to ukazuje obsah.
    pisne = db.relationship("PisenNaStrane", backref="strana", cascade="all, delete-orphan",
                            order_by="PisenNaStrane.id")


class PisenNaStrane(db.Model):
    """Tahle strana nese tuhle píseň a je to její N-tá strana.

    Unese obojí, co zpěvníky opravdu mají: píseň přes víc stran (víc řádků jedné písně
    s `poradi_v_pisni` 1, 2, 3…) i víc písní na jedné straně (víc řádků jedné strany).
    Obojí naráz taky: strana, na které jedna píseň končí a další začíná.
    """

    __tablename__ = "pisne_na_strane"
    __table_args__ = (db.UniqueConstraint("strana_id", "song_id", name="uq_pisen_na_strane"),)

    id = db.Column(db.Integer, primary_key=True)
    strana_id = db.Column(db.Integer, db.ForeignKey("strany.id"), nullable=False, index=True)
    song_id = db.Column(db.String, db.ForeignKey("songs.id"), nullable=False, index=True)
    poradi_v_pisni = db.Column(db.Integer, nullable=False, default=1)

    song = db.relationship("Song")


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

    cover_preview = db.relationship("Obrazek", foreign_keys=[cover_preview_id])
    cover_front_outer = db.relationship("Obrazek", foreign_keys=[cover_front_outer_id])
    cover_front_inner = db.relationship("Obrazek", foreign_keys=[cover_front_inner_id])
    cover_back_inner = db.relationship("Obrazek", foreign_keys=[cover_back_inner_id])
    cover_back_outer = db.relationship("Obrazek", foreign_keys=[cover_back_outer_id])

    # Jména `img_path_cover_*` zůstávají, aby se kvůli téhle změně nepřepisovaly šablony
    # a sto dalších míst; pod nimi je teď řádek v `images` místo textu.
    img_path_cover_preview = cesta_obrazku("cover_preview")
    img_path_cover_front_outer = cesta_obrazku("cover_front_outer")
    img_path_cover_front_inner = cesta_obrazku("cover_front_inner")
    img_path_cover_back_inner = cesta_obrazku("cover_back_inner")
    img_path_cover_back_outer = cesta_obrazku("cover_back_outer")
    is_public = db.Column(db.Integer, default=0)
    # Číslo vytištěné na první straně obsahu. Většinou 1, ale některé zpěvníky číslují
    # od titulní strany, takže první píseň je na straně 3.
    prvni_cislo_strany = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    strany = db.relationship("Strana", backref="songbook", cascade="all, delete-orphan",
                             order_by="Strana.poradi")

class ExportPokus(db.Model):
    """Kolik skládání souborů spustil účet za jeden den.

    Jeden řádek na účet a den, ne řádek na pokus: tabulka se tím nerozroste a stejně
    z ní nic jiného než to číslo nepotřebujeme. Den je text „RRRR-MM-DD“, aby šlo
    počítat bez převodů časových pásem.
    """

    __tablename__ = "export_pokusy"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    den = db.Column(db.String, nullable=False)
    pocet = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (db.UniqueConstraint("user_id", "den", name="uq_export_pokus_den"),)


class UserSongbookAccess(db.Model):
    __tablename__ = "user_songbook_access"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    songbook_id = db.Column(db.String, db.ForeignKey("songbooks.id"), primary_key=True)
    permission = db.Column(db.String, default='view')

# Funkce pro propojení db s Flask aplikací
def init_app(app):
    db.init_app(app)
