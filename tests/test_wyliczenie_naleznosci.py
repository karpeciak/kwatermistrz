"""S22IT1S0P2-36, podzadanie S22IT1S0P2-67: testy reguł wyliczenia należności (`agents/rozliczenia.py`).

Testy jednostkowe – bez bazy i brokera. Kwoty liczone ze stawek w `stawki.py`, żeby zmiana stawki
nie psuła testów, a test sprawdzał samą regułę. Przebieg przez API i interfejs – `test_rozliczenie_naleznosci.py`.
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from kwatermistrz.agents.rozliczenia import czesc_diety, wylicz_naleznosci, zl
from kwatermistrz.stawki import (
    DIETA_KRAJOWA,
    LIMIT_NOCLEGOWY,
    POTRACENIE_ZA_POSILEK,
    RYCZALT_DOJAZDY,
    RYCZALT_NOCLEG,
)

WYJAZD = datetime(2026, 12, 7, 6, 0)


def podroz(godziny: float, **kwargs):
    dane = {"posilki": {}, "bezplatne_zakwaterowanie": False, "dokumenty": []} | kwargs
    return wylicz_naleznosci(WYJAZD, WYJAZD + timedelta(hours=godziny), **dane)


# ---------------------------------------------------------------- dieta – część doby


@pytest.mark.parametrize(
    ("godziny", "czesc"),
    [
        (0, "0"),
        (7.99, "0"),  # podróż do doby: poniżej 8 h dieta nie przysługuje
        (8, "0.5"),  # 8–12 h – połowa
        (12, "0.5"),
        (12.01, "1"),  # ponad 12 h – całość
        (24, "1"),
        (24.5, "1.5"),  # dłuższa podróż: niepełna doba do 8 h – połowa
        (32, "1.5"),
        (32.01, "2"),  # niepełna doba ponad 8 h – całość
        (48, "2"),
        (100, "4.5"),  # 4 pełne doby + 4 h
    ],
)
def test_czesc_diety(godziny, czesc):
    assert czesc_diety(godziny) == Decimal(czesc)


def test_dieta_jednodniowa_bez_noclegu():
    w = podroz(10)
    assert w.dieta == zl(DIETA_KRAJOWA * Decimal("0.5"))
    assert w.zwrot_noclegu == Decimal("0") and w.zwrot_przejazdu == Decimal("0")
    # komunikacja miejscowa za rozpoczętą dobę, bez ryczałtu za nocleg (powrót tego samego dnia)
    assert w.ryczalty == zl(RYCZALT_DOJAZDY)


def test_krotka_podroz_bez_diety():
    w = podroz(5)
    assert w.dieta == Decimal("0") and w.kwota_naleznosci == zl(RYCZALT_DOJAZDY)


# ---------------------------------------------------------------- posiłki


def test_zapewnione_posilki_pomniejszaja_diete():
    posilki = {"śniadanie": 1, "obiad": 2}
    w = podroz(30, posilki=posilki)
    potracenie = sum(DIETA_KRAJOWA * POTRACENIE_ZA_POSILEK[p] * n for p, n in posilki.items())
    assert w.dieta == zl(DIETA_KRAJOWA * Decimal("1.5") - potracenie)


def test_dieta_nie_jest_ujemna_przy_wielu_posilkach():
    w = podroz(10, posilki={posilek: 10 for posilek in POTRACENIE_ZA_POSILEK})
    assert w.dieta == Decimal("0")


def test_nieznany_rodzaj_posilku_jest_pomijany():
    assert podroz(30, posilki={"podwieczorek": 3}).dieta == podroz(30).dieta


# ---------------------------------------------------------------- nocleg i limit (S22IT1S0P2-63)


def test_rachunek_w_limicie_zwracany_w_calosci():
    # wyjazd 7.12, powrót 9.12 – 2 noclegi
    w = podroz(54, dokumenty=[("rachunek za nocleg", Decimal("450.00"))])
    assert w.zwrot_noclegu == Decimal("450.00") and w.kwoty_uznane == [Decimal("450.00")]


def test_rachunek_ponad_limit_uznany_do_wysokosci_limitu():
    limit = LIMIT_NOCLEGOWY * 2
    w = podroz(54, dokumenty=[("rachunek za nocleg", limit + Decimal("123.45"))])
    assert w.zwrot_noclegu == zl(limit) and w.kwoty_uznane == [zl(limit)]


def test_kolejne_rachunki_uznawane_do_wyczerpania_limitu():
    # 2 noclegi; pierwszy rachunek mieści się, drugi tylko w części, trzeci wcale
    limit = LIMIT_NOCLEGOWY * 2
    pierwszy = LIMIT_NOCLEGOWY + Decimal("50")
    dokumenty = [
        ("rachunek za nocleg", pierwszy),
        ("Rachunek za nocleg ", Decimal("400")),
        ("rachunek za nocleg", Decimal("10")),
    ]
    w = podroz(54, dokumenty=dokumenty)
    assert w.kwoty_uznane == [zl(pierwszy), zl(limit - pierwszy), Decimal("0.00")]
    assert w.zwrot_noclegu == zl(limit)


def test_rachunek_za_nocleg_bez_noclegu_nie_jest_zwracany():
    w = podroz(10, dokumenty=[("rachunek za nocleg", Decimal("200"))])
    assert w.zwrot_noclegu == Decimal("0") and w.kwoty_uznane == [Decimal("0.00")]


# ---------------------------------------------------------------- przejazd i pozostałe dokumenty


def test_bilety_i_faktury_to_zwrot_przejazdu():
    dokumenty = [("bilet", Decimal("89.90")), ("faktura", Decimal("20.10")), ("inny", Decimal("5"))]
    w = podroz(30, dokumenty=dokumenty)
    assert w.zwrot_przejazdu == Decimal("115.00") and w.zwrot_noclegu == Decimal("0")
    assert w.kwoty_uznane == [Decimal("89.90"), Decimal("20.10"), Decimal("5.00")]


# ---------------------------------------------------------------- ryczałty


def test_ryczalt_za_nocleg_bez_rachunku():
    # 7.12 6:00 – 9.12 12:00: 3 rozpoczęte doby, 2 noclegi bez rachunku
    w = podroz(54)
    assert w.ryczalty == zl(RYCZALT_DOJAZDY * 3 + RYCZALT_NOCLEG * 2)


def test_brak_ryczaltu_za_nocleg_przy_bezplatnym_zakwaterowaniu():
    assert podroz(54, bezplatne_zakwaterowanie=True).ryczalty == zl(RYCZALT_DOJAZDY * 3)


def test_brak_ryczaltu_za_nocleg_gdy_jest_rachunek():
    w = podroz(54, dokumenty=[("rachunek za nocleg", Decimal("100"))])
    assert w.ryczalty == zl(RYCZALT_DOJAZDY * 3)


# ---------------------------------------------------------------- suma


def test_kwota_naleznosci_to_suma_skladnikow():
    dokumenty = [("bilet", Decimal("89.90")), ("rachunek za nocleg", Decimal("480.00"))]
    w = podroz(54, posilki={"śniadanie": 2}, dokumenty=dokumenty)
    assert w.kwota_naleznosci == zl(w.dieta + w.zwrot_przejazdu + w.zwrot_noclegu + w.ryczalty)
    assert all(skladnik == zl(skladnik) for skladnik in (w.dieta, w.zwrot_przejazdu, w.zwrot_noclegu, w.ryczalty))
