"""S22IT1S0P2-43 – weryfikacja wyliczenia należności i przekazanie rozliczenia do komórki finansowej."""

import asyncio
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pomoc_sesji import zaloguj_z_csrf
from sqlalchemy import select

from kwatermistrz.agents import AGENCI
from kwatermistrz.agents.rozliczenia import wylicz_naleznosci
from kwatermistrz.agents.zgodnosc import TYP_DECYZJI
from kwatermistrz.api.main import app, get_broker
from kwatermistrz.db import sesja
from kwatermistrz.messaging import Komunikat, Typ
from kwatermistrz.models import (
    Delegacja,
    DokumentKosztowy,
    Jednostka,
    KorektaRozliczenia,
    LogDecyzji,
    LogOperacji,
    Rozliczenie,
    Status,
    StatusRozliczenia,
)

ID = "DEL-WER-43"
WYJAZD = datetime(2026, 12, 7, 6, 10)
POWROT = datetime(2026, 12, 11, 19, 5)
DOKUMENTY = [("bilet", "IC 1001", "89.00"), ("rachunek za nocleg", "H/77", "1360.00")]
POPRAWNE = wylicz_naleznosci(WYJAZD, POWROT, {}, False, [(r, Decimal(k)) for r, _, k in DOKUMENTY])
WYDATEK = Decimal("1000.00")  # wydatek jednostki po zatwierdzeniu wyliczenia (S22IT1S0P2-66)


@pytest.fixture(autouse=True)
def zatwierdzone_wyliczenie():
    """Delegacja do rozliczenia z wyliczeniem zatwierdzonym przez delegowanego (gotowe do weryfikacji)."""
    with sesja() as s:
        s.add(Delegacja(id_delegacji=ID, id_zolnierza="ZOL-003", dane_wniosku={}, status=Status.DO_ROZLICZENIA))
        r = Rozliczenie(
            id_delegacji=ID,
            status=StatusRozliczenia.GOTOWE_DO_WERYFIKACJI,
            faktyczny_wyjazd=WYJAZD,
            faktyczny_powrot=POWROT,
            zapewnione_posilki={},
            bezplatne_zakwaterowanie=False,
            dieta=POPRAWNE.dieta,
            zwrot_przejazdu=POPRAWNE.zwrot_przejazdu,
            zwrot_noclegu=POPRAWNE.zwrot_noclegu,
            ryczalty=POPRAWNE.ryczalty,
            kwota_naleznosci=POPRAWNE.kwota_naleznosci,
        )
        s.add(r)
        s.flush()
        for rodzaj, numer, kwota in DOKUMENTY:
            s.add(
                DokumentKosztowy(
                    id_rozliczenia=r.id_rozliczenia,
                    rodzaj=rodzaj,
                    numer=numer,
                    data=date(2026, 12, 11),
                    kwota=Decimal(kwota),
                    klucz_unikalnosci=f"{rodzaj}|{numer}",
                )
            )
        s.get(Jednostka, "JW-1001").budzet_wydatkowany = WYDATEK


def zmien(**pola) -> None:
    with sesja() as s:
        r = s.scalar(select(Rozliczenie).where(Rozliczenie.id_delegacji == ID))
        for nazwa, wartosc in pola.items():
            setattr(r, nazwa, wartosc)


def rozliczenie() -> Rozliczenie:
    with sesja() as s:
        r = s.scalar(select(Rozliczenie).where(Rozliczenie.id_delegacji == ID))
        s.expunge(r)
        return r


def korekty() -> list[KorektaRozliczenia]:
    with sesja() as s:
        lista = s.scalars(select(KorektaRozliczenia).order_by(KorektaRozliczenia.id_korekty)).all()
        s.expunge_all()
        return list(lista)


def wydatek() -> Decimal:
    with sesja() as s:
        return s.get(Jednostka, "JW-1001").budzet_wydatkowany


def wykonaj(broker, typ: str, dane: dict | None = None):
    agent = AGENCI["zgodnosc"](broker)
    funkcja, operacja = agent.obslugi()[typ]
    return asyncio.run(agent.wykonaj(Komunikat(typ=typ, id_delegacji=ID, dane=dane or {}), funkcja, operacja))


# ---------------------------------------------------------------- weryfikacja


def test_zgodne_wyliczenie_bez_korekt(broker):
    ktx = wykonaj(broker, Typ.ZADANIE_WERYFIKACJI)

    assert ktx.wynik == "Sukces"
    assert rozliczenie().status == StatusRozliczenia.ZWERYFIKOWANE
    assert korekty() == []
    assert wydatek() == WYDATEK
    wynik = next(k for k in broker.wyslane if k.typ == Typ.WYNIK_WERYFIKACJI)
    assert wynik.dane["korekty"] == 0
    powiadomienie = next(k for k in broker.wyslane if k.typ == Typ.POWIADOMIENIE)
    assert powiadomienie.dane["do"] == "ZOL-003"


def test_niezgodnosc_jest_korygowana_i_zapisywana(broker):
    zle_noclegi = POPRAWNE.zwrot_noclegu + Decimal("160.00")  # nocleg ponad limitem uznany w całości
    zmien(zwrot_noclegu=zle_noclegi, kwota_naleznosci=POPRAWNE.kwota_naleznosci + Decimal("160.00"))

    ktx = wykonaj(broker, Typ.ZADANIE_WERYFIKACJI)

    r = rozliczenie()
    assert ktx.wynik == "Ostrzeżenie"
    assert r.status == StatusRozliczenia.ZWERYFIKOWANE
    assert (r.zwrot_noclegu, r.kwota_naleznosci) == (POPRAWNE.zwrot_noclegu, POPRAWNE.kwota_naleznosci)
    lista = korekty()
    assert [k.pozycja for k in lista] == ["zwrot noclegu", "kwota należności"]
    assert (lista[0].wartosc_przed, lista[0].wartosc_po) == (zle_noclegi, POPRAWNE.zwrot_noclegu)
    assert lista[0].powod
    assert wydatek() == WYDATEK - Decimal("160.00")  # budżet jednostki poprawiony o różnicę


def test_korekta_to_decyzja_finansowa_do_sprawdzenia(broker):
    zmien(dieta=Decimal("0.00"))

    wykonaj(broker, Typ.ZADANIE_WERYFIKACJI)

    with sesja() as s:
        op = s.scalar(select(LogOperacji).where(LogOperacji.id_delegacji == ID))
        decyzja = s.scalar(select(LogDecyzji).where(LogDecyzji.id_operacji == op.id_operacji))
        assert op.id_agenta == "AG-07"
        assert decyzja.typ_decyzji == TYP_DECYZJI
        assert decyzja.wymaga_zatwierdzenia
        assert "dieta" in decyzja.uzasadnienie


def test_niekompletne_wyliczenie_wstrzymuje_weryfikacje(broker):
    zmien(ryczalty=None)

    ktx = wykonaj(broker, Typ.ZADANIE_WERYFIKACJI)

    assert ktx.wynik == "Ostrzeżenie" and "ryczałty" in ktx.opis
    assert rozliczenie().status == StatusRozliczenia.GOTOWE_DO_WERYFIKACJI
    assert Typ.WYNIK_WERYFIKACJI not in broker.typy()


def test_weryfikacja_tylko_zatwierdzonego_wyliczenia(broker):
    zmien(status=StatusRozliczenia.W_PRZYGOTOWANIU)

    ktx = wykonaj(broker, Typ.ZADANIE_WERYFIKACJI)

    assert ktx.wynik == "Ostrzeżenie"
    assert rozliczenie().status == StatusRozliczenia.W_PRZYGOTOWANIU


# ---------------------------------------------------------------- przekazanie


def test_akceptacja_przekazuje_do_finansow(broker):
    wykonaj(broker, Typ.ZADANIE_WERYFIKACJI)

    wykonaj(broker, Typ.AKCEPTACJA_ROZLICZENIA, {"zastrzezenia": None})

    r = rozliczenie()
    assert r.status == StatusRozliczenia.PRZEKAZANE
    assert r.data_przekazania is not None and r.zastrzezenia is None
    assert Typ.WYNIK_PRZEKAZANIA in broker.typy()


def test_zastrzezenia_przekazywane_razem_z_rozliczeniem(broker):
    """Scenariusz 3a–4a."""
    wykonaj(broker, Typ.ZADANIE_WERYFIKACJI)

    wykonaj(broker, Typ.AKCEPTACJA_ROZLICZENIA, {"zastrzezenia": "Nie uwzględniono biletu powrotnego"})

    r = rozliczenie()
    assert r.status == StatusRozliczenia.PRZEKAZANE_Z_ZASTRZEZENIAMI
    assert r.zastrzezenia == "Nie uwzględniono biletu powrotnego"
    assert r.data_przekazania is not None


def test_przekazanie_tylko_po_weryfikacji(broker):
    ktx = wykonaj(broker, Typ.AKCEPTACJA_ROZLICZENIA)

    assert ktx.wynik == "Ostrzeżenie"
    assert rozliczenie().status == StatusRozliczenia.GOTOWE_DO_WERYFIKACJI
    assert Typ.WYNIK_PRZEKAZANIA not in broker.typy()


# ---------------------------------------------------------------- API i pełny przebieg


@pytest.fixture
def klient(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def dostarcz(broker) -> None:
    asyncio.run(broker.dostarczaj([klasa(broker) for klasa in AGENCI.values()]))


def test_pelny_przebieg_przez_api_konczy_sie_rozliczeniem(broker, klient):
    zmien(dieta=Decimal("0.00"))

    assert klient.post(f"/delegacje/{ID}/rozliczenie/przekazanie").status_code == 202
    dostarcz(broker)
    weryfikacja = klient.get(f"/delegacje/{ID}/rozliczenie/weryfikacja").json()
    assert weryfikacja["status"] == StatusRozliczenia.ZWERYFIKOWANE
    assert weryfikacja["korekty"][0]["pozycja"] == "dieta"

    assert klient.post(f"/delegacje/{ID}/rozliczenie/akceptacja", json={}).status_code == 202
    dostarcz(broker)

    assert rozliczenie().status == StatusRozliczenia.PRZEKAZANE
    with sesja() as s:
        assert s.get(Delegacja, ID).status == Status.ROZLICZONY


def test_api_odrzuca_kroki_w_zlej_kolejnosci(klient):
    assert klient.post(f"/delegacje/{ID}/rozliczenie/akceptacja", json={}).status_code == 409
    zmien(status=StatusRozliczenia.W_PRZYGOTOWANIU)
    assert klient.post(f"/delegacje/{ID}/rozliczenie/przekazanie").status_code == 409
    assert klient.get("/delegacje/NIE-MA/rozliczenie/weryfikacja").status_code == 404


def test_api_przekazuje_zastrzezenia(broker, klient):
    zmien(status=StatusRozliczenia.ZWERYFIKOWANE)

    r = klient.post(f"/delegacje/{ID}/rozliczenie/akceptacja", json={"zastrzezenia": "  Brak biletu  "})

    assert r.status_code == 202
    assert broker.wyslane[-1].typ == Typ.AKCEPTACJA_ROZLICZENIA
    assert broker.wyslane[-1].dane == {"zastrzezenia": "Brak biletu"}


# ---------------------------------------------------------------- przyciski w /app


@pytest.fixture
def przegladarka(broker):
    app.dependency_overrides[get_broker] = lambda: broker
    with TestClient(app, follow_redirects=False) as c:
        zaloguj_z_csrf(c, "ZOL-003")
        yield c
    app.dependency_overrides.clear()


STRONA = f"/app/wnioski/{ID}/rozliczenie"


def test_przyciski_prowadza_od_weryfikacji_do_rozliczenia(broker, przegladarka):
    zmien(dieta=Decimal("0.00"))
    assert "Przekaż do weryfikacji" in przegladarka.get(STRONA).text

    r = przegladarka.post(f"{STRONA}/przekazanie")
    assert r.status_code == 303
    assert "Agent Zgodności weryfikuje" in przegladarka.get(r.headers["location"]).text  # czeka na agenta
    dostarcz(broker)

    strona = przegladarka.get(r.headers["location"]).text
    assert "zweryfikowane" in strona and "Agent Zgodności poprawił wyliczenie (1)" in strona
    assert "Akceptuję – przekaż do komórki finansowej" in strona

    assert przegladarka.post(f"{STRONA}/akceptacja").status_code == 303
    dostarcz(broker)

    assert rozliczenie().status == StatusRozliczenia.PRZEKAZANE
    with sesja() as s:
        assert s.get(Delegacja, ID).status == Status.ROZLICZONY
    strona = przegladarka.get(STRONA).text  # rozliczonej delegacji nadal można obejrzeć rozliczenie
    assert "przekazane do komórki finansowej" in strona and "Akceptuję" not in strona
    assert "Zobacz rozliczenie" in przegladarka.get(f"/app/wnioski/{ID}").text


def test_przycisk_przekazania_z_zastrzezeniami(broker, przegladarka):
    zmien(status=StatusRozliczenia.ZWERYFIKOWANE)
    assert "Przekaż z zastrzeżeniami" in przegladarka.get(STRONA).text

    przegladarka.post(f"{STRONA}/akceptacja", data={"zastrzezenia": "  Brak biletu powrotnego  "})
    dostarcz(broker)

    r = rozliczenie()
    assert r.status == StatusRozliczenia.PRZEKAZANE_Z_ZASTRZEZENIAMI
    assert r.zastrzezenia == "Brak biletu powrotnego"
    assert "Brak biletu powrotnego" in przegladarka.get(STRONA).text


def test_przyciski_w_zlej_kolejnosci_pokazuja_komunikat(broker, przegladarka):
    assert "nie jest jeszcze zweryfikowane" in przegladarka.post(f"{STRONA}/akceptacja").text
    zmien(status=StatusRozliczenia.W_PRZYGOTOWANIU)
    assert "Najpierw zatwierdź wyliczenie" in przegladarka.post(f"{STRONA}/przekazanie").text
    assert "Przekaż do weryfikacji" not in przegladarka.get(STRONA).text
    assert broker.wyslane == []
