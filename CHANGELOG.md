# Novinky

Co přinesla která verze Digitálního zpěvníku. Píše se pro uživatele - co je nového
a co se opravilo, ne jak je to udělané. Ze souboru se skládá stránka /novinky
(backend/verze.py), proto drží pevný tvar:

- nadpis verze `## 3.1.0 · 12. 1. 2027` (číslo · datum vydání), nejnovější nahoře;
- pod ním volitelně jedna věta shrnutí a odrážky s novinkami;
- číslo: první číslo velká změna toho, co web je, druhé nová funkce, třetí jen
  opravy. Tečka u účtu svítí jen u verzí s novou funkcí (x.y.0).

Verze před 3.0.0 se číslovaly zpětně podle velkých milníků v historii projektu
(značky v0.1.0 až v2.3.0 v gitu).

## 3.0.0 · 25. 11. 2026

Vlastní strany zpěvníku: editor stran s akordy.

- Editor stran: strana zpěvníku se dá navrhnout od začátku jako v grafickém
  programu - textové rámečky, styly odstavců, písma, obrázky a náplasti.
- Akordy nad textem: vkládají se klikem na znak, dají se posouvat, transponovat
  a zarovnávají se samy, aby do sebe nenarážely.
- Sloky a refrény se samy číslují, číslo strany se kreslí podle pozice ve zpěvníku.
- Prvky jde přesouvat, zvětšovat, otáčet, řadit do vrstev a zamknout na místě.
- Náplast zakryje část naskenované strany (třeba vypálené číslo strany) - obdélník,
  elipsa, trojúhelník nebo volný tvar.
- Dvojstrana: obě protější strany vedle sebe, i s prvky přes hřbet.
- Navržené strany se čtou ve čtečce a jdou do PDF jako ostrý text, který se dá
  hledat a kopírovat.
- Z editoru zpěvníku jde stranu navrhnout nebo upravit, přidat víc stran naráz
  (prázdné, s ukázkou nebo kopie).
- Účty bez prémia si editor vyzkouší na ukázkové straně.
- Zpětná vazba: záložka u okraje stránky pro hlášení chyb a nápadů, i se snímkem
  obrazovky. Pro přihlášené účty.
- Novinky: tahle stránka a číslo verze v nabídce účtu.

## 2.3.0 · 3. 11. 2026

Správa webu pro správce.

- Správa: přehled všech zpěvníků a uživatelů a záznam zásahů správce.
- Vlastní limit místa pro účet, blokování účtů, odhlášení ze všech zařízení.
- Zpěvníky přes limit místa se zamknou; e-mailem přijde upozornění.
- Neaktivní účty: upozornění po roce, smazání po 30 dnech.
- Lišta se na úzkém okně schová pod menu, jakmile se nevejde na jeden řádek.

## 2.2.0 · 27. 10. 2026

Ovládání prstem.

- Strany v editoru i zpěvníky v přehledu jde přetahovat prstem, s rolováním u okraje.
- Hledání písní na telefonu jako kartičky.
- Nápovědy fungují i na dotykové obrazovce.
- Čtečka na telefonu a tabletu: obsah a tlačítka po ruce.

## 2.1.0 · 14. 10. 2026

Sdílení a společné zpěvníky.

- Sdílení přes pozvánky: poslat kopii, nebo upravovat zpěvník společně.
- Přátelé: žádost a potvrzení, pozvánky přátelům na jedno kliknutí.
- Profil s přezdívkou a profilovou fotkou.
- Limit místa podle stran ve zpěvnících, ukazatel zabraného místa.
- Tisk zpěvníku rovnou z webu.
- Pořadí zpěvníků přetažením, přepínač režimů čtečky jako pilulka.

## 2.0.0 · 7. 10. 2026

Zpěvník jako řada stran.

- Nový editor zpěvníku: jedno „Přidat strany“ místo tří tlačítek, strany bez typů.
- Import zpěvníku z PDF nebo ZIP přímo v editoru.

## 1.5.0 · 23. 9. 2026

Rychlejší čtečka a nové stahování.

- Čtečka načítá zmenšené strany, plné rozlišení až při přiblížení.
- Jedno okno pro stažení: rozsah stran, brožura, průběh i dokončení.
- Náhledy obálek v přehledech.

## 1.4.0 · 2. 9. 2026

Účty.

- Registrace s ověřením e-mailu, obnova zapomenutého hesla.
- Panel účtu v liště, odstranění účtu, kontrola síly hesla.
- Vybraný motiv se pamatuje u účtu.
- Ochrana proti hádání hesla.

## 1.3.0 · 19. 8. 2026

Sjíždění a stahování.

- Automatické sjíždění textu ve čtečce.
- Stažení zpěvníku do PDF a ZIP, v barvě zpěvníku.

## 1.2.0 · 12. 8. 2026

Web na telefonech.

- Stránky, čtečka i hledání se přizpůsobí telefonu.
- Přiblížení drží střed pohledu na místě.

## 1.1.0 · 30. 7. 2026

Správa zpěvníků.

- Správce upravuje veřejné zpěvníky z prohlížeče.
- Strany bez písně (úvod, obsah) jako běžné strany, víc písní na jedné straně.
- Obsah ukazuje vytištěná čísla stran.

## 1.0.0 · 12. 11. 2025

První verze na digizpevnik.cz.

## 0.2.0 · 17. 9. 2025

- Vlastní zpěvníky, barevné motivy, barva zpěvníku.

## 0.1.0 · 28. 8. 2025

- První funkční čtečka zpěvníků.
