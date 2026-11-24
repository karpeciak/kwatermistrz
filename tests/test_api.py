import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from kwatermistrz.api.main import app, get_broker
from kwatermistrz.api.schemas import WniosekIn
from kwatermistrz.api.wnioski import zarejestruj_wniosek
from kwatermistrz.db import sesja
from kwatermistrz.models import Delegacja, Status

WNIOSEK = {
    "id_zolnierza": "ZOL-003",
    "id_przedsiewziecia": "PRZ-01",
    "cel_wyjazdu": "Szkolenie z logistyki wojskowej",
    "miejsce_docelowe": "Toruń",
    "data_wyjazdu": "2026-12-07",
    "data_powrotu": "2026-12-11",
    "srodek_transportu": "pociąg",
}


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_health(klient):
    assert klient.get("/health").json()["status"] == "ok"


def test_zlozenie_wniosku_publikuje_zdarzenie(klient, broker):
    r = klient.post("/wnioski", json=WNIOSEK)
    assert r.status_code == 202
    assert r.json()["status"] == "złożony"
    assert broker.typy() == ["wniosek.zlozony"]


def test_ponowienie_z_tym_samym_kluczem_nie_tworzy_duplikatu_s22(klient, broker):
    naglowki = {"Idempotency-Key": "wniosek-abc-123"}
    r1 = klient.post("/wnioski", json=WNIOSEK, headers=naglowki)
    r2 = klient.post("/wnioski", json=WNIOSEK, headers=naglowki)
    assert r1.json()["id_delegacji"] == r2.json()["id_delegacji"]
    assert len(broker.wyslane) == 1


def test_klucz_idempotencji_ma_limit_zgodny_z_baza(klient, broker):
    r = klient.post("/wnioski", json=WNIOSEK, headers={"Idempotency-Key": "x" * 65})
    assert r.status_code == 422
    assert broker.wyslane == []


def test_ten_sam_klucz_z_innymi_danymi_jest_odrzucany(klient, broker):
    naglowki = {"Idempotency-Key": "wniosek-inny-payload"}
    assert klient.post("/wnioski", json=WNIOSEK, headers=naglowki).status_code == 202

    r = klient.post("/wnioski", json={**WNIOSEK, "miejsce_docelowe": "Gdynia"}, headers=naglowki)

    assert r.status_code == 409
    assert "innymi danymi" in r.json()["detail"]
    with sesja() as s:
        assert s.scalar(select(func.count()).select_from(Delegacja).where(Delegacja.status != Status.ROBOCZY)) == 1
    assert len(broker.wyslane) == 1


def test_konflikt_unikalnosci_po_rownoleglym_zapisie_zwraca_zwycieski_wniosek(broker, monkeypatch):
    wniosek = WniosekIn(**WNIOSEK)
    with sesja() as s:
        istniejaca = Delegacja(
            id_delegacji="DEL-ROWN-1",
            id_zolnierza=wniosek.id_zolnierza,
            id_przedsiewziecia=wniosek.id_przedsiewziecia,
            dane_wniosku=wniosek.model_dump(exclude={"id_zolnierza", "id_przedsiewziecia"}),
            status=Status.ZLOZONY,
            klucz_idempotencji="rownolegly-klucz",
        )
        s.add(istniejaca)
        s.commit()
        prawdziwy_scalar = s.scalar
        pierwsze_sprawdzenie = True

        def scalar_z_nieaktualnym_pierwszym_odczytem(*args, **kwargs):
            nonlocal pierwsze_sprawdzenie
            if pierwsze_sprawdzenie:
                pierwsze_sprawdzenie = False
                return None
            return prawdziwy_scalar(*args, **kwargs)

        monkeypatch.setattr(s, "scalar", scalar_z_nieaktualnym_pierwszym_odczytem)
        wynik = asyncio.run(zarejestruj_wniosek(s, broker, wniosek, "rownolegly-klucz"))

        assert wynik.id_delegacji == "DEL-ROWN-1"
        assert broker.wyslane == []


def test_inny_blad_integralnosci_nie_jest_traktowany_jak_udane_ponowienie(broker, monkeypatch):
    with sesja() as s:
        prawdziwy_commit = s.commit

        def blad_innej_reguly():
            raise IntegrityError("INSERT", {}, Exception("NOT NULL constraint failed: delegacja.status"))

        monkeypatch.setattr(s, "commit", blad_innej_reguly)
        with pytest.raises(IntegrityError, match="NOT NULL"):
            asyncio.run(zarejestruj_wniosek(s, broker, WniosekIn(**WNIOSEK), "klucz-inny-blad"))
        monkeypatch.setattr(s, "commit", prawdziwy_commit)
        s.rollback()
        assert broker.wyslane == []


def test_nieznany_zolnierz(klient):
    assert klient.post("/wnioski", json={**WNIOSEK, "id_zolnierza": "ZOL-999"}).status_code == 404


def test_decyzja_dla_nieistniejacej_delegacji(klient):
    r = klient.post("/delegacje/DEL-X/decyzja", json={"id_osoby": "ZOL-001", "decyzja": "Zgoda"})
    assert r.status_code == 404
