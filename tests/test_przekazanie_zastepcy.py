"""S22IT1S0P2-13: przekazanie wniosku osobie zastępującej po upływie terminu na decyzję (podzadania 126–129).

Scenariusz: termin mija → przypomnienie → termin mija ponownie → przekazanie osobie zastępującej (z informacją
dla wnioskodawcy) → decyzja zastępcy → organizacja delegacji i informacja dla wnioskodawcy oraz przełożonego.
Alternatywnie (7a–8a): zastępca odrzuca wniosek, a wnioskodawca dostaje uzasadnienie.
"""

import asyncio
from datetime import timedelta

import pytest
from conftest import AtrapaBrokera
from fastapi.testclient import TestClient
from pomoc_sesji import zaloguj_z_csrf
from sqlalchemy import select

from kwatermistrz.agents import AGENCI
from kwatermistrz.api.main import app, get_broker
from kwatermistrz.db import sesja, teraz
from kwatermistrz.messaging import Komunikat, Typ
from kwatermistrz.models import Delegacja, LogOperacji, Status

PRZELOZONY, ZASTEPCA, WNIOSKODAWCA = "ZOL-001", "ZOL-002", "ZOL-003"
DEL = "DEL-ZAST-1"
WNIOSEK = {
    "cel_wyjazdu": "Szkolenie z logistyki wojskowej",
    "miejsce_docelowe": "Toruń",
    "data_wyjazdu": "2026-12-07",
    "data_powrotu": "2026-12-11",
    "srodek_transportu": "kolej",
}


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


def utworz(**pola) -> str:
    dane = dict(
        id_delegacji=DEL,
        id_zolnierza=WNIOSKODAWCA,
        dane_wniosku=WNIOSEK,
        status=Status.W_AKCEPTACJI,
        id_zatwierdzajacego=PRZELOZONY,
        termin_decyzji=teraz() + timedelta(hours=48),
    )
    with sesja() as s:
        s.add(Delegacja(**(dane | pola)))
    return DEL


def delegacja() -> Delegacja:
    with sesja() as s:
        return s.get(Delegacja, DEL)


def uplyw_terminu(broker) -> None:
    """Termin decyzji mija i Agent Zatwierdzeń wykonuje kontrolę terminów (zadanie cykliczne)."""
    with sesja() as s:
        s.get(Delegacja, DEL).termin_decyzji = teraz() - timedelta(minutes=1)
    agent = AGENCI["zatwierdzenia"](broker)
    for k, funkcja, operacja in agent.zadania_cykliczne():
        asyncio.run(agent.wykonaj(k, funkcja, operacja))


def decyzja(broker, id_osoby: str, decyzja: str, uzasadnienie: str | None = None) -> None:
    dane = {"id_osoby": id_osoby, "decyzja": decyzja, "uzasadnienie": uzasadnienie}
    asyncio.run(broker.publikuj(Komunikat(typ=Typ.DECYZJA_PRZELOZONEGO, id_delegacji=DEL, dane=dane)))
    asyncio.run(broker.dostarczaj([klasa(broker) for klasa in AGENCI.values()]))


# ---------------------------------------------------------------- scenariusz główny


def test_przypomnienie_przekazanie_i_zgoda_zastepcy(broker):
    utworz()

    uplyw_terminu(broker)  # kroki 3–4
    d = delegacja()
    assert d.liczba_przypomnien == 1 and d.id_zatwierdzajacego == PRZELOZONY and d.termin_decyzji > teraz()
    assert any("Przypomnienie" in t for t in broker.powiadomienia(PRZELOZONY))

    uplyw_terminu(broker)  # kroki 5–6
    d = delegacja()
    assert d.przekazano_zastepcy and d.id_zatwierdzajacego == ZASTEPCA
    assert any("przekazano zastępcy" in t for t in broker.powiadomienia(ZASTEPCA))
    assert any("przekazano zastępcy" in t for t in broker.powiadomienia(WNIOSKODAWCA))

    decyzja(broker, ZASTEPCA, "Zgoda")  # kroki 7–8
    d = delegacja()
    assert d.status == Status.ZATWIERDZONY and d.decyzja == "Zgoda" and d.id_zatwierdzajacego == ZASTEPCA
    assert Typ.ZADANIE_POLECENIA in [k.typ for k in broker.historia]  # rozpoczęto organizację delegacji
    assert any(f"{DEL}: zatwierdzony" in t for t in broker.powiadomienia(WNIOSKODAWCA))
    assert any("osobę zastępującą ZOL-002" in t and "„Zgoda”" in t for t in broker.powiadomienia(PRZELOZONY))

    # warunek końcowy: przebieg przypomnień i przekazania jest udokumentowany w logu operacji
    with sesja() as s:
        operacje = s.scalars(select(LogOperacji.operacja).where(LogOperacji.id_delegacji == DEL)).all()
    assert "Przypomnienie o oczekującym wniosku" in operacje and "Przekazanie wniosku zastępcy" in operacje


def test_zastepca_odrzuca_wniosek_z_uzasadnieniem(broker):
    utworz(id_zatwierdzajacego=ZASTEPCA, przekazano_zastepcy=True, liczba_przypomnien=1)

    decyzja(broker, ZASTEPCA, "Odmowa", "Brak środków w budżecie jednostki")  # 7a–8a
    d = delegacja()
    assert d.status == Status.ODRZUCONY and d.uzasadnienie_decyzji == "Brak środków w budżecie jednostki"
    assert any("odrzucony – uzasadnienie: Brak środków" in t for t in broker.powiadomienia(WNIOSKODAWCA))
    assert any("„Odmowa”" in t for t in broker.powiadomienia(PRZELOZONY))


def test_przelozony_po_przekazaniu_nie_podejmuje_juz_decyzji(broker):
    utworz(id_zatwierdzajacego=ZASTEPCA, przekazano_zastepcy=True, liczba_przypomnien=1)

    decyzja(broker, PRZELOZONY, "Zgoda")
    d = delegacja()
    assert d.status == Status.W_AKCEPTACJI and d.decyzja is None


def test_decyzja_przelozonego_w_terminie_nie_wysyla_informacji_o_zastepstwie(broker):
    utworz()

    decyzja(broker, PRZELOZONY, "Zgoda")
    assert delegacja().status == Status.ZATWIERDZONY
    assert not any("zastępującą" in t for t in broker.powiadomienia(PRZELOZONY))


def test_zwrot_do_uzupelnienia_przekazuje_uzasadnienie_wnioskodawcy(broker):
    utworz()

    decyzja(broker, PRZELOZONY, "Do uzupełnienia", "Dołącz rozkaz")
    assert any("do uzupełnienia – uzasadnienie: Dołącz rozkaz" in t for t in broker.powiadomienia(WNIOSKODAWCA))


# ---------------------------------------------------------------- interfejs


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()


def test_zastepca_widzi_oznaczenie_a_przelozony_liste_przekazanych(klient):
    utworz(id_zatwierdzajacego=ZASTEPCA, przekazano_zastepcy=True, liczba_przypomnien=1)

    zaloguj_z_csrf(klient, ZASTEPCA)
    strona = klient.get("/app/do-zatwierdzenia").text
    assert DEL in strona and "przekazany z zastępstwa" in strona and "termin:" in strona

    zaloguj_z_csrf(klient, PRZELOZONY)
    strona = klient.get("/app/do-zatwierdzenia").text
    assert "Przekazane osobie zastępującej" in strona and DEL in strona
    assert 'name="wnioski" value="DEL-ZAST-1"' not in strona  # przełożony nie może już zdecydować


def test_wniosek_po_przypomnieniu_jest_oznaczony_u_przelozonego(klient):
    utworz(liczba_przypomnien=1)

    zaloguj_z_csrf(klient, PRZELOZONY)
    strona = klient.get("/app/do-zatwierdzenia").text
    assert "po przypomnieniu" in strona and "Przekazane osobie zastępującej" not in strona


def test_strona_wniosku_wskazuje_osobe_ktora_podjela_decyzje(klient, broker):
    utworz(id_zatwierdzajacego=ZASTEPCA, przekazano_zastepcy=True, liczba_przypomnien=1)
    decyzja(broker, ZASTEPCA, "Odmowa", "Termin koliduje z ćwiczeniami")

    zaloguj_z_csrf(klient, WNIOSKODAWCA)
    strona = klient.get(f"/app/wnioski/{DEL}").text
    assert "Decyzja osoby zastępującej" in strona and "Odmowa" in strona
    assert "Uzasadnienie: Termin koliduje z ćwiczeniami" in strona
