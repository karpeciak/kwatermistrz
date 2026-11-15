"""S22IT1S0P2-36: przebieg rozliczenia – wyliczenie (-61), przekroczenie limitu (-63), zatwierdzenie i budżet (-66).

Szczegółowe testy reguł wyliczenia (progi 8 h / 12 h, posiłki, ryczałty) – podzadanie S22IT1S0P2-67.
"""

import asyncio
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pomoc_sesji import zaloguj_z_csrf
from sqlalchemy import select

from kwatermistrz.agents import AGENCI
from kwatermistrz.agents.rozliczenia import TYP_DECYZJI
from kwatermistrz.api.main import app, get_broker
from kwatermistrz.db import sesja
from kwatermistrz.messaging import Typ
from kwatermistrz.models import Delegacja, DokumentKosztowy, Jednostka, LogDecyzji, Rozliczenie, Status

ID = "DEL-ROZ-36"
PODROZ = {
    "faktyczny_wyjazd": "2026-12-07T06:10",
    "faktyczny_powrot": "2026-12-11T19:05",
    "sniadania": "2",
    "obiady": "1",
    "kolacje": "0",
}
DOKUMENTY = [
    {"rodzaj": "bilet", "numer": "IC 1001", "data": "2026-12-07", "kwota": "89.00"},
    {"rodzaj": "bilet", "numer": "IC 1002", "data": "2026-12-11", "kwota": "89.00"},
    {"rodzaj": "rachunek za nocleg", "numer": "H/2026/77", "data": "2026-12-11", "kwota": "1360.00"},
]


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app, follow_redirects=False) as c:
        zaloguj_z_csrf(c, "ZOL-003")
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def delegacja_do_rozliczenia():
    with sesja() as s:
        s.add(
            Delegacja(
                id_delegacji=ID,
                id_zolnierza="ZOL-003",
                dane_wniosku={},
                status=Status.DO_ROZLICZENIA,
                szacowany_koszt=Decimal("1670.00"),
            )
        )
        j = s.get(Jednostka, "JW-1001")
        j.budzet_wykorzystany = Decimal("5000.00")


def dostarcz(broker) -> None:
    asyncio.run(broker.dostarczaj([klasa(broker) for klasa in AGENCI.values()]))


def rozliczenie() -> Rozliczenie:
    with sesja() as s:
        return s.scalar(select(Rozliczenie).where(Rozliczenie.id_delegacji == ID))


def przekaz_dane(klient, broker) -> None:
    assert klient.post(f"/app/wnioski/{ID}/rozliczenie/podroz", data=PODROZ).status_code == 303
    for dokument in DOKUMENTY:
        assert klient.post(f"/app/wnioski/{ID}/rozliczenie/dokumenty", data=dokument).status_code == 303
    assert Typ.ZADANIE_ROZLICZENIA in broker.typy()
    dostarcz(broker)


def test_wyliczenie_naleznosci_z_oznaczeniem_przekroczenia_limitu(klient, broker):
    przekaz_dane(klient, broker)

    r = rozliczenie()
    # 4 doby 12 h 55 min → 5 diet (225 zł) − 2 śniadania i obiad (45 zł); 5 rozpoczętych dób × 9 zł; 4 noclegi × 300 zł
    assert (r.dieta, r.zwrot_przejazdu, r.zwrot_noclegu, r.ryczalty) == (
        Decimal("180.00"),
        Decimal("178.00"),
        Decimal("1200.00"),
        Decimal("45.00"),
    )
    assert r.kwota_naleznosci == Decimal("1603.00")
    with sesja() as s:
        rachunek = s.scalar(select(DokumentKosztowy).where(DokumentKosztowy.numer == "H/2026/77"))
        assert rachunek.przekracza_limit and rachunek.kwota_uznana == Decimal("1200.00")
        decyzja = s.scalar(select(LogDecyzji).where(LogDecyzji.typ_decyzji == TYP_DECYZJI))
        assert decyzja.wymaga_zatwierdzenia  # decyzja finansowa ponad limit – do sprawdzenia przez człowieka

    strona = klient.get(f"/app/wnioski/{ID}/rozliczenie").text
    assert "1603.00 zł" in strona and "Przekroczenie limitu" in strona and "Zatwierdź wyliczenie" in strona


def test_zatwierdzenie_wyliczenia_i_aktualizacja_budzetu(klient, broker):
    przekaz_dane(klient, broker)

    r = klient.post(f"/app/wnioski/{ID}/rozliczenie/zatwierdzenie")
    assert r.status_code == 303
    strona = klient.get(r.headers["location"]).text
    assert "gotowe do weryfikacji" in strona and "Dodaj dokument" not in strona
    assert rozliczenie().status == "gotowe do weryfikacji"
    assert Typ.WYNIK_ROZLICZENIA in broker.typy()
    with sesja() as s:
        j = s.get(Jednostka, "JW-1001")
        assert j.budzet_wykorzystany == Decimal("3330.00")  # zaangażowanie zwolnione o szacowany koszt
        assert j.budzet_wydatkowany == Decimal("1603.00")  # wydatek = należność

    # zatwierdzonego rozliczenia nie można już zmieniać
    assert "zostało już zatwierdzone" in klient.post(f"/app/wnioski/{ID}/rozliczenie/zatwierdzenie").text
    assert "zatwierdzone" in klient.post(f"/app/wnioski/{ID}/rozliczenie/podroz", data=PODROZ).text


def test_bez_czasu_podrozy_nie_ma_wyliczenia(klient, broker):
    klient.post(f"/app/wnioski/{ID}/rozliczenie/dokumenty", data=DOKUMENTY[0])
    assert Typ.ZADANIE_ROZLICZENIA not in broker.typy()
    strona = klient.get(f"/app/wnioski/{ID}/rozliczenie").text
    assert "Wyliczenie pojawi się tutaj" in strona
    assert "Nie ma jeszcze wyliczenia" in klient.post(f"/app/wnioski/{ID}/rozliczenie/zatwierdzenie").text


def test_api_rozliczenia(klient, broker):
    dane = {
        "faktyczny_wyjazd": "2026-12-07T06:10:00",
        "faktyczny_powrot": "2026-12-11T19:05:00",
        "zapewnione_posilki": {"śniadanie": 2, "obiad": 1},
    }
    assert klient.post(f"/delegacje/{ID}/rozliczenie", json=dane).status_code == 202
    zle = {**dane, "faktyczny_powrot": "2026-12-01T10:00:00"}
    assert klient.post(f"/delegacje/{ID}/rozliczenie", json=zle).status_code == 422
    for dokument in DOKUMENTY:
        klient.post(f"/delegacje/{ID}/rozliczenie/dokumenty", json=dokument)
    dostarcz(broker)

    zestawienie = klient.get(f"/delegacje/{ID}/rozliczenie").json()
    assert Decimal(str(zestawienie["wyliczenie"]["kwota_naleznosci"])) == Decimal("1603.00")
    assert [d["przekracza_limit"] for d in zestawienie["dokumenty"]] == [False, False, True]

    assert klient.post(f"/delegacje/{ID}/rozliczenie/zatwierdzenie").status_code == 200
    assert klient.post(f"/delegacje/{ID}/rozliczenie/zatwierdzenie").status_code == 409
