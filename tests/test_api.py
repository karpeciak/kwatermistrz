import pytest
from fastapi.testclient import TestClient

from kwatermistrz.api.main import app, get_broker

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


def test_nieznany_zolnierz(klient):
    assert klient.post("/wnioski", json={**WNIOSEK, "id_zolnierza": "ZOL-999"}).status_code == 404


def test_decyzja_dla_nieistniejacej_delegacji(klient):
    r = klient.post("/delegacje/DEL-X/decyzja", json={"id_osoby": "ZOL-001", "decyzja": "Zgoda"})
    assert r.status_code == 404
