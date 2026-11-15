"""S22IT1S0P2-39 – dobór zakwaterowania zapewniającego terminowy dojazd na miejsce zadania.

Dane początkowe (seed) dla Torunia (PRZ-01, zajęcia od 8:00 → dojazd ≤ 45 min):
OBJ-01 wojskowy bez miejsc, OBJ-02 wojskowy 60 zł / 45 min, OBJ-03 cywilny 260 zł / 15 min,
OBJ-04 cywilny 340 zł (ponad limit) / 5 min, OBJ-05 cywilny 180 zł / 70 min (za daleko).
"""

import asyncio
from datetime import date, time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from kwatermistrz.agents import AGENCI
from kwatermistrz.agents.zakwaterowanie import TYP_DECYZJI, maks_czas_dojazdu
from kwatermistrz.api.main import app, get_broker
from kwatermistrz.db import sesja
from kwatermistrz.messaging import Komunikat, Typ
from kwatermistrz.models import (
    Delegacja,
    LogDecyzji,
    LogOperacji,
    ObiektZakwaterowania,
    Przedsiewziecie,
    Status,
    StatusRezerwacji,
    Zakwaterowanie,
)


def zatwierdzona(id_d: str = "DEL-ZK-1", **zmiany) -> str:
    pola = dict(
        id_delegacji=id_d,
        id_zolnierza="ZOL-003",
        id_przedsiewziecia="PRZ-01",
        dane_wniosku={},
        miejsce_docelowe="Toruń",
        data_wyjazdu=date(2026, 12, 7),
        data_powrotu=date(2026, 12, 11),
        status=Status.ZATWIERDZONY,
        id_zatwierdzajacego="ZOL-001",
        potrzeba_zakwaterowania=True,
    )
    pola.update(zmiany)
    with sesja() as s:
        s.add(Delegacja(**pola))
    return id_d


def dobierz(broker, id_d: str):
    agent = AGENCI["zakwaterowanie"](broker)
    funkcja, operacja = agent.obslugi()[Typ.ZADANIE_ZAKWATEROWANIA]
    k = Komunikat(typ=Typ.ZADANIE_ZAKWATEROWANIA, id_delegacji=id_d)
    return asyncio.run(agent.wykonaj(k, funkcja, operacja))


def propozycje(id_d: str) -> list[Zakwaterowanie]:
    with sesja() as s:
        lista = s.scalars(
            select(Zakwaterowanie).where(Zakwaterowanie.id_delegacji == id_d).order_by(Zakwaterowanie.id_zakwaterowania)
        ).all()
        s.expunge_all()
        return list(lista)


def ustaw_obiekt(id_obiektu: str, **pola) -> None:
    with sesja() as s:
        o = s.get(ObiektZakwaterowania, id_obiektu)
        for nazwa, wartosc in pola.items():
            setattr(o, nazwa, wartosc)


@pytest.mark.parametrize(
    ("godzina", "maks"),
    [(time(8, 0), 45), (time(9, 0), 105), (time(7, 0), 0), (None, 45)],
)
def test_maksymalny_czas_dojazdu_wynika_z_godziny_rozpoczecia(godzina, maks):
    assert maks_czas_dojazdu(godzina) == maks


def test_propozycje_w_limicie_z_terminowym_dojazdem_wojskowe_pierwsze(broker):
    id_d = zatwierdzona()

    ktx = dobierz(broker, id_d)

    lista = propozycje(id_d)
    assert ktx.wynik == "Sukces"
    assert [z.id_obiektu for z in lista] == ["OBJ-02", "OBJ-03"]  # bez miejsc, ponad limit i za daleko odpadają
    assert all(z.status == StatusRezerwacji.PROPOZYCJA and not z.wymaga_odstepstwa for z in lista)
    wojskowy = lista[0]
    assert (wojskowy.data_od, wojskowy.data_do) == (date(2026, 12, 7), date(2026, 12, 11))
    assert wojskowy.cena_za_dobe == Decimal("60.00")
    assert wojskowy.koszt_calkowity == Decimal("240.00")  # 4 noce
    assert wojskowy.czas_dojazdu_min == 45


def test_decyzja_doboru_w_logu(broker):
    id_d = zatwierdzona()

    dobierz(broker, id_d)

    with sesja() as s:
        op = s.scalar(select(LogOperacji).where(LogOperacji.id_delegacji == id_d))
        decyzja = s.scalar(select(LogDecyzji).where(LogDecyzji.id_operacji == op.id_operacji))
        assert op.id_agenta == "AG-05"
        assert decyzja.typ_decyzji == TYP_DECYZJI
        assert decyzja.liczba_wariantow == 2
        assert decyzja.wybrany_wariant == "Hotel garnizonowy Chełmno"
        assert not decyzja.wymaga_zatwierdzenia


def test_brak_obiektu_w_limicie_daje_warianty_z_odstepstwem(broker):
    """Scenariusz 2a: w limicie nic nie zdąży – warianty ponad limitem z terminowym dojazdem."""
    ustaw_obiekt("OBJ-02", liczba_wolnych_miejsc=0)
    ustaw_obiekt("OBJ-03", liczba_wolnych_miejsc=0)
    id_d = zatwierdzona()

    ktx = dobierz(broker, id_d)

    lista = propozycje(id_d)
    assert ktx.wynik == "Ostrzeżenie"
    assert [z.id_obiektu for z in lista] == ["OBJ-04"]
    assert lista[0].wymaga_odstepstwa
    assert Typ.POWIADOMIENIE in broker.typy()
    with sesja() as s:
        assert s.scalar(select(LogDecyzji.wymaga_zatwierdzenia))  # decyzja finansowa – do sprawdzenia


def test_brak_jakiegokolwiek_obiektu(broker):
    id_d = zatwierdzona(id_przedsiewziecia=None, miejsce_docelowe="Hel")

    ktx = dobierz(broker, id_d)

    assert propozycje(id_d) == []
    assert ktx.wynik == "Ostrzeżenie" and "Brak obiektu" in ktx.opis
    powiadomienie = next(k for k in broker.wyslane if k.typ == Typ.POWIADOMIENIE)
    assert powiadomienie.dane["do"] == "ZOL-001"


def test_pozniejsze_zajecia_dopuszczaja_dalszy_obiekt(broker):
    """OBJ-05 (70 min) nie zdąży na 8:00, ale na zajęcia od 9:00 (dojazd do 105 min) już tak."""
    with sesja() as s:
        s.get(Przedsiewziecie, "PRZ-01").godzina_rozpoczecia = time(9, 0)
    id_d = zatwierdzona()

    dobierz(broker, id_d)

    assert "OBJ-05" in [z.id_obiektu for z in propozycje(id_d)]


def test_ponowione_zlecenie_nie_dubluje_propozycji(broker):
    id_d = zatwierdzona()

    dobierz(broker, id_d)
    dobierz(broker, id_d)

    assert len(propozycje(id_d)) == 2


def test_brak_dat_wstrzymuje_dobor(broker):
    id_d = zatwierdzona(data_powrotu=None)

    ktx = dobierz(broker, id_d)

    assert propozycje(id_d) == []
    assert ktx.wynik == "Ostrzeżenie"


def test_po_zatwierdzeniu_propozycje_trafiaja_do_delegowanego(broker):
    """Pełny przebieg: Koordynator zleca dobór, delegowany wybiera, delegacja dostaje nocleg."""
    id_d = zatwierdzona()
    broker.wyslane.append(Komunikat(typ=Typ.ZADANIE_ZAKWATEROWANIA, id_delegacji=id_d))
    asyncio.run(broker.dostarczaj([klasa(broker) for klasa in AGENCI.values()]))
    pierwsza = propozycje(id_d)[0]

    broker.wyslane.append(
        Komunikat(
            typ=Typ.WYBOR_ZAKWATEROWANIA, id_delegacji=id_d, dane={"id_zakwaterowania": pierwsza.id_zakwaterowania}
        )
    )
    asyncio.run(broker.dostarczaj([klasa(broker) for klasa in AGENCI.values()]))

    wybrane = [z for z in propozycje(id_d) if z.status == StatusRezerwacji.WYBRANA]
    assert [z.id_obiektu for z in wybrane] == ["OBJ-02"]


# ---------------------------------------------------------------- API


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_api_zwraca_propozycje(broker, klient):
    id_d = zatwierdzona()
    dobierz(broker, id_d)

    r = klient.get(f"/delegacje/{id_d}/zakwaterowanie")

    assert r.status_code == 200
    pierwsza = r.json()[0]
    assert pierwsza["nazwa"] == "Hotel garnizonowy Chełmno"
    assert pierwsza["czas_dojazdu_min"] == 45
    assert Decimal(pierwsza["koszt_calkowity"]) == Decimal("240.00")
    assert pierwsza["wymaga_odstepstwa"] is False


def test_api_wybor_wysyla_komunikat(broker, klient):
    id_d = zatwierdzona()
    dobierz(broker, id_d)
    broker.wyslane.clear()
    numer = propozycje(id_d)[0].id_zakwaterowania

    r = klient.post(f"/delegacje/{id_d}/wybor-zakwaterowania", json={"id_zakwaterowania": numer})

    assert r.status_code == 202
    assert broker.wyslane[-1].typ == Typ.WYBOR_ZAKWATEROWANIA
    assert broker.wyslane[-1].dane == {"id_zakwaterowania": numer, "uzasadnienie": None}


def test_api_odstepstwo_wymaga_uzasadnienia(broker, klient):
    ustaw_obiekt("OBJ-02", liczba_wolnych_miejsc=0)
    ustaw_obiekt("OBJ-03", liczba_wolnych_miejsc=0)
    id_d = zatwierdzona()
    dobierz(broker, id_d)
    broker.wyslane.clear()
    numer = propozycje(id_d)[0].id_zakwaterowania

    bez = klient.post(f"/delegacje/{id_d}/wybor-zakwaterowania", json={"id_zakwaterowania": numer})
    z = klient.post(
        f"/delegacje/{id_d}/wybor-zakwaterowania",
        json={"id_zakwaterowania": numer, "uzasadnienie": "Jedyny obiekt z miejscami"},
    )

    assert bez.status_code == 422
    assert z.status_code == 202
    assert [k.typ for k in broker.wyslane] == [Typ.WYBOR_ZAKWATEROWANIA]


def test_api_odrzuca_bledny_wybor(broker, klient):
    id_d = zatwierdzona()
    dobierz(broker, id_d)
    inna = zatwierdzona("DEL-ZK-2", status=Status.W_AKCEPTACJI)

    assert klient.post(f"/delegacje/{id_d}/wybor-zakwaterowania", json={"id_zakwaterowania": 9999}).status_code == 404
    numer = propozycje(id_d)[0].id_zakwaterowania
    r = klient.post(f"/delegacje/{inna}/wybor-zakwaterowania", json={"id_zakwaterowania": numer})
    assert r.status_code == 409
