"""Własny wniosek, zatwierdzenie, lista podróży i rzetelny stan oczekiwania."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from kwatermistrz.api.main import app, get_broker
from kwatermistrz.api.schemas import WniosekIn
from kwatermistrz.api.wnioski import zarejestruj_wniosek
from kwatermistrz.db import sesja
from kwatermistrz.lokalnie import BrokerLokalny
from kwatermistrz.messaging import Komunikat, Typ
from kwatermistrz.models import Delegacja, Status, Zolnierz
from kwatermistrz.podroze import zestawienie
from kwatermistrz.web.strony import dane_wniosku


async def wlasny_wniosek(broker):
    with sesja() as s:
        d = await zarejestruj_wniosek(
            s,
            broker,
            WniosekIn(
                id_zolnierza="ZOL-003",
                id_przedsiewziecia="PRZ-02",
                cel_wyjazdu="Samodzielny test",
                miejsce_docelowe="Zegrze",
                data_wyjazdu="2026-12-14",
                data_powrotu="2026-12-18",
                srodek_transportu="kolej",
                potrzeba_zakwaterowania=False,
            ),
        )
        identyfikator = d.id_delegacji
    with sesja() as s:
        assert s.get(Delegacja, identyfikator).status == Status.W_AKCEPTACJI
    await broker.publikuj(
        Komunikat(
            typ=Typ.DECYZJA_PRZELOZONEGO,
            id_delegacji=identyfikator,
            dane={"id_osoby": "ZOL-001", "decyzja": "Zgoda"},
        )
    )
    return identyfikator


def test_wlasny_wniosek_przechodzi_do_wyboru_i_pokazuje_co_blokuje_organizacje():
    broker = BrokerLokalny()
    identyfikator = asyncio.run(wlasny_wniosek(broker))
    with sesja() as s:
        d = s.get(Delegacja, identyfikator)
        osoba = s.get(Zolnierz, "ZOL-001")
        stan = dane_wniosku(s, identyfikator, osoba)
        assert "Oczekiwanie na wybór" in stan["oczekiwanie"]
        assert not stan["w_toku"]
        propozycje = zestawienie(s, d)["warianty"]
        wybor = {
            "id_tam" if w["kierunek"] == "Tam" else "id_powrot": w["id_przejazdu"]
            for w in propozycje
            if w["rekomendowany"]
        }
    asyncio.run(
        broker.publikuj(
            Komunikat(
                typ=Typ.WYBOR_TRANSPORTU,
                id_delegacji=identyfikator,
                dane={**wybor, "id_osoby": "ZOL-001"},
            )
        )
    )
    with sesja() as s:
        stan = dane_wniosku(s, identyfikator, s.get(Zolnierz, "ZOL-001"))
        assert "Transport został wybrany" in stan["oczekiwanie"]
        assert "polecenie wyjazdu" in stan["oczekiwanie"]
        assert not stan["w_toku"]


@pytest.fixture
def klient_lokalny():
    broker = BrokerLokalny()
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app, follow_redirects=False) as klient:
        yield klient
    app.dependency_overrides.clear()


def test_wlasny_wniosek_jest_dostepny_na_liscie_podrozy_po_zatwierdzeniu(klient_lokalny):
    klient = klient_lokalny
    assert klient.post("/app/logowanie", data={"id_zolnierza": "ZOL-003"}).status_code == 303
    r = klient.post("/app/wnioski/nowy", data={"id_przedsiewziecia": "PRZ-02"})
    assert r.status_code == 303
    url_wniosku = r.headers["location"]
    identyfikator = url_wniosku.rsplit("/", 1)[-1]
    klient.post("/app/logowanie", data={"id_zolnierza": "ZOL-001"})
    assert identyfikator in klient.get("/app/do-zatwierdzenia").text
    assert identyfikator not in klient.get("/app/podroze").text
    klient.post(f"/app/do-zatwierdzenia/{identyfikator}", data={"decyzja": "Zgoda"})
    assert f"{url_wniosku}/podroz" in klient.get("/app/podroze").text
    stan = klient.get(url_wniosku + "/stan")
    assert stan.status_code == 200 and "Oczekiwanie na wybór" in stan.text
    assert "agenci pracują" not in stan.text and 'hx-trigger="every 3s"' not in stan.text
    klient.post("/app/logowanie", data={"id_zolnierza": "ZOL-004"})
    assert identyfikator not in klient.get("/app/podroze").text
    assert klient.get(url_wniosku + "/podroz").status_code == 404
