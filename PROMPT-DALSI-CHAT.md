# Prompt pro nové chatovací okno

---

Pracuješ na projektu **digitální zpěvník** v `/Users/aleschudarek/digitalni-zpevnik`.
Je to Flask + SQLite aplikace na prohlížení a stahování zpěvníků, nasazená na
https://digizpevnik.cz. Odpovídej česky.

## Jak pracovat (důležité, vzniklo to z chyb)

- **Nikdy nediagnostikuj UI z kódu.** Vždycky změř v prohlížeči přes Playwright.
  Vzory najdeš v `backend/scripts/measure_ostrost.py`, `measure_reader.py`,
  `screenshot_ui.py`. Preferuj čísla (`getBoundingClientRect`, spočtené styly,
  `elementFromPoint`) před screenshoty — obrázky žerou kontext a číslo otázku vyřeší.
- **Past, která v tomhle projektu už třikrát zabrala:** vlastní CSS `display:` přebíjí
  atribut `hidden`. Skrytý prvek pak zůstane vidět nebo pohlcuje kliknutí. Ke každému
  pravidlu, které nastavuje `display`, patří i `[hidden] { display: none; }`.
- **Commity česky**, popiš *proč*, ne jen co. **Nepřidávej Co-Authored-By** ani jinou
  atribuci Claude — Aleš chce být jediný autor.
- **Nasazení:** `git push` → na serveru `git pull` → `sudo systemctl restart
  gunicorn-zpevnik.service`. SSH: `ssh -i ssh-key-2025-11-06.key ubuntu@92.5.116.155`.
  Před zásahem do dat udělej zálohu DB přes `sqlite3 conn.backup()`, ne `cp`.
  **Nikdy nepouštěj nic, co maže databázi.**
- **Přejímací testy:** `backend/scripts/kontrola_zpevniku.py` (čtečka vs. export) a
  **`backend/scripts/test_export.py`** (skutečné vykreslení PDF/ZIP proti běžícímu
  serveru). Po zásahu do modelů pouštěj **oba** — kontrola_zpevniku sekvenci staví sama
  a PDF nevykresluje, takže sama neodhalí, že je export rozbitý. Jednou se to už stalo.
- **Když vidíš zbytečně složitý kód, navrhni zjednodušení** — Aleš vibecoduje a tyhle
  příležitosti sám nevidí, výslovně o ně stojí. Dej i poctivé pro a proti, rozhodne sám.
- **Nezabíjej běžící pomalý proces kvůli rychlejší cestě, která potřebuje víc pokusů.**
  Když tě napadne lepší postup, rozvíjej ho vedle a přepni, až bude ověřený.

## Co je v projektu hotové (stav k 21. 9. 2026)

Poslední týden proběhly velké zásahy, všechny nasazené a ověřené:

- **Struktura úložiště.** Jediný kořen `data/images`:
  `verejne/songbooks/<id>/covers/front-out.png` pro obálky (role: `front|back` ×
  `out|in`), `verejne/pages/000123.png` pro strany, `uzivatele/<user_id>/…` pro
  soukromé. V cestě **není nic měnitelného** — žádný název zpěvníku, e-mail ani číslo
  strany. Strany jsou ploché schválně: vztah písně a strany je many-to-many na obě
  strany (32 písní přes víc stran, 18 stran se dvěma písněmi, 70 písní ve dvou
  zpěvnících), takže strana nepatří ani písni, ani zpěvníku. Popsáno v
  `docs/ukladani-obrazku.md`.
- **Tabulka `images`.** Identitou obrázku je řádek, ne řetězec s cestou. Model se
  jmenuje **`Obrazek`** (ne `Image`! jako `Image` přebíjel PIL a tiše rozbil celý export
  i zmenšování uploadů nad 2 MB). Kód dál pracuje s `image_path` a `img_path_cover_*`
  jako s textem — drží to vlastnost `cesta_obrazku` v `models.py`.
- **Zmenšené strany do čtečky.** WebP 1100 px přes `/strana/<klic>/<cesta>.webp`,
  originál se dotáhne až při přiblížení (`zajistiOstrost` v `songbook_view.html`).
  Náhledy řeší `backend/nahledy.py`, warm přes `flask nahledy-warm`.
- **Kvóta na účet** `MAX_USER_STORAGE_MB`, výchozí 300 MB, hlídaná v
  `_save_image_with_limit`. V panelu účtu (`dashboard_base.html`) je ukazatel zabraného
  místa s barevným upozorněním od 50 % a 85 %.
- **Kontrola práv u obrázků.** `serve_songbook_image` i `/strana/` se ptají
  `smi_videt_obrazek`: vidí uživatel aspoň jeden zpěvník, ve kterém ten obrázek je?
  Odepření vrací 404, ne 403 (cesty jsou očíslované, 403 by potvrdilo existenci).
- **Exporty.** Cache v `data/exports`, strop 500 MB, **exporty veřejných zpěvníků se
  z cache nikdy nevyhazují**. Skládání běží ve vlákně `daemon=False`, takže pokračuje,
  i když uživatel odejde ze stránky. Postup se píše do zámku, protože workerů jsou
  čtyři a nesdílejí paměť.

`TODO.md` je aktuální a udržovaný — čti ho a odškrtávej v něm.

## Úkol

Minule vzniklo **okno se skládáním souboru** ve čtečce (`songbook_view.html`,
`#stahovani-okno`, funkce `startDownload`, `zobrazStahovani`, `vykresliPostup`,
`oznacHotoveVarianty`). Je v rozbitém stavu, oprav to a dodělej:

**1. Značka „✓ hned" svítí u všech variant, i u nepředgenerovaných.**
Příčinu už mám diagnostikovanou, ověř si ji a oprav: v `songbook_view.html` je pravidlo
`.download-menu span { display: block; }`, které přebíjí atribut `hidden` na
`<span class="hotovo-znak" hidden>`. Značka je proto vidět vždycky, bez ohledu na to, co
vrátí `/songbook/<id>/export-hotove`. (Ten endpoint sám funguje správně — ověřeno.)
Je to tatáž past jako u `.stahovani-zaclona`, kde už `[hidden]` guard je.

**2. Rozvržení okna je rozbité.** Popis a tlačítko „Zavřít" jsou divně posunuté.
Podívej se na `.stahovani-panel`, `.stahovani-co` a `.stahovani-tlacitka` — nejspíš do
nich zasahují pravidla pro `.mode-buttons button` nebo `.download-menu span/strong`,
protože okno leží uvnitř toho stromu. Změř to, nehádej.

**3. Po dokončení okno mizí.** Teď se po stažení zavře samo
(`setTimeout(zavriStahovani, 1500)`). Aleš chce, aby zůstalo otevřené, když ho uživatel
sám nezavřel, a ukázalo stav **„Dokončeno"** s jednoduchým tlačítkem Zavřít.

**4. Přidat totéž do dalších dvou míst.** Stahovat jde i z „Moje zpěvníky"
(`my_songbooks.html`) a admin i z editoru veřejných zpěvníků. Tam je pořád starý výpis,
který ukazuje jen uběhlé sekundy. Sjednoť to na tohle okno — logika se nejspíš vyplatí
vytáhnout do sdíleného souboru místo kopírování do tří šablon.

## Na co narazíš

- Okno se **neotevře u variant, které už leží v cache** — stáhne se rovnou. To je
  správně. Na serveru je `PDF, menší soubor` předgenerované pro všech 31 veřejných
  zpěvníků, takže na testování průběhu použij **`PDF, plné rozlišení`**, které
  předgenerované není.
- Cache exportů se mezi testy hromadí a maskuje, co měříš. `data/exports` je odvozená,
  klidně ji před testem vyprázdni.
- Stránky aplikace **neposílají žádné `Cache-Control`**, takže prohlížeč může po
  nasazení servírovat staré HTML. Při ověřování dělej tvrdý refresh. Stojí za zvážení
  to opravit (`no-cache` na HTML odpovědi) — Aleš o tom ví, ale ještě to nezadal.
