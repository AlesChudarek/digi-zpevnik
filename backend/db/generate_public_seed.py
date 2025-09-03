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

    for rec in songbook_records:
        image_paths = []
        for n in rec["pages"]:
            filename = pages_by_number.get(n)
            if filename:
                image_paths.append(f"1{songbook_id:04d}/{filename}")
                used_pages.add(n)
            else:
                missing_pages.append((rec["title"], n))

        if not image_paths:
            continue

        if rec["author"] == "-":
            missing_authors.append(rec["title"])

        song_entries.append({
            "title": rec["title"],
            "author": rec["author"],
            "image_paths": image_paths
        })

    unused_pages = [f for n, f in pages_by_number.items() if n not in used_pages]

    result = {
        "title": f"Můj zpěvník č.{songbook_id}",
        "first_page_side": "right",
        **covers,
        "intros": [f"1{songbook_id:04d}/{f}" for f in intros],
        "outros": [f"1{songbook_id:04d}/{f}" for f in outros],
        "pages": song_entries
    }

    return result, missing_pages, missing_authors, unused_pages

def main():
    records = parse_excel()

    total_missing_pages = []
    total_missing_authors = []
    total_unused = []

    for sid in range(1, 30):
        folder = os.path.join(SONGBOOKS_PATH, f"1{sid:04d}")
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