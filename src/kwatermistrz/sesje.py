"""Podpisane sesje trybu demonstracyjnego. Nie zastępują docelowego logowania."""

import hashlib
import hmac
import secrets
import time

CIASTECZKO = "kwatermistrz_uzytkownik"
CZAS_SESJI = 8 * 60 * 60


def podpis(tresc: str, klucz: str) -> str:
    return hmac.new(klucz.encode(), tresc.encode(), hashlib.sha256).hexdigest()


def utworz_sesje(id_zolnierza: str, klucz: str) -> str:
    tresc = f"{id_zolnierza}.{int(time.time())}.{secrets.token_hex(16)}"
    return f"{tresc}.{podpis(tresc, klucz)}"


def odczytaj_sesje(token: str | None, klucz: str) -> str | None:
    if not token or len(token) > 256:
        return None
    try:
        osoba, wydano, nonce, sygnatura = token.split(".")
        wiek = time.time() - int(wydano)
    except (ValueError, TypeError):
        return None
    tresc = f"{osoba}.{wydano}.{nonce}"
    if not osoba or not nonce or not 0 <= wiek < CZAS_SESJI:
        return None
    if not sygnatura.isascii() or not hmac.compare_digest(sygnatura, podpis(tresc, klucz)):
        return None
    return osoba


def token_csrf(sesja: str, klucz: str) -> str:
    return podpis(f"csrf:{sesja}", klucz)


def poprawny_csrf(sesja: str | None, token: str | None, klucz: str) -> bool:
    return bool(sesja and token and token.isascii() and hmac.compare_digest(token, token_csrf(sesja, klucz)))
