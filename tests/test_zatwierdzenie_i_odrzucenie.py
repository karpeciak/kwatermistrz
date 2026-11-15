"""S22IT1S0P2-35, podzadanie S22IT1S0P2-92: testy zatwierdzenia i odrzucenia wniosków przez przełożonego.

Uzupełnia `test_zatwierdzanie_zbiorcze.py` o: każdy rodzaj decyzji i jego skutek (status, budżet, organizacja),
jednokrotność decyzji, decyzję osoby nieuprawnionej, decyzję dla pojedynczego wniosku (HTMX) i granice budżetu.
"""

import asyncio
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from kwatermistrz.agents import AGENCI
from kwatermistrz.api.main import app, get_broker
from kwatermistrz.db import sesja
from kwatermistrz.messaging import Typ
from kwatermistrz.models import Delegacja, Jednostka, Status
from kwatermistrz.web.strony import CIASTECZKO

PRZELOZONY = "ZOL-001"  # przełożony ZOL-003 i ZOL-004 w danych początkowych, jednostka JW-1001


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set(CIASTECZKO, PRZELOZONY)
        yield c
    app.dependency_overrides.clear()


def dodaj_wniosek(id_d: str = "DEL-D-1", koszt: str = "470.00", **pola) -> str:
    with sesja() as s:
        s.add(
            Delegacja(
                id_delegacji=id_d,
                id_zolnierza=pola.pop("id_zolnierza", "ZOL-003"),
                dane_wniosku={"cel_wyjazdu": "Szkolenie", "miejsce_docelowe": "Toruń"},
                status=Status.W_AKCEPTACJI,
                id_zatwierdzajacego=pola.pop("id_zatwierdzajacego", PRZELOZONY),
                szacowany_koszt=Decimal(koszt),
                **pola,
            )
        )
    return id_d


def ustaw_budzet(przyznany: str, wykorzystany: str = "0") -> None:
    with sesja() as s:
        j = s.get(Jednostka, "JW-1001")
        j.budzet_delegacji = Decimal(przyznany)
        j.budzet_wykorzystany = Decimal(wykorzystany)


def wykorzystany_budzet() -> Decimal:
    with sesja() as s:
        return s.get(Jednostka, "JW-1001").budzet_wykorzystany or Decimal("0")


def dostarcz(broker) -> None:
    asyncio.run(broker.dostarczaj([klasa(broker) for klasa in AGENCI.values()]))


def delegacja(id_d: str) -> Delegacja:
    with sesja() as s:
        return s.get(Delegacja, id_d)


def decyduj_api(klient, id_d: str, decyzja: str, uzasadnienie: str | None = None, id_osoby: str = PRZELOZONY):
    dane = {"id_osoby": id_osoby, "decyzja": decyzja, "uzasadnienie": uzasadnienie}
    return klient.post(f"/delegacje/{id_d}/decyzja", json=dane)


# ---------------------------------------------------------------- skutki decyzji


def test_zgoda_zatwierdza_wniosek_angazuje_budzet_i_zleca_organizacje(klient, broker):
    id_d = dodaj_wniosek(potrzeba_zakwaterowania=True)
    ustaw_budzet("5000", "1000")

    r = klient.post("/app/do-zatwierdzenia", data={"wnioski": [id_d], "decyzja": "Zgoda"})
    assert r.status_code == 200 and "Przekazano decyzję „Zgoda” dla wniosków: 1" in r.text
    [komunikat] = broker.wyslane
    assert komunikat.typ == Typ.DECYZJA_PRZELOZONEGO and komunikat.dane["koszt"] == "470.00"
    dostarcz(broker)

    d = delegacja(id_d)
    assert d.status == Status.ZATWIERDZONY and d.decyzja == "Zgoda" and d.data_decyzji is not None
    assert wykorzystany_budzet() == Decimal("1470.00")
    # po zatwierdzeniu wniosek znika z listy oczekujących przełożonego
    assert id_d not in klient.get("/app/do-zatwierdzenia").text


def test_odmowa_odrzuca_wniosek_bez_angazowania_budzetu(klient, broker):
    id_d = dodaj_wniosek()
    ustaw_budzet("5000")

    r = klient.post(
        "/app/do-zatwierdzenia", data={"wnioski": [id_d], "decyzja": "Odmowa", "uzasadnienie": "  Brak środków  "}
    )
    assert "Przekazano decyzję „Odmowa”" in r.text
    dostarcz(broker)

    d = delegacja(id_d)
    assert d.status == Status.ODRZUCONY and d.decyzja == "Odmowa"
    assert d.uzasadnienie_decyzji == "Brak środków"  # uzasadnienie bez zbędnych spacji
    assert wykorzystany_budzet() == Decimal("0")


def test_zwrot_do_uzupelnienia(klient, broker):
    id_d = dodaj_wniosek()
    ustaw_budzet("5000")

    r = klient.post("/app/do-zatwierdzenia", data={"wnioski": [id_d], "decyzja": "Do uzupełnienia"})
    assert "Podaj uzasadnienie" in r.text and broker.wyslane == []

    dane = {"wnioski": [id_d], "decyzja": "Do uzupełnienia", "uzasadnienie": "Dołącz rozkaz"}
    klient.post("/app/do-zatwierdzenia", data=dane)
    dostarcz(broker)

    d = delegacja(id_d)
    assert d.status == Status.DO_UZUPELNIENIA and d.uzasadnienie_decyzji == "Dołącz rozkaz"
    assert wykorzystany_budzet() == Decimal("0")


def test_odmowa_samymi_spacjami_jest_bez_uzasadnienia(klient, broker):
    id_d = dodaj_wniosek()
    r = klient.post("/app/do-zatwierdzenia", data={"wnioski": [id_d], "decyzja": "Odmowa", "uzasadnienie": "   "})
    assert "Podaj uzasadnienie" in r.text and broker.wyslane == []
    assert delegacja(id_d).status == Status.W_AKCEPTACJI


# ---------------------------------------------------------------- jednokrotność i uprawnienia (S22IT1S0P2-14)


def test_decyzja_jest_rejestrowana_tylko_raz(klient, broker):
    id_d = dodaj_wniosek()
    ustaw_budzet("5000")

    assert decyduj_api(klient, id_d, "Zgoda").status_code == 202
    assert decyduj_api(klient, id_d, "Odmowa", "zmiana zdania").status_code == 202
    assert decyduj_api(klient, id_d, "Zgoda").status_code == 202
    dostarcz(broker)

    d = delegacja(id_d)
    assert d.status == Status.ZATWIERDZONY and d.decyzja == "Zgoda" and d.uzasadnienie_decyzji is None
    # budżet zaangażowany jeden raz, mimo trzech żądań
    assert wykorzystany_budzet() == Decimal("470.00")


def test_decyzja_osoby_nieuprawnionej_jest_pomijana(klient, broker):
    id_d = dodaj_wniosek()

    assert decyduj_api(klient, id_d, "Zgoda", id_osoby="ZOL-004").status_code == 202
    dostarcz(broker)

    d = delegacja(id_d)
    assert d.status == Status.W_AKCEPTACJI and d.decyzja is None
    assert wykorzystany_budzet() == Decimal("0")


def test_api_odrzuca_nieznana_decyzje_i_nieistniejacy_wniosek(klient, broker):
    id_d = dodaj_wniosek()
    assert decyduj_api(klient, id_d, "Może").status_code == 422
    assert decyduj_api(klient, "DEL-BRAK", "Zgoda").status_code == 404

    dane = {"id_osoby": PRZELOZONY, "wnioski": [id_d], "decyzja": "Odmowa"}
    r = klient.post("/decyzje-zbiorcze", json=dane)
    assert r.status_code == 400 and "uzasadnienie" in r.json()["detail"]
    r = klient.post("/decyzje-zbiorcze", json={**dane, "wnioski": []})
    assert r.status_code == 422
    assert broker.wyslane == []


# ---------------------------------------------------------------- decyzja zbiorcza – wybór wniosków


def test_powtorzone_i_cudze_wnioski_w_decyzji_zbiorczej(klient, broker):
    wlasny = dodaj_wniosek("DEL-D-1")
    cudzy = dodaj_wniosek("DEL-D-2", id_zatwierdzajacego="ZOL-002")
    ustaw_budzet("5000")

    dane = {"id_osoby": PRZELOZONY, "wnioski": [wlasny, wlasny, cudzy], "decyzja": "Zgoda"}
    r = klient.post("/decyzje-zbiorcze", json=dane).json()
    assert r["przekazane"] == [wlasny] and r["pominiete"] == [cudzy]
    assert Decimal(str(r["koszt_laczny"])) == Decimal("470.00")
    assert broker.typy() == [Typ.DECYZJA_PRZELOZONEGO]
    dostarcz(broker)

    assert delegacja(wlasny).status == Status.ZATWIERDZONY
    assert delegacja(cudzy).status == Status.W_AKCEPTACJI


def test_wniosek_juz_rozpatrzony_nie_oczekuje_na_decyzje(klient, broker):
    id_d = dodaj_wniosek()
    ustaw_budzet("5000")
    klient.post("/app/do-zatwierdzenia", data={"wnioski": [id_d], "decyzja": "Zgoda"})
    dostarcz(broker)

    r = klient.post("/app/do-zatwierdzenia", data={"wnioski": [id_d], "decyzja": "Odmowa", "uzasadnienie": "x"})
    assert "nie oczekuje już na Twoją decyzję" in r.text
    assert delegacja(id_d).status == Status.ZATWIERDZONY


# ---------------------------------------------------------------- budżet jednostki


def test_koszt_rowny_pozostalemu_budzetowi_jest_zatwierdzany(klient, broker):
    ids = [dodaj_wniosek("DEL-D-1", "600.00"), dodaj_wniosek("DEL-D-2", "400.00", id_zolnierza="ZOL-004")]
    ustaw_budzet("3000", "2000")  # pozostało dokładnie 1000 zł

    r = klient.post("/decyzje-zbiorcze", json={"id_osoby": PRZELOZONY, "wnioski": ids, "decyzja": "Zgoda"}).json()
    assert r["przekazane"] == ids and r["wstrzymane"] == []
    dostarcz(broker)
    assert [delegacja(i).status for i in ids] == [Status.ZATWIERDZONY] * 2
    assert wykorzystany_budzet() == Decimal("3000.00")


def test_odmowa_nie_jest_wstrzymywana_przez_budzet(klient, broker):
    id_d = dodaj_wniosek(koszt="9000.00")
    ustaw_budzet("1000")

    dane = {"id_osoby": PRZELOZONY, "wnioski": [id_d], "decyzja": "Odmowa", "uzasadnienie": "za drogo"}
    r = klient.post("/decyzje-zbiorcze", json=dane).json()
    assert r["przekazane"] == [id_d] and r["wstrzymane"] == []
    dostarcz(broker)
    assert delegacja(id_d).status == Status.ODRZUCONY


def test_wstrzymanie_w_api_zwraca_wstrzymane_wnioski(klient, broker):
    id_d = dodaj_wniosek(koszt="1500.00")
    ustaw_budzet("1000")

    r = klient.post("/decyzje-zbiorcze", json={"id_osoby": PRZELOZONY, "wnioski": [id_d], "decyzja": "Zgoda"})
    assert r.status_code == 202
    assert r.json()["wstrzymane"] == [id_d] and "komórki finansowej" in r.json()["komunikat"]
    assert "przekroczenie_budzetu" in broker.wyslane[0].dane


# ---------------------------------------------------------------- decyzja dla jednego wniosku (wiersz HTMX)


def test_decyzja_w_wierszu_zestawienia(klient, broker):
    id_d = dodaj_wniosek()
    ustaw_budzet("5000")

    r = klient.post(f"/app/do-zatwierdzenia/{id_d}", data={"decyzja": "Odmowa"})
    assert r.status_code == 200 and "Podaj uzasadnienie" in r.text and broker.wyslane == []

    r = klient.post(f"/app/do-zatwierdzenia/{id_d}", data={"decyzja": "Zgoda"})
    assert r.status_code == 200 and "decyzja przekazana" in r.text and f'id="wniosek-{id_d}"' in r.text
    dostarcz(broker)
    assert delegacja(id_d).status == Status.ZATWIERDZONY


def test_decyzja_w_wierszu_tylko_dla_wlasnych_wnioskow(klient, broker):
    cudzy = dodaj_wniosek(id_zatwierdzajacego="ZOL-002")
    assert klient.post(f"/app/do-zatwierdzenia/{cudzy}", data={"decyzja": "Zgoda"}).status_code == 404
    assert klient.post(f"/app/do-zatwierdzenia/{cudzy}", data={"decyzja": "Awans"}).status_code == 400
    assert broker.wyslane == []
