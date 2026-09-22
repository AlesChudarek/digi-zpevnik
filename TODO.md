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

> Odškrtnuté položky níž místy jmenují skripty, které už v repozitáři nejsou. Dosloužené
> nástroje se z gitu odebíraly, ne mazaly z disku — viz `.gitignore` a položka
> „smazat mrtvé skripty".

- [X] měnit velikost obrázků při nahrání, aby nebyly příliš velké - sekalo se načítání
- [X] odstraňovat obrázky po změně coveru
- [X] denní zálohy dat ze serveru na Mac (`scripts/zaloha-ze-serveru.sh`, launchd)
- [X] **úklid obrázků**: prázdná alfa pryč, šedé skeny na 8bit L. `data/public` 764 → 491 MB
- [X] **průhledné obálky**: 27 z 30 veřejných zpěvníků má barvu měnitelnou
- [X] barva zpěvníku 00005 opravena na `#c69c7c` (v DB byla bílá)
- [X] ~~detekce barvy zpěvníku bere rohy z `coverfrontin`~~ — odpadlo se zrušením
      seedování. Barvu dnes zadává admin ve webovém UI, nedetekuje se z obrázku.
      Konkrétní chyba u 00005 byla opravena už dřív ručně.
- [X] barva zpěvníku se propisuje do PDF (`_flatten_to_rgb` skládala alfu natvrdo na bílou)
- [X] export doplní chybějící stranu obálky místo aby ji vynechal, stejně jako čtečka
- [X] nasadit úklid na server
- [X] nasadit lepší rozlišení z archivu: 4 průhledné obálky (00011, 00015), 3 obálky bez
      průhlednosti (00016, 00026) a 36 vnitřních stran
- [ ] **dodat průhledné obálky, kde chybí.** Změřeno 16. 9. 2026
      (`odebrat_prazdne_obalky.py`): barvu nejde měnit u 00026 a 00101. U 00101 mají
      všechny čtyři obálky alfa kanál, ale 0,0 % průhledných pixelů. Tři průhledné
      varianty pro něj leží v `~/Downloads/zpevnik-unikatni-obalky/`; čtvrtá (zadní
      vnitřní) chybí, a bez ní bude barva pořád neměnitelná — aplikace to vyžaduje
      u všech čtyř stran naráz.
- [X] ~~00022: originály v `~/Downloads/zpevnik-00022-originaly/`~~ — složka neexistuje,
      úkol zanikl. Kdyby se originály někdy našly, průhledná varianta 00022 by za to stála.
- [X] aplikace maže staré obrázky stran (`smaz_osirele_obrazky`) — smaže se jen soubor,
      na který už neukazuje žádný `SongImage` ani sloupec obálky
- [X] 00016 dostal průhledné obálky, prázdné vnitřní odebrány
- [X] náhledy obálek v přehledech (`backend/nahledy.py`, CLI `flask nahledy-warm`).
      Naměřeno na serveru po nasazení: třicet obálek **24,1 MB → 2,89 MB, tedy 8,3x
      méně**. Pozor, komentář v `nahledy.py` uvádí 0,7 MB a 34x — to nejspíš není
      celek, ale jen obálky, které se stihnou načíst nad ohybem při `loading="lazy"`.
      Neověřeno, chce to změřit v prohlížeči, než se to číslo bude někde opakovat.
- [X] **náhledové verze stran do čtečky** (`backend/nahledy.py` profil `STRANA`, routa
      `/strana/<klic>/<cesta>`, `flask nahledy-warm`). Čtečka dostane WebP o šířce 1100 px
      a originál si dotáhne teprve při přiblížení — na akordy se zoomuje a tam je plné
      rozlišení funkce, ne plýtvání. Naměřeno na 1005 stranách: **479 MB → 99,8 MB, tedy
      4,8x** (477 → 99 kB na stranu). Zpěvník 00001 v prohlížeči: **15,89 → 2,27 MB, 7x**.
      Ověřeno `backend/scripts/measure_ostrost.py` v Chromiu: běžné čtení netahá originály,
      výměna při zoomu proběhne bez probliknutí (obrázek nikdy nemá nulovou šířku),
      svitek povýší jen strany kolem obrazovky (2 z 26) a přelistování během dotahování
      nedostane cizí stranu do rámečku.
- [X] **předgenerované náhledy stran nasadit na server** — 1093 náhledů, 118 MB.
      Nepočítaly se na serveru, ale vyrobily na Macu a nahrály rsyncem: 20 sekund místo
      desítek minut na jednom jádře. Aby to šlo, musel se klíč přestat počítat
      z nanosekund — ty rsync nepřenese, takže tentýž soubor měl na každém stroji jiný
      klíč. Ověřeno, že po nahrání se na serveru nic nedogenerovalo.
- [X] **mazání zpěvníku nechává za sebou písně** — opraveno. Mazání zpěvníku teď uklidí
      i písně, které po něm nezůstanou v žádném jiném, a těch 12 sirotků smazala migrace
      úložiště. Ověřeno: 0 písní bez zpěvníku, 0 řádků `song_images` bez zpěvníku.
- [X] **zjednodušit úložiště obrázků a jeho strukturu** — lokálně hotovo,
      `backend/scripts/migrace_uloziste.py`, popsáno v
      [docs/ukladani-obrazku.md](docs/ukladani-obrazku.md). Ze čtyř tvarů cest je jeden a
      nenese nic měnitelného: `verejne/songbooks/<id>/covers/front-out.png` pro obálky,
      `verejne/pages/000123.png` pro strany, `uzivatele/<user_id>/…` pro soukromé.
      1093 souborů, 12 sirotků smazáno, struktura stran ve všech 33 zpěvnících totožná,
      `kontrola_zpevniku.py` hlásí jen starý strom, který schválně zůstal ležet.
      Vypadlo tím i to, co existovalo jen kvůli měnitelným cestám: stěhování souborů při
      odebrání písně, při změně vlastníka a celé `rewrite_path`.
- [X] **nasadit nové úložiště na server** — hotovo 15. 9. 2026. Pořadí i úložiště,
      1093 souborů, 12 sirotků pryč, po migraci 1023 stran a 33 čteček vrací 200.
- [X] **smazat starý strom** — na serveru smazáno. Nedrželo se to „až si provoz sedne",
      protože se dalo ověřit rovnou: seřazené seznamy md5 obou stromů jsou identické
      (1093 souborů v každém), v DB nezbyla jediná cesta mimo nový strom a data jsou
      i v záloze na Macu. Seedy (`data/private/seeds`, `data/public/seeds`) padly spolu
      se seedovacími skripty: 170 ze 174 obrázků byly bajtově shodné duplikáty živých dat
      a JSONy byly zastaralé — 9 z 29 se rozcházelo s databází, vždy tak, že DB měla víc
      stran a opravenější barvy. Obnova z nich by práci mazala, ne vracela. Čtyři unikátní
      obálky jsou v zálohách a v `~/Downloads/zpevnik-unikatni-obalky/`.
      V `data/` tak zůstávají jen `images`, `nahledy` a `exports`.
- [X] **odstranit z kódu čtení staré struktury.** `SONGBOOK_IMAGES_DIR` ani
      `PRIVATE_USER_IMAGES_DIR` v kódu nejsou. `_abs_image_path` teď na cestu mimo nový
      tvar vrátí `None` místo aby ji potichu poskládal do něčeho, co nikam nevede, a
      `/songbooks/<stará cesta>` vrací 404. `kontrola_zpevniku.py` hledá osiřelé soubory
      v `data/images`.
- [X] **tabulka `images`** (`backend/scripts/migrace_tabulka_images.py`). Identitou obrázku
      byl text s cestou uložený na šesti místech; teď je to řádek a všude se na něj ukazuje
      cizím klíčem. 1093 řádků nahradilo 1144 odkazů, z toho 51 byly duplicitní řetězce.
      Čtecí kód se nemusel přepisovat — `association_proxy` drží `image_path`
      i `img_path_cover_*` dál jako text, takže se nesáhlo na 227 míst ani na šablony.
- [X] **smazat mrtvé skripty.** Z gitu ven a jen lokálně: čtyři jednorázové migrace,
      `rebuild_private_from_fs.py` (jména souborů dnes nenesou informaci, z disku DB
      složit nejde), `povysit_obalky.py` (hledá `coverXT.png`), `seed_db.py`
      a `generate_public_seed.py` (seedy nikdy neuměly zpěvník založený přes web
      a obrázky ve staré struktuře už nejsou — obnovu dělá denní záloha),
      `init_db.py` (stál na seed_db a mazal přitom DB i `data/private/users`),
      `create_songbook_db.sql` (schéma bez tabulky `images`) a `scripts/uklid-a-nahrat.sh`
      (pracoval se smazaným stromem). Opravené a dál živé: `_obalky.py`,
      `odebrat_prazdne_obalky.py`, `uklid_obrazku.py`, `kontrola_zpevniku.py`.
- [X] **`song_images.poradi`** (`backend/scripts/migrace_poradi_stran.py`). Muselo jít před
      migrací úložiště: pořadí stran vícestránkové písně bylo dané jen pořadím `id` a jedinou
      nezávislou kontrolou bylo číslo v názvu souboru, které nový standard odstraňuje.
      Naplněno z názvů, **u všech 32 písní bez jediného rozporu** proti pořadí podle `id`.
      `poradi` je pořadí v rámci písně, takže unese obojí: píseň přes víc stran (32) i dvě
      písně na jedné straně (18 stran). Ověřeno, že se pořadí stran ve všech 33 zpěvnících
      nezměnilo, že čtečka `poradi` opravdu čte (prohození dvou stran se projeví) a že
      `kontrola_zpevniku.py` projde před i po. Nasazení nemá pořadí: chybějící sloupec si
      aplikace doplní při startu sama.
- [X] **spustit `migrace_poradi_stran.py --zapsat` na serveru** — hotovo, 1035 řádků,
      bez jediného rozporu.
- [X] ~~`detach_song_from_songbook` se nikde nevolá~~ — funkce toho jména už neexistuje;
      šlo o `_handle_song_delete_for_book` a ta se volá. Zdvojení s DELETE endpointem bylo
      skutečné a zaniklo při migraci úložiště: obě větve se smrskly na „odpoj vazbu, a když
      píseň není nikde jinde, smaž ji".
- [X] u 00010 zbyly nepoužité `T` soubory — už tam nejsou, uklidil je `uklid_obrazku.py`.
      Ověřeno smířením DB proti disku: 1093 souborů a na každý něco z DB ukazuje, volně
      ležící soubor není ani jeden.
- [X] velký check čtečka vs. PDF — `backend/scripts/kontrola_zpevniku.py`, pouští se kdykoliv
- [ ] ZIP balí originály, takže po povýšení obálek v něm budou průhledné PNG bez barvy
- [X] **exporty veřejných zpěvníků se z cache nevyhazují** — stažení veřejného zpěvníku
      má být vždycky hned a aktuální. Nahradí je jedině změna v samotném zpěvníku, kdy se
      změní klíč v názvu souboru. Strop se tak vztahuje jen na soukromé exporty.
      Ověřeno: při překročení stropu zůstaly všechny veřejné a odcházely jen soukromé.
- [ ] `data/exports`: předgenerovaná je jen varianta `small`, na `high` se čeká.
      Předgenerovat i `high` by čekání odstranilo úplně, ne jen zkrátilo: 31 veřejných
      zpěvníků × ~8 MB je zhruba 260 MB, takže by se muselo zvednout `EXPORTS_TOTAL_LIMIT_BYTES`
      z 500 MB (volného místa je 38 GB, takže to jde).
- [X] **`export-warm` si nebral zámek.** Webová cesta pak u téhož zpěvníku nenašla ani
      hotový soubor, ani zámek, a spustila druhé skládání — obě zapisovala do stejného
      `.part` souboru. Výsledkem bylo rozbité PDF, které se přejmenovalo na hotové,
      a u veřejného zpěvníku by tam zůstalo ležet, protože ty se z cache nevyhazují.
      Vidět to bylo na serveru přímo: rozepsaný `.part` a k němu žádný `.lock`.
      Příkaz teď bere týž `O_EXCL` zámek jako web, zapisuje do něj postup (takže čekající
      prohlížeč vidí čísla stran i u zpěvníku, který staví příkaz) a při chybě po sobě
      uklidí. Hlídá `test_export.py`, sekce „export-warm se nepotká se stahováním z webu".
- [X] **`export-warm --songbook ID`** na přestavění jednoho zpěvníku.
- [X] **`kontrola_exportu.py --overit-obsah`** otevře hotové soubory a ověří je (hlavička,
      `%%EOF`, počet stran z posledního `/Count`, u ZIPu `testzip`). Rozbitý export
      veřejného zpěvníku se z cache sám nevyhodí, takže ho musí najít někdo jiný.
- [X] **přidání písně nepředgenerovalo.** `schedule_export_warm` se volalo jen při uložení
      struktury a při smazání písně. Přidání písně ze seznamu (`add-song`) i založení
      vlastní písně (`custom-song`) přitom taky přidávají strany, takže klíč se změnil
      a předpřipravené PDF přestalo platit — další stažení čekalo na skládání. Stará
      verze se nikdy neservírovala, o to nešlo: cache je klíčovaná obsahem, takže
      neaktuální soubor je nedosažitelný, jen se muselo znovu čekat. Hlídá
      `test_export.py`, sekce „předgenerování po přidání písně".
- [X] **souběžná skládání omezená na jedno** (`MAX_CONCURRENT_EXPORTS`, přepsatelné
      z prostředí). Nebrzdí to procesor, ale paměť: jedno skládání má vrchol 170-250 MB
      a server má 979 MB bez swapu, takže při jednom běžícím zbývá ~310 MB. Jádra jsou
      dvě, takže po upgradu paměti dává souběh smysl — proto je to proměnná prostředí
      a ne konstanta v kódu. Pozor: po dobu, kdy `export-warm` něco opravdu staví,
      dostane stažení jiného zpěvníku z webu 429 „server je zaneprázdněn".
- [X] **strop cache počítal i to, co neumí vyhodit.** Do 500 MB se sčítaly všechny
      exporty, ale úklid smí mazat jen soukromé — veřejné jsou z vyhazování vyňaté
      schválně. Rozpočet se tak měřil věcmi, které z něj nejde ubrat: předgenerovaných
      30 veřejných zpěvníků zabírá 201 MB, takže na všechna soukromá stažení zbývalo
      z pětistovky 299 MB, a ten zbytek by se dál zmenšoval s každým veřejným zpěvníkem,
      který přibude. Do stropu se teď počítají jen soukromé exporty a je zvednutý na
      1 GB (`EXPORTS_CACHE_LIMIT_MB`, přepsatelné z prostředí). Stav při opravě: 201 MB
      veřejných, 174 MB soukromých, 375 MB celkem — tedy pod starým stropem, nic se
      zrovna nemazalo.
      Hlídá `test_export.py`, sekce „strop cache počítá jen soukromé exporty".
- [X] **chráněná je jen předgenerovaná varianta, ne všechno veřejné.** Úklid poznával
      chráněný soubor podle prefixu s id zpěvníku, takže veřejnému zpěvníku byly vyňaté
      i `high` a ZIP — a ty se nepředgenerovávají, takže se do cache dostanou až něčím
      stažením a pak už tam zůstaly navždycky. Změřený strop toho hromadění: kdyby si
      někdo postupně stáhl všechny tři varianty u všech 30 veřejných zpěvníků, leželo by
      v cache **977 MB**, které by nikdy nic neuvolnilo (175 MB small + 332 MB high
      + 470 MB ZIP). Chráněný je teď jen `PREDGENEROVANA_VARIANTA` veřejného zpěvníku,
      tedy `small` — jediná, u které platí slib „stažení veřejného zpěvníku je hned".
      U ZIPu to bylo nejabsurdnější: skládá se pod sekundu, ale zabral by nejvíc.
      Nevyhoditelná část je tím omezená na ~175 MB a roste jen s počtem veřejných
      zpěvníků, zhruba o 6 MB na zpěvník.
- [X] **úklid cache řadí podle posledního stažení.** `mtime` hotového exportu byl čas
      postavení, takže soubor stahovaný každý týden pět let vypadal jako nejstarší
      v adresáři. Stačilo na něj sáhnout `os.utime` při každém vydání a je z toho
      poctivé LRU. Odpadla tím potřeba dělit soubory na předvolby a vlastní a mazat
      jedny přednostně — to by vedlo k tomu, že cache plná předvoleb smaže každý
      vlastní export hned po prvním použití.
- [ ] **paralelizace uvnitř jednoho skládání** — zatím ne, ale je změřeno, kdyby se to
      hodilo po upgradu serveru. Na zpěvníku 00006: u varianty `small` je 89 % času
      načtení a zmenšení stran, což jsou na sobě nezávislé kusy práce, a jen 11 % je
      zápis do PDF, který sekvenční zůstat musí. Na dvou jádrech by to dalo až ~1,8×.
      Důvod, proč to nechat být: ta dvě jádra zároveň obsluhují web, takže skládání,
      které si vezme obě, udělá stránku na tu dobu trhanou pro všechny.
- [X] **předgenerovaná PDF byla na serveru nedosažitelná.** 29 z 31 veřejných zpěvníků
      mělo v `data/exports` hotové `small` PDF, na které se klíč netrefil, takže nabídka
      správně hlásila „není hned" a stahování je skládalo znovu. Nešlo o chybu v klíči:
      ty soubory jsou z 25. 8. 2026, tedy z doby **před migrací úložiště**, a ta změnila
      cesty k obrázkům, ze kterých se klíč počítá. `_drop_stale_exports` je neuklidil,
      protože běží až při uložení zpěvníku a veřejné se od té doby needitovaly.
      Zkontroluje to `backend/scripts/kontrola_exportu.py` (spočítá klíč stejně jako
      aplikace a porovná ho s diskem); doplní `flask export-warm --public-only`.
- [X] **klíč exportu z celých sekund, ne z nanosekund** — tatáž lekce jako u náhledů.
      Nanosekundy nepřežijí rsync ani obnovu ze zálohy, takže by tentýž obrázek dal na
      Macu a na serveru jiný klíč a předpřipravené PDF by se nedalo nahrát. Zatím to
      neuhodilo jen proto, že se exporty na Mac nekopírují.
- [X] **strany se na A4 doplňují, ne roztahují.** Export odvozoval DPI zvlášť pro šířku
      a zvlášť pro výšku, takže každá strana vyšla přesně na A4 — a co nemělo poměr A4,
      se na ni natáhlo. Změřeno na všech 1164 stranách: 1163 je do 0,3 % (skeny
      1748×2480), ale obálka „Fildova a Aldova zpěvníku" je čtverec 1536×1536 a
      roztahovala se o **41,4 %**. Doplňuje se teď okraji v barvě strany — u obálky
      barvou zpěvníku, u vnitřní strany bílou. Pod 8 px se nedoplňuje, ať se kvůli třem
      pixelům nesahá na jedenáct set stran. Ověřeno: MediaBox 595,3×841,9 bodů
      (210,0×297,0 mm) a obrázek uvnitř má týž poměr jako stránka, tedy nulové roztažení.
- [X] **okno se skládáním respektuje motiv** a zavře se i klikem vedle panelu. Barvy
      stojí na nových `--panel-*` v `_theme.html` (jednou pro světlé motivy, přebíjí je
      jen `theme-dark`) — panel byl natvrdo bílý s tmavým textem.
- [X] **okno se skládáním sjednocené do všech tří míst** (čtečka, Moje zpěvníky, správa
      veřejných zpěvníků). Logika i vzhled jsou v `frontend/static/js/stahovani.js`
      a `frontend/static/css/stahovani.css`, šablony jen volají `Stahovani.spust`
      a `Stahovani.oznacHotove`. V seznamech tím zmizel holý text s uběhlými sekundami;
      ukazuje se stejný postup jako ve čtečce („Strana 9 z 28, zbývá asi 5 s").
      Okno po stažení nezmizí samo, ale zůstane na stavu **Dokončeno** se zvýrazněným
      Zavřít. Tři opravy, které k tomu patřily:
      `.download-menu span` a `.download-chooser span` přebíjely atribut `hidden`, takže
      značka „✓ hned" svítila u všech variant — zúženo na `button > span` a doplněn
      `[hidden]` guard; okno leželo uvnitř `.mode-buttons` a dědilo odtud kulaté 60px
      tlačítko i `pointer-events: none`, kterým se ten sloupec schovává, takže Zavřít
      nešlo kliknout — visí teď na `<body>`.
      Hlídá to `backend/scripts/test_export_ui.py`: měří rámečky, ne screenshoty.
- [ ] **předělat stahování na „recept" místo tří variant.** Dnes jsou varianty tři
      a jsou zadrátované. Cílem je jedno okno, které se otevře hned po kliknutí na
      stažení (na všech třech místech), nabídne pojmenované předvolby a pod nimi
      sbalené vlastní nastavení, a skončí tlačítkem **Stáhnout** (když to leží v cache)
      nebo **Připravit** (když ne) s dnešním ukazatelem postupu.
      Rozhodnuté:
      - Předvolby jsou jen pojmenované recepty. Ta předgenerovaná (dnešní `small`)
        zůstane jedna a jediná chráněná před úklidem cache.
      - Volby musí být **diskrétní** (3-4 pojmenované stupně, ne posuvník s libovolným
        číslem), ať se prostor receptů nerozpadne a opakované stažení trefí cache.
        Validovat se musí **na serveru**, ne jen v prohlížeči.
      - Základní stav okna je jedno mrknutí a jedno kliknutí. Vlastní volby sbalené.
      - Pojmenování podle toho, co uživatel čeká; kde to není jisté, ⓘ nápověda.
      - Obsah (generovaný rejstřík) jen ve vlastním nastavení, ne v předvolbách.
      - Rozsah stran se zadává jako `12, 24-31, 50` a **čísly stran daného zpěvníku**
        (tedy tím, co je vidět ve čtečce a v obsahu), ne pořadím v souboru. Pod polem
        živý souhrn vybraných písní; u písně, ze které je vybraná jen část stran,
        poznámka „nekompletní". Prázdné strany se tudy dostat můžou.
      - Černobíle: neslibovat menší soubor (vnitřní strany jsou často 8bit šedé už teď),
        ale nabídnout to kvůli tisku. Průhledné obálky se v něm musí skládat na bílou,
        ne na barvu zpěvníku, jinak z červené obálky bude celoplošná tmavá šedá.
        U neprůhledných obálek se nedá dělat nic.
      - Brožura (dvě strany na list A4 s přeskládáním pořadí) ano.
      - **A4/A5 zahozeno**: fyzická velikost se v našem PDF nastavuje dopočítaným DPI,
        takže „A5" by znamenalo tytéž pixely a stejně velký soubor, jen menší tiskovou
        stranu. Co z toho lidi opravdu chtějí, dělá brožura.
      - Pozor při návrhu receptu: export **schválně** nechává prázdné strany a doplňuje
        chybějící části obálky, protože obálka je složený list. „Bez prázdných stran"
        a „jen obsah" jsou volby pro čtení na displeji a tisk rozbíjejí — proto patří
        k předvolbě pojmenované podle záměru, ne jako zaškrtávátko vedle „na tisk".
      Postup: (1) backend z „varianty" na „recept" bez změny UI, (2) nové okno
      s kompresí, obálkou/obsahem a prázdnými stranami, (3) rozsah stran a brožura.
- [ ] **strop na počet stažení za den na účet.** Ochrana proti tomu, aby si někdo
      vyžádáním pořád jiného receptu obsadil skládání všem ostatním. Není priorita —
      návštěvnost je řádu jednoho člověka za měsíc. Číslo je potřeba promyslet.
- [ ] **sjednotit nápovědy v UI.** Dnes jsou dvě různé implementace tooltipů:
      `.tooltip-text` ve čtečce a `.tooltip` s `data-tooltip` v Mých zpěvnících.
      Chtěné jsou dva druhy: klasický hover tooltip pro ikony, tlačítka a jiné
      netextové objekty, a ⓘ nápověda u slovních věcí (typicky volby v okně stahování).
      Udělat jedno sdílené řešení a projít s ním zbytek projektu, stejně jako se to
      udělalo s oknem stahování.
- [ ] **do okna pro stažení přidat volby obsahu.** Okno se skládáním už existuje
      (postup, odhad, značka „✓ hned" u variant v cache). Chybí v něm to druhé: nechat
      uživatele vybrat, co má stažený zpěvník obsahovat — jestli obálku zvlášť, jestli
      vůbec, jestli prázdné strany.

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
- [X] **obrázky se servírují bez kontroly práv** — opraveno. `serve_songbook_image` má
      `@login_required` a ptá se `smi_videt_obrazek`: vidí uživatel aspoň jeden zpěvník,
      ve kterém ten obrázek je? O právech tak rozhoduje zpěvník, ne umístění souboru —
      strana veřejného zpěvníku běžně visí i v něčím soukromém a naopak. Táž kontrola
      přibyla u `/strana/`, kde dosud stačilo přihlášení.
      Při odepření se vrací 404, ne 403: 403 by potvrdilo, že soubor existuje, a cesty
      jsou očíslované od jedničky.
      Ověřeno napříč všemi 14 účty: veřejnou stranu vidí každý přihlášený, soukromou
      přesně vlastník, admin a pět lidí, se kterými je zpěvník sdílený — nikdo jiný.
      Stojí to 1,4 ms na obrázek, celý požadavek 2,5-4 ms.
- [X] **omezit, kolik toho jeden účet nahraje** — `MAX_USER_STORAGE_MB`, výchozí 300 MB.
      Kontrola sedí v `_save_image_with_limit`, což je jediné místo, kudy obrázek na disk
      teče, takže se nedá obejít jiným endpointem. Komu se soubor započítá, se bere
      z cílové cesty (`uzivatele/<id>/…`), ne z přihlášeného uživatele — veřejné zpěvníky
      se nezapočítávají nikomu. Při překročení se vrací 413 se srozumitelnou hláškou,
      transakce se vrátí zpět a rozepsané soubory se smažou, takže po nepovedeném nahrání
      nezůstane nic, co by se do kvóty počítalo.
      Pro orientaci: dnešní největší uživatel má 50 MB (17 % kvóty), běžný zpěvník ~12 MB.
- [X] **ukázat uživateli, kolik místa zabral** — v panelu účtu, nad „Změnit heslo".
      Číslo, tenký pruh a od poloviny barevný vykřičník s vysvětlením pod myší; nad 85 %
      červeně. Adminovi a hostovi se neukazuje: admin zakládá veřejné zpěvníky, které se
      nikomu nezapočítávají, takže by mu svítila pořád nula.
      Změřeno v Chromiu ve všech třech stavech na 1280 i 390 px — na úzké obrazovce je
      panel pod burgerem, ale ukazatel je v něm stejný a nikde nepřečuhuje.
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
