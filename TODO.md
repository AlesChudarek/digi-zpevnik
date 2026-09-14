# TODO

Značky: `[ ]` nehotové, `[X]` hotové, `(?)` nejistý nebo neověřený zápis.

## Editor zpěvníku

- [X] "odstranit píseň" do editoru zpěvníku
- [X] v editoru číslo strany na střed
- [X] v editoru pozice tlačítka uložit
- [X] přidávání blank page (non-song) mezi vlastní písničky
- [X] přidávání vlastní písně v edit songbook "+ přidat píseň" s dalším vyskakovacím oknem
- [X] funkcionalita tlačítek odstranit ze zpěvníku
- [X] sjednotit tlačítka na odstraňování a přidávání písniček
- [X] divné jméno přidané blank page
- [X] odebrat možnost "Přidat obálku" - bude vždy
- [X] opravit nový zpěvník = edit zpěvník
- [X] Nový zpěvník / upravit zpěvník: kliknu uložit a zůstane okno s novým zpěvníkem otevřené
- [ ] Tlačítko uložit při editaci se rozsvítí, když se udělá úprava
- [ ] zjednodušit přidání písničky / non-písničky / prázdné strany na "přidat stránku" a pak
      specifikovat, co na ní bude: prázdná, jen obrázek, písnička, více písniček, nebo
      napojení poslední písně na další strany
- [ ] číslo strany ve spodním čtverečku je pouze pro písničky, pro non-song se nedá překliknout
- [ ] (?) přidat písničku → nový zpěvník: nepřidá písničku, ale vytvoří prázdný zpěvník

## Import zpěvníku z PDF nebo ZIP

- [ ] **založit zpěvník importem místo strana po straně.** Při vytváření nového zpěvníku
      (soukromého, nebo veřejného adminem) půjde vedle dnešní cesty „vlož obrázek, přidej
      píseň, opakuj" zvolit **Vložit PDF/ZIP**:
      - nahraje se PDF nebo zazipované obrázky a rozloží se strana po straně
      - zobrazí se seznam všech stran pod sebou s náhledy
      - u každé strany se určí, co na ní je: obálka, píseň, pokračování předchozí písně,
        prázdná strana… (přesné role doladíme)
      - rovnou se u nich vyplní názvy a autoři písniček
      - jedním uložením se to zkontroluje a založí jako nový zpěvník
- [ ] Import by měl rovnou ukládat podle standardu z
      [docs/ukladani-obrazku.md](docs/ukladani-obrazku.md), ne dělat čtvrté schéma. Souvisí
      to s úkolem „zjednodušit úložiště" níž — dává smysl mít úložiště hotové dřív.
- [ ] Poznámka do začátku: `build_songbook_export_sequence`
      ([app.py:1971](backend/app.py#L1971)) je jediné místo, kde export zjišťuje, odkud
      strany pocházejí. Pokud budeme chtít do PDF vkládat původní stranu místo obrázku,
      patří to tam a renderer se měnit nemusí.

## Písničky a obsah

- [X] "odstranit píseň" do TOC u private písně
- [X] při "Zrušit" v TOC odstranit píseň zavře celé TOC
- [X] v obsahu zpěvníku je ikona pro odstranění tučně
- [X] při vytvoření nové písničky tlačítko uložit píseň zavře celé editační okno
- [X] uložit novou píseň, pak ji smazat, pak přidat další - problém
- [X] konzistentnější ukládání při úpravě zpěvníku (odeberu písničky, pak přidám novou,
      odebrané se vrátí)
- [X] přidání nové písničky neukládá hned, ale počká na "uložit" v rámci zpěvníku
- [X] zobrazit dvoustranu z písničky přes hledat = skoč na titulku
- [X] při hledání písničky by zašedlé ikony neměly dělat nic

## Čtečka

- [X] zvýraznit režim, ve kterém jsem, nebo jedna ikona, co se mění
- [X] každá stránka po načtení sjede o pár pixelů dolů
- [X] ukazatel rychlosti je přilepený k ostatním tlačítkům: mizí s nimi a jejich schování
      ho zároveň zabalí, takže se vrací vždy bez slideru
- [X] na zastavených hodinkách se najetím nic nerozbalí; rozbalí až spuštění, najetí za
      běhu, nebo podržení tlačítka (jediná cesta na dotykové obrazovce)
- [ ] opravit samootáčecí tlačítko pro přepínání módů prohlížení zpěvníku

## Sdílení a uživatelé

- [X] přidat ikonu sdíleného zpěvníku ukazující seznam lidí s přístupem
- [X] přidávat písničku ze search seznamu můžu i do sdílených zpěvníků
- [X] při sdílení zpěvníku máme upozornění, že uživatel může zpěvník smazat, což není pravda
- [X] popup "sdíleno s" je pod obrázky zpěvníku
- [X] po sdílení uživateli okno zmizí, místo aby řeklo, jestli se podařilo

## Data, obrázky a export

- [X] měnit velikost obrázků při nahrání, aby nebyly příliš velké - sekalo se načítání
- [X] odstraňovat obrázky po změně coveru
- [X] denní zálohy dat ze serveru na Mac (`scripts/zaloha-ze-serveru.sh`, launchd)
- [X] **úklid obrázků**: prázdná alfa pryč, šedé skeny na 8bit L. `data/public` 764 → 491 MB
- [X] **průhledné obálky**: 27 z 30 veřejných zpěvníků má barvu měnitelnou
- [X] barva zpěvníku 00005 opravena na `#c69c7c` (v DB byla bílá)
- [ ] **detekce barvy zpěvníku bere rohy z `coverfrontin`**
      ([generate_public_seed.py:180](backend/scripts/generate_public_seed.py#L180),
      [seed_db.py:119](backend/scripts/seed_db.py#L119)), má brát `coverfrontout` — ten mají
      všichni. Kvůli tomu měl 00005 v DB bílou místo hnědé. Audit zbytku: 27 barev sedí,
      u 00020 nejdou rohy použít (grafika až do krajů).
- [X] barva zpěvníku se propisuje do PDF (`_flatten_to_rgb` skládala alfu natvrdo na bílou)
- [X] export doplní chybějící stranu obálky místo aby ji vynechal, stejně jako čtečka
- [X] nasadit úklid na server
- [X] nasadit lepší rozlišení z archivu: 4 průhledné obálky (00011, 00015), 3 obálky bez
      průhlednosti (00016, 00026) a 36 vnitřních stran
- [ ] dodat průhlednou přední obálku, kde chybí: 00016, 00020, 00026 — jediné tři, kterým
      barvu měnit nejde
- [ ] 00022: v `~/Downloads/zpevnik-00022-originaly/` leží originály 1748×2480, ze kterých
      by šlo udělat průhlednou variantu lepší než dnešní 1072×1522
- [X] aplikace maže staré obrázky stran (`smaz_osirele_obrazky`) — smaže se jen soubor,
      na který už neukazuje žádný `SongImage` ani sloupec obálky
- [X] 00016 dostal průhledné obálky, prázdné vnitřní odebrány
- [X] náhledy obálek v přehledech (`backend/nahledy.py`, CLI `flask nahledy-warm`).
      Naměřeno na serveru po nasazení: třicet obálek **24,1 MB → 2,89 MB, tedy 8,3x
      méně**. Pozor, komentář v `nahledy.py` uvádí 0,7 MB a 34x — to nejspíš není
      celek, ale jen obálky, které se stihnou načíst nad ohybem při `loading="lazy"`.
      Neověřeno, chce to změřit v prohlížeči, než se to číslo bude někde opakovat.
- [ ] **náhledové verze stran do čtečky.** Změřeno: strana váží v průměru **878 kB** a
      jeden zpěvník **26 MB**. Čtečka dostává plné 1748×2480 a prohlížeč to zmenšuje sám,
      takže se na telefonu u táboráku tahají megabajty na každé otočení stránky. Pozor:
      tady plné rozlišení není plýtvání, ale funkce — zoomuje se na akordy. Správný tvar
      je posílat menší variantu a plnou dotáhnout až při přiblížení, ne ji zahodit.
- [ ] **zjednodušit úložiště obrázků a jeho strukturu.** Původní zpěvníky drží formát
      `<id>/page7.png`, ale přidávání a odebírání písniček, prázdné strany a změny pořadí
      to postupně rozbily: dnes žijí vedle sebe tři schémata, jméno souboru už neodpovídá
      číslu strany a obrázek leží pod složkou toho zpěvníku, ze kterého náhodou pochází.
      Cílový tvar je popsaný v [docs/ukladani-obrazku.md](docs/ukladani-obrazku.md) —
      obálka patří zpěvníku, strana patří písni, jména nikdy z uploadu. Chce to migraci
      833 obrázků layoutu A i s přepisem cest v DB. Netýká se to jen složek: chce to projít
      i práci s daty v databázi, ať to je postavené, a ne poskládané od boku.
- [ ] `detach_song_from_songbook` v app.py se nikde nevolá, logiku má zdvojenou
      s DELETE endpointem — buď zapojit, nebo smazat
- [ ] u 00010 zbyly čtyři nepoužité `T` soubory (jeho obálky jsou průhledné pod jinými jmény,
      `output-onlinepngtools*.png`) — buď smazat, nebo přejmenovat podle standardu
- [X] velký check čtečka vs. PDF — `backend/scripts/kontrola_zpevniku.py`, pouští se kdykoliv
- [ ] ZIP balí originály, takže po povýšení obálek v něm budou průhledné PNG bez barvy
- [ ] `data/exports`: předgenerovaná je jen varianta `small`, na `high` se čeká
- [ ] **předělat okno pro stažení zpěvníku.** Dnes nabídne tři možnosti a hotovo. Místo
      toho otevřít celé okno, kde si uživatel nastaví, co má stažený zpěvník obsahovat:
      jestli chce obálku zvlášť, jestli ji chce vůbec, jestli mají být prázdné strany,
      a v jakém formátu to chce.

## Mobil a vzhled

- [ ] **projít UI na mobilech celkově**, hlavně úpravu zpěvníku a zakládání nového
- [ ] na telefonu okno pro úpravu zpěvníku vypadá strašně, tabulka se dá posunout doprava,
      ale je useklá
- [ ] noční režim má hrozně tmavou barvu v editoru zpěvníku pro "Uložit" a nahranou fotku coveru

## Uživatelé

- [ ] **využít role pro zpřístupnění funkcí.** Role už v aplikaci jsou (`admin`, `user`,
      `guest`, viz `is_admin` v [app.py:193](backend/app.py#L193)), ale rozhodují jen o pár
      místech. Chce to promyslet, co má která role smět.
- [X] vybraný motiv se pamatuje u účtu, ne jen v prohlížeči
- [ ] další uživatelská nastavení, až budou (motiv už hotový)

## Obsah a prezentace

- [ ] odkaz a lepší propagace organizace Handicap, případně i fotky

## Provoz a bezpečnost

- [X] nasadit na Oracle
- [X] doména digizpevnik.cz a HTTPS (Caddy + Let's Encrypt, obnova sama)
- [X] ověření e-mailu při registraci; stávající účty označeny za ověřené
- [X] omezení počtu pokusů o přihlášení, obnova hesla, ztvrzené session cookies
- [X] odesílání e-mailů přes Brevo (SPF, DKIM, DMARC ověřené, mail-tester 8,9/10)
- [X] měsíční kontrolní e-mail se stavem (uživatelé, zpěvníky, místo, stáří zálohy) —
      drží živý klíč u Brevo a jeho nepřítomnost sama o sobě něco znamená
      (`scripts/mesicni-hlaseni.sh`, spouští cron na serveru — **ověřit, že ten záznam
      v cronu na serveru opravdu je**)
- [ ] **omezit, kolik toho jeden účet nahraje.** Dnes žádný strop není. Jeden obrázek smí
      2 MB, ale počet nikdo nehlídá — při 38 GB volných to je asi 19 000 obrázků do
      zaplnění disku. Ověření e-mailu to ztížilo, ale nezavřelo.
- [ ] bacha na attack stylem "vytvořím tisíc zpěvníků s nepěkným obrázkem, sdílím je
      s někým a pak si je smažu" (zaplním mu schránku bordelem)
- [ ] **lepší hosting kvůli rychlosti odezvy.** Dnešní Oracle free tier má 1 GB RAM a
      jedno slabé jádro; skládání PDF na něm trvá 12-25 s na zpěvník.
- [X] selhání zálohy se hlásí nahlas, ne jen do logu; po úspěchu se zapisuje značka,
      kterou měsíční přehled čte a při stáří nad tři dny upozorní
- [ ] **offline režim** (zatím jen nápad, nepracujeme na tom). Hláška „jsi offline" je
      triviální. Skutečné čtení bez připojení chce service worker a naráží na velikost:
      jeden zpěvník má 15-25 MB, všechny 474 MB, a prohlížeče dávají webu jen část disku.
      Reálná podoba je „stáhnout tenhle zpěvník do zařízení" jako vědomá volba u
      konkrétního zpěvníku. Háček: offline se uživatel nepřihlásí, takže by čtečka musela
      umět běžet z cache bez ověření session. Výrazně by tomu pomohly náhledové verze.
