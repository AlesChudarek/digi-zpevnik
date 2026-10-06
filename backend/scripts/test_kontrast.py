"""Kontrast textu vůči skutečnému pozadí, ve všech motivech a hlavních oknech.

Vzniklo z nálezu, že v motivu Půlnoc byl zavírací křížek v obsahu černý na černém
(1,57 : 1) — a že tatáž dvojice proměnných dělala totéž s tlačítkem „Uložit" i s názvy
nahraných obálek. Z kódu to vidět nebylo: obě barvy jsou proměnné a teprve dosazením
motivu vyjde najevo, že jsou obě tmavé.

Skript projde každý viditelný prvek s textem, dohledá jeho skutečné pozadí (první
neprůhledný předek) a spočítá kontrast podle WCAG. Práh je 4,5 : 1, u velkého písma 3.

Co je známé a vědomě odpuštěné, je v ZNAME — skript spadne jen na něčem novém.

Použití: test_kontrast.py [motiv ...]   (bez argumentů projede všech dvanáct)
Vyžaduje playwright. Běží proti KOPII databáze.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VENV_PY = PROJECT_ROOT / ".venv" / "bin" / "python"
PORT = 5595
PASSWORD = "kontrast"
BOOK = "00006"

MOTIVY = ["green", "blue", "dark", "purple", "amber", "slate", "teal",
          "emerald", "cyan", "indigo", "rose", "pink"]

# Dvojice (text, pozadí), které zůstávají pod prahem a víme o nich. Klíč je dvojice
# barev, hodnota důvod. Odstranit odsud znamená, že se to má opravit.
ZNAME = {
    ("#ffffff", "#00b0d5"): "lišta v motivu Blue - je to značková barva Handicapu",
    ("#ffffff", "#14b8a6"): "lišta v motivu Teal - viz TODO o kontrastu lišty",
    ("#ffffff", "#10b981"): "lišta v motivu Emerald - viz TODO o kontrastu lišty",
    ("#ffffff", "#06b6d4"): "lišta v motivu Cyan - viz TODO o kontrastu lišty",
    ("#ffffff", "#6366f1"): "lišta v motivu Indigo - viz TODO o kontrastu lišty",
    ("#ffffff", "#f43f5e"): "lišta v motivu Rose - viz TODO o kontrastu lišty",
    ("#ffffff", "#ec4899"): "lišta v motivu Pink - viz TODO o kontrastu lišty",
    ("#ffffff", "#7c3aed"): "lišta v motivu Purple - viz TODO o kontrastu lišty",
    ("#92400e", "#f59e0b"): "lišta v motivu Amber - viz TODO o kontrastu lišty",
    ("#ffffff", "#0d9488"): "popisek filtru na podkladu stránky, motiv Teal",
    ("#ffffff", "#0891b2"): "popisek filtru na podkladu stránky, motiv Cyan",
}

selhani = []


def start_server(db_copy):
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_copy.as_posix()}"
    env["FLASK_SECRET_KEY"] = "kontrast"
    env["PYTHONPATH"] = f"{PROJECT_ROOT}:{PROJECT_ROOT / 'backend'}"
    subprocess.run(
        [str(VENV_PY), "-c",
         'import os, sys\n'
         'sys.path[:0] = os.environ["PYTHONPATH"].split(":")\n'
         'from backend.app import app, db, User\n'
         'from werkzeug.security import generate_password_hash\n'
         'with app.app_context():\n'
         '    u = User.query.filter_by(email="admin@test.com").first()\n'
         f'    u.password = generate_password_hash("{PASSWORD}", method="pbkdf2:sha256")\n'
         '    db.session.commit()\n'],
        env=env, check=True, capture_output=True)
    proc = subprocess.Popen(
        [str(VENV_PY), "-m", "flask", "--app", "backend.app", "run",
         "--port", str(PORT), "--no-reload"],
        cwd=str(PROJECT_ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{PORT}"
    for _ in range(160):
        try:
            urllib.request.urlopen(base + "/login", timeout=1)
            return proc, base
        except Exception:
            time.sleep(0.25)
    proc.terminate()
    raise SystemExit("❌ server se nerozjel")


AUDIT = """(kde) => {
  const rgb = (s) => {
    const m = (s||'').match(/[\\d.]+/g);
    if (!m || m.length < 3) return null;
    const a = m.length > 3 ? parseFloat(m[3]) : 1;
    return {r:+m[0], g:+m[1], b:+m[2], a};
  };
  const lum = (c) => {
    const f = (v) => { v/=255; return v<=0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4); };
    return 0.2126*f(c.r)+0.7152*f(c.g)+0.0722*f(c.b);
  };
  const kontrast = (a,b) => {
    const la=lum(a), lb=lum(b);
    return (Math.max(la,lb)+0.05)/(Math.min(la,lb)+0.05);
  };
  const pozadiZa = (el) => {
    let e = el;
    while (e && e !== document.documentElement) {
      const c = rgb(getComputedStyle(e).backgroundColor);
      if (c && c.a >= 0.95) return {barva: c, kde: e.className || e.tagName};
      e = e.parentElement;
    }
    return {barva: {r:255,g:255,b:255,a:1}, kde: 'html'};
  };
  const hex = (c) => '#' + [c.r,c.g,c.b].map(v => v.toString(16).padStart(2,'0')).join('');
  const out = [];
  document.querySelectorAll('*').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || +st.opacity < 0.3) return;
    // jen prvky s vlastním textem, ne obaly
    const vlastni = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (!vlastni) return;
    const barva = rgb(st.color);
    if (!barva || barva.a < 0.5) return;
    const poz = pozadiZa(el);
    const k = kontrast(barva, poz.barva);
    const velky = parseFloat(st.fontSize) >= 24 ||
                  (parseFloat(st.fontSize) >= 18.66 && +st.fontWeight >= 700);
    const prah = velky ? 3 : 4.5;
    if (k >= prah) return;
    out.push({kde,
              prvek: (el.tagName.toLowerCase() + '.' + (el.className||'')).slice(0,46),
              text: (el.textContent||'').trim().slice(0,22),
              barva: hex(barva), pozadi: hex(poz.barva), pozadi_z: String(poz.kde).slice(0,22),
              kontrast: Math.round(k*100)/100, prah});
  });
  return out;
}"""


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("❌ chybí playwright")

    jen = [m for m in sys.argv[1:] if m in MOTIVY] or MOTIVY
    tmp = Path(tempfile.mkdtemp(prefix="kontrast-"))
    db_copy = tmp / "kontrast.db"
    shutil.copy(PROJECT_ROOT / "backend" / "instance" / "zpevnik.db", db_copy)
    server, base = start_server(db_copy)
    nalezy = {}
    try:
        with sync_playwright() as pw:
            browser = pw.firefox.launch()
            for motiv in jen:
                ctx = browser.new_context(viewport={"width": 1280, "height": 900})
                page = ctx.new_page()
                page.goto(base + "/login")
                page.fill('input[name="email"]', "admin@test.com")
                page.fill('input[name="password"]', PASSWORD)
                page.click('button[type="submit"], input[type="submit"]')
                page.wait_for_load_state("networkidle")
                page.evaluate(f"() => window.setDzTheme('{motiv}')")
                page.wait_for_timeout(300)

                vysl = []
                page.goto(base + "/my-songbooks")
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(600)
                vysl += page.evaluate(AUDIT, "moje zpěvníky")
                page.evaluate("() => document.querySelector('.btn-edit').click()")
                page.wait_for_timeout(2200)
                vysl += page.evaluate(AUDIT, "editor zpěvníku")
                page.goto(base + "/search")
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(900)
                vysl += page.evaluate(AUDIT, "hledání")
                page.goto(base + f"/songbook/{BOOK}")
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(1200)
                page.evaluate("() => toggleToc()")
                page.wait_for_timeout(900)
                vysl += page.evaluate(AUDIT, "čtečka + obsah")
                page.evaluate("() => toggleToc()")
                page.wait_for_timeout(300)
                page.evaluate("() => document.getElementById('download-toggle').click()")
                page.wait_for_timeout(1200)
                vysl += page.evaluate(AUDIT, "okno stahování")
                nalezy[motiv] = vysl
                ctx.close()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)

    for motiv in jen:
        videno = {}
        for n in nalezy.get(motiv, []):
            videno[(n["kde"], n["prvek"], n["barva"], n["pozadi"])] = n
        nove = [n for n in videno.values()
                if (n["barva"], n["pozadi"]) not in ZNAME]
        odpustene = len(videno) - len(nove)
        znacka = "✅" if not nove else "❌"
        print(f"  {znacka} {motiv:9} {len(nove)} nových problémů"
              f"{f', {odpustene} známých' if odpustene else ''}")
        for n in sorted(nove, key=lambda x: x["kontrast"]):
            print(f"       {n['kontrast']:5.2f}:1 (min {n['prah']})  {n['kde']:16} "
                  f"{n['prvek']:44} {n['barva']} na {n['pozadi']}  „{n['text']}“")
            selhani.append(f"{motiv}: {n['prvek']} {n['kontrast']}:1")

    if selhani:
        print(f"\n❌ {len(selhani)} míst pod prahem kontrastu")
        return 1
    print("\n✅ ve všech motivech prošel kontrast")
    return 0


if __name__ == "__main__":
    sys.exit(main())
