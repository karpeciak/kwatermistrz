"""S22IT1S0P2-38 – polecenie wyjazdu służbowego po zatwierdzeniu wniosku."""

import asyncio
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from kwatermistrz.agents import AGENCI
from kwatermistrz.agents.polecenie import TYP_DECYZJI, WERSJA_WZORU
from kwatermistrz.api.main import app, get_broker
from kwatermistrz.db import sesja, teraz
from kwatermistrz.messaging import Komunikat, Typ
from kwatermistrz.models import Delegacja, LogDecyzji, LogOperacji, PolecenieWyjazdu, Status


def zatwierdzona(id_d: str = "DEL-PW-1", **zmiany) -> str:
    pola = dict(
        id_delegacji=id_d,
        id_zolnierza="ZOL-003",
        dane_wniosku={"podstawa_numer_rozkazu": "Rozkaz dzienny nr 215/2026", "podstawa_data_rozkazu": "2026-11-10"},
        cel_wyjazdu="Szkolenie z łączności",
        miejsce_docelowe="Zegrze",
        data_wyjazdu=date(2026, 12, 7),
        data_powrotu=date(2026, 12, 11),
        status=Status.ZATWIERDZONY,
        id_zatwierdzajacego="ZOL-001",
        decyzja="Zgoda",
        data_decyzji=datetime(2026, 11, 14, 9, 30),
    )
    pola.update(zmiany)
    with sesja() as s:
        s.add(Delegacja(**pola))
    return id_d


def sporzadz(broker, id_d: str):
    agent = AGENCI["wnioski"](broker)
    funkcja, operacja = agent.obslugi()[Typ.ZADANIE_POLECENIA]
    return asyncio.run(agent.wykonaj(Komunikat(typ=Typ.ZADANIE_POLECENIA, id_delegacji=id_d), funkcja, operacja))


def polecenie(id_d: str) -> PolecenieWyjazdu | None:
    with sesja() as s:
        p = s.scalar(select(PolecenieWyjazdu).where(PolecenieWyjazdu.id_delegacji == id_d))
        if p is not None:
            s.expunge(p)
        return p


def test_polecenie_wypelnione_wedlug_wzoru(broker):
    id_d = zatwierdzona()

    ktx = sporzadz(broker, id_d)

    p = polecenie(id_d)
    assert ktx.wynik == "Sukces"
    assert p.numer == f"PW/{teraz().year}/0001"
    assert p.wersja_wzoru == WERSJA_WZORU
    t = p.tresc
    assert t["delegowany"]["id_zolnierza"] == "ZOL-003"
    assert t["delegowany"]["stopien"] and t["delegowany"]["imie_nazwisko"] and t["delegowany"]["jednostka"]
    assert t["cel"] == "Szkolenie z łączności"
    assert t["miejsce"] == "Zegrze"
    assert t["termin"] == {"od": "2026-12-07", "do": "2026-12-11"}
    assert t["podstawa"] == {"numer_rozkazu": "Rozkaz dzienny nr 215/2026", "data_rozkazu": "2026-11-10"}
    assert t["zatwierdzajacy"]["id_zolnierza"] == "ZOL-001"
    assert t["zatwierdzajacy"]["data_decyzji"] == "2026-11-14"


def test_wynik_do_koordynatora_i_powiadomienie_delegowanego(broker):
    id_d = zatwierdzona()

    sporzadz(broker, id_d)

    wynik = next(k for k in broker.wyslane if k.typ == Typ.WYNIK_POLECENIA)
    assert wynik.dane == {"numer": polecenie(id_d).numer}
    powiadomienie = next(k for k in broker.wyslane if k.typ == Typ.POWIADOMIENIE)
    assert powiadomienie.dane["do"] == "ZOL-003"


def test_kolejne_polecenia_maja_kolejne_numery(broker):
    sporzadz(broker, zatwierdzona("DEL-PW-1"))
    sporzadz(broker, zatwierdzona("DEL-PW-2"))

    rok = teraz().year
    assert polecenie("DEL-PW-1").numer == f"PW/{rok}/0001"
    assert polecenie("DEL-PW-2").numer == f"PW/{rok}/0002"


def test_ponowione_zlecenie_nie_tworzy_drugiego_polecenia(broker):
    id_d = zatwierdzona()

    sporzadz(broker, id_d)
    sporzadz(broker, id_d)

    with sesja() as s:
        assert s.scalar(select(func.count()).select_from(PolecenieWyjazdu)) == 1
    assert [k.dane["numer"] for k in broker.wyslane if k.typ == Typ.WYNIK_POLECENIA] == [f"PW/{teraz().year}/0001"] * 2


def test_podstawa_z_pol_delegacji_ma_pierwszenstwo(broker):
    id_d = zatwierdzona(podstawa_numer_rozkazu="Rozkaz nr 7/2026", podstawa_data_rozkazu=date(2026, 11, 2))

    sporzadz(broker, id_d)

    assert polecenie(id_d).tresc["podstawa"] == {"numer_rozkazu": "Rozkaz nr 7/2026", "data_rozkazu": "2026-11-02"}


@pytest.mark.parametrize(
    ("zmiany", "brak"),
    [
        ({"dane_wniosku": {}}, "numer rozkazu"),
        ({"cel_wyjazdu": None}, "cel wyjazdu"),
        ({"data_powrotu": None}, "data powrotu"),
        ({"data_decyzji": None}, "data decyzji przełożonego"),
    ],
)
def test_brak_danych_wstrzymuje_polecenie(broker, zmiany, brak):
    """Scenariusz 2a: polecenia nie zapisujemy, ostrzeżenie w logu i powiadomienie organizującego."""
    id_d = zatwierdzona(**zmiany)

    ktx = sporzadz(broker, id_d)

    assert polecenie(id_d) is None
    assert ktx.wynik == "Ostrzeżenie" and brak in ktx.opis
    assert Typ.WYNIK_POLECENIA not in broker.typy()
    powiadomienie = next(k for k in broker.wyslane if k.typ == Typ.POWIADOMIENIE)
    assert powiadomienie.dane["do"] == "ZOL-001" and brak in powiadomienie.dane["tresc"]


def test_operacja_i_decyzja_w_logu(broker):
    id_d = zatwierdzona()

    sporzadz(broker, id_d)

    with sesja() as s:
        op = s.scalar(select(LogOperacji).where(LogOperacji.id_delegacji == id_d))
        decyzja = s.scalar(select(LogDecyzji).where(LogDecyzji.id_operacji == op.id_operacji))
        assert op.id_agenta == "AG-02" and op.wynik == "Sukces"
        assert decyzja.typ_decyzji == TYP_DECYZJI and decyzja.wybrany_wariant == polecenie(id_d).numer


# ---------------------------------------------------------------- API


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_api_udostepnia_polecenie(broker, klient):
    id_d = zatwierdzona()
    sporzadz(broker, id_d)

    r = klient.get(f"/delegacje/{id_d}/polecenie")

    assert r.status_code == 200
    assert r.json()["numer"] == polecenie(id_d).numer
    assert r.json()["tresc"]["miejsce"] == "Zegrze"


def test_api_404_gdy_polecenia_jeszcze_nie_ma(klient):
    id_d = zatwierdzona()

    assert klient.get(f"/delegacje/{id_d}/polecenie").status_code == 404
    assert klient.get("/delegacje/NIE-MA/polecenie").status_code == 404
