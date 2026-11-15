from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from kwatermistrz.api import rozliczenia as api_rozliczenia
from kwatermistrz.api.main import app, get_broker
from kwatermistrz.db import sesja
from kwatermistrz.models import Delegacja, DokumentKosztowy, LogOperacji, Rozliczenie, Status


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def delegacja_do_rozliczenia(id_delegacji: str, id_zolnierza: str = "ZOL-003") -> None:
    with sesja() as s:
        s.add(
            Delegacja(
                id_delegacji=id_delegacji,
                id_zolnierza=id_zolnierza,
                dane_wniosku={},
                status=Status.DO_ROZLICZENIA,
            )
        )


def dokument(**zmiany) -> dict:
    dane = {"rodzaj": "Bilet", "numer": "IC 12345", "data": "2026-11-12", "kwota": "123.40"}
    return {**dane, **zmiany}


def test_nowy_dokument_zostaje_przypisany_do_biezacego_rozliczenia(klient):
    delegacja_do_rozliczenia("DEL-R-1")

    odpowiedz = klient.post("/delegacje/DEL-R-1/rozliczenie/dokumenty", json=dokument())

    assert odpowiedz.status_code == 201
    assert odpowiedz.json()["przyjeto"] is True
    with sesja() as s:
        rozliczenie = s.scalar(select(Rozliczenie).where(Rozliczenie.id_delegacji == "DEL-R-1"))
        zapisany = s.scalar(select(DokumentKosztowy))
        wpis = s.scalar(
            select(LogOperacji).where(
                LogOperacji.id_delegacji == "DEL-R-1",
                LogOperacji.operacja == "Dodanie dokumentu kosztowego",
            )
        )
        assert zapisany.id_rozliczenia == rozliczenie.id_rozliczenia
        assert zapisany.kwota == Decimal("123.40")
        assert wpis is not None
        assert wpis.id_agenta == "AG-06"
        assert wpis.id_korelacji == "COR-DEL-R-1"
        assert wpis.wynik == "Sukces"


def test_ten_sam_dokument_jest_odrzucony_w_innym_rozliczeniu_i_odrzucenie_jest_logowane(klient):
    delegacja_do_rozliczenia("DEL-R-2")
    delegacja_do_rozliczenia("DEL-R-3", "ZOL-004")
    assert klient.post("/delegacje/DEL-R-2/rozliczenie/dokumenty", json=dokument()).status_code == 201

    odpowiedz = klient.post("/delegacje/DEL-R-3/rozliczenie/dokumenty", json=dokument())

    assert odpowiedz.status_code == 409
    assert odpowiedz.json()["przyjeto"] is False
    assert "już zarejestrowany" in odpowiedz.json()["komunikat"]
    with sesja() as s:
        assert s.scalar(select(func.count()).select_from(DokumentKosztowy)) == 1
        wpis = s.scalar(
            select(LogOperacji).where(
                LogOperacji.id_delegacji == "DEL-R-3",
                LogOperacji.operacja == "Dodanie dokumentu kosztowego",
            )
        )
        assert wpis is not None
        assert wpis.id_agenta == "AG-06"
        assert wpis.id_korelacji == "COR-DEL-R-3"
        assert wpis.wynik == "Ostrzeżenie"
        assert "duplikat" in wpis.komunikat.lower()


def test_roznice_w_wielkosci_liter_spacjach_i_formacie_kwoty_nie_omijaja_blokady(klient):
    delegacja_do_rozliczenia("DEL-R-4")
    delegacja_do_rozliczenia("DEL-R-5", "ZOL-004")
    pierwszy = dokument(rodzaj="Bilet", numer=" IC   12345 ", kwota="123.4")
    wariant = dokument(rodzaj="Bilet", numer="ic 12345", kwota="123.40")
    assert klient.post("/delegacje/DEL-R-4/rozliczenie/dokumenty", json=pierwszy).status_code == 201

    odpowiedz = klient.post("/delegacje/DEL-R-5/rozliczenie/dokumenty", json=wariant)

    assert odpowiedz.status_code == 409
    assert "numerze, dacie i kwocie" in odpowiedz.json()["komunikat"]
    with sesja() as s:
        assert s.scalar(select(func.count()).select_from(DokumentKosztowy)) == 1
        assert s.scalar(select(DokumentKosztowy)).klucz_unikalnosci == "ic 12345|2026-11-12|123.40"


def test_rozny_rodzaj_nie_pozwala_ponownie_dodac_tego_samego_dokumentu(klient):
    delegacja_do_rozliczenia("DEL-R-RODZAJ-1")
    delegacja_do_rozliczenia("DEL-R-RODZAJ-2", "ZOL-004")
    assert klient.post("/delegacje/DEL-R-RODZAJ-1/rozliczenie/dokumenty", json=dokument()).status_code == 201

    odpowiedz = klient.post(
        "/delegacje/DEL-R-RODZAJ-2/rozliczenie/dokumenty",
        json=dokument(rodzaj="Faktura"),
    )

    assert odpowiedz.status_code == 409
    with sesja() as s:
        assert s.scalar(select(func.count()).select_from(DokumentKosztowy)) == 1


@pytest.mark.parametrize(
    "zmiana",
    [
        {"numer": "IC 12346"},
        {"data": "2026-11-13"},
        {"kwota": "123.41"},
    ],
)
def test_dokumenty_rozniace_sie_numerem_data_lub_kwota_sa_przyjmowane(klient, zmiana):
    delegacja_do_rozliczenia("DEL-R-ROZNE-1")
    delegacja_do_rozliczenia("DEL-R-ROZNE-2", "ZOL-004")
    assert klient.post("/delegacje/DEL-R-ROZNE-1/rozliczenie/dokumenty", json=dokument()).status_code == 201

    odpowiedz = klient.post(
        "/delegacje/DEL-R-ROZNE-2/rozliczenie/dokumenty",
        json=dokument(rodzaj="Faktura", **zmiana),
    )

    assert odpowiedz.status_code == 201
    with sesja() as s:
        assert s.scalar(select(func.count()).select_from(DokumentKosztowy)) == 2


def test_dokument_mozna_dodac_tylko_do_delegacji_wymagajacej_rozliczenia(klient):
    with sesja() as s:
        s.add(Delegacja(id_delegacji="DEL-R-6", id_zolnierza="ZOL-003", dane_wniosku={}, status=Status.W_TRAKCIE))

    odpowiedz = klient.post("/delegacje/DEL-R-6/rozliczenie/dokumenty", json=dokument())

    assert odpowiedz.status_code == 409
    with sesja() as s:
        assert s.scalar(select(func.count()).select_from(DokumentKosztowy)) == 0


def test_konflikt_unikalnosci_w_bazie_jest_czytelnym_odrzuceniem(klient, monkeypatch):
    delegacja_do_rozliczenia("DEL-R-7")
    delegacja_do_rozliczenia("DEL-R-8", "ZOL-004")
    assert klient.post("/delegacje/DEL-R-7/rozliczenie/dokumenty", json=dokument()).status_code == 201
    oryginalne_wyszukiwanie = api_rozliczenia.znajdz_duplikat
    liczba_wyszukan = 0

    def pomin_pierwsze_wyszukanie(s, klucz):
        nonlocal liczba_wyszukan
        liczba_wyszukan += 1
        return None if liczba_wyszukan == 1 else oryginalne_wyszukiwanie(s, klucz)

    monkeypatch.setattr(api_rozliczenia, "znajdz_duplikat", pomin_pierwsze_wyszukanie)

    odpowiedz = klient.post(
        "/delegacje/DEL-R-8/rozliczenie/dokumenty",
        json=dokument(rodzaj="Faktura"),
    )

    assert odpowiedz.status_code == 409
    assert odpowiedz.json()["przyjeto"] is False
    with sesja() as s:
        assert s.scalar(select(func.count()).select_from(DokumentKosztowy)) == 1
        wpis = s.scalar(select(LogOperacji).where(LogOperacji.id_delegacji == "DEL-R-8"))
        assert wpis is not None and wpis.wynik == "Ostrzeżenie"
