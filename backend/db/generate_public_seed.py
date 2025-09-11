import os
import re
import json
import pandas as pd
from pathlib import Path

EXCEL_PATH = "backend/db/Zpěvník - seznam písniček Handicap.xlsx"
SONGBOOKS_PATH = "backend/static/songbooks"
OUTPUT_PATH = "backend/db/public_seed"

COVER_KEYS = [
    "img_path_cover_preview",
    "img_path_cover_front_outer",
    "img_path_cover_front_inner",
    "img_path_cover_back_inner",
    "img_path_cover_back_outer",
]

def normalize_song_name(name):
    return str(name).strip() if pd.notna(name) else None

def parse_excel():
    df = pd.read_excel(EXCEL_PATH)
    df.columns = df.columns.str.strip()
    records = []

    for _, row in df.iterrows():
        title = normalize_song_name(row.iloc[0])
        author = normalize_song_name(row.get("AUTOR", "-")) or "-"
        songbook_id = str(row.get("Zpěvník č.", "")).strip()
        pages = str(row.get("strana", "")).strip()

        if not (title and songbook_id and pages):
            continue

        page_list = []
        for part in re.split(r"[-,.]", pages):
            try:
                page_list.append(int(part))
            except ValueError:
                continue

        records.append({
            "title": title,
            "author": author,
            "songbook_id": int(songbook_id),
            "pages": page_list
        })

    return records

def collect_images(folder):
    images = os.listdir(folder)
    def img(name): return next((f for f in images if f.lower() == name.lower()), None)

    covers = {
        "img_path_cover_preview": f"{Path(folder).name}/{img('coverfrontout.png')}" if img("coverfrontout.png") else None,
        "img_path_cover_front_outer": f"{Path(folder).name}/{img('coverfrontout.png')}" if img("coverfrontout.png") else None,
        "img_path_cover_front_inner": f"{Path(folder).name}/{img('coverfrontin.png')}" if img("coverfrontin.png") else None,
        "img_path_cover_back_inner": f"{Path(folder).name}/{img('coverbackin.png')}" if img("coverbackin.png") else None,
        "img_path_cover_back_outer": f"{Path(folder).name}/{img('coverbackout.png')}" if img("coverbackout.png") else None,
    }

    intros = sorted([f for f in images if re.match(r"intro\d+\.png", f)], key=lambda x: int(re.search(r'\d+', x).group()))
    outros = sorted([f for f in images if re.match(r"outro\d+\.png", f)], key=lambda x: int(re.search(r'\d+', x).group()))
    pages = sorted([f for f in images if re.match(r"page\d+\.png", f)], key=lambda x: int(re.search(r'\d+', x).group()))

    return covers, intros, outros, pages

def generate_songbook_json(songbook_id, records, covers, intros, outros, pages):
    songbook_records = [r for r in records if r["songbook_id"] == songbook_id]
    pages_by_number = {int(re.search(r'\d+', p).group()): p for p in pages}

    used_pages = set()
    song_entries = []
    missing_pages = []
    missing_authors = []

    # New structure: pages array with each page having image_path, optional page_number, and song_ids list
    pages_array = []
    songs_dict = {}
    song_id_counter = 1

    # Build songs dictionary with unique song_id
    for rec in songbook_records:
        song_key = (rec["title"], rec["author"])
        if song_key not in songs_dict:
            songs_dict[song_key] = song_id_counter
            song_id_counter += 1

    # Map songbook_records by song_key for quick access
    song_records_by_key = { (r["title"], r["author"]): r for r in songbook_records }

    # Collect all page numbers used by songs
    all_song_pages = set()
    for rec in songbook_records:
        all_song_pages.update(rec["pages"])

    # Collect all page numbers from images
    all_image_pages = set(pages_by_number.keys())

    # Determine all pages including non-song pages (pages in images but not in songs)
    all_pages = sorted(all_image_pages)

    for page_num in all_pages:
        filename = pages_by_number.get(page_num)
        if not filename:
            continue

        # Find songs on this page
        song_ids_on_page = []
        for song_key, song_id in songs_dict.items():
            rec = song_records_by_key[song_key]
            if page_num in rec["pages"]:
                song_ids_on_page.append(song_id)

        pages_array.append({
            "page_number": page_num,
            "image_path": f"{songbook_id:05d}/{filename}",
            "song_ids": song_ids_on_page
        })
        used_pages.add(page_num)

    # Add intros as non-song pages with empty page_number and song_ids
    for intro_img in intros:
        pages_array.append({
            "page_number": None,
            "image_path": f"{songbook_id:05d}/{intro_img}",
            "song_ids": [],
            "type": "intro"
        })

    # Add outros as non-song pages with empty page_number and song_ids
    for outro_img in outros:
        pages_array.append({
            "page_number": None,
            "image_path": f"{songbook_id:05d}/{outro_img}",
            "song_ids": [],
            "type": "outro"
        })

    # Add any images not assigned to pages (non-numbered pages)
    unused_pages = [f for n, f in pages_by_number.items() if n not in used_pages]
    for filename in unused_pages:
        pages_array.append({
            "page_number": None,
            "image_path": f"{songbook_id:05d}/{filename}",
            "song_ids": [],
            "type": "non-song"
        })

    # Build songs array
    songs_array = []
    for (title, author), song_id in songs_dict.items():
        songs_array.append({
            "song_id": song_id,
            "title": title,
            "author": author
        })

    result = {
        "title": f"Můj zpěvník č.{songbook_id}",
        "first_page_side": "right",
        **covers,
        "pages": pages_array,
        "songs": songs_array
    }

    return result, missing_pages, missing_authors, unused_pages

def main():
    records = parse_excel()

    total_missing_pages = []
    total_missing_authors = []
    total_unused = []

    for sid in range(1, 30):
        folder = os.path.join(SONGBOOKS_PATH, f"{sid:05d}")
        if not os.path.isdir(folder):
            print(f"❌ Složka {folder} neexistuje.")
            continue

        covers, intros, outros, pages = collect_images(folder)
        data, missing_pages, missing_authors, unused = generate_songbook_json(sid, records, covers, intros, outros, pages)

        json_path = os.path.join(OUTPUT_PATH, f"public_seed_{sid:05d}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        if missing_pages:
            total_missing_pages.extend([(sid, *mp) for mp in missing_pages])
        if missing_authors:
            total_missing_authors.extend([(sid, ma) for ma in missing_authors])
        if unused:
            total_unused.extend([(sid, u) for u in unused])

        print(f"✅ Zpěvník {sid:05d} zpracován.")

    if total_missing_pages:
        print("\n❗️ Chybějící obrázky pro písničky:")
        for sid, title, n in total_missing_pages:
            print(f"  - {title} (Zpěvník {sid:05d}, strana {n})")

    if total_missing_authors:
        print("\n🟡 Písně bez autora:")
        for sid, title in total_missing_authors:
            print(f"  - {title} (Zpěvník {sid:05d})")

    if total_unused:
        print("\n❓ Obrázky bez písničky v Excelu:")
        for sid, f in total_unused:
            print(f"  - {f} (Zpěvník {sid:05d})")

if __name__ == "__main__":
    main()