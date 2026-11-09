// Číslo strany: vrstva nad stranou, ne součást textu.
//
// Číslo se nikdy „nezapeče“ do obsahu strany - kreslí se až při zobrazení podle toho,
// kde strana ve zpěvníku skutečně je (číslo i levá/pravá). Vložení strany doprostřed
// zpěvníku tak přečísluje všechno samo. Do obrázku pro PDF se taky nekreslí, dokreslí
// ho až export.
//
// Poloha (x, y v mm, střed čísla) platí pro PRAVOU stranu. Na levé je se zrcadlením
// zrcadlově podle svislé osy (vnější roh zůstane vnějším), bez zrcadlení na stejném
// místě. Tažením čísla na straně se poloha mění; přichytí se ke středu a k okrajům sazby
// a během tažení je vidět náhled dvojstrany s oběma čísly.

const MM_NA_PX = 96 / 25.4;
const PRICHYT_MM = 1.5;

export class CisloStrany {
  // volby: format() -> {sirka, vyska, okraj}, nastaveni() -> nastavení strany,
  // zmen(castNastaveni) - uloží změnu, meritko() -> přiblížení
  constructor(stranka, plocha, volby) {
    this.stranka = stranka;
    this.volby = volby;
    this.el = document.createElement('div');
    this.el.className = 'ps-cislo-strany';
    this.el.title = 'Táhnout: poloha čísla strany';
    stranka.appendChild(this.el);
    this.voditkoX = document.createElement('div');
    this.voditkoX.className = 'ps-voditko-cisla svisle';
    this.voditkoY = document.createElement('div');
    this.voditkoY.className = 'ps-voditko-cisla vodorovne';
    stranka.append(this.voditkoX, this.voditkoY);
    // Plovoucí náhled dvojstrany během tažení.
    this.nahled = document.createElement('div');
    this.nahled.className = 'nahled-dvojstrany plovouci';
    this.nahled.hidden = true;
    plocha.appendChild(this.nahled);
    this.el.addEventListener('pointerdown', (e) => this.zacni(e));
  }

  // Poloha čísla na dané straně (P/L) v mm.
  static poloha(c, f, strana) {
    const x = strana === 'L' && c.zrcadlit ? f.sirka - c.x : c.x;
    return { x, y: c.y };
  }

  vykresli() {
    const n = this.volby.nastaveni();
    const c = n.cislo, f = this.volby.format();
    const { x, y } = CisloStrany.poloha(c, f, n.nahledStrany);
    const s = this.el.style;
    this.el.textContent = String(n.nahledCisla);
    s.left = x + 'mm';
    s.top = y + 'mm';
    s.fontFamily = `'${c.pismo}', sans-serif`;
    s.fontSize = c.velikost + 'pt';
    s.fontWeight = c.tucne ? 700 : 400;
    s.color = c.barva;
    this.el.hidden = !c.zobrazit;
    // Skryté na této straně: v editoru jen bledě, ať je vidět, kde by bylo.
    this.el.classList.toggle('skryte', !!n.cisloSkryte);
  }

  zacni(e) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const n = this.volby.nastaveni();
    this.tah = { strana: n.nahledStrany, posun: false };
    this.el.classList.add('tazene');
    this._pohyb = (ev) => this.pohyb(ev);
    this._konec = () => this.konec();
    window.addEventListener('pointermove', this._pohyb);
    window.addEventListener('pointerup', this._konec);
  }

  pohyb(e) {
    const f = this.volby.format();
    const r = this.stranka.getBoundingClientRect();
    const pxNaMm = MM_NA_PX * this.volby.meritko();
    let x = (e.clientX - r.left) / pxNaMm, y = (e.clientY - r.top) / pxNaMm;
    x = Math.min(Math.max(x, 2), f.sirka - 2);
    y = Math.min(Math.max(y, 2), f.vyska - 2);
    // Přichycení: střed strany, okraje sazby, polovina okraje (číslo v okraji).
    const svisle = [f.sirka / 2, f.okraj, f.sirka - f.okraj, f.okraj / 2, f.sirka - f.okraj / 2];
    const vodorovne = [f.okraj / 2, f.vyska - f.okraj / 2, f.okraj, f.vyska - f.okraj];
    const prichyt = (v, kandidati) => kandidati.find((k) => Math.abs(k - v) < PRICHYT_MM);
    const px = prichyt(x, svisle), py = prichyt(y, vodorovne);
    if (px != null) x = px;
    if (py != null) y = py;
    x = Math.round(x * 2) / 2;
    y = Math.round(y * 2) / 2;
    this.ukazVoditka(px, py);
    // Na levé straně se zrcadlením se tahá zrcadlový obraz - uloží se poloha pro pravou.
    const n = this.volby.nastaveni();
    const pravaX = this.tah.strana === 'L' && n.cislo.zrcadlit ? f.sirka - x : x;
    this.tah.nove = { ...n.cislo, x: pravaX, y };
    this.tah.posun = true;
    const { x: vx, y: vy } = CisloStrany.poloha(this.tah.nove, f, this.tah.strana);
    this.el.style.left = vx + 'mm';
    this.el.style.top = vy + 'mm';
    this.nahled.hidden = false;
    vykresliDvojstranu(this.nahled, f, this.tah.nove, String(n.nahledCisla), this.tah.strana);
  }

  ukazVoditka(x, y) {
    this.voditkoX.style.display = x != null ? 'block' : 'none';
    this.voditkoY.style.display = y != null ? 'block' : 'none';
    if (x != null) this.voditkoX.style.left = x + 'mm';
    if (y != null) this.voditkoY.style.top = y + 'mm';
  }

  konec() {
    window.removeEventListener('pointermove', this._pohyb);
    window.removeEventListener('pointerup', this._konec);
    this.el.classList.remove('tazene');
    this.ukazVoditka(null, null);
    this.nahled.hidden = true;
    const t = this.tah;
    this.tah = null;
    if (t && t.posun) this.volby.zmen({ cislo: t.nove });
  }
}

// Malý náhled dvojstrany: levá a pravá strana s číslem na místě, kde bude. aktivni = 'L'
// nebo 'P' (ta, kterou autor právě upravuje, je zvýrazněná).
export function vykresliDvojstranu(kam, f, c, cislo, aktivni) {
  const sirka = 64, vyska = sirka * f.vyska / f.sirka;
  const mm = sirka / f.sirka;
  kam.textContent = '';
  const n = parseInt(cislo, 10) || 12;
  // Levá strana má sudé číslo, pravá liché (typicky) - ukázka je n-1 / n podle aktivní.
  const cisla = aktivni === 'L' ? { L: n, P: n + 1 } : { L: n - 1, P: n };
  for (const strana of ['L', 'P']) {
    const s = document.createElement('div');
    s.className = 'nahled-strana' + (strana === aktivni ? ' aktivni' : '');
    s.style.width = sirka + 'px';
    s.style.height = vyska + 'px';
    const sazba = document.createElement('div');
    sazba.className = 'nahled-sazba';
    sazba.style.inset = `${f.okraj * mm}px`;
    s.appendChild(sazba);
    if (c.zobrazit) {
      const { x, y } = CisloStrany.poloha(c, f, strana);
      const t = document.createElement('span');
      t.className = 'nahled-cislo';
      t.textContent = String(cisla[strana]);
      t.style.left = x * mm + 'px';
      t.style.top = y * mm + 'px';
      s.appendChild(t);
    }
    const popis = document.createElement('span');
    popis.className = 'nahled-popis';
    popis.textContent = strana === 'L' ? 'levá' : 'pravá';
    s.appendChild(popis);
    kam.appendChild(s);
  }
}
