"""Vypíše pořadí stran všech zpěvníků jako JSON, aby se dalo porovnat před a po refaktoru.

Logika, která ze songbook_pages a song_images složí seznam "jedna strana = jeden obrázek",
se vytahuje z těla songbook_detail() do samostatné funkce. Přesun se musí ověřit, ne
prohlédnout: tenhle skript vypíše výsledek pro každý zpěvník, takže se dá porovnat výstup
před přesunem a po něm. Musí být bit za bit shodný.

Použití:
    python backend/scripts/dump_page_order.py > pred.json
    # ... refaktor ...
    python backend/scripts/dump_page_order.py > po.json
    diff pred.json po.json

Přepínač --vlastni použije novou funkci z app.py, bez něj se použije zdejší kopie původní
logiky. Před refaktorem tedy dávají oba stejný výsledek a po refaktoru se ověřuje,
že to tak zůstalo.
"""
import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(PROJECT_ROOT), str(PROJECT_ROOT / "backend")]


def puvodni_logika(SongbookPage, SongImage, book_id):
    """Kopie řádků 1777-1796 z původního songbook_detail(), záměrně doslovná.

    Nesmí se vylepšovat ani zestručňovat - je to referenční chování, proti kterému se
    nová funkce porovnává.
    """
    raw_pages = SongbookPage.query.filter_by(songbook_id=book_id).order_by(
        SongbookPage.page_number.asc(), SongbookPage.id.asc()
    ).all()

    pages_by_song = {}
    for page in raw_pages:
        pages_by_song.setdefault(page.song_id, []).append(page.page_number)

    image_for_page = {}
    for song_id, page_numbers in pages_by_song.items():
        song_images = SongImage.query.filter_by(song_id=song_id).order_by(SongImage.id.asc()).all()
        for offset, page_number in enumerate(sorted(set(page_numbers))):
            if page_number in image_for_page:
                continue
            image_for_page[page_number] = (
                song_images[offset].image_path if offset < len(song_images) else "blank"
            )

    return [
        {"file": image_for_page[page_number], "page_number": page_number, "kind": "content"}
        for page_number in sorted(image_for_page)
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vlastni", action="store_true",
                        help="použít build_songbook_content_pages() z app.py")
    args = parser.parse_args()

    os.environ.setdefault("FLASK_SECRET_KEY", "dump")
    from backend.app import app
    from backend.models import Songbook, SongbookPage, SongImage

    with app.app_context():
        vysledek = {}
        for sb in Songbook.query.order_by(Songbook.id.asc()).all():
            if args.vlastni:
                from backend.app import build_songbook_content_pages
                pages = build_songbook_content_pages(sb.id)
            else:
                pages = puvodni_logika(SongbookPage, SongImage, sb.id)
            vysledek[sb.id] = pages

        print(json.dumps(vysledek, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
