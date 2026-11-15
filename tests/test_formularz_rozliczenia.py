"""Formularz rozliczenia delegacji (S22IT1S0P2-36, podzadanie S22IT1S0P2-60)."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pomoc_sesji import zaloguj_z_csrf
from sqlalchemy import func, select

from kwatermistrz.api.main import app, get_broker
from kwatermistrz.db import sesja
from kwatermistrz.models import Delegacja, DokumentKosztowy, Rozliczenie, Status


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app, follow_redirects=False) as c:
        zaloguj_z_csrf(c, "ZOL-003")
        yield c
    app.dependency_overrides.clear()


def delegacja_do_rozliczenia() -> None:
    with sesja() as s:
        s.add(
            Delegacja(id_delegacji="DEL-ROZ-1", id_zolnierza="ZOL-003", dane_wniosku={}, status=Status.DO_ROZLICZENIA)
        )


def test_formularz_rozliczenia_ma_wszystkie_sekcje(klient):
    delegacja_do_rozliczenia()
    strona = klient.get("/app/wnioski/DEL-ROZ-1/rozliczenie").text
    assert "Faktyczny czas podróży" in strona and 'type="datetime-local"' in strona
    assert "Śniadania" in strona and "bezpłatne zakwaterowanie" in strona
    assert '<option value="rachunek za nocleg">' in strona
    assert "Wyliczenie pojawi się tutaj" in strona


def test_zapis_czasu_podrozy_i_swiadczen(klient):
    delegacja_do_rozliczenia()
    dane = {
        "faktyczny_wyjazd": "2026-12-07T06:10",
        "faktyczny_powrot": "2026-12-11T19:05",
        "sniadania": "2",
        "obiady": "1",
        "kolacje": "0",
        "bezplatne_zakwaterowanie": "true",
    }
    r = klient.post("/app/wnioski/DEL-ROZ-1/rozliczenie/podroz", data=dane)
    assert r.status_code == 303
    strona = klient.get(r.headers["location"]).text
    assert "Zapisano faktyczny czas podróży" in strona and 'value="2026-12-07T06:10"' in strona

    with sesja() as s:
        rozliczenie = s.scalar(select(Rozliczenie).where(Rozliczenie.id_delegacji == "DEL-ROZ-1"))
        assert rozliczenie.faktyczny_wyjazd == datetime(2026, 12, 7, 6, 10)
        assert rozliczenie.zapewnione_posilki == {"śniadanie": 2, "obiad": 1}
        assert rozliczenie.bezplatne_zakwaterowanie is True


def test_powrot_przed_wyjazdem_jest_odrzucany(klient):
    delegacja_do_rozliczenia()
    dane = {"faktyczny_wyjazd": "2026-12-11T19:05", "faktyczny_powrot": "2026-12-07T06:10"}
    strona = klient.post("/app/wnioski/DEL-ROZ-1/rozliczenie/podroz", data=dane).text
    assert "Powrót musi nastąpić po wyjeździe" in strona
    with sesja() as s:
        assert s.scalar(select(func.count()).select_from(Rozliczenie)) == 0


def test_wyliczenie_pokazuje_skladniki_i_przekroczenie_limitu(klient):
    delegacja_do_rozliczenia()
    with sesja() as s:
        r = Rozliczenie(
            id_delegacji="DEL-ROZ-1",
            dieta=Decimal("225.00"),
            zwrot_przejazdu=Decimal("178.00"),
            zwrot_noclegu=Decimal("1200.00"),
            ryczalty=Decimal("45.00"),
            kwota_naleznosci=Decimal("1648.00"),
        )
        s.add(r)
        s.flush()
        s.add(
            DokumentKosztowy(
                id_rozliczenia=r.id_rozliczenia,
                rodzaj="rachunek za nocleg",
                numer="H/2026/77",
                data=date(2026, 12, 11),
                kwota=Decimal("1360.00"),
                kwota_uznana=Decimal("1200.00"),
                przekracza_limit=True,
                klucz_unikalnosci="rachunek za nocleg|h/2026/77|2026-12-11|1360.00",
            )
        )
    strona = klient.get("/app/wnioski/DEL-ROZ-1/rozliczenie").text
    assert "Kwota należności" in strona and "1648.00 zł" in strona
    assert "ponad limit" in strona and "1200.00 zł" in strona
