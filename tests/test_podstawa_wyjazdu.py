"""S22IT1S0P2-33 (podzadanie 53): podstawa wyjazdu – numer i data rozkazu są wymagane i niesprzeczne z terminem.

Podsumowanie przed złożeniem wymaga podstawy (`wymagaj_podstawy=True`). Agent Wniosków sprawdza sprzeczność
podanej daty rozkazu z terminem i przepisuje podstawę do pól delegacji.
"""

import asyncio
from datetime import date

import pytest
from fastapi.testclient import TestClient
from pomoc_sesji import zaloguj_z_csrf

from kwatermistrz.agents import AGENCI
from kwatermistrz.api.main import app, get_broker
from kwatermistrz.db import sesja
from kwatermistrz.messaging import Komunikat, Typ
from kwatermistrz.models import Delegacja, Status
from kwatermistrz.walidacja import waliduj_wniosek

POPRAWNY = {
    "cel_wyjazdu": "Szkolenie z logistyki",
    "miejsce_docelowe": "Toruń",
    "data_wyjazdu": "2026-12-07",
    "data_powrotu": "2026-12-11",
    "srodek_transportu": "kolej",
    "podstawa_numer_rozkazu": "Rozkaz dzienny nr 215/2026",
    "podstawa_data_rozkazu": "2026-11-10",
}


def bledy(wymagaj_podstawy=True, **zmiany) -> dict:
    wynik = waliduj_wniosek({**POPRAWNY, **zmiany}, jednostka="JW-1001", wymagaj_podstawy=wymagaj_podstawy)
    return {b.pole: b.komunikat for b in wynik}


# ---------------------------------------------------------------- reguły


def test_poprawna_podstawa_wyjazdu():
    assert bledy() == {}


@pytest.mark.parametrize("numer", ["", "   ", None])
def test_numer_rozkazu_jest_wymagany(numer):
    assert bledy(podstawa_numer_rozkazu=numer) == {
        "podstawa_numer_rozkazu": "Uzupełnij numer rozkazu – podstawę wyjazdu"
    }


def test_data_rozkazu_jest_wymagana():
    assert bledy(podstawa_data_rozkazu="") == {"podstawa_data_rozkazu": "Uzupełnij datę rozkazu"}


def test_data_rozkazu_w_zlym_formacie():
    assert "format" in bledy(podstawa_data_rozkazu="10 listopada")["podstawa_data_rozkazu"]


def test_rozkaz_wydany_po_wyjezdzie_jest_sprzeczny():
    assert bledy(podstawa_data_rozkazu="2026-12-08") == {
        "podstawa_data_rozkazu": "Data rozkazu nie może być późniejsza niż data wyjazdu"
    }


def test_rozkaz_z_dnia_wyjazdu_i_w_formacie_polskim_jest_poprawny():
    assert bledy(podstawa_data_rozkazu="2026-12-07") == {}
    assert bledy(podstawa_data_rozkazu="10.11.2026") == {}
    assert bledy(podstawa_data_rozkazu=date(2026, 11, 10)) == {}


def test_bez_wymagania_brak_rozkazu_nie_jest_bledem_ale_sprzecznosc_tak():
    assert bledy(wymagaj_podstawy=False, podstawa_numer_rozkazu="", podstawa_data_rozkazu="") == {}
    assert set(bledy(wymagaj_podstawy=False, podstawa_data_rozkazu="2026-12-20")) == {"podstawa_data_rozkazu"}


def test_bledna_data_wyjazdu_nie_porownuje_daty_rozkazu():
    assert set(bledy(data_wyjazdu="7 grudnia", podstawa_data_rozkazu="2026-12-31")) == {"data_wyjazdu"}


# ---------------------------------------------------------------- podsumowanie w interfejsie


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app, follow_redirects=False) as c:
        zaloguj_z_csrf(c, "ZOL-003")
        yield c
    app.dependency_overrides.clear()


def test_podsumowanie_wskazuje_brak_i_sprzecznosc_rozkazu_przy_polach(klient):
    dane = {**POPRAWNY, "podstawa_numer_rozkazu": "", "podstawa_data_rozkazu": "2026-12-20"}
    strona = klient.post("/app/wnioski/nowy/podsumowanie", data=dane).text
    assert "Wniosku nie można jeszcze złożyć" in strona
    assert "Uzupełnij numer rozkazu" in strona and "Data rozkazu nie może być późniejsza" in strona
    assert 'id="blad-podstawa_numer_rozkazu"' in strona and 'id="blad-podstawa_data_rozkazu"' in strona

    strona = klient.post("/app/wnioski/nowy/podsumowanie", data=POPRAWNY).text
    assert "Podsumowanie wniosku" in strona


# ---------------------------------------------------------------- Agent Wniosków


def zloz_przez_agentow(broker, dane: dict) -> Delegacja:
    with sesja() as s:
        s.add(Delegacja(id_delegacji="DEL-PODST-1", id_zolnierza="ZOL-003", dane_wniosku=dane))
    broker.wyslane.append(Komunikat(typ=Typ.WNIOSEK_ZLOZONY, id_delegacji="DEL-PODST-1"))
    asyncio.run(broker.dostarczaj([klasa(broker) for klasa in AGENCI.values()]))
    with sesja() as s:
        return s.get(Delegacja, "DEL-PODST-1")


def test_agent_przepisuje_podstawe_do_delegacji(broker):
    d = zloz_przez_agentow(broker, POPRAWNY)
    assert d.status == Status.W_AKCEPTACJI
    assert d.podstawa_numer_rozkazu == "Rozkaz dzienny nr 215/2026"
    assert d.podstawa_data_rozkazu == date(2026, 11, 10)


def test_sprzeczna_data_rozkazu_wraca_do_uzupelnienia(broker):
    d = zloz_przez_agentow(broker, {**POPRAWNY, "podstawa_data_rozkazu": "2026-12-20"})
    assert d.status == Status.DO_UZUPELNIENIA


def test_wniosek_z_api_bez_rozkazu_przechodzi_dalej(broker):
    dane = {k: v for k, v in POPRAWNY.items() if not k.startswith("podstawa_")}
    d = zloz_przez_agentow(broker, dane)
    assert d.status == Status.W_AKCEPTACJI and d.podstawa_numer_rozkazu is None
