# Prompt pro nové chatovací okno

---

Pracuješ na projektu **digitální zpěvník** v `/Users/aleschudarek/digitalni-zpevnik`.
Je to Flask + SQLite aplikace na prohlížení a stahování zpěvníků, nasazená na
https://digizpevnik.cz. Odpovídej česky.

## Jak pracovat (důležité, vzniklo to z chyb)

- **Nikdy nediagnostikuj UI z kódu.** Vždycky změř v prohlížeči přes Playwright.
  Vzory najdeš v `backend/scripts/measure_ostrost.py`, `measure_reader.py`,
  `screenshot_ui.py`, `test_export_ui.py`. Preferuj čísla (`getBoundingClientRect`,
  spočtené styly, `elementFromPoint`) před screenshoty — obrázky žerou kontext a číslo
  otázku vyřeší. Za poslední kolo to odhalilo mimo jiné: nápovědu zalomenou do 329 px
  místo 86, mezeru 0 px mezi tlačítky, tlačítko schované pod okrajem obrazovky na
  telefonu na šířku a řádek voleb lámající se dvě plus jedna.
- **Past, která v tomhle projektu už pětkrát zabrala:** vlastní CSS `display:` přebíjí
  atribut `hidden`. Skrytý prvek pak zůstane vidět nebo pohlcuje kliknutí. Ke každému
  pravidlu, které nastavuje `display`, patří i `[hidden] { display: none; }`.
- **Když najdeš mrtvý kód nebo opuštěnou funkcionalitu, řekni o tom a nabídni smazání.**
  Aleš to sám nevidí. Dolož, že je to opravdu mrtvé (kdo to volá, kolik řádků v DB,
  vede na to UI?) a udělej z toho samostatný commit.
- **Když vidíš zbytečně složitý kód, navrhni zjednodušení** — Aleš vibecoduje a tyhle
  příležitosti sám nevidí, výslovně o ně stojí. Dej i poctivé pro a proti, rozhodne sám.
- **Hlášky pro uživatele piš věcně.** Žádné komentáře k tomu, co uživatel čeká nebo
  co mu vadí („nevyjde tam, kde je čekáš", „na displeji to nevadí"). Popiš, co se stane.
- **Commity česky**, popiš *proč*, ne jen co. **Nepřidávej Co-Authored-By** ani jinou
  atribuci Claude — Aleš chce být jediný autor.
- **Nasazení:** `git push` → na serveru `git pull` → `sudo systemctl restart
  gunicorn-zpevnik.service`. SSH: `ssh -i ssh-key-2025-11-06.key ubuntu@92.5.116.155`.
  Před zásahem do dat udělej zálohu DB přes `sqlite3 conn.backup()`, ne `cp`.
  **Nikdy nepouštěj nic, co maže databázi.**
- **Přejímací testy:** `backend/scripts/kontrola_zpevniku.py` (čtečka vs. export),
  **`backend/scripts/test_export.py`** (backend exportu, 79 kontrol),
  **`backend/scripts/test_export_ui.py`** (okno stahování v prohlížeči, 101 kontrol)
  a `backend/scripts/kontrola_exportu.py` (co leží v cache; `--overit-obsah` soubory
  i otevře). Po zásahu do modelů pouštěj kontrola_zpevniku **i** test_export —
  kontrola_zpevniku sekvenci staví sama a PDF nevykresluje, takže sama neodhalí, že je
  export rozbitý. Jednou se to už stalo.
- **Pozor: `test_export.py` před během vymaže `data/exports`.** Když pak lokálně
  uvidíš u stahování „Připravit" místo „Stáhnout", není to chyba — cache je prázdná.
  Na serveru se to ověřuje `kontrola_exportu.py`.
- **Nezabíjej běžící pomalý proces kvůli rychlejší cestě, která potřebuje víc pokusů.**
  Když tě napadne lepší postup, rozvíjej ho vedle a přepni, až bude ověřený.
- **Stránky neposílají `Cache-Control`**, takže prohlížeč po nasazení servíruje staré
  HTML. Při ověřování dělej tvrdý refresh. Stojí za zvážení to opravit (`no-cache` na
  HTML odpovědi) — Aleš o tom ví, ale ještě to nezadal.

## Co je v projektu hotové (stav k 23. 9. 2026)

`TODO.md` je aktuální a udržovaný — **čti ho, je to hlavní zdroj kontextu** a odškrtávej
v něm. Stručně to podstatné:

- **Úložiště obrázků.** Jediný kořen `data/images`, v cestě není nic měnitelného:
  `verejne/songbooks/<id>/covers/front-out.png`, `verejne/pages/000123.png`,
  `uzivatele/<user_id>/…`. Popsáno v `docs/ukladani-obrazku.md`. Identitou obrázku je
  řádek v tabulce `images`, model se jmenuje **`Obrazek`** (ne `Image`! jako `Image`
  přebíjel PIL a tiše rozbil export i zmenšování uploadů).
- **Čtečka.** Zmenšené strany (WebP 1100 px), originál až při přiblížení. Náhledy řeší
  `backend/nahledy.py`, warm přes `flask nahledy-warm`.
- **Práva a kvóty.** `smi_videt_obrazek` u obrázků i `/strana/`, odepření vrací 404.
  Kvóta na účet `MAX_USER_STORAGE_MB` (300 MB) s ukazatelem v panelu účtu.
- **Stahování je hotové a čerstvě předělané.** Stojí na **receptu** místo tří
  zadrátovaných variant:
  - `normalizuj_recept` / `token_receptu` / `recept_z_parametru` v `app.py`. Předvolby
    `small`, `high`, `orig` jsou jen pojmenované recepty a jejich jména jsou tokeny
    v názvech souborů v cache.
  - Volby: formát (pdf/zip), kvalita, části (vše / jen strany / jen obálka), rozsah
    stran (`2, 4, 6-8, 10-` včetně otevřeného konce), prázdné strany, černobíle,
    brožura (dvě strany na list A4 na šířku v pořadí na sešití).
  - Jedno sdílené okno pro všechna tři místa (čtečka, Moje zpěvníky, správa veřejných):
    `frontend/static/js/stahovani.js` + `css/stahovani.css`. Nastavení i průběh v jednom.
  - Cache je klíčovaná obsahem (`songbook_export_key`), úklid je LRU podle posledního
    stažení, chráněná před vyhazováním je jen předgenerovaná varianta veřejného
    zpěvníku. Strop `EXPORTS_CACHE_LIMIT_MB` (1 GB) počítá jen vyhoditelné soubory.
  - `MAX_CONCURRENT_EXPORTS` je 1 (paměť, ne procesor: server má 979 MB bez swapu)
    a `MAX_EXPORT_BUILDS_PER_DAY` je 20 na účet a den (počítají se jen skutečná
    skládání, ne stažení z cache; adminovi se nepočítá nic, tabulka `export_pokusy`).
  - Předgenerované je `small` pro všechny zpěvníky; `flask export-warm` umí
    `--all`, `--public-only` i `--songbook ID`.
- **Provoz.** Oracle free tier, 2 jádra, 979 MB RAM bez swapu, 38 GB volných.
  Doména + HTTPS přes Caddy, e-maily přes Brevo, denní zálohy na Mac, měsíční
  kontrolní e-mail.

## Úkol

Stahování je hotové včetně denního stropu, takže na řadě je něco jiného.
**Pořadí je domluvené s Alešem:**

1. **Sjednotit nápovědy v UI** (`TODO.md`, sekce „Obsah a prezentace"). Projekt má dnes
   dvě různé implementace tooltipů — `.tooltip-text` ve čtečce a `.tooltip`
   s `data-tooltip` v Mých zpěvnících — a k tomu třetí, novou a nejlepší v okně
   stahování (`.napoveda-znak` + plovoucí `.napoveda-bublina`, umí hover, dotyk,
   klávesnici i rolovatelný obsah). Chtěné jsou dva druhy: klasický hover tooltip pro
   ikony a tlačítka, a ⓘ nápověda u slovních voleb. Udělat jedno sdílené řešení
   a projít s ním zbytek projektu, stejně jako se to udělalo s oknem stahování.
   **Malý, dobře ohraničený úkol s jasným vzorem — dobrý první krok.**

2. **Import zpěvníku z PDF nebo ZIP** (`TODO.md`, sekce „Import"). Největší a nejvíc
   užitečná věc v seznamu: zakládat zpěvník nahráním PDF nebo zazipovaných obrázků
   místo strany po straně. Chce to návrh dřív než kód — Aleš ocení, když se nad tím
   nejdřív zamyslíš a probereš to s ním, než začneš psát.

3. **Samootáčecí tlačítko pro přepínání módů čtečky** (`TODO.md`, sekce „Čtečka“).

4. **Mobilní UI** (`TODO.md`, sekce „Mobil a vzhled"). Editor zpěvníku na telefonu
   „vypadá strašně, tabulka se dá posunout doprava, ale je useklá", noční režim má
   v editoru moc tmavé „Uložit" a nahranou fotku obálky. Chce to změřit
   `screenshot_ui.py` na 390 px a projít to.

**Začni tím, že si přečteš `TODO.md`**, a pak se pusť do nápověd.
