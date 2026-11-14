"""Interfejs /app: logowanie demonstracyjne, złożenie wniosku, podgląd statusu, decyzja przełożonego."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from kwatermistrz.agents import AGENCI
from kwatermistrz.api.main import app, get_broker
from kwatermistrz.db import sesja
from kwatermistrz.models import Delegacja, DokumentKosztowy, Status


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()


def zaloguj(klient, id_zolnierza):
    r = klient.post("/app/logowanie", data={"id_zolnierza": id_zolnierza})
    assert r.status_code == 303


def test_bez_logowania_przekierowuje_do_logowania(klient):
    assert klient.get("/").headers["location"] == "/app"
    r = klient.get("/app/pulpit")
    assert r.status_code == 303 and r.headers["location"] == "/app/logowanie"
    assert "ZOL-003" in klient.get("/app/logowanie").text


def test_pelny_scenariusz_wniosku_w_interfejsie(klient, broker):
    zaloguj(klient, "ZOL-003")
    assert "Nie masz jeszcze żadnych wniosków" in klient.get("/app/pulpit").text
    assert "PRZ-01" in klient.get("/app/wnioski/nowy").text

    # przedsięwzięcie uzupełnia puste pola: cel, miejsce i daty
    r = klient.post(
        "/app/wnioski/nowy",
        data={"id_przedsiewziecia": "PRZ-01", "srodek_transportu": "kolej", "potrzeba_zakwaterowania": "true"},
    )
    assert r.status_code == 303
    id_d = r.headers["location"].rsplit("/", 1)[-1]

    asyncio.run(broker.dostarczaj([klasa(broker) for klasa in AGENCI.values()]))
    strona = klient.get(f"/app/wnioski/{id_d}").text
    assert "w akceptacji" in strona and "Michał Stępień" in strona and "Walidacja wniosku" in strona

    zaloguj(klient, "ZOL-001")
    lista = klient.get("/app/do-zatwierdzenia").text
    assert id_d in lista and "Magdalena Szewczyk" in lista

    odp = klient.post(f"/app/do-zatwierdzenia/{id_d}", data={"decyzja": "Zgoda"})
    assert "decyzja przekazana" in odp.text
    asyncio.run(broker.dostarczaj([klasa(broker) for klasa in AGENCI.values()]))
    with sesja() as s:
        assert s.get(Delegacja, id_d).status == Status.ZATWIERDZONY


def test_odmowa_wymaga_uzasadnienia(klient, broker):
    with sesja() as s:
        s.add(
            Delegacja(
                id_delegacji="DEL-UI-1",
                id_zolnierza="ZOL-004",
                dane_wniosku={},
                status=Status.W_AKCEPTACJI,
                id_zatwierdzajacego="ZOL-001",
            )
        )
    zaloguj(klient, "ZOL-001")
    odp = klient.post("/app/do-zatwierdzenia/DEL-UI-1", data={"decyzja": "Odmowa", "uzasadnienie": ""})
    assert "Podaj uzasadnienie" in odp.text and broker.wyslane == []


def test_cudzy_wniosek_jest_niedostepny(klient):
    with sesja() as s:
        s.add(Delegacja(id_delegacji="DEL-UI-2", id_zolnierza="ZOL-004", dane_wniosku={}))
    zaloguj(klient, "ZOL-003")
    assert klient.get("/app/wnioski/DEL-UI-2").status_code == 404


def test_delegowany_dodaje_dokument_na_stronie_rozliczenia(klient):
    with sesja() as s:
        s.add(
            Delegacja(
                id_delegacji="DEL-UI-R",
                id_zolnierza="ZOL-003",
                dane_wniosku={},
                status=Status.DO_ROZLICZENIA,
            )
        )
    zaloguj(klient, "ZOL-003")

    strona_wniosku = klient.get("/app/wnioski/DEL-UI-R")
    assert "/app/wnioski/DEL-UI-R/rozliczenie" in strona_wniosku.text
    assert klient.get("/app/wnioski/DEL-UI-R/rozliczenie").status_code == 200

    odpowiedz = klient.post(
        "/app/wnioski/DEL-UI-R/rozliczenie/dokumenty",
        data={"rodzaj": "bilet", "numer": "IC 987", "data": "2026-11-12", "kwota": "89.00"},
    )

    assert odpowiedz.status_code == 303
    assert odpowiedz.headers["location"] == "/app/wnioski/DEL-UI-R/rozliczenie?wynik=dokument-przyjety"

    strona_po_przekierowaniu = klient.get(odpowiedz.headers["location"])
    assert strona_po_przekierowaniu.status_code == 200
    assert "Dokument kosztowy został przyjęty" in strona_po_przekierowaniu.text

    odswiezona_strona = klient.get(odpowiedz.headers["location"])
    assert odswiezona_strona.status_code == 200
    assert "Dokument kosztowy został przyjęty" in odswiezona_strona.text
    with sesja() as s:
        assert s.scalar(select(func.count()).select_from(DokumentKosztowy)) == 1

    duplikat = klient.post(
        "/app/wnioski/DEL-UI-R/rozliczenie/dokumenty",
        data={"rodzaj": " BILET ", "numer": "ic   987", "data": "2026-11-12", "kwota": "89.0"},
    )
    assert duplikat.status_code == 200
    assert "jest już zarejestrowany" in duplikat.text
    with sesja() as s:
        assert s.scalar(select(DokumentKosztowy)).numer == "IC 987"


def test_wniosek_z_bledami_pokazuje_co_uzupelnic(klient, broker):
    zaloguj(klient, "ZOL-003")
    r = klient.post(
        "/app/wnioski/nowy",
        data={
            "cel_wyjazdu": "",
            "miejsce_docelowe": "Toruń",
            "data_wyjazdu": "2026-12-11",
            "data_powrotu": "2026-12-07",
        },
    )
    id_d = r.headers["location"].rsplit("/", 1)[-1]
    asyncio.run(broker.dostarczaj([klasa(broker) for klasa in AGENCI.values()]))
    strona = klient.get(f"/app/wnioski/{id_d}").text
    assert "do uzupełnienia" in strona
    assert "Uzupełnij cel wyjazdu" in strona and "Data powrotu nie może być wcześniejsza" in strona
