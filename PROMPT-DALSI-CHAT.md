# Prompt pro nové chatovací okno

---

Pracuješ na projektu **digitální zpěvník** v `/Users/aleschudarek/digitalni-zpevnik`.
Flask + SQLite, prohlížení a stahování zpěvníků, nasazené na https://digizpevnik.cz.
**Odpovídej česky.**

## Jak pracovat (vzniklo to z chyb, ber to vážně)

- **Nikdy nečti ani nevyráběj obrázky, dokud o to Aleš výslovně nepožádá.** Ani „jen
  jeden screenshot na ověření". Jeden načtený obrázek stojí násobně víc tokenů než celé
  kolo měření čísly; v minulém sezení to spolykalo čtvrtinu rozpočtu. Když je opravdu
  potřeba, aby se někdo podíval očima, **napiš Alešovi, ať se podívá, a kam** — nedívej
  se sám.
- **Nediagnostikuj UI z kódu, měř ho** přes Playwright a **čísly**:
  `getBoundingClientRect`, spočtené styly, `elementFromPoint`, spočítaný kontrast.
  Vzory: `backend/scripts/test_mobil.py`, `test_napovedy.py`, `test_kontrast.py`,
  `measure_reader.py`, `measure_ostrost.py`.
- **Měř to, na co se ptáš.** Rámeček `<h2>` je blok přes celou šířku, takže se
  s tlačítkem vpravo nahoře překrývá vždycky — nahlásil jsem kvůli tomu neexistující
  chybu. Na šířku samotného textu je `Range.getBoundingClientRect()`.
- **Neřeš do hloubky věc, kterou si Aleš zvládne udělat sám.** U drobnosti typu
  „přehoď pár barev v motivu" stačí říct, kde ta místa jsou. Velký audit si napřed
  nech schválit.
- **Past, která v tomhle projektu zabrala šestkrát:** vlastní CSS `display:` přebíjí
  atribut `hidden`. Ke každému pravidlu, které nastavuje `display`, patří
  i `[hidden] { display: none; }`.
- **Když najdeš mrtvý kód, řekni o tom a nabídni smazání.** Aleš to sám nevidí. Dolož,
  že je to opravdu mrtvé, a udělej z toho samostatný commit.
- **Když vidíš zbytečně složitý kód, navrhni zjednodušení** — Aleš vibecoduje a výslovně
  o ně stojí. Dej poctivé pro a proti, rozhodne sám.
- **Hlášky pro uživatele piš věcně.** Popiš, co se stane; žádné komentáře k tomu, co
  uživatel čeká nebo co mu vadí.
- **Commity česky**, popiš *proč*, ne jen co. **Nepřidávej Co-Authored-By** ani jinou
  atribuci Claude — Aleš chce být jediný autor.
- **Nezabíjej běžící pomalý proces kvůli rychlejší cestě, která potřebuje víc pokusů.**
- **Nasazení:** `git push` → na serveru `git pull` → `sudo systemctl restart
  gunicorn-zpevnik.service`. SSH: `ssh -i ssh-key-2025-11-06.key ubuntu@92.5.116.155`.
  Dokud to nenasadíš, **Aleš tvoje změny nevidí** — na to si dej pozor, už jednou
  čekal na výsledek, který ležel jen na disku. Lokálně si to pustí
  `.venv/bin/python -m flask --app backend.app run` → http://127.0.0.1:5000.
  Před zásahem do dat zálohuj DB přes `sqlite3 conn.backup()`, ne `cp`.
  **Nikdy nepouštěj nic, co maže databázi.**
- **Přejímací testy:** `test_export.py` (backend exportu, 79 kontrol),
  `test_export_ui.py` (okno stahování, 101), `test_napovedy.py` (nápovědy, 40),
  `test_mobil.py` (úzká obrazovka a hlavičky, 32), `test_kontrast.py` (kontrast ve všech
  dvanácti motivech), `kontrola_zpevniku.py` (čtečka vs. export), `kontrola_exportu.py`
  (co leží v cache). Po zásahu do modelů pouštěj kontrola_zpevniku **i** test_export.
  Pozor: `test_export.py` před během vymaže `data/exports`, takže pak lokálně uvidíš
  u stahování „Připravit" místo „Stáhnout" — není to chyba, jen prázdná cache.

## Stav projektu

**`TODO.md` je aktuální a udržovaný — čti ho, je to hlavní zdroj kontextu**
a odškrtávej v něm. Na jeho konci je sekce **„Rucne pridane"** — Alešova schránka na
nálezy mezi chaty. Když tam něco je, **přeřaď to do správné sekce** přepsané do stylu
zbytku souboru a schránku vyprázdni; **neřeš to hned**, jen to zařaď.

Stručně to podstatné:

- **Úložiště obrázků.** Jediný kořen `data/images`, v cestě nic měnitelného:
  `verejne/songbooks/<id>/covers/front-out.png`, `verejne/pages/000123.png`,
  `uzivatele/<user_id>/…`. Popsáno v `docs/ukladani-obrazku.md`. Identitou obrázku je
  řádek v tabulce `images`, model se jmenuje **`Obrazek`** (ne `Image`! jako `Image`
  přebíjel PIL a tiše rozbil export i zmenšování uploadů).
- **Čtečka.** Zmenšené strany (WebP 1100 px), originál až při přiblížení
  (`backend/nahledy.py`, warm přes `flask nahledy-warm`).
- **Práva a kvóty.** `smi_videt_obrazek` u obrázků i `/strana/`, odepření vrací 404.
  Kvóta `MAX_USER_STORAGE_MB` (300 MB) s ukazatelem v panelu účtu.
- **Stahování** stojí na receptu místo zadrátovaných variant (`normalizuj_recept`,
  `token_receptu`, `recept_z_parametru` v `app.py`). Jedno sdílené okno pro všechna tři
  místa: `frontend/static/js/stahovani.js` + `css/stahovani.css`. Cache klíčovaná
  obsahem, LRU úklid, `MAX_CONCURRENT_EXPORTS` 1, `MAX_EXPORT_BUILDS_PER_DAY` 20.
- **Nápovědy jsou sjednocené** do `static/js/napoveda.js` + `css/napoveda.css`, načtené
  v `dashboard_base`. Dva druhy: `data-napoveda="text"` na ikoně nebo tlačítku
  a `<button class="napoveda-znak" data-napoveda="…">` u slovních voleb. Delší HTML jde
  do `.napoveda-obsah` uvnitř `.napoveda-kotva`. Bylo to šest různých implementací.
- **Mobilní lišta** srovnaná: menu pod hamburgerem překrývá obsah (nestrká s ním),
  paleta vlevo a účet vpravo, spouštěčem účtu je jeho e-mail, panely se otevírají
  od kraje, u kterého jejich tlačítko sedí.
- **HTML se neservíruje z cache** (`after_request` dává `no-cache`). Bez toho prohlížeč
  po nasazení držel starou stránku a s ní i starou adresu skriptů.
- **Barvy motivů.** Kontrakt: `--muted-bg` je plocha uvnitř oken (světlá),
  `--brand-dark` = `--muted-text` je text na ní (tmavý). `--panel-*` je něco jiného —
  opravdu tmavé plochy (okno stahování, bubliny nápověd). Motiv Půlnoc tenhle kontrakt
  porušoval a je opravený.
- **Provoz.** Oracle free tier, 2 jádra, 979 MB RAM bez swapu, 38 GB volných.
  Doména + HTTPS přes Caddy, e-maily přes Brevo, denní zálohy na Mac.

### Nenasazeno

Poslední commit (**motiv Půlnoc**) je zacommitovaný, ale **ne nasazený**. Zeptej se
Aleše, jestli to má jít ven, než začneš něco dalšího.

## Úkol: návrh, ne kód

Aleš chce **promyslet a probrat návrh**, ne hned psát. Jde o dvě věci, které spolu
musí ladit, protože dělají totéž:

### 1. Zakládání zpěvníku a přidávání stran je rozsekané

Alešovými slovy: *„Když chci udělat nový zpěvník, dovolí mi to vyrobit jen obálku a až
pak můžu upravovat a přidávat písničky. A přidávání stránek je taky hrozné. Máme zvlášť
tlačítka pro přidávání prázdných stran, nepísničkových stran, stran s písní atd… přitom
vlastně děláme furt to samé."*

Chtěná podoba: **jedno univerzální „Přidat"**, které založí stranu, a teprve pak se
určí, co na ní je:
- prázdno
- nepísničkový obsah (jen obrázek)
- písnička — a rovnou se vyplní
- **více písní na jedné straně** — definovat hned všechny
- **píseň delší než jedna strana** — rovnou napojit a přidat tím víc stran
  (napojování už dnes funguje, viz TODO sekce Editor)

### 2. Import zpěvníku z PDF nebo ZIP

*„Tam máme rovnou strany a stejně bychom museli jen definovat, co na stranách je/není.
Takže to musíme udělat jednotně, aby to byly podobné přístupy."*

Nahraje se PDF nebo zazipované obrázky, rozloží se strana po straně, zobrazí se seznam
stran s náhledy a u každé se určí, co na ní je — **tímtéž rozhraním jako u ručního
přidávání**. Jedním uložením se to zkontroluje a založí.

Detaily obou v `TODO.md`, sekce **„Editor zpěvníku"** a **„Import zpěvníku z PDF nebo
ZIP"**.

### Co si předtím nastudovat

- **Editor, který se opravdu otevírá, je `#new-book-modal`** (tentýž jako „Nový
  zpěvník", dostavěný funkcí `ensureEditSection`), ne `#edit-book-modal`.
  `my_songbooks.html` má totiž za `{% endblock %}` **346 řádků JavaScriptu, které Jinja
  zahodí** — je v nich celý starší editor nad `#edit-book-modal` a nikdy se nespustí.
  K tomu se zbytečně vykresluje jeho markup a CSS (~175 řádků) uvnitř bloku. Viz
  `TODO.md`, sekce **„Mrtvý kód"**. Než začneš editor přepisovat, **tohle ukliď první** —
  jinak budeš upravovat kód, který nikdo nevidí.
- `build_songbook_export_sequence` v `app.py` je jediné místo, kde export zjišťuje,
  odkud strany pocházejí. Pokud by import měl do PDF vkládat původní stranu místo
  obrázku, patří to tam a renderer se měnit nemusí.
- Import má ukládat podle `docs/ukladani-obrazku.md`, ne dělat čtvrté schéma.

### Co ještě visí (ne teď, ale ať o tom víš)

- **Tabulka písní v editoru je na telefonu useknutá** — poslední sloupec „Odebrat".
  Změřeno, podrobnosti v `TODO.md`. Souvisí s tímhle úkolem: když se bude editor
  předělávat, dá smysl to vyřešit při tom.
- Bílý text v liště je pod normou kontrastu v devíti motivech (2,4–3,3 : 1).
- Samootáčecí tlačítko pro přepínání módů čtečky.

**Začni tím, že si přečteš `TODO.md`**, podíváš se na dnešní editor, a pak to
s Alešem probereš — návrh dřív než kód.
