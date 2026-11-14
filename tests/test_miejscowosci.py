import json
from io import BytesIO
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from kwatermistrz import miejscowosci as m
from kwatermistrz.api.slowniki import miejscowosci, srodki_transportu
from kwatermistrz.podroze import SRODKI_TRANSPORTU
from kwatermistrz.web import strony


@pytest.fixture(autouse=True)
def cache(monkeypatch):
    m._cache.clear()
    monkeypatch.setattr(m, "_ostatnie", float("-inf"))


def test_photon_przekazuje_tylko_nazwe_i_filtruje_polskie_miejscowosci(monkeypatch):
    wywolania = []

    def otworz(request, timeout):
        wywolania.append(request.full_url)
        assert timeout == 5
        return BytesIO(
            json.dumps(
                {
                    "features": [
                        {"properties": {"name": "Bydgoszcz", "state": "kujawsko-pomorskie", "countrycode": "PL"}},
                        {"properties": {"name": "Berlin", "countrycode": "DE"}},
                        {"properties": {"name": "Bydgoszcz", "state": "kujawsko-pomorskie", "countrycode": "PL"}},
                    ]
                }
            ).encode()
        )

    monkeypatch.setattr(m, "urlopen", otworz)
    wynik = m.szukaj_miejscowosci("Bydgoszcz")
    assert wynik == [{"nazwa": "Bydgoszcz", "opis": "kujawsko-pomorskie"}]
    assert parse_qs(urlsplit(wywolania[0]).query) == {
        "q": ["Bydgoszcz"],
        "countrycode": ["PL"],
        "limit": ["8"],
        "lang": ["pl"],
        "layer": ["city", "locality"],
    }
    wynik.clear()
    assert m.szukaj_miejscowosci("bydgoszcz") and len(wywolania) == 1


@pytest.mark.parametrize("tekst", ["a", "", "Warszawa 12", "https://example.org", "a" * 81])
def test_nazwa_nie_jest_adresem_ani_danymi_wniosku(tekst):
    with pytest.raises(m.BladMiejscowosci):
        m.szukaj_miejscowosci(tekst)


def test_awaria_api_nie_zwraca_fikcyjnych_miast(monkeypatch):
    def awaria(*args, **kwargs):
        raise URLError("test")

    monkeypatch.setattr(m, "urlopen", awaria)
    with pytest.raises(HTTPException) as e:
        miejscowosci("Bydgoszcz")
    assert e.value.status_code == 503
    assert "samodzielnie" in e.value.detail


def test_slownik_transportu_jest_spojny_z_formularzem():
    assert {w["wartosc"]: w["nazwa"] for w in srodki_transportu()} == SRODKI_TRANSPORTU
    assert "pojazd służbowy" in SRODKI_TRANSPORTU


def test_ograniczenie_czestotliwosci_i_bledny_format(monkeypatch):
    monkeypatch.setattr(m, "urlopen", lambda *args, **kwargs: BytesIO(b'{"features": []}'))
    assert m.szukaj_miejscowosci("Bydgoszcz") == []
    with pytest.raises(m.ZrodloNiedostepne, match="Odczekaj"):
        m.szukaj_miejscowosci("Warszawa")
    monkeypatch.setattr(m, "_ostatnie", float("-inf"))
    monkeypatch.setattr(m, "urlopen", lambda *args, **kwargs: BytesIO(b'{"unexpected": true}'))
    with pytest.raises(m.ZrodloNiedostepne):
        m.szukaj_miejscowosci("Warszawa")


def test_fragment_wyszukiwania_escapuje_dane_z_zewnetrznego_api(monkeypatch):
    monkeypatch.setattr(
        strony,
        "szukaj_miejscowosci",
        lambda q: [{"nazwa": '<script>alert("x")</script>', "opis": "kujawsko-pomorskie"}],
    )
    request = Request({"type": "http", "headers": []})
    html = strony.wyszukaj_miejscowosci(request, "Bydgoszcz", uzytkownik=None).body.decode()
    assert "<script>" not in html
    assert 'data-miejscowosc="&lt;script&gt;' in html
    assert "OpenStreetMap" in html


def test_awaria_fragmentu_nie_blokuje_samodzielnego_wpisania_nazwy(monkeypatch):
    def awaria(q):
        raise m.ZrodloNiedostepne("Wpisz nazwę samodzielnie")

    monkeypatch.setattr(strony, "szukaj_miejscowosci", awaria)
    request = Request({"type": "http", "headers": []})
    html = strony.wyszukaj_miejscowosci(request, "Bydgoszcz", uzytkownik=None).body.decode()
    assert "Wpisz nazwę samodzielnie" in html
    assert "Nie znaleziono" not in html


def test_nowe_api_jest_zarejestrowane_i_wymaga_sesji():
    from kwatermistrz.api.main import app
    from kwatermistrz.api.slowniki import router
    from kwatermistrz.api.zaleznosci import uzytkownik_sesji

    schema = app.openapi()
    for path in ("/slowniki/miejscowosci", "/slowniki/srodki-transportu"):
        assert "get" in schema["paths"][path]
        route = next(r for r in router.routes if r.path == path)
        assert uzytkownik_sesji in [d.call for d in route.dependant.dependencies]
