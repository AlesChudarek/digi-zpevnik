"""Odesílání e-mailů přes SMTP.

Aplikace posílá jen transakční zprávy - ověření adresy při registraci, obnovu hesla.
Žádné rozesílky. Proto tu není žádná fronta ani opakování: když odeslání selže, uživatel
to hned uvidí a může zkusit znovu, což je pro pár zpráv denně přiměřenější než skladování
nedoručených úkolů.

Nastavení jde z prostředí, ať klíč nikdy neleží v repozitáři:

    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_KEY
    MAIL_FROM, MAIL_FROM_NAME, MAIL_REPLY_TO

Bez SMTP_KEY se neodesílá nic a volání to řekne nahlas. Tiché polykání chyb by u ověřovacích
zpráv znamenalo, že se uživatel nezaregistruje a nikdo se to nedozví.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, formataddr, make_msgid

log = logging.getLogger(__name__)

CASOVY_LIMIT = 20  # vteřin; SMTP nesmí držet požadavek uživatele donekonečna


class PostaNenastavena(RuntimeError):
    """Chybí SMTP údaje. Vlastní typ, ať to volající pozná od výpadku spojení."""


def _nastaveni():
    host = os.getenv("SMTP_HOST")
    klic = os.getenv("SMTP_KEY")
    uzivatel = os.getenv("SMTP_USER")
    if not (host and klic and uzivatel):
        raise PostaNenastavena(
            "Chybí SMTP_HOST, SMTP_USER nebo SMTP_KEY v prostředí - e-mail se neodešle.")
    return {
        'host': host,
        'port': int(os.getenv("SMTP_PORT", "587")),
        'uzivatel': uzivatel,
        'klic': klic,
        'odesilatel': os.getenv("MAIL_FROM", "noreply@digizpevnik.cz"),
        'jmeno': os.getenv("MAIL_FROM_NAME", "Digi zpěvník"),
        'odpoved': os.getenv("MAIL_REPLY_TO"),
    }


def posta_je_nastavena() -> bool:
    try:
        _nastaveni()
        return True
    except PostaNenastavena:
        return False


def posli_email(komu: str, predmet: str, text: str, html: str | None = None) -> None:
    """Pošle jednu zprávu. Při neúspěchu vyhodí výjimku, nikdy ji nepolyká.

    Text se posílá vždy, HTML je nepovinné. Obojí zároveň proto, že zpráva jen v HTML
    dostává u filtrů horší hodnocení a některým čtečkám se zobrazí prázdná.
    """
    n = _nastaveni()

    zprava = EmailMessage()
    zprava['Subject'] = predmet
    zprava['From'] = formataddr((n['jmeno'], n['odesilatel']))
    zprava['To'] = komu
    if n['odpoved']:
        # Z noreply adresy nikdo poštu nepřijímá, takže odpověď musí jít jinam. Pozor ale:
        # Reply-To na freemailu (seznam, gmail) proti From na vlastní doméně je typický znak
        # podvodné zprávy a SpamAssassin za to strhává 2,5 bodu pravidlem
        # FREEMAIL_FORGED_REPLYTO. Naměřeno. Vyplatí se tedy nechat MAIL_REPLY_TO prázdné,
        # dokud nebude k dispozici adresa na vlastní doméně.
        zprava['Reply-To'] = n['odpoved']
    # Date a Message-ID si server nedoplní sám a jejich chybějící podoba zhoršuje
    # hodnocení u antispamových filtrů.
    zprava['Date'] = formatdate(localtime=True)
    zprava['Message-ID'] = make_msgid(domain=n['odesilatel'].split('@')[-1])
    zprava.set_content(text)
    if html:
        zprava.add_alternative(html, subtype='html')

    try:
        with smtplib.SMTP(n['host'], n['port'], timeout=CASOVY_LIMIT) as spojeni:
            spojeni.starttls()
            spojeni.login(n['uzivatel'], n['klic'])
            spojeni.send_message(zprava)
    except Exception as chyba:
        # Nahlas do logu, ať se na to přijde dřív než přes stížnost uživatele. Typický
        # důvod po delší odmlce je vypršelý SMTP klíč - Brevo je ruší po 90 dnech nečinnosti.
        log.error("Odeslání e-mailu na %s selhalo: %s", komu, chyba)
        raise

    log.info("E-mail odeslán na %s (%s)", komu, predmet)
