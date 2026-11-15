"""Reguły podpisu, ważności i powiązania CSRF z konkretną sesją."""

from unittest.mock import patch

from kwatermistrz.sesje import CZAS_SESJI, odczytaj_sesje, poprawny_csrf, token_csrf, utworz_sesje


def test_sesja_podpis_i_zmiana_osoby():
    token = utworz_sesje("ZOL-003", "klucz-testowy")
    assert odczytaj_sesje(token, "klucz-testowy") == "ZOL-003"
    assert odczytaj_sesje(token.replace("ZOL-003", "ZOL-001"), "klucz-testowy") is None
    assert odczytaj_sesje(token, "inny-klucz") is None
    assert odczytaj_sesje("ZOL-001", "klucz-testowy") is None


def test_wygasniecie_i_data_z_przyszlosci():
    with patch("time.time", return_value=1000):
        token = utworz_sesje("ZOL-003", "klucz-testowy")
    with patch("time.time", return_value=1000 + CZAS_SESJI):
        assert odczytaj_sesje(token, "klucz-testowy") is None
    with patch("time.time", return_value=999):
        assert odczytaj_sesje(token, "klucz-testowy") is None


def test_csrf_nie_dziala_po_zmianie_sesji():
    sesja = utworz_sesje("ZOL-003", "klucz-testowy")
    csrf = token_csrf(sesja, "klucz-testowy")
    assert poprawny_csrf(sesja, csrf, "klucz-testowy")
    assert not poprawny_csrf(utworz_sesje("ZOL-003", "klucz-testowy"), csrf, "klucz-testowy")
    assert not poprawny_csrf(sesja, None, "klucz-testowy")
