"""S22IT1S0P2-41: aktualizacja planu delegacji po zmianie terminu zadania (podzadania 157–161).

Scenariusz: organizator zmienia termin zadania → system wykrywa zmianę, przygotowuje nowy plan transportu
i zakwaterowania zgodny z limitami i przedstawia delegowanemu porównanie planów → delegowany potwierdza →
system anuluje nieaktualne rezerwacje, rezerwuje nowe, aktualizuje polecenie wyjazdu i rejestruje zmianę.
Alternatywnie (3a–4a): delegowany nie może wziąć udziału – realizacja wstrzymana, sprawa u przełożonego.
"""

import asyncio
from datetime import time, timedelta
from decimal import Decimal

import pytest
from conftest import AtrapaBrokera
from fastapi.testclient import TestClient
from pomoc_sesji import zaloguj_z_csrf
from sqlalchemy import select

from kwatermistrz.agents import AGENCI
from kwatermistrz.agents.koordynator import brakujace_elementy_organizacji
from kwatermistrz.api.main import app, get_broker
from kwatermistrz.db import sesja, teraz
from kwatermistrz.messaging import Komunikat, Typ
from kwatermistrz.models import (
    Delegacja,
    Jednostka,
    LogDecyzji,
    LogOperacji,
    PolecenieWyjazdu,
    Przedsiewziecie,
    Przejazd,
    Status,
    StatusRezerwacji,
    StatusZmianyTerminu,
    Zakwaterowanie,
    ZmianaTerminu,
)

DEL, PRZ = "DEL-TERMIN-41", "PRZ-T41"
DELEGOWANY, PRZELOZONY = "ZOL-003", "ZOL-001"
START = teraz().date() + timedelta(days=20)
KONIEC = START + timedelta(days=4)
NOWY_START, NOWY_KONIEC = START + timedelta(days=7), KONIEC + timedelta(days=7)
ZAANGAZOWANE = Decimal("1000.00")


class BrokerZHistoria(AtrapaBrokera):
    """Atrapa brokera, która pamięta wszystkie komunikaty – także te zużyte już przy dostarczaniu."""

    def __init__(self) -> None:
        super().__init__()
        self.historia: list[Komunikat] = []

    async def publikuj(self, k: Komunikat) -> None:
        self.historia.append(k)
        await super().publikuj(k)

    def powiadomienia(self, do: str) -> list[str]:
        return [k.dane["tresc"] for k in self.historia if k.typ == Typ.POWIADOMIENIE and k.dane.get("do") == do]


@pytest.fixture
def broker() -> BrokerZHistoria:
    return BrokerZHistoria()


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def zorganizowana_delegacja():
    """Zorganizowana delegacja do Torunia na szkolenie o 10:00: przejazdy, nocleg w OBJ-02 i polecenie wyjazdu."""
    with sesja() as s:
        s.add(
            Przedsiewziecie(
                id_przedsiewziecia=PRZ,
                nazwa="Szkolenie testowe -41",
                miejscowosc="Toruń",
                data_rozpoczecia=START,
                data_zakonczenia=KONIEC,
                godzina_rozpoczecia=time(10, 0),
            )
        )
        s.add(
            Delegacja(
                id_delegacji=DEL,
                id_zolnierza=DELEGOWANY,
                id_przedsiewziecia=PRZ,
                cel_wyjazdu="Szkolenie testowe -41",
                miejsce_docelowe="Toruń",
                data_wyjazdu=START,
                data_powrotu=KONIEC,
                srodek_transportu="kolej",
                potrzeba_zakwaterowania=True,
                status=Status.ZORGANIZOWANY,
                id_zatwierdzajacego=PRZELOZONY,
                decyzja="Zgoda",
            )
        )
        s.flush()
        for kierunek, data, polaczenie in (("Tam", START, "POL-01"), ("Powrót", KONIEC, "POL-01-P")):
            s.add(
                Przejazd(
                    id_delegacji=DEL,
                    kierunek=kierunek,
                    srodek_transportu="kolej",
                    data_przejazdu=data,
                    id_polaczenia=polaczenie,
                    koszt=Decimal("89.00"),
                    status=StatusRezerwacji.WYBRANA,
                )
            )
        s.add(
            Zakwaterowanie(
                id_delegacji=DEL,
                id_obiektu="OBJ-02",
                data_od=START,
                data_do=KONIEC,
                cena_za_dobe=Decimal("60.00"),
                koszt_calkowity=Decimal("240.00"),
                status=StatusRezerwacji.WYBRANA,
                czas_dojazdu_min=45,
            )
        )
        s.add(
            PolecenieWyjazdu(
                numer="PW/2099/0041",
                id_delegacji=DEL,
                wersja_wzoru="test",
                tresc={"termin": {"od": START.isoformat(), "do": KONIEC.isoformat()}},
            )
        )
        s.get(Jednostka, "JW-1001").budzet_wykorzystany = ZAANGAZOWANE


def dostarcz(broker, k: Komunikat | None = None) -> None:
    if k is not None:
        asyncio.run(broker.publikuj(k))
    asyncio.run(broker.dostarczaj([klasa(broker) for klasa in AGENCI.values()]))


def zmien_termin(klient, broker, start=NOWY_START, koniec=NOWY_KONIEC, **dodatkowe):
    odp = klient.post(
        f"/przedsiewziecia/{PRZ}/termin",
        json={"data_rozpoczecia": start.isoformat(), "data_zakonczenia": koniec.isoformat(), **dodatkowe},
    )
    dostarcz(broker)
    return odp


def zdecyduj(broker, potwierdz: bool, uzasadnienie: str = "", osoba: str = DELEGOWANY) -> None:
    dane = {"potwierdz": potwierdz, "uzasadnienie": uzasadnienie, "id_osoby": osoba}
    dostarcz(broker, Komunikat(typ=Typ.DECYZJA_NOWEGO_PLANU, id_delegacji=DEL, dane=dane))


def przejazdy(status: str) -> list[Przejazd]:
    with sesja() as s:
        zapytanie = select(Przejazd).where(Przejazd.id_delegacji == DEL).order_by(Przejazd.id_przejazdu)
        return [r for r in s.scalars(zapytanie) if r.status == status]


def noclegi(status: str) -> list[Zakwaterowanie]:
    with sesja() as s:
        zapytanie = select(Zakwaterowanie).where(Zakwaterowanie.id_delegacji == DEL)
        return [z for z in s.scalars(zapytanie) if z.status == status]


def zmiany() -> list[ZmianaTerminu]:
    with sesja() as s:
        return list(s.scalars(select(ZmianaTerminu).where(ZmianaTerminu.id_delegacji == DEL)))


def delegacja() -> Delegacja:
    with sesja() as s:
        return s.get(Delegacja, DEL)


# ---------------------------------------------------------------- podzadanie 157: wykrycie zmiany i nowy plan


def test_zmiana_terminu_daje_nowy_plan_do_potwierdzenia(klient, broker):
    odp = zmien_termin(klient, broker, powod="Przesunięcie terminu szkolenia przez organizatora")

    assert odp.status_code == 202 and odp.json()["delegacje"] == [DEL]
    with sesja() as s:
        p = s.get(Przedsiewziecie, PRZ)
        assert (p.data_rozpoczecia, p.pierwotna_data_zakonczenia) == (NOWY_START, KONIEC)
    (z,) = zmiany()
    assert z.status == StatusZmianyTerminu.OCZEKUJE
    assert (z.nowy_wyjazd, z.nowy_powrot) == (NOWY_START, NOWY_KONIEC)
    assert z.stary_plan["termin"].startswith(START.strftime("%d.%m.%Y"))
    assert z.nowy_plan["tam"] and z.nowy_plan["powrot"] and z.nowy_plan["nocleg"]
    nowe = przejazdy(StatusRezerwacji.NOWY_PLAN)
    assert {(r.kierunek, r.data_przejazdu) for r in nowe} == {("Tam", NOWY_START), ("Powrót", NOWY_KONIEC)}
    tam = next(r for r in nowe if r.kierunek == "Tam")
    assert tam.id_polaczenia == "POL-01"  # kolej zgodna z preferencją, przyjazd 8:45 przed zajęciami o 10:00
    (nocleg,) = noclegi(StatusRezerwacji.NOWY_PLAN)
    assert nocleg.id_obiektu == "OBJ-02"  # dotychczasowy obiekt nadal spełnia warunki
    assert len(przejazdy(StatusRezerwacji.WYBRANA)) == 2  # stary plan obowiązuje do potwierdzenia
    assert delegacja().status == Status.ZATWIERDZONY  # dotychczasowa organizacja jest nieaktualna
    assert any("Porównaj dotychczasowy i nowy plan" in t for t in broker.powiadomienia(DELEGOWANY))


def test_dzien_wczesniej_gdy_zaden_kurs_nie_zdazy(klient, broker):
    zmien_termin(klient, broker, godzina_rozpoczecia="08:00")  # POL-01 przyjeżdża 8:45 – za późno

    tam = next(r for r in przejazdy(StatusRezerwacji.NOWY_PLAN) if r.kierunek == "Tam")
    assert tam.data_przejazdu == NOWY_START - timedelta(days=1)
    (nocleg,) = noclegi(StatusRezerwacji.NOWY_PLAN)
    assert nocleg.data_od == NOWY_START - timedelta(days=1)


def test_ponowione_zdarzenie_nie_dubluje_planu(klient, broker):
    zmien_termin(klient, broker)
    dane = {
        "stary_start": START.isoformat(),
        "stary_koniec": KONIEC.isoformat(),
        "nowy_start": NOWY_START.isoformat(),
        "nowy_koniec": NOWY_KONIEC.isoformat(),
    }
    with sesja() as s:  # daty delegacji zmieniają się dopiero po potwierdzeniu planu
        assert s.get(Delegacja, DEL).data_wyjazdu == START

    dostarcz(broker, Komunikat(typ=Typ.ZMIANA_TERMINU, id_delegacji=DEL, dane=dane))

    assert len(zmiany()) == 1
    assert len(przejazdy(StatusRezerwacji.NOWY_PLAN)) == 2


def test_kolejna_zmiana_przed_potwierdzeniem_zastepuje_poprzednia(klient, broker):
    zmien_termin(klient, broker)
    zmien_termin(klient, broker, start=NOWY_START + timedelta(days=1), koniec=NOWY_KONIEC + timedelta(days=1))

    statusy = [z.status for z in zmiany()]
    assert statusy == [StatusZmianyTerminu.ZASTAPIONA, StatusZmianyTerminu.OCZEKUJE]
    assert {r.data_przejazdu for r in przejazdy(StatusRezerwacji.NOWY_PLAN)} == {
        NOWY_START + timedelta(days=1),
        NOWY_KONIEC + timedelta(days=1),
    }


def test_brak_planu_w_budzecie_trafia_do_przelozonego(klient, broker):
    with sesja() as s:
        j = s.get(Jednostka, "JW-1001")
        j.budzet_wykorzystany = j.budzet_delegacji  # brak wolnych środków na dłuższy wyjazd

    zmien_termin(klient, broker, koniec=NOWY_KONIEC + timedelta(days=3))  # trzy noclegi więcej

    (z,) = zmiany()
    assert z.status == StatusZmianyTerminu.DO_UZGODNIENIA and "budżet" in z.uzasadnienie
    assert przejazdy(StatusRezerwacji.NOWY_PLAN) == []
    assert any("nie można przygotować planu" in t for t in broker.powiadomienia(PRZELOZONY))


def test_api_zmiany_terminu_waliduje_dane(klient, broker):
    assert zmien_termin(klient, broker, start=START, koniec=KONIEC).status_code == 409  # bez zmian
    wczoraj = teraz().date() - timedelta(days=1)
    assert zmien_termin(klient, broker, start=wczoraj, koniec=KONIEC).status_code == 422
    assert zmien_termin(klient, broker, start=NOWY_KONIEC, koniec=NOWY_START).status_code == 422
    assert (
        klient.post(
            "/przedsiewziecia/NIE-MA/termin",
            json={"data_rozpoczecia": NOWY_START.isoformat(), "data_zakonczenia": NOWY_KONIEC.isoformat()},
        ).status_code
        == 404
    )
    assert zmiany() == []


def test_delegacja_w_trakcie_nie_jest_przeplanowywana(klient, broker):
    with sesja() as s:
        s.get(Delegacja, DEL).status = Status.W_TRAKCIE

    odp = zmien_termin(klient, broker)

    assert odp.json()["delegacje"] == [] and zmiany() == []


# ---------------------------------------------------------------- podzadanie 158: potwierdzenie i nowe rezerwacje


def test_potwierdzenie_rezerwuje_nowy_plan_i_aktualizuje_polecenie(klient, broker):
    zmien_termin(klient, broker, powod="Zmiana harmonogramu")
    (z,) = zmiany()
    roznica = z.roznica_kosztu

    zdecyduj(broker, potwierdz=True)

    assert {r.data_przejazdu for r in przejazdy(StatusRezerwacji.WYBRANA)} == {NOWY_START, NOWY_KONIEC}
    assert {r.data_przejazdu for r in przejazdy(StatusRezerwacji.ANULOWANA)} == {START, KONIEC}
    assert all("zmianie terminu" in r.powod_zmiany for r in przejazdy(StatusRezerwacji.ANULOWANA))
    (nocleg,) = noclegi(StatusRezerwacji.WYBRANA)
    assert (nocleg.data_od, nocleg.data_do) == (NOWY_START, NOWY_KONIEC)
    d = delegacja()
    assert (d.data_wyjazdu, d.data_powrotu, d.pierwotna_data_powrotu) == (NOWY_START, NOWY_KONIEC, KONIEC)
    assert d.status == Status.ZORGANIZOWANY  # Koordynator znów uznał wyjazd za zorganizowany
    with sesja() as s:
        tresc = s.scalar(select(PolecenieWyjazdu).where(PolecenieWyjazdu.id_delegacji == DEL)).tresc
        assert tresc["termin"] == {"od": NOWY_START.isoformat(), "do": NOWY_KONIEC.isoformat()}
        assert tresc["zmiany_terminu"][0]["powod"] == "Zmiana harmonogramu"
        assert s.get(Jednostka, "JW-1001").budzet_wykorzystany == ZAANGAZOWANE + roznica
    assert zmiany()[0].status == StatusZmianyTerminu.POTWIERDZONA
    assert any("Potwierdzenie zmiany planu" in t for t in broker.powiadomienia(DELEGOWANY))


def test_decyzje_zapisane_w_logu(klient, broker):
    zmien_termin(klient, broker)
    zdecyduj(broker, potwierdz=True)

    with sesja() as s:
        operacje = {o.operacja for o in s.scalars(select(LogOperacji).where(LogOperacji.id_delegacji == DEL))}
        decyzje = {x.typ_decyzji for x in s.scalars(select(LogDecyzji))}
    assert {"Nowy plan po zmianie terminu zadania", "Decyzja delegowanego o nowym planie"} <= operacje
    assert {"Nowy plan po zmianie terminu zadania", "Decyzja delegowanego o nowym planie"} <= decyzje


def test_o_planie_decyduje_tylko_delegowany(klient, broker):
    zmien_termin(klient, broker)

    zdecyduj(broker, potwierdz=True, osoba=PRZELOZONY)

    assert zmiany()[0].status == StatusZmianyTerminu.OCZEKUJE
    assert len(przejazdy(StatusRezerwacji.NOWY_PLAN)) == 2


# ---------------------------------------------------------------- podzadanie 160: brak możliwości udziału (3a–4a)


def test_brak_mozliwosci_udzialu_wstrzymuje_delegacje(klient, broker):
    zmien_termin(klient, broker)

    zdecyduj(broker, potwierdz=False, uzasadnienie="Urlop zaplanowany w nowym terminie")

    (z,) = zmiany()
    assert z.status == StatusZmianyTerminu.WSTRZYMANA and z.uzasadnienie.startswith("Urlop")
    assert przejazdy(StatusRezerwacji.WYBRANA) == [] and przejazdy(StatusRezerwacji.NOWY_PLAN) == []
    assert noclegi(StatusRezerwacji.WYBRANA) == []
    assert delegacja().status == Status.ZATWIERDZONY  # nie zostanie zorganizowana bez przełożonego
    assert any("Zdecyduj o dalszej realizacji" in t for t in broker.powiadomienia(PRZELOZONY))
    assert any("wstrzymana" in t for t in broker.powiadomienia(DELEGOWANY))


# ---------------------------------------------------------------- podzadanie 159: ekran i API dla delegowanego


def test_ekran_porownania_i_potwierdzenie(klient, broker):
    zmien_termin(klient, broker, powod="Zmiana harmonogramu")
    zaloguj_z_csrf(klient, DELEGOWANY)

    strona = klient.get(f"/app/wnioski/{DEL}/nowy-plan").text
    assert "Dotychczasowy plan" in strona and "Nowy plan" in strona and "Zmiana harmonogramu" in strona
    assert NOWY_START.strftime("%d.%m.%Y") in strona and "Potwierdzam nowy plan" in strona
    assert "Porównaj plany i potwierdź" in klient.get(f"/app/wnioski/{DEL}").text

    odp = klient.post(f"/app/wnioski/{DEL}/nowy-plan", data={"potwierdz": "tak"})
    assert odp.status_code == 303
    assert broker.historia[-1].typ == Typ.DECYZJA_NOWEGO_PLANU and broker.historia[-1].dane["potwierdz"]


def test_brak_udzialu_wymaga_przyczyny(klient, broker):
    zmien_termin(klient, broker)
    zaloguj_z_csrf(klient, DELEGOWANY)

    strona = klient.post(f"/app/wnioski/{DEL}/nowy-plan", data={"potwierdz": "nie", "uzasadnienie": " "}).text

    assert "Podaj przyczynę" in strona
    assert all(k.typ != Typ.DECYZJA_NOWEGO_PLANU for k in broker.historia)


def test_api_nowego_planu(klient, broker):
    zaloguj_z_csrf(klient, DELEGOWANY)
    assert klient.get(f"/delegacje/{DEL}/nowy-plan").status_code == 404
    assert klient.post(f"/delegacje/{DEL}/decyzja-nowego-planu", json={"potwierdz": True}).status_code == 409

    zmien_termin(klient, broker)

    plan = klient.get(f"/delegacje/{DEL}/nowy-plan").json()
    assert plan["status"] == StatusZmianyTerminu.OCZEKUJE and plan["nowy"]["termin"]
    assert klient.post(f"/delegacje/{DEL}/decyzja-nowego-planu", json={"potwierdz": True}).status_code == 202
    zaloguj_z_csrf(klient, PRZELOZONY)
    assert klient.post(f"/delegacje/{DEL}/decyzja-nowego-planu", json={"potwierdz": True}).status_code == 403


# ---------------------------------------------------------------- uwagi z przeglądu PR #33


def zadania_koordynatora(broker) -> list[str]:
    koordynator = AGENCI["koordynator"](broker)
    return [k.id_delegacji for k, _funkcja, _operacja in koordynator.zadania_cykliczne()]


def test_cykl_koordynatora_nie_przywraca_organizacji_przed_potwierdzeniem(klient, broker):
    zmien_termin(klient, broker)

    assert DEL not in zadania_koordynatora(broker)  # stare rezerwacje „wybrana” nie dają kompletu organizacji
    with sesja() as s:
        braki = brakujace_elementy_organizacji(s, s.get(Delegacja, DEL))
    assert braki == ["potwierdzenie nowego planu"]
    assert delegacja().status == Status.ZATWIERDZONY

    zdecyduj(broker, potwierdz=True)

    assert delegacja().status == Status.ZORGANIZOWANY
    assert DEL not in zadania_koordynatora(broker)


def test_odwolany_przejazd_i_zamienniki_z_poprzedniego_terminu_sa_anulowane(klient, broker):
    with sesja() as s:  # S22IT1S0P2-17: przewoźnik odwołał przejazd tam, czeka zamiennik na stary termin
        tam = s.scalar(select(Przejazd).where(Przejazd.id_delegacji == DEL, Przejazd.kierunek == "Tam"))
        tam.status = StatusRezerwacji.ODWOLANA
        s.add(
            Przejazd(
                id_delegacji=DEL,
                kierunek="Tam",
                srodek_transportu="kolej",
                data_przejazdu=START,
                id_polaczenia="POL-02",
                koszt=Decimal("55.00"),
                status=StatusRezerwacji.ALTERNATYWA,
            )
        )
        s.get(Delegacja, DEL).status = Status.ZATWIERDZONY

    zmien_termin(klient, broker)
    zdecyduj(broker, potwierdz=True)

    assert przejazdy(StatusRezerwacji.ODWOLANA) == [] and przejazdy(StatusRezerwacji.ALTERNATYWA) == []
    wybrane = przejazdy(StatusRezerwacji.WYBRANA)
    assert sorted((r.kierunek, r.data_przejazdu) for r in wybrane) == [("Powrót", NOWY_KONIEC), ("Tam", NOWY_START)]
