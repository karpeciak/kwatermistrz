"""S22IT1S0P2-34: powiadomienie delegowanego o zmianie w podróży wraz z propozycją rozwiązania zastępczego.

Scenariusz: dostawca usługi (przewoźnik albo obiekt noclegowy) zgłasza zmianę lub odwołanie usługi → system
identyfikuje delegację w trakcie realizacji, przygotowuje rozwiązanie zastępcze zgodne z warunkami delegacji i limitem
kosztów i powiadamia delegowanego → delegowany akceptuje → system aktualizuje plan, uzupełnia dokumentację i informuje
przełożonego. Alternatywnie (3a–4a): brak odpowiedzi w wyznaczonym czasie → sprawa u osoby dyżurnej jednostki.
"""

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import AtrapaBrokera
from fastapi.testclient import TestClient
from pomoc_sesji import zaloguj_z_csrf
from sqlalchemy import select

from kwatermistrz.agents import AGENCI
from kwatermistrz.api.main import app, get_broker
from kwatermistrz.db import sesja, teraz
from kwatermistrz.messaging import Komunikat, Typ
from kwatermistrz.models import (
    Delegacja,
    Jednostka,
    ObiektZakwaterowania,
    PolecenieWyjazdu,
    Przejazd,
    Status,
    StatusRezerwacji,
    StatusZmianyUslugi,
    Zakwaterowanie,
    ZmianaUslugi,
)

DEL = "DEL-PODROZ-1"
DELEGOWANY, PRZELOZONY, DYZURNY = "ZOL-003", "ZOL-001", "ZOL-002"
DZIS = teraz().date()
WYJAZD, POWROT = DZIS - timedelta(days=1), DZIS + timedelta(days=3)


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


def utworz(status: str = Status.W_TRAKCIE) -> dict[str, int]:
    """Delegacja w trakcie realizacji do Torunia: powrót POL-01-P, nocleg w Hotelu Kopernik, polecenie wyjazdu."""
    with sesja() as s:
        s.add(
            Delegacja(
                id_delegacji=DEL,
                id_zolnierza=DELEGOWANY,
                cel_wyjazdu="Szkolenie z logistyki wojskowej",
                miejsce_docelowe="Toruń",
                data_wyjazdu=WYJAZD,
                data_powrotu=POWROT,
                srodek_transportu="kolej",
                potrzeba_zakwaterowania=True,
                status=status,
                id_zatwierdzajacego=PRZELOZONY,
                decyzja="Zgoda",
            )
        )
        s.flush()
        powrot = Przejazd(
            id_delegacji=DEL,
            kierunek="Powrót",
            srodek_transportu="kolej",
            data_przejazdu=POWROT,
            id_polaczenia="POL-01-P",
            koszt=Decimal("89.00"),
            status=StatusRezerwacji.WYBRANA,
        )
        nocleg = Zakwaterowanie(
            id_delegacji=DEL,
            id_obiektu="OBJ-03",
            data_od=WYJAZD,
            data_do=POWROT,
            cena_za_dobe=Decimal("260.00"),
            koszt_calkowity=Decimal("1040.00"),
            status=StatusRezerwacji.WYBRANA,
            czas_dojazdu_min=15,
        )
        s.add_all([powrot, nocleg])
        s.add(
            PolecenieWyjazdu(
                numer="PW/2099/0034",
                id_delegacji=DEL,
                wersja_wzoru="test",
                tresc={"termin": {"od": WYJAZD.isoformat(), "do": POWROT.isoformat()}},
            )
        )
        s.get(Jednostka, "JW-1001").budzet_wykorzystany = Decimal("1000.00")
        s.flush()
        return {"powrot": powrot.id_przejazdu, "nocleg": nocleg.id_zakwaterowania}


def dostarcz(broker, k: Komunikat | None = None) -> None:
    if k is not None:
        asyncio.run(broker.publikuj(k))
    asyncio.run(broker.dostarczaj([klasa(broker) for klasa in AGENCI.values()]))


def zglos(broker, usluga: str, id_uslugi: int, rodzaj: str = "odwołanie", **dane) -> None:
    dane = {"usluga": usluga, "rodzaj": rodzaj, "id_uslugi": id_uslugi, "data": DZIS.isoformat(), **dane}
    dostarcz(broker, Komunikat(typ=Typ.ZMIANA_USLUGI, id_delegacji=DEL, dane=dane))


def odpowiedz(broker, akceptacja: bool = True) -> None:
    dane = {"id_zmiany": zmiany()[0].id_zmiany, "akceptacja": akceptacja, "id_osoby": DELEGOWANY}
    dostarcz(broker, Komunikat(typ=Typ.DECYZJA_ZMIANY_USLUGI, id_delegacji=DEL, dane=dane))


def zmiany() -> list[ZmianaUslugi]:
    with sesja() as s:
        return list(s.scalars(select(ZmianaUslugi).where(ZmianaUslugi.id_delegacji == DEL)))


def przejazd(id_przejazdu: int) -> Przejazd:
    with sesja() as s:
        return s.get(Przejazd, id_przejazdu)


def nocleg(id_zakwaterowania: int) -> Zakwaterowanie:
    with sesja() as s:
        return s.get(Zakwaterowanie, id_zakwaterowania)


def polecenie() -> dict:
    with sesja() as s:
        return s.scalar(select(PolecenieWyjazdu).where(PolecenieWyjazdu.id_delegacji == DEL)).tresc


def cykl(broker) -> None:
    monitor = AGENCI["monitorujacy"](broker)
    for k, funkcja, operacja in monitor.zadania_cykliczne():
        asyncio.run(monitor.wykonaj(k, funkcja, operacja))


# ---------------------------------------------------------------- podzadanie 147: zgłoszenie i identyfikacja


def test_api_identyfikuje_delegacje_w_trakcie_i_przygotowuje_rozwiazanie(klient, broker):
    ids = utworz()

    odp = klient.post(
        "/zmiany-uslug",
        json={
            "usluga": "transport",
            "rodzaj": "odwołanie",
            "id_polaczenia": "POL-01-P",
            "data": POWROT.isoformat(),
            "opis": "Awaria taboru",
        },
    )
    assert odp.status_code == 202 and odp.json()["delegacje"] == [DEL]
    dostarcz(broker)

    (zmiana,) = zmiany()
    assert zmiana.status == StatusZmianyUslugi.OCZEKUJE and zmiana.id_przejazdu == ids["powrot"]
    assert przejazd(ids["powrot"]).status == StatusRezerwacji.ZAKLOCONA
    zastepczy = przejazd(zmiana.id_przejazdu_zastepczego)
    assert zastepczy.status == StatusRezerwacji.ZASTEPCZA and zastepczy.id_polaczenia == "POL-03-P"  # najtańszy
    assert zmiana.zgodny_z_limitem is True and zmiana.koszt_zastepczy == Decimal("47.00")
    assert zmiana.termin_odpowiedzi > teraz()
    (tresc,) = broker.powiadomienia(DELEGOWANY)
    assert DEL in tresc and "odwołanie" in tresc and "Rozwiązanie zastępcze" in tresc and "Odpowiedz do" in tresc


def test_api_odrzuca_zmiane_bez_delegacji_w_trakcie(klient):
    utworz(status=Status.ZORGANIZOWANY)  # podróż jeszcze się nie rozpoczęła – to obsługuje -17

    odp = klient.post(
        "/zmiany-uslug",
        json={"usluga": "transport", "rodzaj": "odwołanie", "id_polaczenia": "POL-01-P", "data": POWROT.isoformat()},
    )
    assert odp.status_code == 404


def test_api_wymaga_kursu_i_opoznienia(klient):
    bez_kursu = klient.post("/zmiany-uslug", json={"usluga": "transport", "rodzaj": "odwołanie", "data": "2026-12-01"})
    assert bez_kursu.status_code == 422
    bez_minut = klient.post(
        "/zmiany-uslug",
        json={"usluga": "transport", "rodzaj": "opóźnienie", "id_polaczenia": "POL-01-P", "data": "2026-12-01"},
    )
    assert bez_minut.status_code == 422


def test_ponowione_zgloszenie_nie_dubluje_rozwiazania(broker):
    ids = utworz()

    zglos(broker, "transport", ids["powrot"])
    zglos(broker, "transport", ids["powrot"])

    assert len(zmiany()) == 1


# ---------------------------------------------------------------- podzadanie 148: rozwiązanie zastępcze


def test_opoznienie_proponuje_tylko_kurs_ktory_dojedzie_wczesniej(broker):
    ids = utworz()

    # POL-01-P przyjeżdża o 19:35, z opóźnieniem 20 min o 19:55 – najtańszy POL-03-P (20:35) nic by nie dał,
    # proponowany jest kurs, który dojedzie wcześniej (POL-02-P 19:45, POL-04-P 19:50, POL-05-P 19:30)
    zglos(broker, "transport", ids["powrot"], rodzaj="opóźnienie", opoznienie_min=20)

    (zmiana,) = zmiany()
    assert zmiana.status == StatusZmianyUslugi.OCZEKUJE and "opóźnienie 20 min" in zmiana.opis
    assert przejazd(zmiana.id_przejazdu_zastepczego).id_polaczenia in {"POL-02-P", "POL-04-P", "POL-05-P"}


def test_opoznienie_bez_potrzeby_zamiennika_nie_zmienia_planu(broker):
    ids = utworz()

    zglos(broker, "zakwaterowanie", ids["nocleg"], rodzaj="opóźnienie", opis="Zameldowanie dopiero od 18:00")

    (zmiana,) = zmiany()
    assert zmiana.status == StatusZmianyUslugi.BEZ_ZMIAN and zmiana.id_zakwaterowania_zastepczego is None
    assert nocleg(ids["nocleg"]).status == StatusRezerwacji.WYBRANA
    assert any("bez zmian" in t for t in broker.powiadomienia(DELEGOWANY))


def test_brak_dostepnosci_noclegu_daje_obiekt_wojskowy_w_limicie(broker):
    ids = utworz()

    zglos(broker, "zakwaterowanie", ids["nocleg"], rodzaj="brak dostępności")

    (zmiana,) = zmiany()
    zastepczy = nocleg(zmiana.id_zakwaterowania_zastepczego)
    assert zastepczy.id_obiektu == "OBJ-02"  # wojskowy z miejscami i dojazdem na czas – przed cywilnymi
    assert zastepczy.data_od == DZIS and zastepczy.data_do == POWROT
    assert zmiana.koszt_zastepczy == Decimal("180.00") and zmiana.zgodny_z_limitem is True
    assert nocleg(ids["nocleg"]).status == StatusRezerwacji.ZAKLOCONA


def test_brak_rozwiazania_w_budzecie_przekazuje_sprawe_dyzurnemu(broker):
    ids = utworz()
    with sesja() as s:
        j = s.get(Jednostka, "JW-1001")
        j.budzet_delegacji, j.budzet_wykorzystany = Decimal("0"), Decimal("1000.00")

    zglos(broker, "transport", ids["powrot"])

    (zmiana,) = zmiany()
    assert zmiana.status == StatusZmianyUslugi.U_DYZURNEGO and zmiana.id_dyzurnego == DYZURNY
    assert any("osoba dyżurna" in t.lower() for t in broker.powiadomienia(DYZURNY))
    assert any("przekazano osobie dyżurnej" in t for t in broker.powiadomienia(DELEGOWANY))


# ---------------------------------------------------------------- podzadanie 150: akceptacja i aktualizacja planu


def test_akceptacja_przejazdu_aktualizuje_plan_dokumentacje_i_informuje_przelozonego(klient, broker):
    ids = utworz()
    zglos(broker, "transport", ids["powrot"])
    (zmiana,) = zmiany()

    zaloguj_z_csrf(klient, DELEGOWANY)
    odp = klient.post(
        f"/app/wnioski/{DEL}/zmiana-w-podrozy", data={"id_zmiany": str(zmiana.id_zmiany), "odpowiedz": "akceptuje"}
    )
    assert odp.status_code == 303
    dostarcz(broker)

    assert zmiany()[0].status == StatusZmianyUslugi.ZAAKCEPTOWANA
    assert przejazd(ids["powrot"]).status == StatusRezerwacji.ANULOWANA
    assert przejazd(zmiana.id_przejazdu_zastepczego).status == StatusRezerwacji.WYBRANA
    with sesja() as s:
        assert s.get(Jednostka, "JW-1001").budzet_wykorzystany == Decimal("958.00")  # 47 zł zamiast 89 zł
    (wpis,) = polecenie()["zmiany_w_podrozy"]
    assert wpis["zdarzenie"] == "odwołanie" and "POL" not in wpis["rozwiazanie"] and wpis["koszt"] == "47.00"
    assert any("zaakceptował rozwiązanie zastępcze" in t for t in broker.powiadomienia(PRZELOZONY))
    assert any("Potwierdzenie zmiany" in t for t in broker.powiadomienia(DELEGOWANY))


def test_akceptacja_noclegu_przenosi_miejsca_i_uzupelnia_polecenie(broker):
    ids = utworz()
    zglos(broker, "zakwaterowanie", ids["nocleg"], rodzaj="brak dostępności")
    with sesja() as s:
        miejsca = {o: s.get(ObiektZakwaterowania, o).liczba_wolnych_miejsc for o in ("OBJ-02", "OBJ-03")}

    odpowiedz(broker)

    with sesja() as s:
        assert s.get(ObiektZakwaterowania, "OBJ-02").liczba_wolnych_miejsc == miejsca["OBJ-02"] - 1
        assert s.get(ObiektZakwaterowania, "OBJ-03").liczba_wolnych_miejsc == miejsca["OBJ-03"] + 1
        assert s.get(Jednostka, "JW-1001").budzet_wykorzystany == Decimal("400.00")  # 180 zł zamiast 3 × 260 zł
    assert nocleg(ids["nocleg"]).status == StatusRezerwacji.ANULOWANA
    assert polecenie()["zakwaterowanie"]["obiekt"] == "Hotel garnizonowy Chełmno"


def test_odpowiada_tylko_delegowany(klient, broker):
    ids = utworz()
    zglos(broker, "transport", ids["powrot"])
    (zmiana,) = zmiany()

    zaloguj_z_csrf(klient, PRZELOZONY)
    odp = klient.post(f"/delegacje/{DEL}/odpowiedz-na-zmiane", json={"id_zmiany": zmiana.id_zmiany, "akceptacja": True})
    assert odp.status_code == 403
    assert zmiany()[0].status == StatusZmianyUslugi.OCZEKUJE


# ---------------------------------------------------------------- podzadanie 151: brak odpowiedzi i odrzucenie


def test_brak_odpowiedzi_w_terminie_przekazuje_sprawe_osobie_dyzurnej(broker):
    ids = utworz()
    zglos(broker, "transport", ids["powrot"])
    cykl(broker)
    assert zmiany()[0].status == StatusZmianyUslugi.OCZEKUJE  # termin jeszcze nie minął

    with sesja() as s:
        s.get(ZmianaUslugi, zmiany()[0].id_zmiany).termin_odpowiedzi = teraz() - timedelta(minutes=1)
    cykl(broker)

    (zmiana,) = zmiany()
    assert zmiana.status == StatusZmianyUslugi.U_DYZURNEGO and zmiana.id_dyzurnego == DYZURNY
    assert przejazd(zmiana.id_przejazdu_zastepczego).status == StatusRezerwacji.ANULOWANA
    assert any("Brak odpowiedzi" in t for t in broker.powiadomienia(DYZURNY))
    assert any("przekazano osobie dyżurnej" in t for t in broker.powiadomienia(DELEGOWANY))


def test_odrzucenie_przekazuje_sprawe_osobie_dyzurnej(broker):
    ids = utworz()
    zglos(broker, "transport", ids["powrot"])

    odpowiedz(broker, akceptacja=False)

    assert zmiany()[0].status == StatusZmianyUslugi.U_DYZURNEGO
    assert przejazd(ids["powrot"]).status == StatusRezerwacji.ZAKLOCONA


# ---------------------------------------------------------------- podzadanie 149: ekran


def test_ekran_pokazuje_zmiane_rozwiazanie_i_termin(klient, broker):
    ids = utworz()
    zglos(broker, "transport", ids["powrot"])
    zaloguj_z_csrf(klient, DELEGOWANY)

    strona = klient.get(f"/app/wnioski/{DEL}/zmiana-w-podrozy").text
    assert "Dostawca zgłosił zmianę: odwołanie" in strona and "Rozwiązanie zastępcze" in strona
    assert "Termin odpowiedzi" in strona and "Akceptuję rozwiązanie zastępcze" in strona

    wniosek = klient.get(f"/app/wnioski/{DEL}").text
    assert f"/app/wnioski/{DEL}/zmiana-w-podrozy" in wniosek
    assert "oczekiwanie na Twoją odpowiedź" in wniosek


def test_ekran_po_przekazaniu_nie_pozwala_odpowiedziec(klient, broker):
    ids = utworz()
    zglos(broker, "transport", ids["powrot"])
    odpowiedz(broker, akceptacja=False)
    zaloguj_z_csrf(klient, DELEGOWANY)

    strona = klient.get(f"/app/wnioski/{DEL}/zmiana-w-podrozy").text
    assert "Akceptuję rozwiązanie zastępcze" not in strona and "u osoby dyżurnej" in strona

    odp = klient.post(
        f"/app/wnioski/{DEL}/zmiana-w-podrozy",
        data={"id_zmiany": str(zmiany()[0].id_zmiany), "odpowiedz": "akceptuje"},
    )
    assert odp.status_code == 200 and "nie czeka już na odpowiedź" in odp.text
