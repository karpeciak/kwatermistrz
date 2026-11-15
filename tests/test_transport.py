"""S22IT1S0P2-27/-57: reguły, agent, API i formularz z sesją."""

import asyncio
import re
from datetime import date, time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from kwatermistrz.agents.koordynator import brakujace_elementy_organizacji
from kwatermistrz.agents.transport import AgentTransportu
from kwatermistrz.api.main import app, get_broker
from kwatermistrz.db import sesja
from kwatermistrz.messaging import Komunikat, Typ
from kwatermistrz.models import Delegacja, LogDecyzji, Polaczenie, Przedsiewziecie, Przejazd, Status, StatusRezerwacji
from kwatermistrz.podroze import BladPodrozy, WyborPodrozy, ocena, sprawdz_decyzje, zestawienie
from kwatermistrz.sesje import CIASTECZKO


def wykonaj(broker, typ, dane=None):
    agent = AgentTransportu(broker)
    k = Komunikat(typ=typ, id_delegacji="DEL-T", dane=dane or {})
    return asyncio.run(agent.wykonaj(k, *agent.obslugi()[typ]))


def przygotuj(broker, **pola):
    dane = dict(
        id_delegacji="DEL-T",
        id_zolnierza="ZOL-003",
        id_zatwierdzajacego="ZOL-001",
        id_przedsiewziecia="PRZ-TEST-TRANSPORT",
        miejsce_docelowe="Toruń",
        data_wyjazdu=date(2026, 12, 7),
        data_powrotu=date(2026, 12, 11),
        status=Status.ZATWIERDZONY,
        dane_wniosku={},
    )
    with sesja() as s:
        # Scenariusz poprawnego wyboru musi mieć kurs docierający przed szkoleniem.
        # PRZ-01 z seeda zaczyna się o 8:00, a POL-01 przyjeżdża dopiero o 8:45.
        s.add(
            Przedsiewziecie(
                id_przedsiewziecia="PRZ-TEST-TRANSPORT",
                nazwa="Szkolenie testowe transportu",
                miejscowosc="Toruń",
                data_rozpoczecia=date(2026, 12, 7),
                data_zakonczenia=date(2026, 12, 11),
                godzina_rozpoczecia=time(9),
            )
        )
        s.flush()
        s.add(Delegacja(**(dane | pola)))
    return wykonaj(broker, Typ.ZADANIE_TRANSPORTU)


def rekomendowane(s):
    wynik = zestawienie(s, s.get(Delegacja, "DEL-T"))
    return {
        "id_tam" if w["kierunek"] == "Tam" else "id_powrot": w["id_przejazdu"]
        for w in wynik["warianty"]
        if w["rekomendowany"]
    }


def decyzja(broker, **pola):
    with sesja() as s:
        dane = rekomendowane(s)
    dane.update(id_osoby="ZOL-001", **pola)
    return wykonaj(broker, Typ.WYBOR_TRANSPORTU, dane)


def test_warianty_obu_kierunkow_i_audyt(broker):
    assert przygotuj(broker).wynik == "Sukces"
    with sesja() as s:
        rows = list(s.scalars(select(Przejazd)))
        assert {r.kierunek for r in rows} == {"Tam", "Powrót"}
        assert len([r for r in rows if r.rekomendowany]) == 2
        assert all(r.status == StatusRezerwacji.PROPOZYCJA for r in rows)
        logs = list(s.scalars(select(LogDecyzji)))
        assert len(logs) == 2
        assert all(wpis.rodzaj_decyzji == "Regułowa" and wpis.wymaga_zatwierdzenia for wpis in logs)
    assert Typ.WYNIK_TRANSPORTU not in broker.typy()


def test_ponowienie_zadania_nie_dubluje_propozycji(broker):
    przygotuj(broker)
    with sesja() as s:
        ids = list(s.scalars(select(Przejazd.id_przejazdu)))
    wykonaj(broker, Typ.ZADANIE_TRANSPORTU)
    with sesja() as s:
        assert list(s.scalars(select(Przejazd.id_przejazdu))) == ids


def test_wszystkie_kursy_spoznione_nie_daja_rekomendacji_tam(broker):
    wynik = przygotuj(broker, id_przedsiewziecia="PRZ-01")
    assert wynik.wynik == "Ostrzeżenie"
    with sesja() as s:
        tam = [w for w in zestawienie(s, s.get(Delegacja, "DEL-T"))["warianty"] if w["kierunek"] == "Tam"]
        assert tam and all(w["spozniony"] and not w["rekomendowany"] for w in tam)
    assert Typ.WYNIK_TRANSPORTU not in broker.typy()


def test_zapis_obu_kierunkow_i_ponowienie(broker):
    przygotuj(broker)
    assert decyzja(broker).wynik == "Sukces"
    assert decyzja(broker).wynik == "Sukces"
    with sesja() as s:
        wybrane = list(s.scalars(select(Przejazd).where(Przejazd.status == StatusRezerwacji.WYBRANA)))
        assert {r.kierunek for r in wybrane} == {"Tam", "Powrót"}
        assert len(wybrane) == 2
        audyt = list(s.scalars(select(LogDecyzji).where(LogDecyzji.typ_decyzji == "Rekomendacja wariantu podróży")))
        assert all(w.decyzja_czlowieka == "Zatwierdzona" and w.id_weryfikujacego == "ZOL-001" for w in audyt)
        assert "przejazd" not in brakujace_elementy_organizacji(s, s.get(Delegacja, "DEL-T"))
    assert Typ.WYNIK_TRANSPORTU in broker.typy()


def test_agent_odrzuca_podstawiona_osobe(broker):
    przygotuj(broker)
    with sesja() as s:
        dane = rekomendowane(s) | {"id_osoby": "ZOL-004"}
    wynik = wykonaj(broker, Typ.WYBOR_TRANSPORTU, dane)
    assert wynik.wynik == "Błąd"
    assert Typ.WYNIK_TRANSPORTU not in broker.typy()


def test_odrzucenie_wszystkich_przekazuje_do_uzgodnienia(broker):
    przygotuj(broker)
    dane = dict(id_osoby="ZOL-001", odrzuc_wszystkie=True, uzasadnienie="Potrzebny inny termin")
    assert wykonaj(broker, Typ.WYBOR_TRANSPORTU, dane).wynik == "Ostrzeżenie"
    with sesja() as s:
        assert all(r.status == StatusRezerwacji.DO_UZGODNIENIA for r in s.scalars(select(Przejazd)))
        assert "przejazd" in brakujace_elementy_organizacji(s, s.get(Delegacja, "DEL-T"))
    assert Typ.WYNIK_TRANSPORTU in broker.typy()


def test_inny_wariant_wymaga_uzasadnienia(broker):
    przygotuj(broker)
    with sesja() as s:
        dane = rekomendowane(s)
        dane["id_powrot"] = s.scalar(
            select(Przejazd.id_przejazdu).where(Przejazd.kierunek == "Powrót", Przejazd.rekomendowany.is_(False))
        )
        d = s.get(Delegacja, "DEL-T")
        with pytest.raises(BladPodrozy, match="uzasadnienie"):
            sprawdz_decyzje(s, d, WyborPodrozy(**dane))
        assert len(sprawdz_decyzje(s, d, WyborPodrozy(**dane, uzasadnienie="Mniej przesiadek"))) == 2


def test_spozniony_wariant_nie_jest_rekomendowany_i_nie_mozna_go_wybrac(broker):
    przygotuj(broker)
    with sesja() as s:
        dane = rekomendowane(s)
        dane["id_tam"] = s.scalar(select(Przejazd.id_przejazdu).where(Przejazd.id_polaczenia == "POL-02"))
        with pytest.raises(BladPodrozy, match="rozpoczęcie"):
            sprawdz_decyzje(s, s.get(Delegacja, "DEL-T"), WyborPodrozy(**dane, uzasadnienie="Taniej"))


def test_przejazd_nocny_uwzglednia_dzien_przyjazdu(broker):
    przygotuj(broker)
    with sesja() as s:
        p = s.get(Polaczenie, "POL-01")
        p.godzina_odjazdu = time(23)
        p.czas_min = 180
        wynik = zestawienie(s, s.get(Delegacja, "DEL-T"))
        nocny = next(w for w in wynik["warianty"] if w["kierunek"] == "Tam" and w["czas_min"] == 180)
        assert nocny["przyjazd"].date() == date(2026, 12, 8)
        assert nocny["spozniony"] and not nocny["rekomendowany"]


def test_zmiana_kryterium_i_brak_polaczen(broker):
    przygotuj(broker, miejsce_docelowe="Brak miasta")
    with sesja() as s:
        assert zestawienie(s, s.get(Delegacja, "DEL-T"))["brak_kierunkow"] == ["Tam", "Powrót"]
    assert Typ.WYNIK_TRANSPORTU not in broker.typy()


def test_kryterium_czasu_zmienia_rekomendacje_powrotu(broker):
    przygotuj(broker)
    with sesja() as s:
        d = s.get(Delegacja, "DEL-T")
        standard = zestawienie(s, d)
        szybkie = zestawienie(s, d, "czas")

        def rekomendacja(wynik):
            return next(w for w in wynik["warianty"] if w["kierunek"] == "Powrót" and w["rekomendowany"])

        assert rekomendacja(standard)["id_przejazdu"] != rekomendacja(szybkie)["id_przejazdu"]
        assert rekomendacja(szybkie)["czas_min"] < rekomendacja(standard)["czas_min"]


def test_odwrocone_kierunki_i_cudza_delegacja_sa_odrzucane(broker):
    przygotuj(broker)
    with sesja() as s:
        d = s.get(Delegacja, "DEL-T")
        wybor = rekomendowane(s)
        with pytest.raises(BladPodrozy, match="kierunkach"):
            sprawdz_decyzje(s, d, WyborPodrozy(id_tam=wybor["id_powrot"], id_powrot=wybor["id_tam"]))
        s.add(Delegacja(id_delegacji="DEL-INNA", id_zolnierza="ZOL-004", status=Status.ZATWIERDZONY))
        s.flush()
        with pytest.raises(BladPodrozy, match="tej delegacji"):
            sprawdz_decyzje(s, s.get(Delegacja, "DEL-INNA"), WyborPodrozy(**wybor))


def test_kryteria_maja_stabilna_kolejnosc():
    with sesja() as s:
        oferty = list(s.scalars(select(Polaczenie).where(Polaczenie.skad == "Warszawa", Polaczenie.dokad == "Toruń")))
        data = date(2026, 12, 6)
        assert min(oferty, key=lambda p: ocena(p, data, None, "koszt")).id_polaczenia == "POL-03"
        assert min(oferty, key=lambda p: ocena(p, data, None, "czas")).id_polaczenia == "POL-05"
        assert min(oferty, key=lambda p: ocena(p, data, None, "punktualnosc")).id_polaczenia == "POL-04"


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()


def zaloguj(klient, osoba="ZOL-001"):
    assert klient.post("/app/logowanie", data={"id_zolnierza": osoba}).status_code == 303


def csrf(klient):
    html = klient.get("/app/wnioski/DEL-T/podroz").text
    return re.search(r'name="csrf" value="([a-f0-9]+)"', html).group(1)


def test_api_wymaga_sesji_uprawnien_i_csrf(klient, broker):
    przygotuj(broker)
    url = "/delegacje/DEL-T/warianty-podrozy"
    assert klient.get(url).status_code == 401
    zaloguj(klient, "ZOL-004")
    assert klient.get(url).status_code == 404
    zaloguj(klient, "ZOL-003")
    assert klient.get(url).status_code == 200
    with sesja() as s:
        dane = rekomendowane(s)
    assert klient.post("/delegacje/DEL-T/wybor-podrozy", json=dane).status_code == 403
    assert (
        klient.post("/delegacje/DEL-T/wybor-podrozy", json=dane, headers={"X-CSRF-Token": csrf(klient)}).status_code
        == 403
    )


def test_api_agent_i_odswiezony_formularz(klient, broker):
    przygotuj(broker)
    zaloguj(klient)
    widok = klient.get("/app/wnioski/DEL-T/podroz")
    assert widok.status_code == 200 and "Rekomendowany" in widok.text
    assert "Powrót" in widok.text and "Punktualność" in widok.text
    dane = klient.get("/delegacje/DEL-T/warianty-podrozy?kryterium=czas").json()
    assert dane["kryterium"] == "czas"
    with sesja() as s:
        wybor = rekomendowane(s)
    token = csrf(klient)
    broker.wyslane.clear()
    odp = klient.post("/delegacje/DEL-T/wybor-podrozy", json=wybor, headers={"X-CSRF-Token": token})
    assert odp.status_code == 202
    asyncio.run(broker.dostarczaj([AgentTransportu(broker)]))
    assert "Decyzja została zapisana" in klient.get("/app/wnioski/DEL-T/podroz").text
    assert klient.post("/delegacje/DEL-T/wybor-podrozy", json=wybor, headers={"X-CSRF-Token": token}).status_code == 409


def test_formularz_publikuje_wybor_i_waliduje_obydwa_kierunki(klient, broker):
    przygotuj(broker)
    zaloguj(klient)
    token = csrf(klient)
    with sesja() as s:
        dane = rekomendowane(s)
    url = "/app/wnioski/DEL-T/podroz"
    assert klient.post(url, data={"csrf": token, "id_tam": dane["id_tam"]}).status_code == 422
    assert klient.post(url, data={"csrf": token, **dane}).status_code == 303
    assert broker.wyslane[-1].typ == Typ.WYBOR_TRANSPORTU
    assert broker.wyslane[-1].dane["id_osoby"] == "ZOL-001"


def test_cookie_jest_podpisane_i_wylogowanie_usuwa_sesje(klient, broker):
    przygotuj(broker)
    zaloguj(klient)
    token = csrf(klient)
    assert klient.cookies.get(CIASTECZKO) != "ZOL-001"
    assert klient.post("/app/wyloguj", data={"csrf": token}).status_code == 303
    assert klient.get("/app/pulpit").status_code == 303
    klient.cookies.set(CIASTECZKO, "ZOL-001")
    assert klient.get("/delegacje/DEL-T/warianty-podrozy").status_code == 401
