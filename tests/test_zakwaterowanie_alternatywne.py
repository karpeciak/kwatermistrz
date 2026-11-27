"""S22IT1S0P2-15: zapewnienie zakwaterowania alternatywnego w granicach limitu (podzadania 130–133).

Scenariusz: brak wolnych miejsc w obiektach wojskowych w miejscu delegacji → system informuje o tym i przedstawia
obiekty cywilne w limicie → delegowany wybiera obiekt → system potwierdza zgodność z limitem i budżetem,
rezerwuje i uzupełnia dokumentację delegacji o dane noclegu i uzasadnienie wyboru.
Alternatywnie (3a–4a): delegowany odrzuca wszystkie obiekty – sprawa u osoby odpowiedzialnej.

Toruń (PRZ-01, zajęcia od 8:00 → dojazd ≤ 45 min): OBJ-01 wojskowy bez miejsc, OBJ-02 wojskowy 60 zł (w teście
bez miejsc), OBJ-03 cywilny 260 zł, OBJ-04 cywilny 340 zł (ponad limit), OBJ-05 cywilny za daleko.
"""

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from conftest import AtrapaBrokera
from fastapi.testclient import TestClient
from pomoc_sesji import zaloguj_z_csrf
from sqlalchemy import select

from kwatermistrz.agents import AGENCI
from kwatermistrz.api.main import app, get_broker
from kwatermistrz.db import sesja
from kwatermistrz.messaging import Komunikat, Typ
from kwatermistrz.models import (
    Delegacja,
    LogDecyzji,
    ObiektZakwaterowania,
    PolecenieWyjazdu,
    Status,
    StatusRezerwacji,
    Zakwaterowanie,
)
from kwatermistrz.stawki import szacuj_koszt

DEL, DELEGOWANY, ODPOWIEDZIALNY = "DEL-ALT-15", "ZOL-003", "ZOL-001"
WYJAZD, POWROT = date(2026, 12, 7), date(2026, 12, 11)


class BrokerZHistoria(AtrapaBrokera):
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
def zatwierdzona_delegacja():
    with sesja() as s:
        s.add(
            Delegacja(
                id_delegacji=DEL,
                id_zolnierza=DELEGOWANY,
                id_przedsiewziecia="PRZ-01",
                dane_wniosku={},
                miejsce_docelowe="Toruń",
                data_wyjazdu=WYJAZD,
                data_powrotu=POWROT,
                status=Status.ZATWIERDZONY,
                id_zatwierdzajacego=ODPOWIEDZIALNY,
                potrzeba_zakwaterowania=True,
                szacowany_koszt=szacuj_koszt(WYJAZD, POWROT, True),
            )
        )
        s.add(
            PolecenieWyjazdu(
                numer="PW/2099/0015",
                id_delegacji=DEL,
                wersja_wzoru="test",
                tresc={"termin": {"od": WYJAZD.isoformat(), "do": POWROT.isoformat()}},
            )
        )


def bez_miejsc_wojskowych() -> None:
    with sesja() as s:
        s.get(ObiektZakwaterowania, "OBJ-02").liczba_wolnych_miejsc = 0  # OBJ-01 nie ma miejsc w danych startowych


def dostarcz(broker, typ: str | None = None, **dane) -> None:
    if typ is not None:
        asyncio.run(broker.publikuj(Komunikat(typ=typ, id_delegacji=DEL, dane=dane)))
    asyncio.run(broker.dostarczaj([klasa(broker) for klasa in AGENCI.values()]))


def noclegi(status: str | None = None) -> list[Zakwaterowanie]:
    with sesja() as s:
        lista = s.scalars(select(Zakwaterowanie).where(Zakwaterowanie.id_delegacji == DEL)).all()
        return [z for z in lista if status is None or z.status == status]


def tresc_polecenia() -> dict:
    with sesja() as s:
        return s.scalar(select(PolecenieWyjazdu).where(PolecenieWyjazdu.id_delegacji == DEL)).tresc


# ---------------------------------------------------------------- podzadanie 130, 132: informacja i obiekty cywilne


def test_brak_miejsc_wojskowych_daje_obiekty_cywilne_w_limicie_z_informacja(broker):
    bez_miejsc_wojskowych()

    dostarcz(broker, Typ.ZADANIE_ZAKWATEROWANIA)

    propozycje = noclegi(StatusRezerwacji.PROPOZYCJA)
    assert [z.id_obiektu for z in propozycje] == ["OBJ-03"]  # cywilny w limicie z dojazdem na czas
    assert all(z.cena_za_dobe <= Decimal("300") and z.czas_dojazdu_min is not None for z in propozycje)
    assert any("Brak wolnych miejsc w obiektach wojskowych" in t for t in broker.powiadomienia(DELEGOWANY))


def test_przy_wolnych_miejscach_wojskowych_brak_informacji(broker):
    dostarcz(broker, Typ.ZADANIE_ZAKWATEROWANIA)

    assert noclegi(StatusRezerwacji.PROPOZYCJA)[0].id_obiektu == "OBJ-02"
    assert not any("Brak wolnych miejsc" in t for t in broker.powiadomienia(DELEGOWANY))


# ---------------------------------------------------------------- podzadanie 131: rezerwacja i dokumentacja


def test_rezerwacja_obiektu_cywilnego_ma_uzasadnienie_i_trafia_do_polecenia(broker):
    bez_miejsc_wojskowych()
    dostarcz(broker, Typ.ZADANIE_ZAKWATEROWANIA)
    (kopernik,) = noclegi(StatusRezerwacji.PROPOZYCJA)

    dostarcz(broker, Typ.WYBOR_ZAKWATEROWANIA, id_zakwaterowania=kopernik.id_zakwaterowania)

    (wybrany,) = noclegi(StatusRezerwacji.WYBRANA)
    assert wybrany.id_obiektu == "OBJ-03"
    assert "Obiekt cywilny – brak wolnych miejsc w obiektach wojskowych w Toruń" in wybrany.uzasadnienie
    dane = tresc_polecenia()["zakwaterowanie"]
    assert dane["obiekt"] == "Hotel Kopernik" and dane["rodzaj"] == "Cywilny"
    assert (dane["od"], dane["do"], dane["koszt"]) == (WYJAZD.isoformat(), POWROT.isoformat(), "1040.00")
    assert dane["uzasadnienie"] == wybrany.uzasadnienie
    assert tresc_polecenia()["termin"]["od"] == WYJAZD.isoformat()  # reszta polecenia bez zmian


def test_rezerwacja_obiektu_wojskowego_bez_uzasadnienia_cywilnego(broker):
    dostarcz(broker, Typ.ZADANIE_ZAKWATEROWANIA)
    garnizonowy = next(z for z in noclegi(StatusRezerwacji.PROPOZYCJA) if z.id_obiektu == "OBJ-02")

    dostarcz(broker, Typ.WYBOR_ZAKWATEROWANIA, id_zakwaterowania=garnizonowy.id_zakwaterowania)

    (wybrany,) = noclegi(StatusRezerwacji.WYBRANA)
    assert "Obiekt cywilny" not in wybrany.uzasadnienie
    assert tresc_polecenia()["zakwaterowanie"]["rodzaj"] == "Wojskowy"


# ---------------------------------------------------------------- 3a–4a: odrzucenie wszystkich obiektów


def test_odrzucenie_wszystkich_obiektow_przekazuje_sprawe(broker):
    bez_miejsc_wojskowych()
    dostarcz(broker, Typ.ZADANIE_ZAKWATEROWANIA)

    dostarcz(broker, Typ.ODRZUCENIE_ZAKWATEROWANIA, przyczyna="Za daleko od jednostki", id_osoby=DELEGOWANY)

    (odrzucony,) = noclegi(StatusRezerwacji.DO_UZGODNIENIA)
    assert "Za daleko od jednostki" in odrzucony.uzasadnienie
    assert noclegi(StatusRezerwacji.PROPOZYCJA) == [] and noclegi(StatusRezerwacji.WYBRANA) == []
    assert any("Zorganizuj nocleg w granicach limitu" in t for t in broker.powiadomienia(ODPOWIEDZIALNY))
    assert any("przekazano osobie odpowiedzialnej" in t for t in broker.powiadomienia(DELEGOWANY))
    with sesja() as s:
        decyzja = s.scalar(select(LogDecyzji).where(LogDecyzji.typ_decyzji == "Odrzucenie propozycji zakwaterowania"))
        assert decyzja.wybrany_wariant == "Do uzgodnienia"


def test_odrzuca_tylko_delegowany_z_przyczyna(broker):
    dostarcz(broker, Typ.ZADANIE_ZAKWATEROWANIA)

    dostarcz(broker, Typ.ODRZUCENIE_ZAKWATEROWANIA, przyczyna="Nie", id_osoby=ODPOWIEDZIALNY)
    dostarcz(broker, Typ.ODRZUCENIE_ZAKWATEROWANIA, przyczyna="  ", id_osoby=DELEGOWANY)

    assert noclegi(StatusRezerwacji.DO_UZGODNIENIA) == []


# ---------------------------------------------------------------- ekran i API


def test_ekran_pokazuje_informacje_i_przyjmuje_odrzucenie(klient, broker):
    bez_miejsc_wojskowych()
    dostarcz(broker, Typ.ZADANIE_ZAKWATEROWANIA)
    zaloguj_z_csrf(klient, DELEGOWANY)

    strona = klient.get(f"/app/wnioski/{DEL}/zakwaterowanie").text
    assert "Brak wolnych miejsc w obiektach wojskowych" in strona and "Hotel Kopernik" in strona
    assert "Żaden obiekt mi nie odpowiada" in strona
    assert "Podaj przyczynę" in klient.post(f"/app/wnioski/{DEL}/zakwaterowanie/odrzucenie", data={}).text

    odp = klient.post(f"/app/wnioski/{DEL}/zakwaterowanie/odrzucenie", data={"przyczyna": "Za drogo dla mnie"})
    assert odp.status_code == 303
    dostarcz(broker)
    assert "zorganizuje" in klient.get(f"/app/wnioski/{DEL}/zakwaterowanie").text


def test_api_odrzucenia(klient, broker):
    zaloguj_z_csrf(klient, DELEGOWANY)
    assert klient.post(f"/delegacje/{DEL}/odrzucenie-zakwaterowania", json={"przyczyna": "Za drogo"}).status_code == 409

    dostarcz(broker, Typ.ZADANIE_ZAKWATEROWANIA)

    assert klient.post(f"/delegacje/{DEL}/odrzucenie-zakwaterowania", json={"przyczyna": "Za drogo"}).status_code == 202
    zaloguj_z_csrf(klient, ODPOWIEDZIALNY)
    assert klient.post(f"/delegacje/{DEL}/odrzucenie-zakwaterowania", json={"przyczyna": "Za drogo"}).status_code == 403
