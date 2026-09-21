import os
import mimetypes
import sys
import json
import click
from flask import Flask, render_template, redirect, url_for, request, flash, session, send_from_directory, jsonify, send_file, g
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, UserMixin, current_user
from flask.cli import with_appcontext
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
import re
import unicodedata
from datetime import datetime, timedelta
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
# Jediný kořen obrázků, viz docs/ukladani-obrazku.md. Starý strom
# (data/public/images/songbooks, data/private/users) byl smazán 15. 9. 2026 poté, co
# se ověřilo, že má v novém stromu přesnou kopii - proto se už nikde nečte.
IMAGES_DIR = Path(__file__).parent.parent / 'data' / 'images'

# Ubuntu 22.04 nemá .webp ve své tabulce typů, takže Flask posílal náhledy jako
# application/octet-stream. Prohlížeč si obsah domyslí, ale proxy ani cache nemusí.
mimetypes.add_type('image/webp', '.webp')

try:
    MAX_IMAGE_UPLOAD_MB = max(0.5, float(os.getenv("MAX_IMAGE_UPLOAD_MB", "2.0")))
except Exception:
    MAX_IMAGE_UPLOAD_MB = 2.0
MAX_IMAGE_UPLOAD_BYTES = int(MAX_IMAGE_UPLOAD_MB * 1024 * 1024)
# Strop na to, kolik smí jeden účet nahrát. Jeden obrázek smí 2 MB, ale počet nikdo
# nehlídal, takže se aplikace dala použít jako cloud na fotky - a při 38 GB volných by
# ji zaplnilo asi 19 000 obrázků. Pro orientaci naměřeno: nejobsáhlejší dnešní uživatel
# má 50 MB (123stránkový zpěvník), běžný zpěvník váží kolem 12 MB.
try:
    MAX_USER_STORAGE_MB = max(10.0, float(os.getenv("MAX_USER_STORAGE_MB", "300")))
except Exception:
    MAX_USER_STORAGE_MB = 300.0
MAX_USER_STORAGE_BYTES = int(MAX_USER_STORAGE_MB * 1024 * 1024)

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
# v2: strany se na A4 doplňují, místo aby se na ni roztahovaly, a klíč cache se počítá
# z celých sekund. Obojí mění výsledek, takže starší buildy musí přestat platit.
EXPORT_GENERATOR_VERSION = b'v2'


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


class KvotaPrekrocena(Exception):
    """Účet by uploadem překročil svůj strop. Nese čísla, ať se dá říct o kolik."""

    def __init__(self, zabrano: int, pridavek: int):
        self.zabrano, self.pridavek = zabrano, pridavek
        super().__init__("překročena kvóta")


def _uzivatel_z_cilove_cesty(dest_path: Path):
    """Komu se soubor započítá. Veřejné zpěvníky nikomu - ty zakládá jen admin.

    Bere se z cesty, ne z přihlášeného uživatele: cesta je to jediné, co rozhoduje,
    kam bajty opravdu padnou, takže se kontrola nedá obejít jiným endpointem.
    """
    try:
        rel = dest_path.resolve().relative_to(IMAGES_DIR.resolve())
    except (ValueError, OSError):
        return None
    casti = rel.parts
    if len(casti) >= 2 and casti[0] == 'uzivatele' and casti[1].isdigit():
        return int(casti[1])
    return None


def zabrane_misto(user_id: int) -> int:
    """Kolik bajtů má účet nahraných. Pravdou je disk, ne databáze - sirotek, na který
    už neukazuje žádný řádek, místo pořád zabírá."""
    koren = IMAGES_DIR / 'uzivatele' / str(user_id)
    try:
        return sum(p.stat().st_size for p in koren.rglob('*') if p.is_file())
    except OSError:
        return 0


def _zbyva_uzivateli(user_id: int) -> int:
    """Zbývající místo. V rámci jednoho požadavku se sečte jednou a pak se jen odečítá,
    ať nahrání deseti stran neprochází složku desetkrát."""
    try:
        cache = g._kvota_cache
    except (AttributeError, RuntimeError):
        cache = {}
        try:
            g._kvota_cache = cache
        except RuntimeError:
            pass  # mimo požadavek (CLI, skripty) - jen se necachuje
    if user_id not in cache:
        cache[user_id] = MAX_USER_STORAGE_BYTES - zabrane_misto(user_id)
    return cache[user_id]


def _zapocti(user_id: int, bajtu: int, cesta: Path) -> None:
    try:
        g._kvota_cache[user_id] -= bajtu
    except (AttributeError, KeyError, RuntimeError):
        pass
    try:
        g._zapsane_soubory.append(cesta)
    except (AttributeError, RuntimeError):
        try:
            g._zapsane_soubory = [cesta]
        except RuntimeError:
            pass


def _save_image_with_limit(file_storage, dest_path: Path, ext_hint=None):
    data = _prepare_image_bytes(file_storage, ext_hint=ext_hint)
    # Jediné místo, kudy obrázek na disk teče, takže i jediné, kde se kvóta musí hlídat.
    uid = _uzivatel_z_cilove_cesty(dest_path)
    if uid is not None:
        zbyva = _zbyva_uzivateli(uid)
        if len(data) > zbyva:
            raise KvotaPrekrocena(MAX_USER_STORAGE_BYTES - zbyva, len(data))
    with open(dest_path, 'wb') as fh:
        fh.write(data)
    if uid is not None:
        _zapocti(uid, len(data), dest_path)

try:
    # Prefer balíčkové importy pro nasazení (backend.app jako modul)
    from .models import (
        Song,
        Obrazek,
        SongImage,
        SongbookPage,
        SongbookIntroOutroImage,
        Songbook,
        Author,
        User,
        UserSongbookAccess,
        LoginAttempt,
        db,
        init_app,
    )
except ImportError:  # fallback pro přímé spuštění skriptu
    from models import Song, Obrazek, SongImage, SongbookPage, SongbookIntroOutroImage, Songbook, Author, User, UserSongbookAccess, LoginAttempt, db, init_app

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

def smi_tvorit(user):
    """Smí uživatel zakládat obsah a stahovat zpěvníky?

    Dvě podmínky, každá z jiného důvodu: host je sdílený účet, takže mu nic vlastního
    nepatří, a neověřená adresa znamená, že o uživateli nevíme, jak ho zastihnout. Obojí
    jsou zároveň ty operace, které stojí server nejvíc výkonu.
    """
    if not user.is_authenticated or is_guest(user):
        return False
    return bool(getattr(user, 'email_verified', False))


def is_guest(user):
    """Jediné místo, kde se rozhoduje, jestli jde o hosta.

    Dřív se to zjišťovalo třemi způsoby zároveň - podle role, podle e-mailu
    guest@guest.com a podle příznaku v session. Tři odpovědi na jednu otázku se dřív nebo
    později rozejdou, takže platí jen role.
    """
    return user.is_authenticated and getattr(user, 'role', None) == 'guest'


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

# Za reverzní proxy (Caddy) dorazí požadavek na gunicorn jako obyčejné HTTP, takže by si
# Flask myslel, že běží nešifrovaně. ProxyFix mu dá přečíst hlavičky X-Forwarded-*, které
# Caddy posílá. Bez toho by odkazy generované přes url_for(_external=True) vycházely jako
# http:// - to zatím nikde nepoužíváme, ale ověřovací odkaz v registračním e-mailu ano bude.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Cookie s přihlášením se nikdy nesmí poslat nešifrovaně a nemá co dělat v JavaScriptu.
# Zapíná se podle prostředí: na produkci ano, ve vývoji na http://localhost by Secure
# znamenalo, že se cookie neuloží vůbec a nešlo by se přihlásit.
app.config['SESSION_COOKIE_SECURE'] = _str_to_bool(os.getenv("HTTPS_ONLY"), False)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['REMEMBER_COOKIE_SECURE'] = app.config['SESSION_COOKIE_SECURE']
app.config['REMEMBER_COOKIE_HTTPONLY'] = True
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


def _dopln_chybejici_sloupce():
    """Doplní sloupce, které přibyly v modelech, ať nezáleží na pořadí nasazení.

    Projekt nemá migrační nástroj a `create_all` umí jen chybějící tabulky, ne sloupce.
    Kdyby se nasadil kód dřív než migrační skript, každý dotaz na `song_images` by spadl.
    Tohle je ta pojistka; je to jedno PRAGMA při startu a po doplnění už nic nedělá.

    Hodnoty se tady nedopočítávají, jen se sloupec doplní s výchozí hodnotou.
    """
    from sqlalchemy import text
    with app.app_context():
        try:
            sloupce = {r[1] for r in db.session.execute(text("PRAGMA table_info(song_images)"))}
            if sloupce and 'poradi' not in sloupce:
                db.session.execute(text(
                    "ALTER TABLE song_images ADD COLUMN poradi INTEGER NOT NULL DEFAULT 1"))
                db.session.commit()
                app.logger.warning("song_images.poradi doplněn s výchozí hodnotou")
        except Exception as chyba:  # noqa: BLE001 - chybějící DB při startu není důvod spadnout
            # Gunicorn startuje víc workerů naráz, takže se o sloupec pokusí každý z nich
            # a všichni kromě prvního dostanou "duplicate column". Výsledek je správný,
            # není důvod to hlásit jako problém.
            if 'duplicate column' not in str(chyba).lower():
                app.logger.warning("kontrola sloupců neproběhla: %s", chyba)


_dopln_chybejici_sloupce()


@app.errorhandler(KvotaPrekrocena)
def _kvota_prekrocena(chyba):
    """Upload přes strop účtu. Vrátí se to, co se stihlo zapsat, aby po nepovedeném
    nahrání nezůstaly na disku kusy, které by se do kvóty počítaly."""
    db.session.rollback()
    for cesta in getattr(g, '_zapsane_soubory', []):
        try:
            Path(cesta).unlink(missing_ok=True)
        except OSError:
            pass

    zbyva = max(0, MAX_USER_STORAGE_BYTES - chyba.zabrano)
    return jsonify({
        'ok': False,
        'error': (f"Nahrávání by překročilo váš limit {mb(MAX_USER_STORAGE_BYTES)}. "
                  f"Zbývá vám {mb(zbyva)} a tenhle soubor má {mb(chyba.pridavek)}. "
                  f"Uvolněte místo smazáním zpěvníku, který už nepotřebujete."),
        'kvota_bytes': MAX_USER_STORAGE_BYTES,
        'zabrano_bytes': chyba.zabrano,
    }), 413

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

def zpevniky_s_obrazkem(cesta: str):
    """Zpěvníky, ve kterých ten obrázek je - jako strana, obálka nebo intro.

    O právech nerozhoduje, kde soubor leží, ale komu patří zpěvník, který ho ukazuje.
    Umístění to říct nemůže: strana veřejného zpěvníku běžně visí i v něčím soukromém
    (dnes 70 písniček) a naopak. Od zavedení tabulky `images` je to pár dotazů na
    `image_id` místo porovnávání řetězců v šesti sloupcích.
    """
    obraz = Obrazek.query.filter_by(cesta=cesta).first()
    if obraz is None:
        return []
    ids = {r[0] for r in db.session.query(SongbookPage.songbook_id)
           .join(SongImage, SongImage.song_id == SongbookPage.song_id)
           .filter(SongImage.image_id == obraz.id).distinct()}
    ids |= {r[0] for r in db.session.query(SongbookIntroOutroImage.songbook_id)
            .filter_by(image_id=obraz.id).distinct()}
    ids |= {r[0] for r in db.session.query(Songbook.id).filter(db.or_(
        Songbook.cover_preview_id == obraz.id,
        Songbook.cover_front_outer_id == obraz.id,
        Songbook.cover_front_inner_id == obraz.id,
        Songbook.cover_back_inner_id == obraz.id,
        Songbook.cover_back_outer_id == obraz.id))}
    return Songbook.query.filter(Songbook.id.in_(ids)).all() if ids else []


def smi_videt_obrazek(user, cesta: str) -> bool:
    """Vidí uživatel aspoň jeden zpěvník, ve kterém ten obrázek je?

    Obrázek, na který neukazuje žádný zpěvník, nevidí nikdo. Je to buď sirotek před
    úklidem, nebo uhodnutá cesta.
    """
    return any(can_view_songbook(user, sb) for sb in zpevniky_s_obrazkem(cesta))


# Originální obrázky zpěvníků. Zmenšené varianty servírují /nahled/ a /strana/.
@app.route('/songbooks/<path:filename>')
@login_required
def serve_songbook_image(filename):
    try:
        if not _je_nova_cesta(filename):
            return ("Not Found", 404)
        if not smi_videt_obrazek(current_user, filename):
            # Schválně 404, ne 403: 403 by potvrdilo, že takový soubor existuje, a cesty
            # jsou dnes očíslované od jedničky, takže by se daly procházet.
            return ("Not Found", 404)
        return send_from_directory(str(IMAGES_DIR), filename)
    except Exception:
        # Fallback 404-like behavior without exposing internals
        return ("Not Found", 404)


# ---------- Storage layout helpers (public vs private songbooks) ----------
def _book_storage_base(book: Songbook):
    """Složka zpěvníku v novém stromu: (absolutní cesta, cesta uložená v DB).

    Dřív se skládala ze slugu e-mailu a názvu zpěvníku, takže se po přejmenování rozešla
    se skutečností - a musel to zachraňovat fallback, který cestu zpětně odhadoval
    z uloženého sloupce obálky. Teď v ní nic měnitelného není.

    Strany sem nepatří, ty leží ploché v `pages/` - viz `_nova_cesta_strany`.
    """
    koren = _koren_pro_zpevnik(book)
    rel = f"{koren}/songbooks/{book.id}"
    return IMAGES_DIR / rel, rel


def _rel_for_stored_file(abs_path: Path, book: Songbook) -> str:
    """DB path for a file already written somewhere under the book's base dir."""
    base_abs, rel_prefix = _book_storage_base(book)
    return str(Path(rel_prefix) / abs_path.relative_to(base_abs))


def _je_nova_cesta(rel_path) -> bool:
    return isinstance(rel_path, str) and rel_path.startswith(('verejne/', 'uzivatele/'))


def _abs_image_path(rel_path: str):
    """Z uložené cesty udělá soubor na disku.

    Cesta musí začínat `verejne/` nebo `uzivatele/<uid>/`. Cokoliv jiného je pozůstatek
    starého tvaru nebo překlep a vrací se None - dřív se to potichu poskládalo do cesty,
    která nikam nevedla.
    """
    if not _je_nova_cesta(rel_path):
        return None
    return IMAGES_DIR / rel_path


ROLE_OBALEK = {'coverfrontout': 'front-out', 'coverfrontin': 'front-in',
               'coverbackin': 'back-in', 'coverbackout': 'back-out'}


def _koren_pro_zpevnik(book) -> str:
    """`verejne`, nebo `uzivatele/<user_id>`.

    Dělí se podle toho, kdo obrázek nahrál - ne podle názvu zpěvníku a e-mailu jako dřív.
    `user_id` je celé číslo, které se nikdy nemění, takže se cesta nemá jak rozejít
    se skutečností.
    """
    if getattr(book, 'is_public', 0):
        return 'verejne'
    return f"uzivatele/{book.owner_id}" if getattr(book, 'owner_id', None) else 'verejne'


def _cesta_obalky(book, role: str, pripona: str) -> str:
    """Obálky mají pevná jména, protože jsou právě čtyři a každá má jinou roli."""
    return f"{_koren_pro_zpevnik(book)}/songbooks/{book.id}/covers/{ROLE_OBALEK.get(role, role)}{pripona}"


def _nova_cesta_strany(book, pripona: str) -> str:
    """Další volné číslo v `pages/` daného kořene.

    Strana nepatří zpěvníku ani písni - viz docs/ukladani-obrazku.md - takže leží plochá
    a dostane jen pořadové číslo. Pravdou je disk, ne databáze: kdyby dva uploady dorazily
    zároveň, volající soubor stejně zakládá výhradním zápisem a při kolizi si řekne o další.
    """
    koren = _koren_pro_zpevnik(book)
    adresar = IMAGES_DIR / koren / 'pages'
    adresar.mkdir(parents=True, exist_ok=True)
    nejvyssi = 0
    for p in adresar.iterdir():
        try:
            nejvyssi = max(nejvyssi, int(p.stem))
        except (ValueError, OSError):
            continue
    return f"{koren}/pages/{nejvyssi + 1:06d}{pripona}"


def _next_public_songbook_id() -> str:
    """Lowest free 5-digit id, so admin-created books keep the 00001.. numbering."""
    taken = {sid for (sid,) in db.session.query(Songbook.id).all()}
    n = 1
    while f"{n:05d}" in taken:
        n += 1
    return f"{n:05d}"


# ---------- Helpers for song file ownership/migration ----------
def _handle_song_delete_for_book(sb: Songbook, song: Song):
    """Odebere píseň z jednoho zpěvníku a uklidí, co po ní zbylo.

    Dřív to muselo řešit, který zpěvník je "původní", protože pod ním ležely soubory.
    Ten pojem zanikl: strana leží v `pages/` a nepatří žádnému zpěvníku, takže stačí
    odpojit vazbu a podívat se, jestli píseň ještě někde je.

    Necommituje. Vrací i `kandidati` - cesty, které se po commitu nabídnou
    `smaz_osirele_obrazky`, protože tutéž stranu může nést ještě jiná píseň.
    """
    imgs = SongImage.query.filter_by(song_id=song.id).all()
    kandidati = [img.image_path for img in imgs]
    db.session.query(SongbookPage).filter_by(songbook_id=sb.id, song_id=song.id).delete()
    db.session.flush()

    if db.session.query(SongbookPage.id).filter_by(song_id=song.id).first():
        # Píseň zůstává v jiném zpěvníku. Dřív se tu soubory stěhovaly, protože cesta
        # nesla "původní" zpěvník; dnes nenese nic měnitelného a stěhovat není co.
        return {'detached_only': True, 'kandidati': kandidati}

    db.session.query(SongImage).filter_by(song_id=song.id).delete()
    db.session.delete(song)
    # Soubory maže volající po commitu, protože tutéž stranu může nést jiná píseň.
    return {'deleted_song': True, 'kandidati': kandidati}

# Sloupce s obálkami. Na jednom místě, ať se seznam nemusí opisovat v každé funkci,
# která obálky prochází.
SLOTY_OBALEK = [
    ('img_path_cover_preview', 'náhled'),
    ('img_path_cover_front_outer', 'přední ven'),
    ('img_path_cover_front_inner', 'přední dovnitř'),
    ('img_path_cover_back_inner', 'zadní dovnitř'),
    ('img_path_cover_back_outer', 'zadní ven'),
]


def smaz_osirele_obrazky(kandidati):
    """Smaže obrázky, na které už z databáze nikdo neukazuje.

    Odebrání strany ze zpěvníku dosud mazalo soubor jen u soukromých zpěvníků. U veřejných
    se jen zrušil odkaz a soubor zůstal ležet - tak vznikly ty, které se musely uklízet
    ručně. Tohle to dodělává pro obě cesty.

    Smazat se smí teprve tehdy, když na řádek v `images` neukazuje žádná píseň, obálka ani
    intro. Tutéž stranu totiž může nést druhá píseň a týž zpěvník může být ve dvou
    zpěvnících - sedmdesát písniček dnes je.

    Volat až po commitu, jinak dotazy uvidí ještě neodstraněné řádky.
    """
    kandidati = {c for c in kandidati if c}
    if not kandidati:
        return 0

    smazano = 0
    osirele = []
    for obraz in Obrazek.query.filter(Obrazek.cesta.in_(kandidati)).all():
        pouzity = (
            db.session.query(SongImage.id).filter_by(image_id=obraz.id).first()
            or db.session.query(SongbookIntroOutroImage.id).filter_by(image_id=obraz.id).first()
            or db.session.query(Songbook.id).filter(db.or_(
                Songbook.cover_preview_id == obraz.id,
                Songbook.cover_front_outer_id == obraz.id,
                Songbook.cover_front_inner_id == obraz.id,
                Songbook.cover_back_inner_id == obraz.id,
                Songbook.cover_back_outer_id == obraz.id)).first()
        )
        if pouzity:
            continue
        cesta = _abs_image_path(obraz.cesta)
        try:
            if cesta and cesta.is_file():
                cesta.unlink()
                smazano += 1
        except OSError as chyba:
            # Nepodařené smazání nesmí shodit požadavek, který uživatel poslal. Nejhorší
            # následek je soubor navíc na disku, což kontrola_zpevniku.py stejně najde.
            app.logger.warning("obrázek %s se nepodařilo smazat: %s", obraz.cesta, chyba)
        # Řádek jde pryč i tehdy, když soubor na disku nebyl - jinak by v images
        # zůstával odkaz na nic.
        osirele.append(obraz)

    if osirele:
        for obraz in osirele:
            db.session.delete(obraz)
        db.session.commit()
    return smazano


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


VELKA_PISMENA = 'ABCDEFGHIJKLMNOPQRSTUVWXYZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ'
PISMENA_A_CISLICE = VELKA_PISMENA + VELKA_PISMENA.lower() + '0123456789'


def chyba_hesla(heslo: str):
    """Vrací popis první nesplněné podmínky, nebo None když je heslo v pořádku.

    Stejná pravidla hlídá i formulář v prohlížeči, ale ten se dá obejít vypnutým
    JavaScriptem, takže poslední slovo má server.
    """
    if len(heslo) < 8:
        return 'Heslo musí mít aspoň 8 znaků.'
    if not any(z in VELKA_PISMENA for z in heslo):
        return 'Heslo musí obsahovat velké písmeno.'
    if all(z in PISMENA_A_CISLICE for z in heslo):
        return 'Heslo musí obsahovat speciální znak, například . , ! ? - _ # @'
    return None


# ---------- OMEZENÍ POKUSŮ O PŘIHLÁŠENÍ ----------
# Bez tohohle jde hesla zkoušet donekonečna. Počítá se ve dvou rovinách zvlášť: na e-mail,
# aby nešlo louskat konkrétní účet, a na IP adresu, aby nešlo zkoušet jedno heslo proti
# mnoha účtům.
OKNO_POKUSU = timedelta(minutes=15)
MAX_POKUSU_EMAIL = 5
MAX_POKUSU_IP = 20
DRZET_POKUSY = timedelta(hours=24)


def _ip_klienta():
    # ProxyFix už hlavičku X-Forwarded-For vyhodnotil, takže remote_addr je skutečný klient.
    return request.remote_addr or 'neznama'


def prihlaseni_zablokovano(email):
    """Vyčerpal někdo počet pokusů? Vrací zbývající minuty, nebo None když je čisto."""
    od = datetime.utcnow() - OKNO_POKUSU
    for sloupec, hodnota, limit in ((LoginAttempt.email, (email or '').lower(), MAX_POKUSU_EMAIL),
                                    (LoginAttempt.ip, _ip_klienta(), MAX_POKUSU_IP)):
        pokusy = (LoginAttempt.query
                  .filter(sloupec == hodnota, LoginAttempt.cas >= od)
                  .order_by(LoginAttempt.cas.asc()).all())
        if len(pokusy) >= limit:
            # Odblokuje se, až nejstarší pokus vypadne z okna.
            zbyva = (pokusy[0].cas + OKNO_POKUSU) - datetime.utcnow()
            return max(1, int(zbyva.total_seconds() // 60) + 1)
    return None


def zaznamenej_neuspesny_pokus(email):
    db.session.add(LoginAttempt(email=(email or '').lower(), ip=_ip_klienta(),
                                cas=datetime.utcnow()))
    # Úklid starých záznamů při zápisu, ať tabulka neroste donekonečna.
    LoginAttempt.query.filter(LoginAttempt.cas < datetime.utcnow() - DRZET_POKUSY).delete()
    db.session.commit()


def zapomen_pokusy(email):
    LoginAttempt.query.filter(
        (LoginAttempt.email == (email or '').lower()) | (LoginAttempt.ip == _ip_klienta())
    ).delete(synchronize_session=False)
    db.session.commit()

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

        zbyva = prihlaseni_zablokovano(email)
        if zbyva is not None:
            # Heslo se schválně ani neověřuje - jinak by šlo měřením času zjistit,
            # jestli bylo správné.
            flash(f'Příliš mnoho pokusů o přihlášení. Zkuste to znovu za {zbyva} min.', 'error')
            return render_template('auth.html')

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            zapomen_pokusy(email)
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            zaznamenej_neuspesny_pokus(email)
            # Stejná hláška pro neexistující účet i špatné heslo, ať se nedá zjistit,
            # které e-maily jsou zaregistrované.
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

        chyba = chyba_hesla(password)
        if chyba:
            flash(chyba, 'error')
            return redirect(url_for('register'))
        if password != (request.form.get('password2') or password):
            flash('Hesla se neshodují.', 'error')
            return redirect(url_for('register'))

        if User.query.filter_by(email=email).first():
            flash('Účet už existuje', 'error')
            return redirect(url_for('register'))
        else:
            hashed_password = generate_password_hash(password, method='pbkdf2:sha256', salt_length=16)
            new_user = User(email=email, password=hashed_password, role='user',
                            email_verified=False)
            db.session.add(new_user)
            db.session.commit()
            # Nepouštíme ho pryč a nenutíme ho nejdřív ověřit. Kdyby si adresu překlepl nebo
            # zpráva spadla do spamu, zůstal by s mrtvým účtem a nemá se jak dostat dovnitř.
            # Dovnitř tedy může, jen s omezením a s pruhem nahoře.
            login_user(new_user)
            if posli_overovaci_email(new_user):
                flash('Registrace proběhla. Poslali jsme vám ověřovací e-mail.', 'success')
            else:
                flash('Registrace proběhla, ale ověřovací e-mail se nepodařilo odeslat. '
                      'Zkuste ho poslat znovu později.', 'error')
            return redirect(url_for('dashboard'))

    return render_template('auth.html')

@app.route('/logout', methods=['POST'])
def logout():
    logout_user()
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
    return redirect(url_for('dashboard'))

def posli_overovaci_email(user) -> bool:
    """Odešle ověřovací odkaz. Vrací, jestli se to povedlo - volající o tom uživatele zpraví."""
    try:
        from .mail import posli_email
        from .tokeny import token_overeni
    except ImportError:
        from mail import posli_email
        from tokeny import token_overeni
    odkaz = url_for('overit_email', token=token_overeni(user), _external=True)
    try:
        posli_email(
            user.email,
            "Potvrzení e-mailové adresy - Digi zpěvník",
            "Dobrý den,\n\n"
            "někdo si na webu Digi zpěvník zaregistroval účet s touto e-mailovou adresou. "
            "Pokud jste to byli vy, potvrďte prosím adresu otevřením následující stránky "
            "a stisknutím tlačítka:\n\n"
            f"{odkaz}\n\n"
            "Odkaz platí 24 hodin. Samotné otevření stránky nic nepotvrzuje, potvrzení "
            "provede až stisknutí tlačítka.\n\n"
            "Pokud jste se neregistrovali, nemusíte dělat nic. Bez potvrzení se s adresou "
            "nedá pracovat a účet zůstane omezený.\n\n"
            "https://digizpevnik.cz\n\n"
            "Tato zpráva byla vygenerována automaticky, neodpovídejte na ni.\n",
            "<p>Dobrý den,</p>"
            "<p>někdo si na webu <strong>Digi zpěvník</strong> zaregistroval účet s touto "
            "e-mailovou adresou. Pokud jste to byli vy, potvrďte prosím adresu:</p>"
            f'<p><a href="{odkaz}">Potvrdit e-mailovou adresu</a></p>'
            "<p>Odkaz platí 24 hodin. Samotné otevření stránky nic nepotvrzuje, potvrzení "
            "provede až stisknutí tlačítka na ní.</p>"
            "<p>Pokud jste se neregistrovali, nemusíte dělat nic. Bez potvrzení se "
            "s adresou nedá pracovat a účet zůstane omezený.</p>"
            "<p>Tato zpráva byla vygenerována automaticky, neodpovídejte na ni.</p>",
        )
        return True
    except Exception as chyba:  # noqa: BLE001 - registrace nesmí spadnout kvůli poště
        app.logger.error("Ověřovací e-mail pro %s se neodeslal: %s", user.email, chyba)
        return False


ZNAME_MOTIVY = {'green', 'blue', 'dark', 'purple', 'amber', 'slate', 'teal',
                'emerald', 'cyan', 'indigo', 'rose', 'pink'}


NAHLEDY_DIR = Path(__file__).parent.parent / 'data' / 'nahledy'


def _nahledy():
    try:
        from . import nahledy
    except ImportError:
        import nahledy
    return nahledy


def url_nahledu(songbook):
    """Adresa zmenšeného náhledu obálky, nebo None když zpěvník obálku nemá.

    Klíč je součástí adresy schválně - po výměně obálky se adresa změní, takže se náhled
    udělá znovu sám a prohlížeč nepodstrčí starý obrázek z cache.
    """
    rel = getattr(songbook, 'img_path_cover_preview', None)
    if not rel:
        return None
    zdroj = _abs_image_path(rel)
    if not zdroj:
        return None
    n = _nahledy()
    k = n.klic(zdroj, n.OBALKA)
    if not k:
        return None
    return url_for('nahled_obalky', book_id=songbook.id, klic=k)


app.jinja_env.globals['url_nahledu'] = url_nahledu


@app.route('/nahled/<book_id>/<klic>.webp')
@login_required
def nahled_obalky(book_id, klic):
    songbook = Songbook.query.get_or_404(book_id)
    if not can_view_songbook(current_user, songbook):
        return ("Access denied", 403)

    rel = songbook.img_path_cover_preview
    zdroj = _abs_image_path(rel) if rel else None
    n = _nahledy()
    if not zdroj or n.klic(zdroj, n.OBALKA) != klic:
        # Klíč nesedí na dnešní obálku - odkaz je z dřívějška. Ať si prohlížeč vyzvedne
        # aktuální adresu, místo aby dostal cizí obrázek.
        return ("Not Found", 404)

    cil = n.soubor_nahledu(NAHLEDY_DIR, book_id, klic)
    if not cil.exists() and not n.vyrob(zdroj, cil, n.OBALKA):
        # Když se náhled nepovede, ať stránka nezůstane bez obrázku.
        return redirect(url_for('serve_songbook_image', filename=rel))
    n.uklid_starych(NAHLEDY_DIR, book_id, klic)

    odpoved = send_from_directory(str(NAHLEDY_DIR), cil.name)
    # Ta adresa už nikdy neponese jiný obsah, protože klíč je z obsahu originálu.
    odpoved.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return odpoved


def url_strany(rel):
    """Adresa zmenšené strany pro čtečku, nebo původní obrázek, když se klíč nedá spočítat.

    Vrací se vždycky něco použitelného - když se na náhled nedostane (chybějící soubor,
    nečitelný stat), čtečka dostane originál a jen se nic neušetří.
    """
    if not rel:
        return None
    zdroj = _abs_image_path(rel)
    n = _nahledy()
    k = n.klic(zdroj, n.STRANA) if zdroj else None
    if not k:
        return url_for('serve_songbook_image', filename=rel)
    # `.webp` na konci schválně: adresa nese cestu k originálu, aby se z ní dal náhled
    # vyrobit, ale obsah je WebP. Bez té přípony vypadá v inspektoru jako plné PNG
    # a není jak poznat, že zmenšování vůbec funguje.
    return url_for('nahled_strany', klic=k, filename=rel + '.webp')


app.jinja_env.globals['url_strany'] = url_strany


def misto_uzivatele(user):
    """Kolik místa účet zabral, pro zobrazení v panelu účtu.

    Vrací None u hosta a admina - admin zakládá veřejné zpěvníky, které se nikomu
    nezapočítávají, takže by mu ukazatel pořád svítil nulu a jen mátl.
    """
    if not getattr(user, 'is_authenticated', False) or getattr(user, 'role', None) in ('guest', 'admin'):
        return None
    zabrano = zabrane_misto(user.id)
    podil = zabrano / MAX_USER_STORAGE_BYTES if MAX_USER_STORAGE_BYTES else 0
    return {
        'zabrano': zabrano,
        'kvota': MAX_USER_STORAGE_BYTES,
        'procent': min(100, round(podil * 100)),
        # Dva prahy: nad polovinou stojí za to o tom vědět, nad 85 % už je potřeba
        # něco udělat, než nahrávání spadne na strop.
        'stav': 'kriticky' if podil >= 0.85 else ('varovani' if podil >= 0.5 else 'ok'),
    }


def mb(bajtu):
    """Velikost pro člověka. Desetinné místo jen pod 100 MB, ať to neskáče."""
    h = (bajtu or 0) / 1024 / 1024
    return (f"{h:.1f}".replace('.', ',') if h < 100 else f"{h:.0f}") + " MB"


app.jinja_env.globals['misto_uzivatele'] = misto_uzivatele
app.jinja_env.globals['mb'] = mb



def mapa_stran(page_files):
    """Pro každou stranu ve zpěvníku adresa jejího náhledu: {cesta: adresa}.

    Čtečka si adresu náhledu nemůže složit sama, protože v sobě nese otisk originálu.
    Počítá se tedy jednou na serveru a do stránky se pošle hotová mapa.
    """
    mapa = {}
    for pair in page_files or []:
        for page in pair or []:
            if not page:
                continue
            rel = page if isinstance(page, str) else page.get('file')
            if not rel or rel in ('none', 'blank') or rel in mapa:
                continue
            adresa = url_strany(rel)
            if adresa:
                mapa[rel] = adresa
    return mapa


app.jinja_env.globals['mapa_stran'] = mapa_stran



def _je_pod(cesta: Path, koren: Path) -> bool:
    """Leží soubor opravdu pod povoleným kořenem? Jméno přichází z adresy, takže se na
    jeho nevinnost nedá spoléhat."""
    try:
        return koren.resolve() in cesta.resolve().parents
    except OSError:
        return False


@app.route('/strana/<klic>/<path:filename>')
@login_required
def nahled_strany(klic, filename):
    """Zmenšená strana pro čtečku. Originál zůstává na /songbooks/<filename>."""
    if not filename.endswith('.webp'):
        return ("Not Found", 404)
    filename = filename[:-len('.webp')]
    zdroj = _abs_image_path(filename)
    n = _nahledy()
    if not zdroj or not _je_pod(zdroj, IMAGES_DIR):
        return ("Not Found", 404)
    if n.klic(zdroj, n.STRANA) != klic:
        # Klíč nesedí na dnešní podobu obrázku - odkaz je z dřívějška. Ať si prohlížeč
        # vyzvedne aktuální adresu, místo aby dostal cizí obrázek.
        return ("Not Found", 404)
    if not smi_videt_obrazek(current_user, filename):
        return ("Not Found", 404)

    otisk = n.otisk_cesty(filename)
    cil = n.soubor_strany(NAHLEDY_DIR, otisk, klic)
    if not cil.exists() and not n.vyrob(zdroj, cil, n.STRANA):
        # Když se náhled nepovede, ať čtečka nezůstane bez strany.
        return redirect(url_for('serve_songbook_image', filename=filename))
    n.uklid_starych_stran(NAHLEDY_DIR, otisk, klic)

    odpoved = send_from_directory(str(NAHLEDY_DIR / 'strany'), cil.name)
    # Ta adresa už nikdy neponese jiný obsah, protože klíč je z obsahu originálu.
    odpoved.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return odpoved


@app.route('/api/nastaveni/tema', methods=['POST'])
@login_required
def uloz_tema():
    """Zapamatuje si vybraný motiv u účtu, ať ho uživatel nepřepíná na každém zařízení."""
    tema = (request.get_json(silent=True) or {}).get('tema') or request.form.get('tema')
    if tema not in ZNAME_MOTIVY:
        return jsonify({'ok': False, 'error': 'neznámý motiv'}), 400
    if is_guest(current_user):
        # Host je sdílený účet, takže by si volbu přepisovali navzájem.
        return jsonify({'ok': True, 'ulozeno': False})
    current_user.theme = tema
    db.session.commit()
    return jsonify({'ok': True, 'ulozeno': True})


@app.route('/profil')
@login_required
def profil():
    return render_template('profil.html')


@app.route('/profil/zmena-hesla', methods=['POST'])
@login_required
def zmena_hesla():
    if is_guest(current_user):
        return redirect(url_for('profil'))
    stare = request.form.get('stare') or ''
    nove = request.form.get('nove') or ''
    nove2 = request.form.get('nove2') or ''

    if not check_password_hash(current_user.password, stare):
        # Počítá se stejně jako neúspěšné přihlášení: kdo zná session, ale ne heslo,
        # by tudy jinak mohl heslo hádat bez omezení.
        zaznamenej_neuspesny_pokus(current_user.email)
        flash('Současné heslo nesouhlasí.', 'error')
    elif chyba_hesla(nove):
        flash(chyba_hesla(nove), 'error')
    elif nove != nove2:
        flash('Nová hesla se neshodují.', 'error')
    else:
        current_user.password = generate_password_hash(nove, method='pbkdf2:sha256',
                                                       salt_length=16)
        db.session.commit()
        zapomen_pokusy(current_user.email)
        flash('Heslo bylo změněno.', 'success')
    return redirect(url_for('profil'))


@app.route('/smazat-ucet', methods=['GET', 'POST'])
@login_required
def smazat_ucet():
    """Zrušení účtu i s tím, co k němu patří.

    Potvrzuje se opsáním vlastní e-mailové adresy, ne jen kliknutím. Je to nevratné a
    mizí při tom i soukromé zpěvníky, takže omylem spuštěné to být nemá.
    """
    if is_guest(current_user):
        flash('Účet hosta se ruší odhlášením.', 'error')
        return redirect(url_for('dashboard'))

    moje = Songbook.query.filter_by(owner_id=current_user.id).all()
    if request.method == 'POST':
        if (request.form.get('potvrzeni') or '').strip().lower() != current_user.email.lower():
            flash('Pro potvrzení opište přesně svou e-mailovou adresu.', 'error')
            return render_template('smazat_ucet.html', zpevniky=moje)

        uid, email = current_user.id, current_user.email
        soubory = []
        for sb in moje:
            for sloupec, _ in SLOTY_OBALEK:
                cesta = getattr(sb, sloupec, None)
                if cesta:
                    soubory.append(cesta)
            for strana in build_songbook_content_pages(sb.id):
                if strana['file'] != 'blank':
                    soubory.append(strana['file'])
            db.session.query(SongbookPage).filter_by(songbook_id=sb.id).delete()
            db.session.query(UserSongbookAccess).filter_by(songbook_id=sb.id).delete()
            db.session.delete(sb)

        db.session.query(UserSongbookAccess).filter_by(user_id=uid).delete()
        logout_user()
        db.session.query(User).filter_by(id=uid).delete()
        db.session.commit()

        # Až po commitu a jen to, na co už nikdo neukazuje - písničku můžou sdílet i cizí
        # zpěvníky a smazat jim obrázky by je rozbilo.
        smaz_osirele_obrazky(soubory)
        app.logger.info("Účet %s smazán i s %d zpěvníky", email, len(moje))
        flash('Váš účet byl odstraněn.', 'success')
        return redirect(url_for('index'))

    return render_template('smazat_ucet.html', zpevniky=moje)


@app.route('/zapomenute-heslo', methods=['GET', 'POST'])
def zapomenute_heslo():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip()
        user = User.query.filter_by(email=email).first()
        # Odpověď je vždycky stejná, i když účet neexistuje. Jinak by šlo přes tenhle
        # formulář zjišťovat, které adresy jsou zaregistrované.
        if user and not is_guest(user):
            if prihlaseni_zablokovano(email) is None:
                zaznamenej_neuspesny_pokus(email)
                try:
                    from .mail import posli_email
                    from .tokeny import token_hesla
                except ImportError:
                    from mail import posli_email
                    from tokeny import token_hesla
                odkaz = url_for('obnova_hesla', token=token_hesla(user), _external=True)
                try:
                    posli_email(
                        user.email,
                        "Obnova hesla - Digi zpěvník",
                        "Dobrý den,\n\n"
                        "někdo požádal o obnovu hesla k účtu na webu Digi zpěvník. Pokud "
                        "jste to byli vy, nastavte si nové heslo na této stránce:\n\n"
                        f"{odkaz}\n\n"
                        "Odkaz platí dvě hodiny a dá se použít jen jednou. Jakmile si "
                        "heslo změníte, přestane platit.\n\n"
                        "Pokud jste o obnovu nežádali, nemusíte dělat nic. Vaše heslo "
                        "zůstává beze změny a nikdo se k účtu nedostal.\n\n"
                        "https://digizpevnik.cz\n\n"
                        "Tato zpráva byla vygenerována automaticky, neodpovídejte na ni.\n",
                        "<p>Dobrý den,</p>"
                        "<p>někdo požádal o obnovu hesla k účtu na webu "
                        "<strong>Digi zpěvník</strong>. Pokud jste to byli vy, nastavte si "
                        "nové heslo:</p>"
                        f'<p><a href="{odkaz}">Nastavit nové heslo</a></p>'
                        "<p>Odkaz platí dvě hodiny a dá se použít jen jednou. Jakmile si "
                        "heslo změníte, přestane platit.</p>"
                        "<p>Pokud jste o obnovu nežádali, nemusíte dělat nic. Vaše heslo "
                        "zůstává beze změny a nikdo se k účtu nedostal.</p>"
                        "<p>Tato zpráva byla vygenerována automaticky, neodpovídejte "
                        "na ni.</p>",
                    )
                except Exception as chyba:  # noqa: BLE001
                    app.logger.error("Obnova hesla pro %s se neodeslala: %s", email, chyba)
        flash('Pokud u nás účet s touto adresou existuje, poslali jsme na ni odkaz '
              'na obnovu hesla.', 'success')
        return redirect(url_for('login'))
    return render_template('heslo.html', rezim='zadost')


@app.route('/obnova-hesla/<token>', methods=['GET', 'POST'])
def obnova_hesla(token):
    try:
        from .tokeny import uzivatel_z_hesla
    except ImportError:
        from tokeny import uzivatel_z_hesla
    user = uzivatel_z_hesla(token, User)
    if not user:
        return render_template('heslo.html', rezim='neplatny')
    if request.method == 'POST':
        heslo = request.form.get('password') or ''
        chyba = chyba_hesla(heslo)
        if chyba:
            flash(chyba, 'error')
            return render_template('heslo.html', rezim='nastaveni', token=token)
        user.password = generate_password_hash(heslo, method='pbkdf2:sha256', salt_length=16)
        # Kdo si dokázal vyzvednout odkaz z e-mailu, prokázal tím i vlastnictví adresy.
        user.email_verified = True
        db.session.commit()
        zapomen_pokusy(user.email)
        flash('Heslo bylo změněno, můžete se přihlásit.', 'success')
        return redirect(url_for('login'))
    return render_template('heslo.html', rezim='nastaveni', token=token)


@app.route('/overit-email/<token>', methods=['GET', 'POST'])
def overit_email(token):
    """Ověření adresy.

    GET jen ukáže stránku s tlačítkem, POST teprve ověří. Vypadá to zbytečně, ale je to
    nutné: Brevo u transakčních zpráv přepisuje odkazy kvůli sledování a bezpečnostní
    skenery v poštovních systémech si odkazy samy otevírají, aby zkontrolovaly, kam vedou.
    Kdyby ověřovalo samotné otevření, skener by odkaz spotřeboval dřív než uživatel.
    """
    try:
        from .tokeny import uzivatel_z_overeni
    except ImportError:
        from tokeny import uzivatel_z_overeni
    user = uzivatel_z_overeni(token, User)
    if not user:
        return render_template('overeni.html', stav='neplatny')
    if user.email_verified:
        return render_template('overeni.html', stav='hotovo', email=user.email)
    if request.method == 'POST':
        user.email_verified = True
        db.session.commit()
        return render_template('overeni.html', stav='potvrzeno', email=user.email)
    return render_template('overeni.html', stav='potvrdit', email=user.email)


@app.route('/poslat-overeni-znovu', methods=['POST'])
@login_required
def poslat_overeni_znovu():
    if current_user.email_verified or is_guest(current_user):
        return redirect(url_for('dashboard'))
    # Stejné počítadlo jako u přihlašování, ať z toho není nástroj na zahlcení cizí schránky.
    if prihlaseni_zablokovano(current_user.email) is not None:
        flash('Ověřovací e-mail jsme právě posílali. Zkuste to prosím za chvíli.', 'error')
        return redirect(request.referrer or url_for('dashboard'))
    zaznamenej_neuspesny_pokus(current_user.email)
    if posli_overovaci_email(current_user):
        flash('Ověřovací e-mail jsme poslali znovu.', 'success')
    else:
        flash('E-mail se nepodařilo odeslat, zkuste to prosím později.', 'error')
    return redirect(request.referrer or url_for('dashboard'))


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
                .order_by(SongImage.poradi.asc(), SongImage.id.asc()).all()):
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
    return render_template('dashboard.html', guest=is_guest(current_user))

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

    return render_template('search.html', rows=results, guest=is_guest(current_user))

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
    song_images = SongImage.query.filter_by(song_id=song.id).order_by(SongImage.poradi.asc(), SongImage.id.asc()).all()
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

    # Strany leží ploché v pages/ a nepatří ani písni, ani zpěvníku - jedna strana může
    # nést dvě písně a jedna píseň může být ve dvou zpěvnících.
    saved = 0
    for idx, file_storage in files:
        pripona = Path(secure_filename(Path(file_storage.filename).name) or '').suffix.lower() or '.png'
        # Číslo se přiděluje po jednom a soubor se hned zapíše, takže další přidělení
        # už ho vidí obsazené.
        rel_path = _nova_cesta_strany(sb, pripona)
        abs_path = IMAGES_DIR / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        _save_image_with_limit(file_storage, abs_path, ext_hint=pripona)
        # Pořadí v rámci písně, ne ve zpěvníku. Bere se z pořadí nahraných souborů,
        # protože jméno souboru o pořadí nic neříká a říkat nemá.
        db.session.add(SongImage(song_id=new_song_id, poradi=saved + 1, image_path=rel_path))
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
    kandidati = [img.image_path for img in imgs]

    # Odpojit z tohohle zpěvníku. Soubory se nestěhují: cesta už nenese, ze kterého
    # zpěvníku strana pochází, takže se nemá s čím rozejít.
    db.session.query(SongbookPage).filter_by(songbook_id=sb.id, song_id=song.id).delete()
    db.session.flush()

    zbyva = db.session.query(SongbookPage.id).filter_by(song_id=song.id).first()
    if zbyva:
        db.session.commit()
        schedule_export_warm(songbook_id)
        return jsonify({'ok': True, 'detached_only': True})

    # Píseň už není v žádném zpěvníku, takže může pryč i s vazbami. Soubory až po
    # commitu a přes smaz_osirele_obrazky, protože tutéž stranu může nést jiná píseň.
    db.session.query(SongImage).filter_by(song_id=song.id).delete()
    db.session.delete(song)
    db.session.commit()
    smaz_osirele_obrazky(kandidati)
    schedule_export_warm(songbook_id)
    return jsonify({'ok': True, 'deleted_song': True})

@app.route('/public-songbooks')
@login_required
def public_songbooks():
    # Show all public songbooks for everyone
    songbooks = db.session.execute(
        db.select(Songbook).where(Songbook.is_public == 1)
    ).scalars().all()
    return render_template(
        'public_songbooks.html',
        songbooks=songbooks,
        guest=is_guest(current_user),
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
    if is_guest(current_user):
        return jsonify({"ok": False, "error": "Guests cannot create songbooks"}), 403
    if not smi_tvorit(current_user):
        return jsonify({"ok": False,
                        "error": "Nejdřív si prosím ověřte e-mailovou adresu"}), 403

    want_public = request.form.get('is_public') in ('1', 'true', 'True', 'on')
    if want_public and not is_admin(current_user):
        return jsonify({"ok": False, "error": "Veřejný zpěvník může vytvořit jen admin."}), 403

    title = (request.form.get('title') or '').strip() or ('Nový zpěvník' if want_public else 'Můj zpěvník')
    use_cover = request.form.get('use_cover', '1') in ('1', 'true', 'True', 'on')

    if want_public:
        # Keep the seeded 00001.. numbering so public books stay consistent
        sid = _next_public_songbook_id()
        koren = 'verejne'
    else:
        # Generate a simple unique ID scoped by user and timestamp
        sid = f"u{current_user.id}-{int(time.time())}"
        koren = f"uzivatele/{current_user.id}"
    # Ani název, ani e-mail - obojí se mění, a cesta by pak lhala.
    rel_dir = Path(koren) / 'songbooks' / sid / 'covers'
    abs_dir = IMAGES_DIR / rel_dir

    # Prepare file save helper
    def save_cover(file_storage, name_hint):
        if not file_storage:
            return None
        # Normalize extension
        ext = (Path(file_storage.filename).suffix or '.png').lower()
        if ext not in ['.png', '.jpg', '.jpeg', '.webp', '.svg']:
            ext = '.png'
        abs_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{ROLE_OBALEK.get(name_hint, name_hint)}{ext}"
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

            # Soubory se nestěhují a cesty se nepřepisují. Dřív to nutné bylo, protože
            # složka nesla jméno vlastníka a název zpěvníku; dnes je cesta uložená natvrdo
            # a o tom, kdo zpěvník vlastní, rozhoduje jedině `owner_id`. Obrázky zůstávají
            # v kořeni toho, kdo je nahrál - to je i správně pro počítání zabraného místa.
            sb.owner_id = new_owner.id
            db.session.delete(chosen_entry)
            db.session.commit()

            return jsonify({"ok": True})

    # Strany zpěvníku se posbírají ještě před smazáním, ale nemažou se natvrdo: tutéž
    # stranu může mít ještě jiný zpěvník. Rozhodne až smaz_osirele_obrazky po commitu.
    song_ids = {r.song_id for r in SongbookPage.query.filter_by(songbook_id=sb.id).all()}
    kandidati = [r.image_path for r in
                 SongImage.query.filter(SongImage.song_id.in_(song_ids)).all()] if song_ids else []
    kandidati += [c for c in (sb.img_path_cover_front_outer, sb.img_path_cover_front_inner,
                              sb.img_path_cover_back_inner, sb.img_path_cover_back_outer,
                              sb.img_path_cover_preview) if c]
    kandidati += [io.image_path for io in sb.intros_outros if io.image_path]

    # Písně, které po smazání nebudou v žádném zpěvníku, nemá smysl držet.
    db.session.delete(sb)
    db.session.flush()
    for song_id in song_ids:
        if not db.session.query(SongbookPage.id).filter_by(song_id=song_id).first():
            db.session.query(SongImage).filter_by(song_id=song_id).delete()
            osirela = Song.query.get(song_id)
            if osirela:
                db.session.delete(osirela)
    db.session.commit()

    smaz_osirele_obrazky(kandidati)

    # Složka obálek zpěvníku už na nic neukazuje, ta může celá.
    try:
        adresar = IMAGES_DIR / _koren_pro_zpevnik(sb) / 'songbooks' / sb.id
        if adresar.exists() and adresar.resolve() != IMAGES_DIR.resolve():
            shutil.rmtree(adresar, ignore_errors=True)
    except Exception:
        pass  # ignore file removal errors

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
        priv_rows = (db.session.query(SongImage.song_id)
                     .join(Obrazek, Obrazek.id == SongImage.image_id)
                     .filter(SongImage.song_id.in_(song_ids),
                             Obrazek.cesta.like('uzivatele/%'))
                     .distinct().all())
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

    # Cesty obrázků, které by po uložení mohly zůstat bez odkazu. Plní se cestou dolů,
    # vyhodnocuje až po commitu.
    ke_smazani_soubory = set()

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

    def save_cover(file_storage, name_hint):
        if not file_storage:
            return None
        # Jméno se z uploadu nebere. Dřív se přebíralo to uživatelovo, takže výměna obálky
        # založila `co_radi_hrajeme.png` vedle `coverfrontout.png` a role souboru se dala
        # zjistit jen z databáze. Obálky jsou čtyři a každá má pevné jméno podle role.
        ext = (Path(file_storage.filename).suffix or '.png').lower()
        if ext not in ['.png', '.jpg', '.jpeg', '.webp', '.svg']:
            ext = '.png'
        rel = _cesta_obalky(sb, name_hint, ext)
        abs_path = IMAGES_DIR / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        _save_image_with_limit(file_storage, abs_path, ext_hint=ext)
        return rel

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

            # Soubor se ukládá jednou, ať už na straně stojí jedna píseň nebo tři.
            # Dřív se rozlišovalo, jestli je strana sdílená, a nesdílená se ukládala pod
            # jednu z písní - to teď nedává smysl, protože strana nepatří ani jedné.
            saved_paths = []
            for offset, file_storage in enumerate(files, start=1):
                pripona = Path(secure_filename(Path(file_storage.filename).name) or '').suffix.lower() or '.png'
                rel_path = _nova_cesta_strany(sb, pripona)
                abs_path = IMAGES_DIR / rel_path
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                _save_image_with_limit(file_storage, abs_path, ext_hint=pripona)
                saved_paths.append(rel_path)

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
                # Každá z písní na téhle straně dostane vlastní číslování od 1 - poradi
                # je pořadí v rámci písně, takže sdílená strana může být pro jednu píseň
                # první a pro druhou druhá.
                for poradi, rel_path in enumerate(saved_paths, 1):
                    db.session.add(SongImage(song_id=song_id, poradi=poradi,
                                             image_path=rel_path))
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
            # Cesty obrázků odebíraných písniček si musíme zapamatovat teď, dokud na ně
            # ještě vedou řádky v databázi. Smazat se smí až po commitu a jen ty, na které
            # už nikdo neukazuje - viz smaz_osirele_obrazky.
            ke_smazani_soubory.update(
                p for (p,) in db.session.query(Obrazek.cesta)
                .join(SongImage, SongImage.image_id == Obrazek.id)
                .filter(SongImage.song_id.in_(list(to_delete))).all())
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
    # Až po commitu, protože se rozhoduje podle toho, co v databázi zbylo.
    smaz_osirele_obrazky(ke_smazani_soubory)
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
        song_images = SongImage.query.filter_by(song_id=song_id).order_by(SongImage.poradi.asc(), SongImage.id.asc()).all()
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
      - "blank" inside the content is kept and drawn as an empty page, so printed page
        numbers still line up.

    Chybějící část obálky se doplní prázdnou stranou v barvě zpěvníku, stejně jako to
    dělá čtečka. Dřív se přeskakovala, jenže obálka je složený list: vynechat jednu její
    stranu znamená posunout všechny dvoustrany a vytisknout něco, co se nedá složit.
    Prázdná vnitřní strana obálky navíc nemusí být soubor - jednolitá barevná plocha se
    dá nakreslit, a na rozdíl od obrázku se přebarví spolu se zpěvníkem.

    This is the only place the export learns where pages come from. When PDF import
    lands, "use the archived source page instead of the image" belongs here and the
    renderer will not have to change.
    """
    sequence = []
    # Obálka smí být průhledná, aby šla barva zpěvníku měnit bez překreslování obrázku.
    # Musí ji tedy sem dostat i export, jinak PDF složí alfu na bílou a obálka zbělá.
    barva_obalky = getattr(songbook, 'color', None) or '#FFFFFF'

    # Stejné pravidlo jako ve čtečce: buď zpěvník obálku má a pak má všechny čtyři její
    # strany, nebo ji nemá vůbec a nedoplňuje se nic.
    ma_obalku = any([
        songbook.img_path_cover_front_outer, songbook.img_path_cover_front_inner,
        songbook.img_path_cover_back_inner, songbook.img_path_cover_back_outer,
    ])

    def add(rel_path, kind):
        if kind == "cover":
            if not ma_obalku:
                return
            sequence.append({"file": rel_path or "blank", "kind": kind, "bg": barva_obalky})
        elif rel_path:
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


def _hex_to_rgb(hex_color, default=(255, 255, 255)):
    """#rgb nebo #rrggbb na trojici. Cokoliv nečitelného vrací default."""
    h = (hex_color or '').strip().lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    if len(h) != 6:
        return default
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default


def _flatten_to_rgb(image, background=(255, 255, 255)):
    """Drop the alpha channel onto a solid background.

    Every scanned page is stored as RGBA and PDF has no plain alpha: left as it is, the
    pages come out on a black background. A solid fill rather than a transparency mask,
    because an SMask inflates the file and prints unpredictably.

    Bílá platí pro vnitřní strany. Obálka dostane barvu zpěvníku - ta je u průhledné
    obálky jediné, co ji odlišuje, a čtečka ji pod obálku kreslí taky.
    """
    if image.mode == 'RGB':
        return image
    if image.mode not in ('RGBA', 'LA', 'PA'):
        image = image.convert('RGBA')
    canvas = Image.new('RGB', image.size, background)
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
        # Celé sekundy, ne nanosekundy - tatáž lekce jako u náhledů (nahledy.klic).
        # Nanosekundy nepřežijí rsync ani obnovu ze zálohy, takže by tentýž obrázek dal
        # na Macu a na serveru jiný klíč a předpřipravené PDF by se nedalo nahrát.
        # Na rozlišení verzí souboru sekundy stačí, zvlášť když je v podpisu i velikost.
        digest.update(f"|{int(stat.st_mtime) if stat else 0}|{stat.st_size if stat else 0}\n".encode())
        # Barva se propisuje do pixelů průhledné obálky, takže její změna musí dát jiný
        # klíč. Bez tohohle by po přebarvení zpěvníku zůstalo viset staré PDF.
        digest.update(f"|{item.get('bg') or ''}\n".encode())
    return digest.hexdigest()[:16]


def _open_export_page(item):
    """One page as an RGB image. A missing file must not sink the whole export."""
    rel_path = item['file']
    pozadi = _hex_to_rgb(item.get('bg'))
    abs_path = None if rel_path == 'blank' else _abs_image_path(rel_path)
    if abs_path is None or not abs_path.exists():
        return Image.new('RGB', PAGE_PX, pozadi)
    with Image.open(abs_path) as raw:
        # Načíst pixely, dokud je soubor otevřený. U stran s alfou je stáhne až
        # skládání na pozadí, ale strana, která je rovnou RGB, se vrací tak jak je -
        # a po zavření souboru by z ní nešlo číst.
        raw.load()
        return _flatten_to_rgb(raw, pozadi)


# Pod tolik pixelů se strana nedoplňuje. Skeny jsou 1748x2480, což je proti A4 o 0,3 %
# vedle, tedy tři pixely na stranu - nepoznatelné v tisku a nestojí za to sahat na
# jedenáct set stran. Čtvercová obálka, která se roztahovala o 41 %, je nad tím řádově.
A4_PADDING_TOLERANCE_PX = 8


def _fit_to_a4(page, pozadi):
    """Doplnit stranu na poměr A4 beze změny poměru jejích vlastních pixelů.

    PDF se ukládá tak, že se DPI odvodí zvlášť pro šířku a zvlášť pro výšku, takže
    strana vyjde přesně na A4 - ale co nemělo poměr A4, se na ni natáhlo. Čtvercová
    obálka 1536x1536 se tím roztáhla o 41 % do výšky. Doplněním okrajů vyjde strana na
    A4 taky, jen bez deformace.

    Doplňuje se barvou strany: u obálky barvou zpěvníku, u vnitřní strany bílou. Pruh
    tak splyne s tím, co kolem něj je, místo aby vypadal jako chyba.
    """
    sirka, vyska = page.size
    cil = A4_INCHES[0] / A4_INCHES[1]
    if sirka / vyska > cil:
        nova = (sirka, round(sirka / cil))     # širší než A4, přidat nahoru a dolů
    else:
        nova = (round(vyska * cil), vyska)     # užší, přidat po stranách
    if (nova[0] - sirka <= A4_PADDING_TOLERANCE_PX
            and nova[1] - vyska <= A4_PADDING_TOLERANCE_PX):
        return page
    platno = Image.new('RGB', nova, pozadi)
    platno.paste(page, ((nova[0] - sirka) // 2, (nova[1] - vyska) // 2))
    page.close()
    return platno


def render_songbook_pdf(sequence, out_path, variant, on_page=None):
    """Write the songbook to a PDF, one page at a time.

    Streamed deliberately. Pillow's save_all with append_images holds every page decoded
    at once, and a 123-page book at 1748x2480 RGBA is over 2 GB - instant OOM on a 1 GB
    box. Appending page by page keeps memory flat: measured 170-250 MB peak regardless
    of whether the book has 26 pages or 123.

    The physical size is pinned to A4 by deriving DPI from each page's own pixel size,
    so scans at other resolutions still come out A4 and no bitmap is rescaled unless the
    variant asks for it. Strana, která poměr A4 nemá, se předtím doplní okraji - jinak
    by ji to odvození DPI na A4 natáhlo.
    """
    settings = EXPORT_VARIANTS[variant]
    quality, max_edge = settings['quality'], settings['max_edge']

    first = True
    for item in sequence:
        started = time.time()
        page = _open_export_page(item)
        # Doplnit dřív než zmenšit, ať doplněná strana skončí na téže výšce jako
        # ostatní. Obráceně by se přes max_edge přetáhla o výšku doplněných okrajů.
        page = _fit_to_a4(page, _hex_to_rgb(item.get('bg')))
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


def render_songbook_zip(sequence, out_path, on_page=None):
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
            if on_page:
                on_page(index)


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
    except OSError:
        return

    files = [p for p in EXPORTS_DIR.glob('*') if p.is_file() and p.suffix in ('.pdf', '.zip')]
    total = sum(p.stat().st_size for p in files if p.exists())

    # Exporty veřejných zpěvníků se z cache nevyhazují. Stažení veřejného zpěvníku má být
    # vždycky hned a aktuální; nahrazuje je jedině změna v samotném zpěvníku, kdy se změní
    # klíč a starou verzi smaže glob výš. Kdyby je vytlačil někdo, kdo si vyexportoval
    # hodně svých, čekal by další návštěvník 12-25 s na přegenerování.
    try:
        verejne = {re.sub(r'[^A-Za-z0-9_]', '_', sid) for (sid,) in
                   db.session.query(Songbook.id).filter(Songbook.is_public == 1).all()}
    except Exception:  # noqa: BLE001 - úklid nesmí shodit export
        verejne = set()

    def je_verejny(path: Path) -> bool:
        # Jméno je "<id>-<varianta>-<klíč>", a id samo může obsahovat pomlčky, takže se
        # porovnává prefix, ne první díl.
        return any(path.name.startswith(v + '-') for v in verejne)

    vyhoditelne = [p for p in files if not je_verejny(p)]
    for path in sorted(vyhoditelne, key=lambda p: p.stat().st_mtime if p.exists() else 0):
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


def _zapis_postup(lock_path: Path, hotovo: int, celkem: int, zacatek: float) -> None:
    """Kolik stran je hotových, do zámku.

    Do zámku schválně: workerů jsou čtyři a nesdílejí paměť, takže na stav se ptá jiný
    proces, než ten, co soubor staví - proměnná by mu byla k ničemu. Zámek už existuje,
    je per build a mizí s ním, takže se nemá jak rozejít.
    """
    try:
        lock_path.write_text(f"{hotovo}|{celkem}|{zacatek:.0f}")
    except OSError:
        pass  # postup je pohodlí, ne podmínka


def _precti_postup(lock_path: Path):
    try:
        hotovo, celkem, zacatek = lock_path.read_text().split('|')
        return int(hotovo), int(celkem), float(zacatek)
    except (OSError, ValueError):
        return None


def _build_export_file(book_id, variant, kind, paths):
    """Run one export to completion. Runs in a thread, so it must not raise."""
    try:
        with app.app_context():
            songbook = Songbook.query.get(book_id)
            if songbook is None:
                raise RuntimeError(f"zpěvník {book_id} mezitím zmizel")
            sequence = build_songbook_export_sequence(songbook)
            celkem = len(sequence)
            zacatek = time.time()
            hotovo = [0]

            def krok(*_):
                hotovo[0] += 1
                _zapis_postup(paths['lock'], hotovo[0], celkem, zacatek)

            _zapis_postup(paths['lock'], 0, celkem, zacatek)
            if kind == 'pdf':
                render_songbook_pdf(sequence, paths['part'], variant, on_page=krok)
            else:
                render_songbook_zip(sequence, paths['part'], on_page=krok)
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


def _resolve_export_request(book_id, kind, variant=None):
    """Shared by the download and the status route: authorise, then locate the file.

    `variant` se dá předat natvrdo; jinak se bere z adresy. Potřebuje to routa, která se
    ptá na všechny tři varianty naráz.
    """
    songbook = Songbook.query.get_or_404(book_id)
    if not can_view_songbook(current_user, songbook):
        return None, ("Access denied", 403)
    # Skládání PDF je nejdražší operace, kterou umí návštěvník spustit, a na stroji s 1 GB
    # paměti trvá desítky sekund. Hostům proto stahování nepatří - ať si založí účet.
    if not smi_tvorit(current_user):
        # Skládání PDF je nejdražší operace, kterou umí návštěvník spustit, a na stroji
        # s 1 GB paměti trvá desítky sekund. Hostům a neověřeným účtům proto nepatří.
        zprava = ('Stahování je jen pro přihlášené uživatele' if is_guest(current_user)
                  else 'Stahování bude dostupné po ověření e-mailové adresy')
        return None, (jsonify({'error': zprava}), 403)

    if kind == 'pdf':
        variant = variant or request.args.get('q', 'small')
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
        odpoved = {'state': 'building'}
        postup = _precti_postup(paths['lock'])
        if postup:
            hotovo, celkem, zacatek = postup
            odpoved.update(hotovo=hotovo, celkem=celkem)
            uplynulo = time.time() - zacatek
            if hotovo > 0 and celkem > hotovo:
                # Odhad z průměru dosud hotových stran. Zaokrouhluje se nahoru na pět
                # vteřin, ať číslo neposkakuje o jednotky při každém dotazu.
                zbyva = uplynulo / hotovo * (celkem - hotovo)
                odpoved['zbyva_s'] = max(5, int(round(zbyva / 5.0)) * 5)
        return jsonify(odpoved)
    return jsonify({'state': 'idle'})


@app.route('/songbook/<book_id>/export-hotove')
@login_required
def songbook_export_hotove(book_id):
    """Které varianty už leží v cache, aby nabídka mohla říct 'stáhne se hned'.

    Ptá se to jedním dotazem za všechny tři, ne třemi - nabídka je jedna a otevře se
    naráz celá.
    """
    songbook = Songbook.query.get_or_404(book_id)
    if not can_view_songbook(current_user, songbook) or not smi_tvorit(current_user):
        return jsonify({})

    # Sekvence se staví jednou pro všechny tři: je to ta dražší část a pro PDF i ZIP
    # je stejná.
    sequence = build_songbook_export_sequence(songbook)
    hotove = {}
    for kind, variant in (('pdf', 'small'), ('pdf', 'high'), ('zip', 'orig')):
        key = songbook_export_key(sequence, variant)
        paths = _export_paths(book_id, variant, kind, key)
        hotove[f"{kind}-{variant}"] = paths['final'].exists()
    return jsonify(hotove)


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
            song_images = SongImage.query.filter_by(song_id=song.id).order_by(SongImage.poradi.asc(), SongImage.id.asc()).all()
            current_toc_page += len(song_images) if song_images else 1
            processed_songs.add(page.song_id)
            continue

        # Get all images for this song
        song_images = SongImage.query.filter_by(song_id=song.id).order_by(SongImage.poradi.asc(), SongImage.id.asc()).all()
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
        can_manage_songbook=can_manage,
        smi_stahovat=smi_tvorit(current_user)
    )

@app.context_processor
def inject_user_status():
    return dict(
        guest=is_guest(current_user),
        logged_in=current_user.is_authenticated,
        # Motiv uložený u účtu. Šablona ho vloží do stránky, takže se použije hned při
        # vykreslení - kdyby se dotahoval až skriptem, blikla by na okamžik cizí barva.
        tema_uctu=(getattr(current_user, 'theme', None)
                   if current_user.is_authenticated else None),
    )

# ---------- CLI PŘÍKAZY ----------

@app.cli.command("migrace-overeni")
@click.option("--overit-stavajici/--neoverovat", default=True,
              help="označit stávající účty za ověřené (výchozí)")
@with_appcontext
def migrace_overeni(overit_stavajici):
    """Přidá sloupec email_verified a označí stávající účty za ověřené.

    Lidé, kteří už účet mají, za nic nemůžou - nutit je zpětně k ověření by znamenalo
    otravovat je kvůli něčemu, co v době jejich registrace neexistovalo.
    """
    from sqlalchemy import text
    sloupce = [r[1] for r in db.session.execute(text("PRAGMA table_info(users)"))]
    if 'email_verified' not in sloupce:
        db.session.execute(text(
            "ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT 0"))
        db.session.commit()
        click.echo("sloupec email_verified přidán")
    else:
        click.echo("sloupec email_verified už existoval")

    if overit_stavajici:
        pocet = db.session.query(User).filter(
            (User.email_verified == False) | (User.email_verified.is_(None))  # noqa: E712
        ).update({User.email_verified: True}, synchronize_session=False)
        db.session.commit()
        click.echo(f"označeno jako ověřených: {pocet} účtů")

    if 'theme' not in [r[1] for r in db.session.execute(text("PRAGMA table_info(users)"))]:
        db.session.execute(text("ALTER TABLE users ADD COLUMN theme VARCHAR"))
        db.session.commit()
        click.echo("sloupec theme přidán")

    for u in User.query.order_by(User.id).all():
        click.echo(f"  {u.id:3} {u.email:34} role={u.role:6} ověřen={bool(u.email_verified)}")


@app.cli.command("nahledy-warm")
@click.option("--jen", type=click.Choice(['obalky', 'strany']), default=None,
              help="udělat jen jednu skupinu (výchozí obě)")
@with_appcontext
def nahledy_warm(jen):
    """Předpřipraví náhledy obálek a stran, ať na ně první návštěvník nečeká."""
    n = _nahledy()

    if jen != 'strany':
        hotovo = preskoceno = chyb = 0
        for sb in Songbook.query.all():
            rel = sb.img_path_cover_preview
            zdroj = _abs_image_path(rel) if rel else None
            k = n.klic(zdroj, n.OBALKA) if zdroj else None
            if not k:
                preskoceno += 1
                continue
            cil = n.soubor_nahledu(NAHLEDY_DIR, sb.id, k)
            if cil.exists() or n.vyrob(zdroj, cil, n.OBALKA):
                hotovo += 1
            else:
                chyb += 1
            n.uklid_starych(NAHLEDY_DIR, sb.id, k)
        velikost = sum(f.stat().st_size for f in NAHLEDY_DIR.glob('*.webp')) if NAHLEDY_DIR.exists() else 0
        click.echo(f"obálky: {hotovo} hotovo, {preskoceno} bez obálky, {chyb} chyb, "
                   f"celkem {velikost / 1e6:.2f} MB")

    if jen != 'obalky':
        # Tatáž písnička může být ve dvou zpěvnících, takže se cesty opakují; množina
        # zařídí, že se obrázek zmenšuje jednou.
        cesty = {r.image_path for r in SongImage.query.all() if r.image_path}
        cesty |= {r.image_path for r in SongbookIntroOutroImage.query.all() if r.image_path}
        # Obálky patří do obou skupin. Nahoře dostaly malý náhled do přehledů, ale čtečka
        # je ukazuje jako běžné strany, takže potřebují i variantu v šířce strany. Bez
        # tohohle se čtyři obálky každého zpěvníku dogenerovávaly až za provozu.
        for sb in Songbook.query.all():
            cesty |= {c for c in (sb.img_path_cover_front_outer, sb.img_path_cover_front_inner,
                                  sb.img_path_cover_back_inner, sb.img_path_cover_back_outer) if c}
        hotovo = preskoceno = chyb = 0
        for i, rel in enumerate(sorted(cesty), 1):
            zdroj = _abs_image_path(rel)
            k = n.klic(zdroj, n.STRANA) if zdroj else None
            if not k:
                preskoceno += 1
                continue
            cil = n.soubor_strany(NAHLEDY_DIR, n.otisk_cesty(rel), k)
            if cil.exists() or n.vyrob(zdroj, cil, n.STRANA):
                hotovo += 1
            else:
                chyb += 1
            n.uklid_starych_stran(NAHLEDY_DIR, n.otisk_cesty(rel), k)
            if i % 100 == 0:
                click.echo(f"  ... {i}/{len(cesty)}")
        strany_dir = NAHLEDY_DIR / 'strany'
        velikost = sum(f.stat().st_size for f in strany_dir.glob('*.webp')) if strany_dir.exists() else 0
        click.echo(f"strany: {hotovo} hotovo, {preskoceno} bez souboru, {chyb} chyb, "
                   f"celkem {velikost / 1e6:.2f} MB")


@app.cli.command("mesicni-hlaseni")
@click.option("--komu", default=None, help="kam poslat (výchozí MAIL_REPLY_TO nebo MAIL_FROM)")
@click.option("--jen-vypsat", is_flag=True, help="nic neposílat, jen ukázat obsah")
@with_appcontext
def mesicni_hlaseni(komu, jen_vypsat):
    """Krátký přehled o stavu, posílaný jednou měsíčně.

    Má dva úkoly zároveň. Za prvé udrží živý SMTP klíč - Brevo ho ruší po 90 dnech
    nečinnosti a naše jediné zprávy jsou registrace a obnova hesla, takže čtvrt roku ticha
    je běžný stav. Za druhé je to kontrolka: když zpráva nepřijde, něco je rozbité.

    Proto se posílá bez ohledu na to, jestli se něco změnilo. Zpráva "vše v pořádku" nese
    informaci právě tím, že dorazila.
    """
    import shutil
    from datetime import datetime

    uzivatelu = User.query.count()
    neovereni = User.query.filter_by(email_verified=False).count()
    verejnych = Songbook.query.filter_by(is_public=1).count()
    soukromych = Songbook.query.filter(Songbook.is_public != 1).count()
    pisnicek = Song.query.count()

    koren = Path(app.root_path).parent
    velikost = sum(f.stat().st_size for f in (koren / 'data').rglob('*') if f.is_file())
    volno = shutil.disk_usage(str(koren)).free

    # Značku píše zálohovací skript na Macu po úspěšném běhu. Když zestárne, znamená to,
    # že zálohy tiše přestaly chodit - a to je jediná kopie dat mimo tenhle server.
    znacka = Path.home() / '.posledni-zaloha'
    if znacka.exists():
        stari_dnu = (datetime.now() - datetime.fromtimestamp(znacka.stat().st_mtime)).days
        zaloha = f"před {stari_dnu} dny" if stari_dnu else "dnes"
    else:
        stari_dnu, zaloha = None, "neznámo (značka chybí)"

    varovani = []
    if stari_dnu is None or stari_dnu > 3:
        varovani.append(f"Poslední záloha: {zaloha}. Zálohy zřejmě nechodí.")
    if volno < 5 * 1024**3:
        varovani.append(f"Na disku zbývá jen {volno / 1024**3:.1f} GB.")

    radky = [
        "Měsíční přehled Digi zpěvníku.",
        "",
        f"Uživatelé:       {uzivatelu} (z toho {neovereni} neověřených)",
        f"Zpěvníky:        {verejnych} veřejných, {soukromych} soukromých",
        f"Písničky:        {pisnicek}",
        f"Data na disku:   {velikost / 1e6:.0f} MB",
        f"Volné místo:     {volno / 1024**3:.1f} GB",
        f"Poslední záloha: {zaloha}",
        "",
    ]
    if varovani:
        radky += ["POZOR:"] + [f"  - {v}" for v in varovani] + [""]
    else:
        radky += ["Nic k řešení.", ""]
    radky += [
        "Tahle zpráva chodí jednou měsíčně schválně i když je všechno v pořádku:",
        "udržuje živý přístup k odesílání pošty, který by jinak po 90 dnech nečinnosti",
        "vypršel a shodil registrace. Když nedorazí, něco se pokazilo.",
        "",
        "https://digizpevnik.cz",
    ]
    text = "\n".join(radky)

    if jen_vypsat:
        click.echo(text)
        return

    # Vlastní proměnná, ne MAIL_REPLY_TO: ta je schválně prázdná, protože Reply-To na
    # freemail stojí 2,5 bodu u antispamových filtrů. A MAIL_FROM je noreply adresa, kam
    # se nic nedoručí - hlášení by tiše mizelo.
    cil = komu or os.getenv("HLASENI_KOMU")
    if not cil:
        raise click.ClickException(
            "Chybí HLASENI_KOMU v prostředí - není kam přehled poslat.")
    predmet = "Digi zpěvník - měsíční přehled"
    if varovani:
        predmet += " (něco vyžaduje pozornost)"
    try:
        from .mail import posli_email
    except ImportError:
        from mail import posli_email
    posli_email(cil, predmet, text,
                "<pre style=\"font-family:monospace;font-size:14px\">"
                + text.replace('&', '&amp;').replace('<', '&lt;') + "</pre>")
    click.echo(f"odesláno na {cil}")


@app.cli.command("posli-test")
@click.argument("adresa")
def posli_test(adresa):
    """Pošle testovací zprávu. Slouží k ověření doručitelnosti přes mail-tester.com."""
    try:
        from .mail import posli_email
    except ImportError:
        from mail import posli_email
    posli_email(
        adresa,
        "Testovací zpráva z Digi zpěvníku",
        "Dobrý den,\n\n"
        "tohle je testovací zpráva z aplikace Digi zpěvník, která spravuje naskenované "
        "zpěvníky letního tábora. Posíláme ji proto, abychom si ověřili, že odesílání "
        "e-mailů funguje správně a že zprávy procházejí kontrolou pravosti odesílatele.\n\n"
        "Aplikace posílá jen dva druhy zpráv: potvrzení e-mailové adresy při registraci "
        "a odkaz na obnovu zapomenutého hesla. Žádné novinky ani nabídky rozesílat "
        "nebudeme.\n\n"
        "Pokud vám tato zpráva přišla omylem, nic se neděje a stačí ji smazat. Nikdo "
        "vás na jejím základě nikam nepřihlásil ani neregistroval.\n\n"
        "Zpěvníky najdete na https://digizpevnik.cz\n\n"
        "Tato zpráva byla vygenerována automaticky.\n",
        "<p>Dobrý den,</p>"
        "<p>tohle je testovací zpráva z aplikace <strong>Digi zpěvník</strong>, která "
        "spravuje naskenované zpěvníky letního tábora. Posíláme ji proto, abychom si "
        "ověřili, že odesílání e-mailů funguje správně a že zprávy procházejí kontrolou "
        "pravosti odesílatele.</p>"
        "<p>Aplikace posílá jen dva druhy zpráv: potvrzení e-mailové adresy při registraci "
        "a odkaz na obnovu zapomenutého hesla. Žádné novinky ani nabídky rozesílat "
        "nebudeme.</p>"
        "<p>Pokud vám tato zpráva přišla omylem, nic se neděje a stačí ji smazat. Nikdo "
        "vás na jejím základě nikam nepřihlásil ani neregistroval.</p>"
        "<p>Zpěvníky najdete na <a href=\"https://digizpevnik.cz\">digizpevnik.cz</a></p>"
        "<p>Tato zpráva byla vygenerována automaticky.</p>",
    )
    click.echo(f"Odesláno na {adresa}")


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
