"""Nastavení gunicornu na serveru (`gunicorn -c backend/gunicorn_conf.py ...`).

Hlavně kvůli logům. Když worker nestihne do `timeout` odpovědět, gunicorn ho zabije
a v logu zůstane jen „WORKER TIMEOUT (pid …)“ - bez toho, co dělal. Tady se před
zabitím zapíše požadavek, na kterém visel, a kde přesně v kódu stál.

Všechno jde na stderr, tedy do journalu:
    sudo journalctl -u gunicorn-zpevnik -f
"""
import sys
import threading
import time
import traceback

# Každý požadavek jedním řádkem: kdo, co, výsledek, velikost a doba v milisekundách.
accesslog = '-'
access_log_format = '%(h)s "%(r)s" %(s)s %(B)s %(M)sms'


def pre_request(worker, req):
    worker.aktualni_pozadavek = (req.method, req.path, time.time())


def post_request(worker, req, environ, resp):
    worker.aktualni_pozadavek = None


def worker_abort(worker):
    """Gunicorn pošle workeru před zabitím SIGABRT a zavolá tohle."""
    pozadavek = getattr(worker, 'aktualni_pozadavek', None)
    if pozadavek:
        metoda, cesta, od = pozadavek
        popis = f"{metoda} {cesta} (běží {time.time() - od:.0f} s)"
    else:
        popis = "žádný požadavek - práci držela vlákna na pozadí"
    vlakna = {t.ident: t.name for t in threading.enumerate()}
    radky = [f"WORKER {worker.pid} ZABIT PO TIMEOUTU: {popis}"]
    for ident, ramec in sys._current_frames().items():
        radky.append(f"--- vlákno {vlakna.get(ident, ident)}:")
        radky.extend(l.rstrip() for l in traceback.format_stack(ramec)[-12:])
    worker.log.critical("\n".join(radky))
