"""Podepsané odkazy pro ověření adresy a obnovu hesla.

Tokeny se nikam neukládají - jsou podepsané tajným klíčem aplikace a nesou v sobě, komu
patří a kdy vznikly. Databáze tedy nemusí držet tabulku, která by se musela uklízet.

Jednorázovost se u obnovy hesla řeší jinak než evidencí: do podpisu vstupuje kus
současného otisku hesla, takže jakmile se heslo změní, všechny dřív vydané odkazy přestanou
platit. Použitý odkaz tak nejde použít podruhé a starý odkaz ve schránce nikoho nepustí
dovnitř po tom, co si uživatel heslo mezitím změnil.
"""

from __future__ import annotations

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# Různá sůl pro různý účel: odkaz na ověření adresy nesmí jít použít jako odkaz na změnu
# hesla, i když je oboje podepsané stejným klíčem.
SUL_OVERENI = 'overeni-emailu'
SUL_HESLO = 'obnova-hesla'

PLATNOST_OVERENI = 24 * 3600      # den; kdo se k mailu dostane později, požádá znovu
PLATNOST_HESLA = 2 * 3600         # dvě hodiny, u hesla je krátká platnost na místě


def _podepisovac(sul: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt=sul)


def token_overeni(user) -> str:
    return _podepisovac(SUL_OVERENI).dumps(user.id)


def uzivatel_z_overeni(token: str, User):
    """Vrací uživatele, nebo None když je odkaz neplatný či prošlý."""
    try:
        user_id = _podepisovac(SUL_OVERENI).loads(token, max_age=PLATNOST_OVERENI)
    except (BadSignature, SignatureExpired):
        return None
    return User.query.get(user_id)


def token_hesla(user) -> str:
    # Kus otisku hesla v podpisu = odkaz zneplatní sám sebe, jakmile se heslo změní.
    return _podepisovac(SUL_HESLO).dumps({'id': user.id, 'h': (user.password or '')[-16:]})


def uzivatel_z_hesla(token: str, User):
    try:
        data = _podepisovac(SUL_HESLO).loads(token, max_age=PLATNOST_HESLA)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict):
        return None
    user = User.query.get(data.get('id'))
    if not user or (user.password or '')[-16:] != data.get('h'):
        # Heslo se mezitím změnilo, takže tenhle odkaz už svoje odsloužil.
        return None
    return user
