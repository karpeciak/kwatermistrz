"""Formularz wniosku o wyjazd (S22IT1S0P2-33, podzadanie S22IT1S0P2-52)."""

import re

import pytest
from fastapi.testclient import TestClient
from pomoc_sesji import zaloguj_z_csrf
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from kwatermistrz.api.main import app, get_broker
from kwatermistrz.db import sesja
from kwatermistrz.models import Delegacja

WNIOSEK = {
    "cel_wyjazdu": "szkolenie",
    "miejsce_docelowe": "Toruń",
    "data_wyjazdu": "2026-12-07",
    "data_powrotu": "2026-12-11",
    "podstawa_numer_rozkazu": "Rozkaz dzienny nr 215/2026",
    "podstawa_data_rozkazu": "2026-11-10",
    "srodek_transportu": "kolej",
    "potrzeba_zakwaterowania": "true",
}


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app, follow_redirects=False) as c:
        zaloguj_z_csrf(c, "ZOL-003")
        yield c
    app.dependency_overrides.clear()


def liczba_delegacji() -> int:
    with sesja() as s:
        # wersja robocza (S22IT1S0P2-37) nie jest złożonym wnioskiem
        return s.scalar(select(func.count()).select_from(Delegacja).where(Delegacja.status != "roboczy"))


def test_formularz_pokazuje_dane_wnioskodawcy_z_ewidencji(klient):
    strona = klient.get("/app/wnioski/nowy").text
    assert "por. Magdalena Szewczyk" in strona and "1 kompania zaopatrzenia" in strona
    assert "JW 1001 – Batalion Logistyczny" in strona
    assert 'list="cele-wyjazdu"' in strona and "oddelegowanie" in strona
    assert 'action="/app/wnioski/nowy/podsumowanie"' in strona


def test_podsumowanie_przed_zlozeniem_nie_rejestruje_wniosku(klient, broker):
    strona = klient.post("/app/wnioski/nowy/podsumowanie", data=WNIOSEK).text
    assert "Podsumowanie wniosku" in strona and "Złóż wniosek" in strona
    assert "07.12.2026 – 11.12.2026" in strona and "5 dni" in strona and "Rozkaz dzienny nr 215/2026" in strona
    assert liczba_delegacji() == 0 and broker.wyslane == []


def test_podsumowanie_przenosi_stabilny_klucz_do_finalnego_zlozenia(klient, broker):
    pierwsze = klient.post("/app/wnioski/nowy/podsumowanie", data=WNIOSEK).text
    drugie = klient.post("/app/wnioski/nowy/podsumowanie", data=WNIOSEK).text
    klucz_1 = re.search(r'name="klucz_idempotencji" value="([a-f0-9]+)"', pierwsze)[1]
    klucz_2 = re.search(r'name="klucz_idempotencji" value="([a-f0-9]+)"', drugie)[1]
    assert klucz_1 == klucz_2
    assert f'name="klucz_idempotencji" value="{klucz_1}"' in klient.get("/app/wnioski/nowy").text

    dane = {**WNIOSEK, "klucz_idempotencji": klucz_1}
    pierwsza = klient.post("/app/wnioski/nowy", data=dane)
    ponowiona = klient.post("/app/wnioski/nowy", data=dane)

    assert pierwsza.status_code == ponowiona.status_code == 303
    assert pierwsza.headers["location"] == ponowiona.headers["location"]
    assert liczba_delegacji() == 1
    assert len(broker.wyslane) == 1


def test_blad_finalnego_zapisu_pokazuje_ponowienie_i_zachowuje_dane(klient, broker, monkeypatch):
    podsumowanie = klient.post("/app/wnioski/nowy/podsumowanie", data=WNIOSEK).text
    klucz = re.search(r'name="klucz_idempotencji" value="([a-f0-9]+)"', podsumowanie)[1]
    from kwatermistrz.web import strony

    prawdziwy_zapis = strony.zarejestruj_wniosek

    async def przerwany_zapis(*args, **kwargs):
        raise OperationalError("INSERT", {}, Exception("przerwane polaczenie"))

    monkeypatch.setattr(strony, "zarejestruj_wniosek", przerwany_zapis)
    r = klient.post("/app/wnioski/nowy", data={**WNIOSEK, "klucz_idempotencji": klucz})

    assert r.status_code == 503
    assert 'role="alert"' in r.text and "Nie udało się bezpiecznie zapisać wniosku" in r.text
    assert 'value="Toruń"' in r.text and f'value="{klucz}"' in r.text
    assert f'name="csrf" value="{klient.headers["X-CSRF-Token"]}"' in r.text
    assert "Spróbuj ponownie" in r.text and "wersji roboczej" in r.text
    assert liczba_delegacji() == 0 and broker.wyslane == []

    monkeypatch.setattr(strony, "zarejestruj_wniosek", prawdziwy_zapis)
    ponowienie = klient.post("/app/wnioski/nowy", data={**WNIOSEK, "klucz_idempotencji": klucz})
    assert ponowienie.status_code == 303
    assert liczba_delegacji() == 1 and len(broker.wyslane) == 1


def test_niekompletne_dane_wracaja_do_formularza_z_bledami(klient):
    dane = {**WNIOSEK, "cel_wyjazdu": "", "data_powrotu": "2026-12-01"}
    strona = klient.post("/app/wnioski/nowy/podsumowanie", data=dane).text
    assert "Wniosku nie można jeszcze złożyć" in strona
    assert "Uzupełnij cel wyjazdu" in strona and "Data powrotu nie może być wcześniejsza" in strona
    assert 'value="Toruń"' in strona and 'aria-invalid="true"' in strona
    assert liczba_delegacji() == 0


def test_popraw_dane_wraca_do_formularza_z_wpisanymi_wartosciami(klient):
    strona = klient.post("/app/wnioski/nowy/podsumowanie?popraw=1", data=WNIOSEK).text
    assert 'id="formularz-wniosku"' in strona and 'value="Rozkaz dzienny nr 215/2026"' in strona
    assert "Wniosku nie można jeszcze złożyć" not in strona


def test_przedsiewziecie_uzupelnia_puste_pola_w_podsumowaniu(klient):
    dane = {"id_przedsiewziecia": "PRZ-01", "podstawa_numer_rozkazu": "R 1/2026", "podstawa_data_rozkazu": "2026-11-10"}
    strona = klient.post("/app/wnioski/nowy/podsumowanie", data=dane).text
    assert "Podsumowanie wniosku" in strona and "Toruń" in strona and "Szkolenie z logistyki wojskowej" in strona


def test_pojazd_prywatny_wymaga_danych_pojazdu_i_trafia_do_wniosku(klient):
    dane = {**WNIOSEK, "srodek_transportu": "samochód prywatny"}
    strona = klient.post("/app/wnioski/nowy/podsumowanie", data=dane).text
    assert "Uzupełnij numer rejestracyjny" in strona

    dane |= {"nr_rejestracyjny": "WX 12345", "pojemnosc_silnika": "1598"}
    assert "Podsumowanie wniosku" in klient.post("/app/wnioski/nowy/podsumowanie", data=dane).text

    r = klient.post("/app/wnioski/nowy", data=dane)
    assert r.status_code == 303
    with sesja() as s:
        d = s.scalar(select(Delegacja))
        assert d.dane_wniosku["dane_transportu"] == {"nr_rejestracyjny": "WX 12345", "pojemnosc_silnika": "1598"}


def test_potwierdzenie_zlozenia_wniosku(klient):
    r = klient.post("/app/wnioski/nowy", data=WNIOSEK)
    strona = klient.get(r.headers["location"]).text
    assert "Wniosek przyjęty" in strona and "status „złożony”" in strona
