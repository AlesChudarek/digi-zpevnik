# Digitální zpěvník

Webová aplikace pro prohlížení a správu zpěvníků. Umožňuje:

- Registraci a přihlášení uživatelů
- Označování oblíbených písní
- Tvorbu vlastních zpěvníků
- Upload vlastních stránek (např. PNG)

## Lokální vývoj

- Vytvoř virtuální prostředí a nainstaluj závislosti: `python3 -m venv .venv && source .venv/bin/activate && pip install -r backend/requirements.txt`
- Zajisti konfiguraci v `.env` (minimálně `FLASK_SECRET_KEY`, `DATABASE_URL` pokud nechceš výchozí SQLite).
- Inicializuj databázi: `flask --app backend.app init-db`
- Volitelně vytvoř admin účet: `flask --app backend.app create-admin`
- Spusť server pro vývoj: `flask --app backend.app run --debug` nebo `python backend/app.py`

## Nasazení

- Na cílový server zkopíruj repozitář včetně složek `data/public`, `data/private` a databáze v `backend/instance` (nebo vlastní, pokud používáš jiný `DATABASE_URL`).
- Vytvoř produkční `.env` se skutečnými tajnými hodnotami (`FLASK_SECRET_KEY`, `DATABASE_URL`, případně `FLASK_DEBUG=False`).
- Nainstaluj závislosti v izolovaném prostředí: `pip install -r backend/requirements.txt`
- Inicializuj databázi a účty pomocí CLI příkazů: `flask --app backend.app init-db`, `flask --app backend.app create-admin`
- Spusť aplikaci pomocí WSGI serveru, např. `gunicorn -w 4 -b 0.0.0.0:8000 'backend.app:app'`
- Statická data slouží Flask ze složky `frontend/static`; u produkce je vhodné je servírovat reverse proxy (Nginx, Traefik).
