"""Logowanie klienta testowego z tokenem do poprawnych żądań formularzy."""

from kwatermistrz.config import settings
from kwatermistrz.sesje import CIASTECZKO, token_csrf


def zaloguj_z_csrf(klient, id_zolnierza="ZOL-003"):
    odpowiedz = klient.post("/app/logowanie", data={"id_zolnierza": id_zolnierza})
    assert odpowiedz.status_code == 303
    klient.headers["X-CSRF-Token"] = token_csrf(klient.cookies[CIASTECZKO], settings.session_secret.get_secret_value())
    return odpowiedz
