"""S22IT1S0P2-35: zbiorcze zatwierdzenie wniosków oczekujących na decyzję przełożonego."""

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from kwatermistrz.agents import AGENCI
from kwatermistrz.agents.zatwierdzenia import TYP_KONTROLI_BUDZETU
from kwatermistrz.api.main import app, get_broker
from kwatermistrz.db import sesja
from kwatermistrz.messaging import Typ
from kwatermistrz.models import Delegacja, Jednostka, LogDecyzji, Status
from kwatermistrz.stawki import szacuj_koszt


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()


def zaloguj(klient, id_zolnierza):
    klient.post("/app/logowanie", data={"id_zolnierza": id_zolnierza})


def dodaj_wnioski(*koszty, id_zatwierdzajacego="ZOL-001") -> list[str]:
    ids = [f"DEL-Z-{nr}" for nr in range(1, len(koszty) + 1)]
    with sesja() as s:
        for nr, (id_d, koszt) in enumerate(zip(ids, koszty, strict=True)):
            s.add(
                Delegacja(
                    id_delegacji=id_d,
                    id_zolnierza="ZOL-003" if nr % 2 == 0 else "ZOL-004",
                    dane_wniosku={"cel_wyjazdu": "Szkolenie", "miejsce_docelowe": "Toruń"},
                    status=Status.W_AKCEPTACJI,
                    id_zatwierdzajacego=id_zatwierdzajacego,
                    szacowany_koszt=Decimal(koszt),
                )
            )
    return ids


def ustaw_budzet(przyznany: str, wykorzystany: str = "0") -> None:
    with sesja() as s:
        j = s.get(Jednostka, "JW-1001")
        j.budzet_delegacji = Decimal(przyznany)
        j.budzet_wykorzystany = Decimal(wykorzystany)


def dostarcz(broker) -> None:
    asyncio.run(broker.dostarczaj([klasa(broker) for klasa in AGENCI.values()]))


def delegacja(id_d: str) -> Delegacja:
    with sesja() as s:
        return s.get(Delegacja, id_d)


def wykorzystany_budzet() -> Decimal:
    with sesja() as s:
        return s.get(Jednostka, "JW-1001").budzet_wykorzystany


def test_szacowany_koszt_wyjazdu():
    # 5 dób: dieta i komunikacja miejscowa 5 × 54 zł, przejazd 200 zł, 4 noclegi w limicie 300 zł
    assert szacuj_koszt(date(2026, 12, 7), date(2026, 12, 11), True) == Decimal("1670.00")
    assert szacuj_koszt(date(2026, 12, 7), date(2026, 12, 11), False) == Decimal("470.00")
    assert szacuj_koszt(None, date(2026, 12, 11), True) is None


def test_zestawienie_pokazuje_wnioskodawce_koszty_i_budzet(klient):
    dodaj_wnioski("1670.00", "470.00")
    ustaw_budzet("10000", "2000")
    zaloguj(klient, "ZOL-001")

    strona = klient.get("/app/do-zatwierdzenia").text
    assert "DEL-Z-1" in strona and "DEL-Z-2" in strona
    assert "Magdalena Szewczyk" in strona and "1 kompania zaopatrzenia" in strona
    assert "1\u00a0670,00\u00a0zł" in strona and "8\u00a0000,00\u00a0zł" in strona
    assert "Zatwierdź zaznaczone" in strona

    api = klient.get("/przelozeni/ZOL-001/do-zatwierdzenia").json()
    assert {w["id_delegacji"] for w in api["wnioski"]} == {"DEL-Z-1", "DEL-Z-2"}
    assert Decimal(str(api["budzet"]["pozostaly"])) == Decimal("8000")


def test_zbiorcze_zatwierdzenie_jedna_decyzja(klient, broker):
    ids = dodaj_wnioski("1670.00", "470.00")
    ustaw_budzet("10000")
    zaloguj(klient, "ZOL-001")

    odp = klient.post("/app/do-zatwierdzenia", data={"wnioski": ids, "decyzja": "Zgoda"})
    assert odp.status_code == 200 and "Przekazano decyzję" in odp.text
    dostarcz(broker)

    for id_d in ids:
        d = delegacja(id_d)
        assert d.status == Status.ZATWIERDZONY and d.decyzja == "Zgoda" and d.data_decyzji is not None
    assert wykorzystany_budzet() == Decimal("2140.00")


def test_przekroczenie_budzetu_wstrzymuje_zatwierdzenie(klient, broker):
    ids = dodaj_wnioski("1670.00", "470.00")
    ustaw_budzet("2000")
    zaloguj(klient, "ZOL-001")

    odp = klient.post("/app/do-zatwierdzenia", data={"wnioski": ids, "decyzja": "Zgoda"})
    assert "przekracza dostępny budżet" in odp.text
    dostarcz(broker)

    assert [delegacja(i).status for i in ids] == [Status.W_AKCEPTACJI] * 2
    assert wykorzystany_budzet() == Decimal("0")
    with sesja() as s:
        wpisy = s.scalars(select(LogDecyzji).where(LogDecyzji.typ_decyzji == TYP_KONTROLI_BUDZETU)).all()
        assert len(wpisy) == 2 and all(w.wymaga_zatwierdzenia for w in wpisy)
    assert "czeka na decyzję komórki finansowej" in klient.get("/app/do-zatwierdzenia").text


def test_odrzucenie_zbiorcze_wymaga_uzasadnienia(klient, broker):
    ids = dodaj_wnioski("470.00")
    zaloguj(klient, "ZOL-001")

    odp = klient.post("/app/do-zatwierdzenia", data={"wnioski": ids, "decyzja": "Odmowa"})
    assert "Podaj uzasadnienie" in odp.text and broker.wyslane == []

    klient.post(
        "/app/do-zatwierdzenia",
        data={"wnioski": ids, "decyzja": "Odmowa", "uzasadnienie": "Brak środków w kwartale"},
    )
    dostarcz(broker)
    d = delegacja(ids[0])
    assert d.status == Status.ODRZUCONY and d.uzasadnienie_decyzji == "Brak środków w kwartale"


def test_cudze_wnioski_sa_pomijane(klient, broker):
    dodaj_wnioski("470.00", id_zatwierdzajacego="ZOL-002")
    zaloguj(klient, "ZOL-001")

    odp = klient.post("/app/do-zatwierdzenia", data={"wnioski": ["DEL-Z-1"], "decyzja": "Zgoda"})
    assert "nie oczekuje już na Twoją decyzję" in odp.text
    odp = klient.post("/app/do-zatwierdzenia", data={"decyzja": "Zgoda"})
    assert "Zaznacz co najmniej jeden" in odp.text
    assert broker.wyslane == []


def test_api_decyzja_zbiorcza(klient, broker):
    ids = dodaj_wnioski("470.00", "470.00")

    r = klient.post("/decyzje-zbiorcze", json={"id_osoby": "ZOL-001", "wnioski": ids, "decyzja": "Zgoda"})
    assert r.status_code == 202 and r.json()["przekazane"] == ids
    assert broker.typy() == [Typ.DECYZJA_PRZELOZONEGO] * 2
