"""S22IT1S0P2-17: zmiana przejazdu po jego odwołaniu przez przewoźnika (podzadania 134–138).

Scenariusz: przewoźnik odwołuje zarezerwowany przejazd → system informuje delegowanego i przedstawia przejazdy
alternatywne z terminowym dojazdem w budżecie jednostki → delegowany wybiera jeden z nich → system anuluje starą
rezerwację, rezerwuje nowy przejazd, aktualizuje dokumentację (i nocleg przy zmianie dnia przybycia) i potwierdza
zmiany. Alternatywnie (3a–4a): delegowany odrzuca wszystkie propozycje i sprawę przejmuje osoba odpowiedzialna.
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
from kwatermistrz.api.main import app, get_broker
from kwatermistrz.db import sesja, teraz
from kwatermistrz.messaging import Komunikat, Typ
from kwatermistrz.models import (
    Delegacja,
    Jednostka,
    LogOperacji,
    PolecenieWyjazdu,
    Przedsiewziecie,
    Przejazd,
    Status,
    StatusRezerwacji,
    Zakwaterowanie,
)

DEL = "DEL-ODW-1"
DELEGOWANY, ORGANIZATOR = "ZOL-003", "ZOL-001"
WYJAZD = teraz().date() + timedelta(days=10)
POWROT = WYJAZD + timedelta(days=4)


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


def utworz(status: str = Status.ZORGANIZOWANY, godzina_rozpoczecia: time | None = None) -> dict[str, int]:
    """Zorganizowana delegacja do Torunia: przejazd tam (POL-01) i z powrotem, nocleg i polecenie wyjazdu."""
    with sesja() as s:
        if godzina_rozpoczecia:
            s.add(
                Przedsiewziecie(
                    id_przedsiewziecia="PRZ-T17",
                    nazwa="Szkolenie testowe",
                    miejscowosc="Toruń",
                    data_rozpoczecia=WYJAZD,
                    data_zakonczenia=POWROT,
                    godzina_rozpoczecia=godzina_rozpoczecia,
                )
            )
        s.add(
            Delegacja(
                id_delegacji=DEL,
                id_zolnierza=DELEGOWANY,
                id_przedsiewziecia="PRZ-T17" if godzina_rozpoczecia else None,
                cel_wyjazdu="Szkolenie z logistyki wojskowej",
                miejsce_docelowe="Toruń",
                data_wyjazdu=WYJAZD,
                data_powrotu=POWROT,
                srodek_transportu="kolej",
                potrzeba_zakwaterowania=True,
                status=status,
                id_zatwierdzajacego=ORGANIZATOR,
                decyzja="Zgoda",
            )
        )
        s.flush()
        tam = Przejazd(
            id_delegacji=DEL,
            kierunek="Tam",
            srodek_transportu="kolej",
            data_przejazdu=WYJAZD,
            id_polaczenia="POL-01",
            koszt=Decimal("89.00"),
            status=StatusRezerwacji.WYBRANA,
        )
        powrot = Przejazd(
            id_delegacji=DEL,
            kierunek="Powrót",
            srodek_transportu="kolej",
            data_przejazdu=POWROT,
            id_polaczenia="POL-01-P",
            koszt=Decimal("89.00"),
            status=StatusRezerwacji.WYBRANA,
        )
        s.add_all([tam, powrot])
        s.add(
            Zakwaterowanie(
                id_delegacji=DEL,
                id_obiektu="OBJ-03",
                data_od=WYJAZD,
                data_do=POWROT,
                cena_za_dobe=Decimal("260.00"),
                koszt_calkowity=Decimal("1040.00"),
                status=StatusRezerwacji.WYBRANA,
                czas_dojazdu_min=15,
            )
        )
        s.add(
            PolecenieWyjazdu(
                numer="PW/2099/0017",
                id_delegacji=DEL,
                wersja_wzoru="test",
                tresc={"termin": {"od": WYJAZD.isoformat(), "do": POWROT.isoformat()}},
            )
        )
        s.get(Jednostka, "JW-1001").budzet_wykorzystany = Decimal("1000.00")
        s.flush()
        return {"tam": tam.id_przejazdu, "powrot": powrot.id_przejazdu}


def dostarcz(broker, k: Komunikat | None = None) -> None:
    if k is not None:
        asyncio.run(broker.publikuj(k))
    asyncio.run(broker.dostarczaj([klasa(broker) for klasa in AGENCI.values()]))


def odwolaj(broker, id_przejazdu: int, przyczyna: str = "Awaria taboru") -> None:
    dane = {"id_przejazdu": id_przejazdu, "przyczyna": przyczyna}
    dostarcz(broker, Komunikat(typ=Typ.ODWOLANIE_PRZEJAZDU, id_delegacji=DEL, dane=dane))


def przejazdy(status: str | None = None) -> list[Przejazd]:
    with sesja() as s:
        zapytanie = select(Przejazd).where(Przejazd.id_delegacji == DEL).order_by(Przejazd.id_przejazdu)
        return [r for r in s.scalars(zapytanie) if status is None or r.status == status]


def delegacja() -> Delegacja:
    with sesja() as s:
        return s.get(Delegacja, DEL)


# ---------------------------------------------------------------- podzadanie 134: odwołanie i propozycje


def test_odwolanie_przez_api_daje_propozycje_alternatywne_i_powiadomienie(klient, broker):
    ids = utworz()

    odp = klient.post(
        f"/delegacje/{DEL}/przejazdy/{ids['tam']}/odwolanie",
        json={"przyczyna": "Awaria taboru", "przewoznik": "PKP Intercity"},
    )
    assert odp.status_code == 202
    dostarcz(broker)

    odwolany = przejazdy(StatusRezerwacji.ODWOLANA)
    assert [r.id_przejazdu for r in odwolany] == [ids["tam"]]
    assert "PKP Intercity: Awaria taboru" in odwolany[0].powod_zmiany
    alternatywy = przejazdy(StatusRezerwacji.ALTERNATYWA)
    assert {r.id_polaczenia for r in alternatywy} == {"POL-02", "POL-03", "POL-04", "POL-05"}
    assert all(r.kierunek == "Tam" and r.data_przejazdu == WYJAZD for r in alternatywy)
    assert [r.id_polaczenia for r in alternatywy if r.rekomendowany] == ["POL-03"]  # najtańszy terminowy
    assert delegacja().status == Status.ZATWIERDZONY  # organizacja znów niekompletna
    assert any("odwołał przejazd" in t for t in broker.powiadomienia(DELEGOWANY))


def test_propozycje_zapewniaja_przybycie_przed_rozpoczeciem_zadania(broker):
    ids = utworz(godzina_rozpoczecia=time(18, 35))

    odwolaj(broker, ids["tam"])

    # POL-02 (19:05) i POL-03 (18:40) dojeżdżają po rozpoczęciu o 18:35 – nie są proponowane
    assert {r.id_polaczenia for r in przejazdy(StatusRezerwacji.ALTERNATYWA)} == {"POL-04", "POL-05"}


def test_gdy_nikt_nie_zdazy_w_dniu_wyjazdu_propozycje_sa_dzien_wczesniej(broker):
    ids = utworz(godzina_rozpoczecia=time(12, 0))

    odwolaj(broker, ids["tam"])

    alternatywy = przejazdy(StatusRezerwacji.ALTERNATYWA)
    assert alternatywy and all(r.data_przejazdu == WYJAZD - timedelta(days=1) for r in alternatywy)
    assert all("dzień wcześniej" in r.uzasadnienie for r in alternatywy)


def test_brak_zamiennika_w_budzecie_przekazuje_sprawe_organizatorowi(broker):
    ids = utworz()
    with sesja() as s:
        j = s.get(Jednostka, "JW-1001")
        j.budzet_delegacji, j.budzet_wykorzystany = Decimal("0"), Decimal("1000.00")

    odwolaj(broker, ids["tam"])

    assert przejazdy(StatusRezerwacji.ALTERNATYWA) == []
    assert any("zorganizuj przejazd indywidualnie" in t for t in broker.powiadomienia(ORGANIZATOR))
    assert any("przekazano osobie odpowiedzialnej" in t for t in broker.powiadomienia(DELEGOWANY))


def test_ponowne_odwolanie_nie_dubluje_propozycji(broker):
    ids = utworz()

    odwolaj(broker, ids["tam"])
    odwolaj(broker, ids["tam"])

    assert len(przejazdy(StatusRezerwacji.ALTERNATYWA)) == 4


def test_api_nie_przyjmuje_odwolania_po_rozpoczeciu_podrozy(klient):
    ids = utworz(status=Status.W_TRAKCIE)

    odp = klient.post(f"/delegacje/{DEL}/przejazdy/{ids['tam']}/odwolanie", json={"przyczyna": "Awaria taboru"})
    assert odp.status_code == 409


# ---------------------------------------------------------------- podzadanie 135: anulowanie i nowa rezerwacja


def test_wybor_zamiennika_anuluje_stara_rezerwacje_i_aktualizuje_dokumentacje(klient, broker):
    ids = utworz()
    odwolaj(broker, ids["tam"])
    pol_02 = next(r for r in przejazdy(StatusRezerwacji.ALTERNATYWA) if r.id_polaczenia == "POL-02")

    zaloguj_z_csrf(klient, DELEGOWANY)
    odp = klient.post(f"/app/wnioski/{DEL}/przejazd-alternatywny", data={"id_przejazdu": str(pol_02.id_przejazdu)})
    assert odp.status_code == 303
    dostarcz(broker)

    statusy = {r.id_przejazdu: r.status for r in przejazdy()}
    assert statusy[ids["tam"]] == StatusRezerwacji.ANULOWANA
    assert statusy[pol_02.id_przejazdu] == StatusRezerwacji.WYBRANA
    assert statusy[ids["powrot"]] == StatusRezerwacji.WYBRANA
    assert list(statusy.values()).count(StatusRezerwacji.ANULOWANA) == 4  # stara rezerwacja + 3 niewybrane
    assert delegacja().status == Status.ZORGANIZOWANY  # Koordynator po wyniku transportu
    with sesja() as s:
        assert s.get(Jednostka, "JW-1001").budzet_wykorzystany == Decimal("966.00")  # 55 zł zamiast 89 zł
        polecenie = s.scalar(select(PolecenieWyjazdu).where(PolecenieWyjazdu.id_delegacji == DEL))
        assert "PKP Intercity (TLK)" in polecenie.tresc["zmiany_przejazdu"][0]["nowy"]
    assert any("Potwierdzenie zmiany przejazdu" in t for t in broker.powiadomienia(DELEGOWANY))


def test_wczesniejszy_dojazd_przesuwa_nocleg_i_date_wyjazdu(broker):
    ids = utworz(godzina_rozpoczecia=time(12, 0))
    odwolaj(broker, ids["tam"])
    rekomendowany = next(r for r in przejazdy(StatusRezerwacji.ALTERNATYWA) if r.rekomendowany)

    dane = {"id_przejazdu": rekomendowany.id_przejazdu, "id_osoby": DELEGOWANY}
    dostarcz(broker, Komunikat(typ=Typ.WYBOR_PRZEJAZDU_ALTERNATYWNEGO, id_delegacji=DEL, dane=dane))

    dzien_wczesniej = WYJAZD - timedelta(days=1)
    assert delegacja().data_wyjazdu == dzien_wczesniej
    with sesja() as s:
        nocleg = s.scalar(select(Zakwaterowanie).where(Zakwaterowanie.id_delegacji == DEL))
        assert nocleg.data_od == dzien_wczesniej and nocleg.koszt_calkowity == Decimal("1300.00")  # 5 dób
        polecenie = s.scalar(select(PolecenieWyjazdu).where(PolecenieWyjazdu.id_delegacji == DEL))
        assert polecenie.tresc["termin"]["od"] == dzien_wczesniej.isoformat()


def test_zamiennik_wybiera_tylko_delegowany(klient, broker):
    ids = utworz()
    odwolaj(broker, ids["tam"])
    propozycja = przejazdy(StatusRezerwacji.ALTERNATYWA)[0]

    zaloguj_z_csrf(klient, ORGANIZATOR)
    odp = klient.post(
        f"/delegacje/{DEL}/wybor-przejazdu-alternatywnego", json={"id_przejazdu": propozycja.id_przejazdu}
    )
    assert odp.status_code == 403
    assert len(przejazdy(StatusRezerwacji.ALTERNATYWA)) == 4


# ---------------------------------------------------------------- podzadanie 137: odrzucenie wszystkich (3a–4a)


def test_odrzucenie_wszystkich_przekazuje_sprawe_osobie_odpowiedzialnej(klient, broker):
    ids = utworz()
    odwolaj(broker, ids["tam"])
    zaloguj_z_csrf(klient, DELEGOWANY)

    bez_przyczyny = klient.post(f"/app/wnioski/{DEL}/przejazd-alternatywny/odrzucenie", data={"przyczyna": " "})
    assert bez_przyczyny.status_code == 200 and "Podaj przyczynę" in bez_przyczyny.text

    odp = klient.post(
        f"/app/wnioski/{DEL}/przejazd-alternatywny/odrzucenie", data={"przyczyna": "Żaden nie pasuje do służby"}
    )
    assert odp.status_code == 303
    dostarcz(broker)

    assert przejazdy(StatusRezerwacji.ALTERNATYWA) == []
    assert len(przejazdy(StatusRezerwacji.DO_UZGODNIENIA)) == 4
    assert any("zorganizuj przejazd indywidualnie" in t for t in broker.powiadomienia(ORGANIZATOR))
    assert any("przekazano osobie odpowiedzialnej" in t for t in broker.powiadomienia(DELEGOWANY))
    with sesja() as s:
        wyniki = s.scalars(
            select(LogOperacji.wynik).where(
                LogOperacji.id_delegacji == DEL, LogOperacji.operacja == "Zmiana przejazdu po odwołaniu"
            )
        ).all()
    assert wyniki == ["Ostrzeżenie"]


# ---------------------------------------------------------------- podzadanie 136: ekran


def test_ekran_pokazuje_odwolanie_i_propozycje_z_rekomendacja(klient, broker):
    ids = utworz()
    odwolaj(broker, ids["tam"])
    zaloguj_z_csrf(klient, DELEGOWANY)

    strona = klient.get(f"/app/wnioski/{DEL}/przejazd-alternatywny").text
    assert "Przewoźnik odwołał przejazd tam" in strona and "Awaria taboru" in strona
    assert strona.count('name="id_przejazdu"') == 4 and "rekomendowany" in strona
    assert "Żaden przejazd mi nie odpowiada" in strona

    wniosek = klient.get(f"/app/wnioski/{DEL}").text
    assert f"/app/wnioski/{DEL}/przejazd-alternatywny" in wniosek
    assert "Oczekiwanie na wybór przejazdu alternatywnego" in wniosek


def test_ekran_bez_odwolania_informuje_ze_nic_nie_odwolano(klient):
    utworz()
    zaloguj_z_csrf(klient, DELEGOWANY)

    strona = klient.get(f"/app/wnioski/{DEL}/przejazd-alternatywny").text
    assert "Żaden przejazd tej delegacji nie został odwołany" in strona
