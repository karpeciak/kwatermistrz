"""S22IT1S0P2-42: ponowienie zlecenia i powiadomienie delegowanego o opóźnieniu realizacji (podzadania 162–165).

Scenariusz: zatwierdzona delegacja czeka na warianty transportu → system wykrywa przekroczenie dopuszczalnego czasu,
ponawia zlecenie i podaje przewidywany termin → delegowany potwierdza oczekiwanie → po przywróceniu działania
Agent Transportu przygotowuje warianty → delegowany wybiera i rezerwuje transport (jak w S22IT1S0P2-27).
Alternatywnie (3a–4a): delegowany rezygnuje – zlecenie wycofane, incydent, sprawa u osoby odpowiedzialnej.
"""

import asyncio
from datetime import date, timedelta

import pytest
from conftest import AtrapaBrokera
from fastapi.testclient import TestClient
from pomoc_sesji import zaloguj_z_csrf
from sqlalchemy import select

from kwatermistrz.agents import AGENCI
from kwatermistrz.agents.opoznienia import DOPUSZCZALNY_CZAS, MAKS_PONOWIEN
from kwatermistrz.api.main import app, get_broker
from kwatermistrz.db import sesja, teraz
from kwatermistrz.messaging import Komunikat, Typ
from kwatermistrz.models import Delegacja, LogOperacji, Przejazd, Status, StatusZlecenia, ZlecenieAgenta

DEL, DELEGOWANY, ODPOWIEDZIALNY = "DEL-OPOZN-42", "ZOL-003", "ZOL-001"


class BrokerZHistoria(AtrapaBrokera):
    def __init__(self) -> None:
        super().__init__()
        self.historia: list[Komunikat] = []

    async def publikuj(self, k: Komunikat) -> None:
        self.historia.append(k)
        await super().publikuj(k)

    def powiadomienia(self, do: str) -> list[str]:
        return [k.dane["tresc"] for k in self.historia if k.typ == Typ.POWIADOMIENIE and k.dane.get("do") == do]

    def zlecenia_transportu(self) -> int:
        return sum(1 for k in self.historia if k.typ == Typ.ZADANIE_TRANSPORTU)


@pytest.fixture
def broker() -> BrokerZHistoria:
    return BrokerZHistoria()


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()


def zatwierdzona(minut_temu: int = 15) -> None:
    """Delegacja zatwierdzona `minut_temu` minut temu – Agent Transportu jeszcze nie odpowiedział."""
    with sesja() as s:
        s.add(
            Delegacja(
                id_delegacji=DEL,
                id_zolnierza=DELEGOWANY,
                id_przedsiewziecia="PRZ-01",
                dane_wniosku={},
                cel_wyjazdu="Szkolenie z logistyki wojskowej",
                miejsce_docelowe="Toruń",
                data_wyjazdu=date(2026, 12, 6),
                data_powrotu=date(2026, 12, 11),
                srodek_transportu="kolej",
                potrzeba_zakwaterowania=False,
                status=Status.ZATWIERDZONY,
                data_zmiany_statusu=teraz() - timedelta(minutes=minut_temu),
                id_zatwierdzajacego=ODPOWIEDZIALNY,
                decyzja="Zgoda",
            )
        )


def cykl(broker) -> list[str]:
    """Jeden przebieg cyklicznej kontroli Agenta Monitorującego – zwraca kontrolowane delegacje."""
    monitor = AGENCI["monitorujacy"](broker)
    zadania = monitor.zadania_cykliczne()
    for k, funkcja, operacja in zadania:
        asyncio.run(monitor.wykonaj(k, funkcja, operacja))
    return [k.id_delegacji for k, _f, _o in zadania]


def awaria_transportu(broker) -> None:
    """Agent Transportu nie działa – ponowione zlecenia nie zostają obsłużone."""
    broker.wyslane[:] = [k for k in broker.wyslane if k.typ != Typ.ZADANIE_TRANSPORTU]


def dostarcz(broker, typ: str | None = None, **dane) -> None:
    if typ is not None:
        asyncio.run(broker.publikuj(Komunikat(typ=typ, id_delegacji=DEL, dane=dane)))
    asyncio.run(broker.dostarczaj([klasa(broker) for klasa in AGENCI.values()]))


def zlecenie() -> ZlecenieAgenta | None:
    with sesja() as s:
        return s.scalar(select(ZlecenieAgenta).where(ZlecenieAgenta.id_delegacji == DEL))


def przesun_termin_w_przeszlosc() -> None:
    with sesja() as s:
        z = s.scalar(select(ZlecenieAgenta).where(ZlecenieAgenta.id_delegacji == DEL))
        z.przewidywany_termin = teraz() - timedelta(minutes=1)


def przejazdy() -> list[Przejazd]:
    with sesja() as s:
        return list(s.scalars(select(Przejazd).where(Przejazd.id_delegacji == DEL)))


# ---------------------------------------------------------------- podzadanie 162: wykrycie i ponowienie


def test_przekroczony_czas_ponawia_zlecenie_i_podaje_termin(broker):
    zatwierdzona()

    assert cykl(broker) == [DEL]

    z = zlecenie()
    assert z.status == StatusZlecenia.OPOZNIONE and z.liczba_ponowien == 1
    assert teraz() + DOPUSZCZALNY_CZAS - timedelta(minutes=1) < z.przewidywany_termin <= teraz() + DOPUSZCZALNY_CZAS
    assert broker.zlecenia_transportu() == 1  # zlecenie ponowione
    (tresc,) = broker.powiadomienia(DELEGOWANY)
    assert "trwa dłużej niż zwykle" in tresc and "przewidywany termin odpowiedzi" in tresc
    assert "Potwierdź oczekiwanie albo zrezygnuj" in tresc
    with sesja() as s:
        op = s.scalar(select(LogOperacji).where(LogOperacji.id_delegacji == DEL, LogOperacji.id_agenta == "AG-08"))
        assert op.wynik == "Ostrzeżenie" and "Przekroczono czas realizacji" in op.komunikat


def test_zlecenie_w_terminie_nie_jest_ponawiane(broker):
    zatwierdzona(minut_temu=5)

    assert cykl(broker) == []
    assert zlecenie() is None and broker.zlecenia_transportu() == 0


def test_kolejne_ponowienie_dopiero_po_przewidywanym_terminie(broker):
    zatwierdzona()
    cykl(broker)

    assert cykl(broker) == []  # przewidywany termin jeszcze nie minął
    przesun_termin_w_przeszlosc()
    cykl(broker)

    assert zlecenie().liczba_ponowien == 2 and broker.zlecenia_transportu() == 2


def test_wyczerpane_ponowienia_wycofuja_zlecenie(broker):
    zatwierdzona()
    cykl(broker)
    with sesja() as s:
        z = s.scalar(select(ZlecenieAgenta).where(ZlecenieAgenta.id_delegacji == DEL))
        z.liczba_ponowien, z.przewidywany_termin = MAKS_PONOWIEN, teraz() - timedelta(minutes=1)

    cykl(broker)

    z = zlecenie()
    assert z.status == StatusZlecenia.WYCOFANE and f"po {MAKS_PONOWIEN} ponowieniach" in z.incydent
    assert any("Zorganizuj transport indywidualnie" in t for t in broker.powiadomienia(ODPOWIEDZIALNY))


# ---------------------------------------------------------------- podzadanie 164: realizacja po przywróceniu działania


def test_po_przywroceniu_dzialania_warianty_trafiaja_do_delegowanego(broker):
    zatwierdzona()
    cykl(broker)
    awaria_transportu(broker)
    dostarcz(broker, Typ.DECYZJA_OCZEKIWANIA, czekam=True, id_osoby=DELEGOWANY)
    assert zlecenie().status == StatusZlecenia.OCZEKIWANIE and przejazdy() == []

    przesun_termin_w_przeszlosc()
    cykl(broker)  # kolejne ponowienie
    dostarcz(broker)  # Agent Transportu znów działa i obsługuje ponowione zlecenie
    assert przejazdy()  # warianty przygotowane
    cykl(broker)

    z = zlecenie()
    assert z.status == StatusZlecenia.ZREALIZOWANE and z.data_zakonczenia is not None
    assert any("warianty transportu są gotowe" in t for t in broker.powiadomienia(DELEGOWANY))
    assert cykl(broker) == []  # sprawa zamknięta


def test_po_potwierdzeniu_oczekiwania_kolejne_powiadomienie_bez_pytania(broker):
    zatwierdzona()
    cykl(broker)
    awaria_transportu(broker)
    dostarcz(broker, Typ.DECYZJA_OCZEKIWANIA, czekam=True, id_osoby=DELEGOWANY)
    przesun_termin_w_przeszlosc()

    cykl(broker)

    ostatnie = broker.powiadomienia(DELEGOWANY)[-1]
    assert "przewidywany termin odpowiedzi" in ostatnie and "Potwierdź oczekiwanie" not in ostatnie


# ---------------------------------------------------------------- 3a–4a: rezygnacja z oczekiwania


def test_rezygnacja_wycofuje_zlecenie_rejestruje_incydent_i_przekazuje_sprawe(broker):
    zatwierdzona()
    cykl(broker)
    awaria_transportu(broker)

    dostarcz(broker, Typ.DECYZJA_OCZEKIWANIA, czekam=False, powod="Wyjazd za dwa dni", id_osoby=DELEGOWANY)
    dostarcz(broker, Typ.ZADANIE_TRANSPORTU)  # spóźnione zlecenie dociera po przywróceniu działania

    z = zlecenie()
    assert z.status == StatusZlecenia.WYCOFANE and "Wyjazd za dwa dni" in z.incydent
    assert przejazdy() == []  # spóźnione zlecenie transportu pominięte
    assert any("Zorganizuj transport indywidualnie" in t for t in broker.powiadomienia(ODPOWIEDZIALNY))
    assert any("wycofano" in t for t in broker.powiadomienia(DELEGOWANY))
    with sesja() as s:
        incydent = s.scalar(
            select(LogOperacji).where(
                LogOperacji.id_delegacji == DEL, LogOperacji.operacja == "Decyzja o oczekiwaniu na realizację zlecenia"
            )
        )
        assert incydent.wynik == "Ostrzeżenie" and incydent.komunikat.startswith("Incydent:")
    assert cykl(broker) == []


def test_o_oczekiwaniu_decyduje_tylko_delegowany(broker):
    zatwierdzona()
    cykl(broker)
    awaria_transportu(broker)

    dostarcz(broker, Typ.DECYZJA_OCZEKIWANIA, czekam=False, id_osoby=ODPOWIEDZIALNY)

    assert zlecenie().status == StatusZlecenia.OPOZNIONE


# ---------------------------------------------------------------- podzadanie 163: ekran i API


def test_ekran_wniosku_pokazuje_opoznienie_i_przyjmuje_decyzje(klient, broker):
    zatwierdzona()
    cykl(broker)
    zaloguj_z_csrf(klient, DELEGOWANY)

    strona = klient.get(f"/app/wnioski/{DEL}").text
    assert "Przygotowanie transportu trwa dłużej niż zwykle" in strona
    assert "Przewidywany termin odpowiedzi" in strona and "Poczekam na realizację" in strona

    odp = klient.post(f"/app/wnioski/{DEL}/zlecenie-transportu", data={"czekam": "tak"})
    assert odp.status_code == 303 and "zlecenie-oczekiwanie" in odp.headers["location"]
    assert broker.historia[-1].typ == Typ.DECYZJA_OCZEKIWANIA and broker.historia[-1].dane["czekam"]


def test_ekran_po_wycofaniu(klient, broker):
    zatwierdzona()
    cykl(broker)
    awaria_transportu(broker)
    dostarcz(broker, Typ.DECYZJA_OCZEKIWANIA, czekam=False, id_osoby=DELEGOWANY)
    zaloguj_z_csrf(klient, DELEGOWANY)

    assert "Zlecenie przygotowania transportu wycofano" in klient.get(f"/app/wnioski/{DEL}").text


def test_api_zlecenia_transportu(klient, broker):
    zatwierdzona()
    zaloguj_z_csrf(klient, DELEGOWANY)
    assert klient.get(f"/delegacje/{DEL}/zlecenie-transportu").status_code == 404
    assert klient.post(f"/delegacje/{DEL}/oczekiwanie-na-zlecenie", json={"czekam": True}).status_code == 409

    cykl(broker)

    stan = klient.get(f"/delegacje/{DEL}/zlecenie-transportu").json()
    assert stan["status"] == StatusZlecenia.OPOZNIONE and stan["liczba_ponowien"] == 1
    assert klient.post(f"/delegacje/{DEL}/oczekiwanie-na-zlecenie", json={"czekam": True}).status_code == 202
    zaloguj_z_csrf(klient, ODPOWIEDZIALNY)
    assert klient.post(f"/delegacje/{DEL}/oczekiwanie-na-zlecenie", json={"czekam": True}).status_code == 403
