"""S22IT1S0P2-9: historia przynaleznosci zolnierza i snapshot jednostki delegacji."""

import asyncio
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pomoc_sesji import zaloguj_z_csrf
from sqlalchemy import select

from kwatermistrz.agents.polecenie import tresc_polecenia
from kwatermistrz.agents.zatwierdzenia import AgentZatwierdzen
from kwatermistrz.api.main import app, get_broker
from kwatermistrz.api.rozliczenia import zatwierdz_wyliczenie
from kwatermistrz.api.schemas import WniosekIn
from kwatermistrz.api.wnioski import zarejestruj_wniosek
from kwatermistrz.api.zatwierdzenia import decyzja_zbiorcza
from kwatermistrz.db import sesja
from kwatermistrz.messaging import Komunikat, Typ
from kwatermistrz.models import (
    Delegacja,
    Jednostka,
    PrzynaleznoscJednostki,
    Rozliczenie,
    Status,
    Zolnierz,
)
from kwatermistrz.przynaleznosc import historia_przynaleznosci, jednostka_na_dzien, zmien_jednostke

NOWA_JEDNOSTKA = {
    "id_jednostki": "JW-2002",
    "nazwa": "JW 2002 - Batalion Dowodzenia",
    "miejscowosc": "Krakow",
    "budzet_delegacji": Decimal("100000"),
}


@pytest.fixture(autouse=True)
def druga_jednostka():
    with sesja() as s:
        s.add(Jednostka(**NOWA_JEDNOSTKA))
        for wpis in s.scalars(select(PrzynaleznoscJednostki).where(PrzynaleznoscJednostki.data_do.is_(None))):
            wpis.data_od = date.today() - timedelta(days=30)


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()


def data_po_biezacym_wpisie(id_zolnierza: str = "ZOL-003") -> date:
    with sesja() as s:
        wpis = s.scalar(
            select(PrzynaleznoscJednostki).where(
                PrzynaleznoscJednostki.id_zolnierza == id_zolnierza,
                PrzynaleznoscJednostki.data_do.is_(None),
            )
        )
        assert wpis is not None
        return wpis.data_od + timedelta(days=1)


def zmien_jednostke_jako_przelozony(s, id_zolnierza, id_jednostki, data_od, **kwargs):
    return zmien_jednostke(
        s,
        id_zolnierza,
        id_jednostki,
        data_od,
        operator=s.get(Zolnierz, "ZOL-001"),
        **kwargs,
    )


def test_poprawna_zmiana_zamyka_poprzedni_wpis_i_aktualizuje_zolnierza():
    data_zmiany = data_po_biezacym_wpisie()
    with sesja() as s:
        nowy = zmien_jednostke_jako_przelozony(
            s,
            "ZOL-003",
            "JW-2002",
            data_zmiany,
            powod="Zmiana etatu",
            zrodlo="api",
        )
        s.commit()
        assert nowy.data_od == data_zmiany and nowy.data_do is None

    with sesja() as s:
        wpisy = historia_przynaleznosci(s, "ZOL-003")
        assert [w.id_jednostki for w in wpisy[:2]] == ["JW-2002", "JW-1001"]
        assert wpisy[1].data_do == data_zmiany - timedelta(days=1)
        assert s.get(Zolnierz, "ZOL-003").id_jednostki == "JW-2002"


@pytest.mark.parametrize(
    ("id_zolnierza", "id_jednostki", "status"),
    [("ZOL-X", "JW-2002", 404), ("ZOL-003", "JW-X", 404), ("ZOL-003", "JW-1001", 409)],
)
def test_blad_zmiany_nie_modyfikuje_stanu(id_zolnierza, id_jednostki, status):
    data_zmiany = data_po_biezacym_wpisie()
    with sesja() as s, pytest.raises(HTTPException) as blad:
        zmien_jednostke_jako_przelozony(s, id_zolnierza, id_jednostki, data_zmiany)
    assert blad.value.status_code == status
    with sesja() as s:
        assert s.get(Zolnierz, "ZOL-003").id_jednostki == "JW-1001"
        assert len(historia_przynaleznosci(s, "ZOL-003")) == 1


def test_data_nie_moze_nakladac_sie_na_biezacy_przedzial():
    with sesja() as s:
        biezacy = historia_przynaleznosci(s, "ZOL-003")[0]
        with pytest.raises(HTTPException) as blad:
            zmien_jednostke_jako_przelozony(s, "ZOL-003", "JW-2002", biezacy.data_od)
    assert blad.value.status_code == 409


def test_data_przyszla_jest_odrzucana_bez_zmiany_stanu():
    with sesja() as s, pytest.raises(HTTPException) as blad:
        zmien_jednostke_jako_przelozony(s, "ZOL-003", "JW-2002", date.today() + timedelta(days=1))
    assert blad.value.status_code == 422
    with sesja() as s:
        assert s.get(Zolnierz, "ZOL-003").id_jednostki == "JW-1001"


def test_jednostka_na_dzien_odczytuje_wlasciwy_przedzial():
    data_zmiany = data_po_biezacym_wpisie()
    with sesja() as s:
        zmien_jednostke_jako_przelozony(s, "ZOL-003", "JW-2002", data_zmiany)
        assert jednostka_na_dzien(s, "ZOL-003", data_zmiany - timedelta(days=1)).id_jednostki == "JW-1001"
        assert jednostka_na_dzien(s, "ZOL-003", data_zmiany).id_jednostki == "JW-2002"


def test_jednostka_na_dzien_przed_historia_uzywa_najwczesniejszego_wpisu():
    with sesja() as s:
        wpis = historia_przynaleznosci(s, "ZOL-003")[0]
        s.get(Zolnierz, "ZOL-003").id_jednostki = "JW-2002"
        assert jednostka_na_dzien(s, "ZOL-003", wpis.data_od - timedelta(days=1)).id_jednostki == "JW-1001"


def test_jednostka_na_dzien_bez_historii_uzywa_biezacej_jednostki():
    with sesja() as s:
        wpis = historia_przynaleznosci(s, "ZOL-003")[0]
        s.delete(wpis)
        s.flush()
        assert jednostka_na_dzien(s, "ZOL-003", date(2000, 1, 1)).id_jednostki == "JW-1001"


def test_api_wlasnego_profilu_jest_tylko_do_odczytu(klient):
    assert klient.get("/zolnierze/ZOL-003/przynaleznosc").status_code == 401
    zaloguj_z_csrf(klient)
    assert klient.get("/zolnierze/ZOL-003/przynaleznosc").status_code == 200
    dane = {"id_jednostki": "JW-2002", "data_od": data_po_biezacym_wpisie().isoformat(), "powod": "Przeniesienie"}
    assert klient.post("/zolnierze/ZOL-003/przynaleznosc", json=dane).status_code == 403


def test_api_przelozony_zmienia_jednostke_bezposredniego_podwladnego(klient):
    zaloguj_z_csrf(klient, "ZOL-001")
    dane = {"id_jednostki": "JW-2002", "data_od": data_po_biezacym_wpisie().isoformat(), "powod": "Przeniesienie"}
    assert klient.get("/zolnierze/ZOL-003/przynaleznosc").status_code == 200
    klient.headers.pop("X-CSRF-Token")
    assert klient.post("/zolnierze/ZOL-003/przynaleznosc", json=dane).status_code == 403
    zaloguj_z_csrf(klient, "ZOL-001")
    odpowiedz = klient.post("/zolnierze/ZOL-003/przynaleznosc", json=dane)
    assert odpowiedz.status_code == 200
    wynik = odpowiedz.json()
    assert wynik["aktualna_jednostka"]["id_jednostki"] == "JW-2002"
    assert wynik["historia"][0]["powod"] == "Przeniesienie"
    assert wynik["historia"][0]["id_operatora"] == "ZOL-001"


def test_api_odrzuca_profil_osoby_spoza_bezposrednich_podwladnych(klient):
    zaloguj_z_csrf(klient, "ZOL-002")
    dane = {"id_jednostki": "JW-2002", "data_od": data_po_biezacym_wpisie().isoformat()}
    assert klient.get("/zolnierze/ZOL-003/przynaleznosc").status_code == 403
    assert klient.post("/zolnierze/ZOL-003/przynaleznosc", json=dane).status_code == 403


def test_api_i_formularz_odrzucaja_dalszego_podwladnego(klient):
    with sesja() as s:
        s.get(Zolnierz, "ZOL-003").id_przelozonego = "ZOL-002"
    zaloguj_z_csrf(klient, "ZOL-001")
    dane = {"id_jednostki": "JW-2002", "data_od": data_po_biezacym_wpisie().isoformat()}
    assert klient.get("/zolnierze/ZOL-003/przynaleznosc").status_code == 403
    assert klient.post("/zolnierze/ZOL-003/przynaleznosc", json=dane).status_code == 403
    assert klient.get("/app/zolnierze/ZOL-003/przynaleznosc").status_code == 403
    assert klient.post("/app/zolnierze/ZOL-003/przynaleznosc", data=dane).status_code == 403


def test_formularz_zachowuje_dane_po_bledzie_i_pokazuje_historie(klient):
    zaloguj_z_csrf(klient, "ZOL-001")
    strona = klient.get("/app/zolnierze/ZOL-003/przynaleznosc")
    assert strona.status_code == 200 and "Historia przypisań" in strona.text
    biezaca_data = data_po_biezacym_wpisie() - timedelta(days=1)
    odpowiedz = klient.post(
        "/app/zolnierze/ZOL-003/przynaleznosc",
        data={"id_jednostki": "JW-2002", "data_od": biezaca_data.isoformat(), "powod": "Zachowany powod"},
    )
    assert odpowiedz.status_code == 409
    assert "Zachowany powod" in odpowiedz.text and biezaca_data.isoformat() in odpowiedz.text


def test_formularz_wlasnego_profilu_jest_tylko_do_odczytu(klient):
    zaloguj_z_csrf(klient, "ZOL-003")
    strona = klient.get("/app/zolnierze/ZOL-003/przynaleznosc")
    assert strona.status_code == 200
    assert "Zapisz zmianę jednostki" not in strona.text
    assert "Bezpośredni podwładni" not in strona.text
    odpowiedz = klient.post(
        "/app/zolnierze/ZOL-003/przynaleznosc",
        data={"id_jednostki": "JW-2002", "data_od": data_po_biezacym_wpisie().isoformat()},
    )
    assert odpowiedz.status_code == 403


def test_formularz_pozwala_wybrac_i_zmienic_bezposredniego_podwladnego(klient):
    zaloguj_z_csrf(klient, "ZOL-001")
    strona = klient.get("/app/zolnierze/ZOL-001/przynaleznosc")
    assert strona.status_code == 200
    assert "/app/zolnierze/ZOL-003/przynaleznosc" in strona.text
    profil = klient.get("/app/zolnierze/ZOL-003/przynaleznosc")
    assert profil.status_code == 200 and "Zapisz zmianę jednostki" in profil.text
    odpowiedz = klient.post(
        "/app/zolnierze/ZOL-003/przynaleznosc",
        data={"id_jednostki": "JW-2002", "data_od": data_po_biezacym_wpisie().isoformat()},
    )
    assert odpowiedz.status_code == 303
    with sesja() as s:
        assert historia_przynaleznosci(s, "ZOL-003")[0].id_operatora == "ZOL-001"


def test_formularz_odrzuca_osobe_spoza_bezposrednich_podwladnych(klient):
    zaloguj_z_csrf(klient, "ZOL-002")
    assert klient.get("/app/zolnierze/ZOL-003/przynaleznosc").status_code == 403
    odpowiedz = klient.post(
        "/app/zolnierze/ZOL-003/przynaleznosc",
        data={"id_jednostki": "JW-2002", "data_od": data_po_biezacym_wpisie().isoformat()},
    )
    assert odpowiedz.status_code == 403


def test_blad_zapisu_wycofuje_zamkniecie_poprzedniego_wpisu(monkeypatch):
    data_zmiany = data_po_biezacym_wpisie()
    with sesja() as s:

        def blad_zapisu():
            raise RuntimeError("symulowany błąd zapisu")

        monkeypatch.setattr(s, "flush", blad_zapisu)
        with pytest.raises(RuntimeError, match="symulowany"):
            zmien_jednostke_jako_przelozony(s, "ZOL-003", "JW-2002", data_zmiany)

    with sesja() as s:
        wpisy = historia_przynaleznosci(s, "ZOL-003")
        assert len(wpisy) == 1 and wpisy[0].data_do is None
        assert s.get(Zolnierz, "ZOL-003").id_jednostki == "JW-1001"


def test_nowa_delegacja_dostaje_snapshot_jednostki_z_dnia_wyjazdu(broker):
    data_zmiany = data_po_biezacym_wpisie()
    with sesja() as s:
        zmien_jednostke_jako_przelozony(s, "ZOL-003", "JW-2002", data_zmiany)
        s.commit()
        wniosek = WniosekIn(
            id_zolnierza="ZOL-003",
            cel_wyjazdu="Szkolenie",
            miejsce_docelowe="Torun",
            data_wyjazdu=(data_zmiany - timedelta(days=1)).isoformat(),
            data_powrotu=data_zmiany.isoformat(),
        )
        delegacja = asyncio.run(zarejestruj_wniosek(s, broker, wniosek))
        assert delegacja.id_jednostki == "JW-1001"


def test_stara_delegacja_zachowuje_jednostke_w_poleceniu_po_przeniesieniu():
    data_zmiany = data_po_biezacym_wpisie()
    with sesja() as s:
        delegacja = Delegacja(
            id_delegacji="DEL-SNAPSHOT",
            id_zolnierza="ZOL-003",
            id_jednostki="JW-1001",
            dane_wniosku={},
        )
        s.add(delegacja)
        zmien_jednostke_jako_przelozony(s, "ZOL-003", "JW-2002", data_zmiany)
        assert "JW 1001" in tresc_polecenia(s, delegacja)["delegowany"]["jednostka"]


def test_zatwierdzenie_starej_delegacji_angazuje_budzet_snapshotu(broker):
    data_zmiany = data_po_biezacym_wpisie()
    with sesja() as s:
        s.add(
            Delegacja(
                id_delegacji="DEL-BUDZET-SNAPSHOT",
                id_zolnierza="ZOL-003",
                id_jednostki="JW-1001",
                id_zatwierdzajacego="ZOL-001",
                dane_wniosku={},
                status=Status.W_AKCEPTACJI,
            )
        )
        zmien_jednostke_jako_przelozony(s, "ZOL-003", "JW-2002", data_zmiany)

    komunikat = Komunikat(
        typ=Typ.DECYZJA_PRZELOZONEGO,
        id_delegacji="DEL-BUDZET-SNAPSHOT",
        dane={"id_osoby": "ZOL-001", "decyzja": "Zgoda", "koszt": "125.00"},
    )
    agent = AgentZatwierdzen(broker)
    asyncio.run(agent.wykonaj(komunikat, *agent.obslugi()[Typ.DECYZJA_PRZELOZONEGO]))
    with sesja() as s:
        assert s.get(Jednostka, "JW-1001").budzet_wykorzystany == Decimal("125.00")
        assert s.get(Jednostka, "JW-2002").budzet_wykorzystany == Decimal("0")


def test_rozliczenie_starej_delegacji_ksieguje_wydatek_w_snapshocie(broker):
    data_zmiany = data_po_biezacym_wpisie()
    with sesja() as s:
        delegacja = Delegacja(
            id_delegacji="DEL-ROZ-SNAPSHOT",
            id_zolnierza="ZOL-003",
            id_jednostki="JW-1001",
            dane_wniosku={},
            status=Status.DO_ROZLICZENIA,
            szacowany_koszt=Decimal("100.00"),
        )
        s.add(delegacja)
        s.add(Rozliczenie(id_delegacji=delegacja.id_delegacji, kwota_naleznosci=Decimal("80.00")))
        s.get(Jednostka, "JW-1001").budzet_wykorzystany = Decimal("100.00")
        zmien_jednostke_jako_przelozony(s, "ZOL-003", "JW-2002", data_zmiany)
        s.commit()
        assert asyncio.run(zatwierdz_wyliczenie(s, broker, delegacja)) is None

    with sesja() as s:
        stara = s.get(Jednostka, "JW-1001")
        nowa = s.get(Jednostka, "JW-2002")
        assert stara.budzet_wykorzystany == Decimal("0") and stara.budzet_wydatkowany == Decimal("80.00")
        assert nowa.budzet_wydatkowany == Decimal("0")


def test_zbiorcza_kontrola_budzetu_rozlicza_snapshoty_osobno(broker):
    with sesja() as s:
        s.get(Jednostka, "JW-1001").budzet_delegacji = Decimal("100.00")
        s.get(Jednostka, "JW-2002").budzet_delegacji = Decimal("100.00")
        s.add_all(
            [
                Delegacja(
                    id_delegacji="DEL-GRUPA-1",
                    id_zolnierza="ZOL-003",
                    id_jednostki="JW-1001",
                    id_zatwierdzajacego="ZOL-001",
                    dane_wniosku={},
                    status=Status.W_AKCEPTACJI,
                    szacowany_koszt=Decimal("80.00"),
                ),
                Delegacja(
                    id_delegacji="DEL-GRUPA-2",
                    id_zolnierza="ZOL-004",
                    id_jednostki="JW-2002",
                    id_zatwierdzajacego="ZOL-001",
                    dane_wniosku={},
                    status=Status.W_AKCEPTACJI,
                    szacowany_koszt=Decimal("80.00"),
                ),
            ]
        )
        s.commit()
        wynik = asyncio.run(
            decyzja_zbiorcza(s, broker, s.get(Zolnierz, "ZOL-001"), ["DEL-GRUPA-1", "DEL-GRUPA-2"], "Zgoda")
        )
    assert wynik.przekazane == ["DEL-GRUPA-1", "DEL-GRUPA-2"]
    assert wynik.wstrzymane == []
