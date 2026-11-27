"""S22IT1S0P2-40: zastąpienie zakwaterowania przekraczającego limit wariantem zgodnym z limitem (podzadania 153–156).

Scenariusz: delegowany wybiera obiekt → system informuje o przekroczeniu limitu, wstrzymuje rezerwację i przedstawia
obiekty w limicie (najpierw wojskowe) → delegowany wybiera jeden z nich → system potwierdza zgodność z limitem
i budżetem, rezerwuje i aktualizuje zobowiązania budżetu jednostki.
Alternatywnie (2a–4a): brak obiektów w limicie → wniosek o odstępstwo z uzasadnieniem → zgoda przełożonego →
rezerwacja pierwotnie wybranego obiektu i informacja dla delegowanego.

Toruń (PRZ-01, zajęcia od 8:00 → dojazd ≤ 45 min): OBJ-01 wojskowy bez miejsc, OBJ-02 wojskowy 60 zł,
OBJ-03 cywilny 260 zł, OBJ-04 cywilny 340 zł (ponad limit 300 zł), OBJ-05 cywilny 70 min dojazdu (za daleko).
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
    Jednostka,
    LogDecyzji,
    ObiektZakwaterowania,
    Status,
    StatusRezerwacji,
    Zakwaterowanie,
)
from kwatermistrz.stawki import szacuj_koszt

DEL, DELEGOWANY, PRZELOZONY = "DEL-LIMIT-40", "ZOL-003", "ZOL-001"
WYJAZD, POWROT = date(2026, 12, 7), date(2026, 12, 11)  # 4 noce
SZACUNEK = szacuj_koszt(WYJAZD, POWROT, True)  # zawiera 4 noclegi po 300 zł
ZAANGAZOWANE = Decimal("5000.00")


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
                id_zatwierdzajacego=PRZELOZONY,
                potrzeba_zakwaterowania=True,
                szacowany_koszt=SZACUNEK,
            )
        )
        s.get(Jednostka, "JW-1001").budzet_wykorzystany = ZAANGAZOWANE


def dostarcz(broker, typ: str | None = None, **dane) -> None:
    if typ is not None:
        asyncio.run(broker.publikuj(Komunikat(typ=typ, id_delegacji=DEL, dane=dane)))
    asyncio.run(broker.dostarczaj([klasa(broker) for klasa in AGENCI.values()]))


def przygotuj_propozycje(broker) -> None:
    dostarcz(broker, Typ.ZADANIE_ZAKWATEROWANIA)


def noclegi(status: str | None = None) -> list[Zakwaterowanie]:
    with sesja() as s:
        lista = s.scalars(select(Zakwaterowanie).where(Zakwaterowanie.id_delegacji == DEL)).all()
        return [z for z in lista if status is None or z.status == status]


def miejsca(id_obiektu: str) -> int:
    with sesja() as s:
        return s.get(ObiektZakwaterowania, id_obiektu).liczba_wolnych_miejsc


def zobowiazania() -> tuple[Decimal, Decimal]:
    with sesja() as s:
        return s.get(Jednostka, "JW-1001").budzet_wykorzystany, s.get(Delegacja, DEL).szacowany_koszt


def bez_miejsc_w_limicie() -> None:
    with sesja() as s:
        for id_obiektu in ("OBJ-02", "OBJ-03"):
            s.get(ObiektZakwaterowania, id_obiektu).liczba_wolnych_miejsc = 0


# ---------------------------------------------------------------- podzadanie 153: wstrzymanie i propozycje w limicie


def test_obiekt_ponad_limit_wstrzymuje_rezerwacje(broker):
    przygotuj_propozycje(broker)
    miejsca_przed = miejsca("OBJ-04")

    dostarcz(broker, Typ.WYBOR_OBIEKTU, id_obiektu="OBJ-04", id_osoby=DELEGOWANY)

    (wstrzymana,) = noclegi(StatusRezerwacji.WSTRZYMANA)
    assert wstrzymana.id_obiektu == "OBJ-04" and wstrzymana.wymaga_odstepstwa
    assert "przekracza limit" in wstrzymana.uzasadnienie
    assert noclegi(StatusRezerwacji.WYBRANA) == []
    assert miejsca("OBJ-04") == miejsca_przed  # nic nie zarezerwowano
    assert any("przekracza limit noclegowy" in t and "najpierw wojskowe" in t for t in broker.powiadomienia(DELEGOWANY))
    with sesja() as s:
        decyzja = s.scalar(select(LogDecyzji).where(LogDecyzji.typ_decyzji == "Kontrola limitu noclegowego"))
        assert decyzja.wybrany_wariant == "Wstrzymano rezerwację" and decyzja.wymaga_zatwierdzenia


def test_wybor_obiektu_w_limicie_po_wstrzymaniu_rezerwuje_i_aktualizuje_budzet(broker):
    przygotuj_propozycje(broker)
    dostarcz(broker, Typ.WYBOR_OBIEKTU, id_obiektu="OBJ-04", id_osoby=DELEGOWANY)
    garnizonowy = next(z for z in noclegi(StatusRezerwacji.PROPOZYCJA) if z.id_obiektu == "OBJ-02")
    miejsca_przed = miejsca("OBJ-02")

    dostarcz(broker, Typ.WYBOR_ZAKWATEROWANIA, id_zakwaterowania=garnizonowy.id_zakwaterowania)

    (wybrany,) = noclegi(StatusRezerwacji.WYBRANA)
    assert wybrany.id_obiektu == "OBJ-02"
    assert noclegi(StatusRezerwacji.WSTRZYMANA) == []  # wstrzymany wybór anulowany
    assert miejsca("OBJ-02") == miejsca_przed - 1
    oszczednosc = (Decimal("300.00") - Decimal("60.00")) * 4  # szacunek w limicie − faktyczny koszt noclegów
    assert zobowiazania() == (ZAANGAZOWANE - oszczednosc, SZACUNEK - oszczednosc)


def test_obiekt_z_katalogu_w_limicie_rezerwuje_od_razu(broker):
    dostarcz(broker, Typ.WYBOR_OBIEKTU, id_obiektu="OBJ-03", id_osoby=DELEGOWANY)

    (wybrany,) = noclegi(StatusRezerwacji.WYBRANA)
    assert wybrany.id_obiektu == "OBJ-03" and not wybrany.wymaga_odstepstwa
    assert zobowiazania()[0] == ZAANGAZOWANE - (Decimal("300.00") - Decimal("260.00")) * 4


@pytest.mark.parametrize(("id_obiektu", "przyczyna"), [("OBJ-01", "Brak wolnych miejsc"), ("OBJ-05", "dojechać")])
def test_obiekt_bez_miejsc_albo_za_daleko_nie_jest_wybierany(broker, id_obiektu, przyczyna):
    agent = AGENCI["zakwaterowanie"](broker)
    funkcja, operacja = agent.obslugi()[Typ.WYBOR_OBIEKTU]
    k = Komunikat(typ=Typ.WYBOR_OBIEKTU, id_delegacji=DEL, dane={"id_obiektu": id_obiektu, "id_osoby": DELEGOWANY})

    ktx = asyncio.run(agent.wykonaj(k, funkcja, operacja))

    assert ktx.wynik == "Ostrzeżenie" and przyczyna in ktx.opis
    assert noclegi() == []


# ---------------------------------------------------------------- 2a–4a, podzadanie 155: odstępstwo i decyzja


def test_brak_obiektow_w_limicie_pozwala_zlozyc_wniosek_o_odstepstwo(broker):
    dostarcz(broker, Typ.WYBOR_OBIEKTU, id_obiektu="OBJ-04", id_osoby=DELEGOWANY)  # brak propozycji w limicie
    (wstrzymana,) = noclegi(StatusRezerwacji.WSTRZYMANA)
    assert any("wniosek o odstępstwo" in t for t in broker.powiadomienia(DELEGOWANY))

    dostarcz(
        broker,
        Typ.WYBOR_ZAKWATEROWANIA,
        id_zakwaterowania=wstrzymana.id_zakwaterowania,
        uzasadnienie="Jedyny obiekt z miejscami przy jednostce szkolącej",
    )

    (wniosek,) = noclegi(StatusRezerwacji.DO_DECYZJI_PRZELOZONEGO)
    assert wniosek.id_obiektu == "OBJ-04"
    assert any("wymaga odstępstwa" in t for t in broker.powiadomienia(PRZELOZONY))


def test_zgoda_przelozonego_rezerwuje_pierwotnie_wybrany_obiekt(broker):
    bez_miejsc_w_limicie()
    przygotuj_propozycje(broker)  # 2a: same warianty z odstępstwem
    (bulwar,) = [z for z in noclegi(StatusRezerwacji.PROPOZYCJA) if z.id_obiektu == "OBJ-04"]
    dostarcz(broker, Typ.WYBOR_ZAKWATEROWANIA, id_zakwaterowania=bulwar.id_zakwaterowania, uzasadnienie="Brak miejsc")

    dostarcz(broker, Typ.DECYZJA_ODSTEPSTWA, zgoda=True, uzasadnienie="", id_osoby=PRZELOZONY)

    (wybrany,) = noclegi(StatusRezerwacji.WYBRANA)
    assert wybrany.id_obiektu == "OBJ-04" and "zatwierdzone przez ZOL-001" in wybrany.uzasadnienie
    nadwyzka = (Decimal("340.00") - Decimal("300.00")) * 4
    assert zobowiazania() == (ZAANGAZOWANE + nadwyzka, SZACUNEK + nadwyzka)
    assert any("zarezerwowano pierwotnie wybrany obiekt" in t for t in broker.powiadomienia(DELEGOWANY))


def test_odmowa_przelozonego_odrzuca_wniosek(broker):
    bez_miejsc_w_limicie()
    przygotuj_propozycje(broker)
    (bulwar,) = [z for z in noclegi(StatusRezerwacji.PROPOZYCJA) if z.id_obiektu == "OBJ-04"]
    dostarcz(broker, Typ.WYBOR_ZAKWATEROWANIA, id_zakwaterowania=bulwar.id_zakwaterowania, uzasadnienie="Brak miejsc")

    dostarcz(broker, Typ.DECYZJA_ODSTEPSTWA, zgoda=False, uzasadnienie="Za drogo", id_osoby=PRZELOZONY)

    assert noclegi(StatusRezerwacji.WYBRANA) == [] and noclegi(StatusRezerwacji.DO_DECYZJI_PRZELOZONEGO) == []
    assert "Odmowa przełożonego: Za drogo" in [z for z in noclegi() if z.id_obiektu == "OBJ-04"][0].uzasadnienie
    assert zobowiazania() == (ZAANGAZOWANE, SZACUNEK)
    assert any("nie zgodził się na odstępstwo" in t for t in broker.powiadomienia(DELEGOWANY))


def test_o_odstepstwie_decyduje_tylko_przelozony(broker):
    bez_miejsc_w_limicie()
    przygotuj_propozycje(broker)
    (bulwar,) = [z for z in noclegi(StatusRezerwacji.PROPOZYCJA) if z.id_obiektu == "OBJ-04"]
    dostarcz(broker, Typ.WYBOR_ZAKWATEROWANIA, id_zakwaterowania=bulwar.id_zakwaterowania, uzasadnienie="Brak miejsc")

    dostarcz(broker, Typ.DECYZJA_ODSTEPSTWA, zgoda=True, uzasadnienie="", id_osoby=DELEGOWANY)

    assert len(noclegi(StatusRezerwacji.DO_DECYZJI_PRZELOZONEGO)) == 1


def test_zgoda_bez_mozliwosci_rezerwacji_zamyka_wniosek_i_powiadamia(broker):
    """Z przeglądu PR #34: zgoda przy braku budżetu nie może zostawić wniosku „do decyzji” bez informacji."""
    bez_miejsc_w_limicie()
    przygotuj_propozycje(broker)
    (bulwar,) = [z for z in noclegi(StatusRezerwacji.PROPOZYCJA) if z.id_obiektu == "OBJ-04"]
    dostarcz(broker, Typ.WYBOR_ZAKWATEROWANIA, id_zakwaterowania=bulwar.id_zakwaterowania, uzasadnienie="Brak miejsc")
    with sesja() as s:
        j = s.get(Jednostka, "JW-1001")
        j.budzet_wykorzystany = j.budzet_delegacji  # brak wolnych środków na nadwyżkę ponad limit

    dostarcz(broker, Typ.DECYZJA_ODSTEPSTWA, zgoda=True, uzasadnienie="", id_osoby=PRZELOZONY)

    assert noclegi(StatusRezerwacji.WYBRANA) == [] and noclegi(StatusRezerwacji.DO_DECYZJI_PRZELOZONEGO) == []
    (zamkniety,) = [z for z in noclegi(StatusRezerwacji.ANULOWANA) if z.id_obiektu == "OBJ-04"]
    assert "rezerwacja niemożliwa" in zamkniety.uzasadnienie and "budżet" in zamkniety.uzasadnienie
    assert any("Wybierz inny obiekt" in t for t in broker.powiadomienia(DELEGOWANY))
    assert any("Zdecyduj, jak zorganizować nocleg" in t for t in broker.powiadomienia(PRZELOZONY))
    assert zobowiazania()[1] == SZACUNEK  # szacowany koszt bez zmian

    with sesja() as s:  # delegowany nie jest zablokowany – może wybrać inny obiekt
        s.get(ObiektZakwaterowania, "OBJ-02").liczba_wolnych_miejsc = 5
    dostarcz(broker, Typ.WYBOR_OBIEKTU, id_obiektu="OBJ-02", id_osoby=DELEGOWANY)
    (wybrany,) = noclegi(StatusRezerwacji.WYBRANA)
    assert wybrany.id_obiektu == "OBJ-02"


# ---------------------------------------------------------------- podzadanie 154: ekrany i API


def test_ekran_pokazuje_katalog_i_wstrzymanie(klient, broker):
    przygotuj_propozycje(broker)
    zaloguj_z_csrf(klient, DELEGOWANY)
    strona = klient.get(f"/app/wnioski/{DEL}/zakwaterowanie").text
    assert "Inny obiekt z katalogu" in strona and "Hotel Bulwar" in strona and "ponad limit" in strona

    odp = klient.post(f"/app/wnioski/{DEL}/zakwaterowanie/katalog", data={"id_obiektu": "OBJ-04"})
    assert odp.status_code == 303
    dostarcz(broker)

    strona = klient.get(f"/app/wnioski/{DEL}/zakwaterowanie").text
    assert "Przekroczony limit noclegowy" in strona and "w pierwszej kolejności obiekty wojskowe" in strona
    assert strona.index("Hotel garnizonowy") < strona.index("Hotel Kopernik")  # wojskowe pierwsze


def test_ekran_przelozonego_i_zgoda(klient, broker):
    bez_miejsc_w_limicie()
    przygotuj_propozycje(broker)
    (bulwar,) = [z for z in noclegi(StatusRezerwacji.PROPOZYCJA) if z.id_obiektu == "OBJ-04"]
    dostarcz(broker, Typ.WYBOR_ZAKWATEROWANIA, id_zakwaterowania=bulwar.id_zakwaterowania, uzasadnienie="Brak miejsc")

    zaloguj_z_csrf(klient, DELEGOWANY)
    assert klient.get(f"/app/wnioski/{DEL}/odstepstwo").status_code == 404  # tylko przełożony
    zaloguj_z_csrf(klient, PRZELOZONY)
    assert "Rozpatrz wniosek" in klient.get(f"/app/wnioski/{DEL}").text
    strona = klient.get(f"/app/wnioski/{DEL}/odstepstwo").text
    assert "Hotel Bulwar" in strona and "Brak miejsc" in strona and "Zgadzam się na odstępstwo" in strona

    assert klient.post(f"/app/wnioski/{DEL}/odstepstwo", data={"zgoda": "tak"}).status_code == 303
    assert "Podaj przyczynę odmowy" in klient.post(f"/app/wnioski/{DEL}/odstepstwo", data={"zgoda": "nie"}).text
    assert broker.historia[-1].typ == Typ.DECYZJA_ODSTEPSTWA and broker.historia[-1].dane["zgoda"]


def test_api_wyboru_obiektu_i_decyzji(klient, broker):
    zaloguj_z_csrf(klient, DELEGOWANY)
    assert klient.post(f"/delegacje/{DEL}/wybor-obiektu", json={"id_obiektu": "OBJ-06"}).status_code == 409  # Zegrze
    assert klient.post(f"/delegacje/{DEL}/wybor-obiektu", json={"id_obiektu": "OBJ-04"}).status_code == 202
    assert klient.post(f"/delegacje/{DEL}/decyzja-odstepstwa", json={"zgoda": True}).status_code == 403
    zaloguj_z_csrf(klient, PRZELOZONY)
    assert klient.post(f"/delegacje/{DEL}/decyzja-odstepstwa", json={"zgoda": True}).status_code == 409
    assert klient.post(f"/delegacje/{DEL}/decyzja-odstepstwa", json={"zgoda": False}).status_code == 422
